"""Offline tests for the structuring step: the model is replaced by a fake that returns canned JSON."""

import json

import pytest

from cookbook.extract.structure import PROMPT, StructureError, structure_recipe
from cookbook.llm import GeminiClient, LLMResult
from cookbook.models import Recipe
from test_validate import _recipe, _transcript  # a made-up recipe and its transcript


class FakeTextLLM:
    name = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def structure(self, text, prompt, schema):
        self.calls.append((text, prompt, schema))
        return LLMResult(self.answers.pop(0), "fake-model", 100, 50, 0.1)


def _json(recipe=None) -> str:
    return (recipe or _recipe()).model_dump_json()


def test_the_model_gets_the_transcript_the_rules_and_the_schema():
    llm = FakeTextLLM(_json())
    structure_recipe(_transcript(), llm)
    [(text, prompt, schema)] = llm.calls
    assert text == _transcript().as_prompt() and text.startswith("p1:L1\tRindergulasch")
    assert prompt == PROMPT and schema is Recipe


def test_a_valid_answer_becomes_a_checked_recipe():
    result = structure_recipe(_transcript(), FakeTextLLM(_json()))
    assert result.recipe == _recipe()
    assert [(i.code, i.refers_to) for i in result.issues] == [("no_quantity", "i4")]
    assert len(result.calls) == 1


def test_the_checks_run_on_what_the_model_returned():
    bad = _recipe()
    bad.ingredients[0].quantity = 600  # the model changed 500 to 600
    result = structure_recipe(_transcript(), FakeTextLLM(_json(bad)))
    assert [i.code for i in result.issues if i.severity == "high"] == ["quantity_mismatch"]


def test_an_answer_that_is_not_json_gets_one_retry_with_the_error():
    llm = FakeTextLLM("Here is your recipe: {", _json())
    result = structure_recipe(_transcript(), llm)
    assert len(result.calls) == 2 and result.recipe == _recipe()
    assert "previous answer was not valid" in llm.calls[1][1] and llm.calls[1][1].startswith(PROMPT)


def test_json_of_the_wrong_shape_is_retried_too():
    wrong = json.dumps({"ingredients": [{"id": "i1", "name": "Salz"}]})  # name_en and quote are required
    result = structure_recipe(_transcript(), FakeTextLLM(wrong, _json()))
    assert len(result.calls) == 2


def test_two_bad_answers_are_an_error():
    with pytest.raises(StructureError):
        structure_recipe(_transcript(), FakeTextLLM("not json", "still not json"))


def test_retries_can_be_turned_off():
    llm = FakeTextLLM("not json", _json())
    with pytest.raises(StructureError):
        structure_recipe(_transcript(), llm, retries=0)
    assert len(llm.calls) == 1


def test_the_prompt_forbids_guessing():
    flat = " ".join(PROMPT.split())  # the prompt wraps lines; compare without the line breaks
    for rule in ("Never add an ingredient", "Never guess an amount", "copied exactly", "`stated` is false"):
        assert rule in flat


# --- the Gemini client's structured call, with the model call replaced ----------
def _client(tmp_path, generate, fallbacks=("fb",)):
    c = object.__new__(GeminiClient)
    c.model, c.fallbacks, c.attempts = "main", list(fallbacks), 2
    c.cache_dir, c._sleep, c._generate_structured = tmp_path, (lambda s: None), generate
    return c


def _answer(model):
    return LLMResult("{}", model, 1, 1, 0.0)


def test_structured_answers_are_cached_per_text_prompt_and_schema(tmp_path):
    calls = []

    def generate(model, text, prompt, schema):
        calls.append(text)
        return _answer(model)

    client = _client(tmp_path, generate)
    assert not client.structure("a", "prompt", Recipe).cached
    assert client.structure("a", "prompt", Recipe).cached
    assert not client.structure("b", "prompt", Recipe).cached  # another text
    assert not client.structure("a", "other prompt", Recipe).cached
    assert not client.structure("a", "prompt", type("Other", (Recipe,), {})).cached  # another schema
    assert calls == ["a", "b", "a", "a"]


def test_structuring_falls_back_to_another_model_like_reading_does(tmp_path):
    class Down(Exception):
        code = 503

    def generate(model, text, prompt, schema):
        if model == "main":
            raise Down()
        return _answer(model)

    res = _client(tmp_path, generate).structure("a", "prompt", Recipe)
    assert (res.model, res.fell_back_from, res.attempts) == ("fb", "main", 3)
