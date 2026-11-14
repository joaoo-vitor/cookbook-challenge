"""Offline tests for the Claude client. The SDK's client is replaced by a stub that records the request."""

import base64
import json
import sys
from types import SimpleNamespace as NS

import pytest

from cookbook.extract.structure import structure_recipe
from cookbook.llm import (
    ClaudeClient, EmptyResponse, GeminiClient, LLMResult, classify_error, make_client,
)
from cookbook.models import Recipe
from test_validate import _recipe, _transcript

JPEG = b"\xff\xd8\xff" + b"fake jpeg bytes"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake png bytes"


class StubMessages:
    """Answers with `responses` in turn (an exception is raised) and keeps every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def _answer(text="Hello", stop="end_turn", usage=(120, 30), blocks=None):
    blocks = blocks if blocks is not None else [NS(type="thinking", thinking=""), NS(type="text", text=text)]
    return NS(content=blocks, stop_reason=stop, usage=NS(input_tokens=usage[0], output_tokens=usage[1]))


def _client(tmp_path, *responses, fallbacks=()):
    c = object.__new__(ClaudeClient)  # skips the constructor, which needs the SDK and credentials
    c.model, c.fallbacks, c.attempts = "main", list(fallbacks), 2
    c.cache_dir, c._sleep = tmp_path, (lambda s: None)
    c._client = NS(messages=StubMessages(*responses))
    return c, c._client.messages


def test_a_page_is_sent_as_a_base64_image_then_the_prompt(tmp_path):
    c, messages = _client(tmp_path, _answer("Pastry"))
    result = c.transcribe(JPEG, "Transcribe this.")
    [request] = messages.requests
    [message] = request["messages"]
    image, prompt = message["content"]
    assert message["role"] == "user"
    assert image["type"] == "image" and image["source"]["type"] == "base64"
    assert base64.b64decode(image["source"]["data"]) == JPEG
    assert prompt == {"type": "text", "text": "Transcribe this."}
    assert result.text == "Pastry" and result.model == "main"


@pytest.mark.parametrize("image, media_type", [(JPEG, "image/jpeg"), (PNG, "image/png")])
def test_the_image_type_comes_from_the_first_bytes(tmp_path, image, media_type):
    c, messages = _client(tmp_path, _answer())
    c.transcribe(image, "p")
    assert messages.requests[0]["messages"][0]["content"][0]["source"]["media_type"] == media_type


def test_no_temperature_is_sent_and_reading_uses_low_effort(tmp_path):
    c, messages = _client(tmp_path, _answer())
    c.transcribe(JPEG, "p")
    request = messages.requests[0]
    assert "temperature" not in request  # the current Claude models reject a non-default one
    assert request["output_config"] == {"effort": "low"}
    assert request["max_tokens"] >= 4096


def test_only_the_text_blocks_are_the_answer_and_tokens_are_reported(tmp_path):
    blocks = [NS(type="thinking", thinking="hmm"), NS(type="text", text="line one\n"), NS(type="text", text="line two")]
    c, _ = _client(tmp_path, _answer(blocks=blocks, usage=(1500, 220)))
    result = c.transcribe(JPEG, "p")
    assert result.text == "line one\nline two"
    assert (result.input_tokens, result.output_tokens, result.thinking_tokens) == (1500, 220, None)


def _objects(node, found):
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            _objects(value, found)
    elif isinstance(node, list):
        for value in node:
            _objects(value, found)
    return found


def test_structuring_sends_a_json_schema_with_closed_objects(tmp_path):
    c, messages = _client(tmp_path, _answer("{}"))
    c.structure("p1:L1\tSalt", "Fill the schema.", Recipe)
    request = messages.requests[0]
    fmt = request["output_config"]["format"]
    assert fmt["type"] == "json_schema" and request["output_config"]["effort"] == "medium"
    objects = _objects(fmt["schema"], [])
    assert len(objects) > 1 and all(o.get("additionalProperties") is False for o in objects)
    prompt, transcript = request["messages"][0]["content"]
    assert prompt["text"] == "Fill the schema." and transcript["text"] == "Transcript:\np1:L1\tSalt"


def test_a_claude_answer_goes_through_the_whole_structuring_step(tmp_path):
    c, _ = _client(tmp_path, _answer(_recipe().model_dump_json()))
    result = structure_recipe(_transcript(), c)
    assert result.recipe == _recipe()
    assert [(i.code, i.refers_to) for i in result.issues] == [("no_quantity", "i4")]


@pytest.mark.parametrize("response, why", [
    (_answer(blocks=[]), "no text"),
    (_answer(text="  "), "no text"),
    (_answer(blocks=[NS(type="text", text="")], stop="refusal"), "refusal"),
    (_answer(text="half an ans", stop="max_tokens"), "max_tokens"),
])
def test_an_empty_refused_or_cut_off_answer_is_an_error_not_a_reading(tmp_path, response, why):
    c, _ = _client(tmp_path, response)
    c.attempts = 1
    with pytest.raises(Exception) as caught:  # every model failed: the reason is in the message
        c.transcribe(JPEG, "p")
    assert why in str(caught.value) or why in str(caught.value.__cause__)


def test_missing_credentials_give_a_clear_message_and_are_not_retried(tmp_path):
    error = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, ...')
    c, messages = _client(tmp_path, error)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        c.transcribe(JPEG, "p")
    assert len(messages.requests) == 1  # fatal: no retry, no fallback


def test_an_overloaded_error_is_retried_and_then_falls_back(tmp_path):
    class Overloaded(Exception):
        status_code = 529

    c, messages = _client(tmp_path, Overloaded(), Overloaded(), _answer("from the fallback"), fallbacks=["backup"])
    result = c.transcribe(JPEG, "p")
    assert [r["model"] for r in messages.requests] == ["main", "main", "backup"]
    assert (result.text, result.model, result.fell_back_from, result.attempts) == ("from the fallback", "backup", "main", 3)
    assert classify_error(Overloaded()) == "retry"


def test_answers_are_cached_per_provider_and_model(tmp_path):
    c, messages = _client(tmp_path, _answer("first"))
    assert not c.transcribe(JPEG, "p").cached
    again = c.transcribe(JPEG, "p")
    assert again.cached and again.text == "first" and len(messages.requests) == 1
    # a Gemini client with the same model name and the same input must not read Claude's cache entry
    g = object.__new__(GeminiClient)
    g.model, g.fallbacks, g.attempts, g.cache_dir, g._sleep = "main", [], 1, tmp_path, (lambda s: None)
    g._generate = lambda model, image, mime, prompt: LLMResult("gemini's own answer", model, 1, 1, 0.0)
    from_gemini = g.transcribe(JPEG, "p")
    assert not from_gemini.cached and from_gemini.text == "gemini's own answer"


# --- the factory and the optional dependency ------------------------------------
def test_make_client_builds_the_right_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    gemini = make_client("gemini", "some-model", ["fb"])
    assert (gemini.name, gemini.model, gemini.fallbacks) == ("gemini", "some-model", ["fb"])
    claude = make_client("claude")
    assert (claude.name, claude.model, claude.fallbacks) == ("claude", "claude-haiku-5-5", [])


def test_the_claude_model_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    assert make_client("claude").model == "claude-sonnet-5-5"
    assert make_client("claude", "claude-haiku-5-5").model == "claude-haiku-5-5"  # an explicit model wins


def test_an_unknown_provider_is_an_error():
    with pytest.raises(ValueError, match="unknown provider"):
        make_client("openai")


def test_without_the_anthropic_package_the_message_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `import anthropic` fail
    with pytest.raises(RuntimeError, match=r"pip install -e"):
        ClaudeClient()


def test_the_same_reader_guard_looks_at_the_provider_as_well_as_the_model(tmp_path):
    from cookbook.extract.transcribe import transcribe_pdf
    from test_transcribe import FakeLLM, _scan

    first = FakeLLM("Salz und Pfeffer dazugeben", model="same-name")
    first.name = "gemini"
    other = FakeLLM("Salz und Pfeffer dazugeben", model="same-name")
    other.name = "claude"
    transcribe_pdf(_scan(tmp_path), first, second=other)  # different providers: no error up front

    other.name = "gemini"  # the same provider and model: it really is the same reader twice
    with pytest.raises(ValueError, match="different model"):
        transcribe_pdf(_scan(tmp_path), first, second=other)
