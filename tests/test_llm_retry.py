"""Tests for retry with backoff and model fallback. No network: model calls are scripted."""

import pytest

from cookbook.llm import (
    AllModelsFailed, GeminiClient, LLMResult, call_with_fallback, classify_error, read_cache,
)


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


class ReadTimeout(Exception):
    pass


def _result(model):
    return LLMResult("text", model, 1, 1, 0.0)


def _scripted(script, log):
    """fn(model) that plays back script[model]: an exception is raised, anything else answers."""
    def fn(model):
        log.append(model)
        step = script[model].pop(0)
        if isinstance(step, Exception):
            raise step
        return _result(model)
    return fn


def _run(script, models, sleeps, **kw):
    log = []
    kw.setdefault("jitter", lambda: 0)
    outcome = call_with_fallback(models, _scripted(script, log), sleep=sleeps.append, **kw)
    return outcome, log


@pytest.mark.parametrize("exc, kind", [
    (ApiError(503), "retry"), (ApiError(429), "retry"), (ApiError(500), "retry"),
    (ApiError(408), "retry"), (ReadTimeout(), "retry"),
    (ApiError(404), "next_model"),
    (ApiError(400), "fatal"), (ApiError(401), "fatal"), (ApiError(403), "fatal"),
    (ValueError("our own bug"), "fatal"),
])
def test_classify_error(exc, kind):
    assert classify_error(exc) == kind


def test_status_code_attribute_is_understood_too():
    e = Exception("x")
    e.status_code = 503
    assert classify_error(e) == "retry"


def test_success_on_the_first_call_does_not_wait():
    sleeps = []
    outcome, log = _run({"a": ["ok"]}, ["a"], sleeps)
    assert (outcome.model, outcome.calls, sleeps, log) == ("a", 1, [], ["a"])


def test_temporary_errors_are_retried_with_doubling_waits():
    sleeps = []
    outcome, _ = _run({"a": [ApiError(503), ApiError(429), "ok"]}, ["a"], sleeps)
    assert (outcome.model, outcome.calls) == ("a", 3)
    assert sleeps == [1.0, 2.0]


def test_jitter_adds_up_to_a_quarter():
    sleeps = []
    _run({"a": [ApiError(503), ApiError(503), "ok"]}, ["a"], sleeps, jitter=lambda: 1)
    assert sleeps == [1.25, 2.5]


def test_failed_calls_are_recorded_in_order():
    outcome, _ = _run({"a": [ApiError(404)], "b": [ApiError(503), "ok"]}, ["a", "b"], [])
    assert [f.split(":")[0] for f in outcome.failures] == ["a", "b"]
    assert "ApiError" in outcome.failures[0] and "error 503" in outcome.failures[1]


def test_the_wait_is_capped():
    sleeps = []
    _run({"a": [ApiError(503)] * 5 + ["ok"]}, ["a"], sleeps, attempts=6, max_delay=4)
    assert sleeps == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_after_the_retries_the_next_model_is_tried_without_a_final_wait():
    sleeps = []
    outcome, log = _run({"a": [ApiError(503)] * 3, "b": ["ok"]}, ["a", "b"], sleeps)
    assert (outcome.model, outcome.calls) == ("b", 4)
    assert log == ["a", "a", "a", "b"]
    assert len(sleeps) == 2  # between a's three calls, none before switching


def test_a_missing_model_skips_its_retries():
    sleeps = []
    outcome, log = _run({"a": [ApiError(404)], "b": ["ok"]}, ["a", "b"], sleeps)
    assert (outcome.model, log, sleeps) == ("b", ["a", "b"], [])


def test_a_fatal_error_is_raised_at_once_and_no_other_model_is_tried():
    sleeps = []
    err = ApiError(403)
    log = []
    with pytest.raises(ApiError) as caught:
        call_with_fallback(["a", "b"], _scripted({"a": [err], "b": ["ok"]}, log), sleep=sleeps.append)
    assert caught.value is err and log == ["a"] and sleeps == []


def test_when_everything_fails_the_message_lists_each_model():
    last = ApiError(503)
    with pytest.raises(AllModelsFailed) as caught:
        call_with_fallback(["a", "b"], _scripted({"a": [ApiError(503)] * 2, "b": [ApiError(429), last]}, []),
                           attempts=2, sleep=lambda s: None)
    message = str(caught.value)
    assert "4 call(s)" in message and "a: ApiError" in message and "b: ApiError" in message
    assert caught.value.__cause__ is last


# --- the client, with the model call replaced ---------------------------------
def _client(tmp_path, generate, fallbacks=("fb",)):
    c = object.__new__(GeminiClient)  # skips the constructor, which needs a key and the SDK
    c.model, c.fallbacks, c.attempts = "main", list(fallbacks), 2
    c.cache_dir, c._sleep, c._generate = tmp_path, (lambda s: None), generate
    return c


JPEG = b"\xff\xd8\xff" + b"image"


def test_a_fallback_answer_says_so_and_counts_every_call(tmp_path):
    calls = []

    def generate(model, image, mime, prompt):
        calls.append(model)
        if model == "main":
            raise ApiError(503)
        return _result(model)

    res = _client(tmp_path, generate).transcribe(JPEG, "p")
    assert (res.model, res.fell_back_from, res.attempts) == ("fb", "main", 3)
    assert calls == ["main", "main", "fb"]
    assert len(res.failures) == 2 and all(f.startswith("main:") for f in res.failures)


def test_the_primary_model_is_tried_again_before_a_cached_fallback_is_used(tmp_path):
    state = {"main_up": False}
    calls = []

    def generate(model, image, mime, prompt):
        calls.append(model)
        if model == "main" and not state["main_up"]:
            raise ApiError(503)
        return _result(model)

    client = _client(tmp_path, generate)
    first = client.transcribe(JPEG, "p")
    assert first.model == "fb" and not first.cached
    second = client.transcribe(JPEG, "p")  # main still down: the fallback answer comes from the cache
    assert second.model == "fb" and second.cached and second.fell_back_from == "main"
    assert calls.count("fb") == 1
    state["main_up"] = True
    third = client.transcribe(JPEG, "p")  # main is back: it answers, not the cached fallback
    assert third.model == "main" and third.fell_back_from is None


def test_answers_are_cached_under_the_model_that_gave_them(tmp_path):
    from cookbook.llm import cache_key

    client = _client(tmp_path, lambda model, image, mime, prompt: _result(model))
    client.transcribe(JPEG, "p")
    assert read_cache(tmp_path, cache_key("gemini", "main", "p", JPEG)).model == "main"
    assert read_cache(tmp_path, cache_key("gemini", "fb", "p", JPEG)) is None


def test_a_fatal_error_is_not_retried_or_switched(tmp_path):
    calls = []

    def generate(model, image, mime, prompt):
        calls.append(model)
        raise ApiError(401)

    with pytest.raises(ApiError):
        _client(tmp_path, generate).transcribe(JPEG, "p")
    assert calls == ["main"]


def test_the_constructor_drops_the_primary_from_its_fallbacks():
    c = GeminiClient(model="a", api_key="not-a-real-key", fallbacks=["a", "b"])
    assert c.fallbacks == ["b"]
