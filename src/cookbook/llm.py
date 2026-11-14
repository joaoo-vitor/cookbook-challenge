"""The only module that talks to a model provider.

Everything else depends on the small `VisionLLM` protocol, so a provider can be
swapped (Gemini now; Hugging Face or Claude later) without touching extract code.

Responses are cached on disk, keyed on provider, model, prompt and image bytes,
so re-running the same page costs nothing and gives the same text.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

CACHE_DIR = Path(".cache") / "llm"  # gitignored
# Models that worked for our key in November 2026. Names change and get retired:
# override with GEMINI_MODEL / GEMINI_SECOND_MODEL and check `--list-models`.
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_SECOND_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash",)  # tried, in order, when a reader's own model keeps failing
# Claude: the cheapest current model. Override with CLAUDE_MODEL / CLAUDE_SECOND_MODEL / CLAUDE_FALLBACK_MODELS.
# There is no default fallback for Claude: the next model up costs 20 times more.
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
CLAUDE_TRANSCRIBE_EFFORT = "low"  # reading a page needs little reasoning, and reasoning is billed as output
CLAUDE_STRUCTURE_EFFORT = "medium"

ATTEMPTS_PER_MODEL = 3  # calls to one model before giving up on it
BASE_DELAY = 1.0  # seconds before the first retry; doubles each time
MAX_DELAY = 20.0
REQUEST_TIMEOUT = 90.0  # seconds. A call normally takes 3 to 15 s; we saw hangs of 5 minutes


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    seconds: float
    cached: bool = False
    thinking_tokens: int | None = None  # billed as output by some providers
    attempts: int = 1  # calls made to get this answer, across retries and fallback models
    fell_back_from: str | None = None  # the model we asked first, when another one answered
    failures: list[str] = field(default_factory=list)  # errors from the calls that failed first


class VisionLLM(Protocol):
    name: str

    def transcribe(self, image: bytes, prompt: str) -> LLMResult: ...


class TextLLM(Protocol):
    name: str

    def structure(self, text: str, prompt: str, schema: type[BaseModel]) -> LLMResult: ...


def sniff_mime(image: bytes) -> str:
    """Image type from the first bytes (the file's "magic bytes"), PNG or JPEG."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ValueError("unsupported image format: expected PNG or JPEG bytes")


# --- cache -------------------------------------------------------------------
def cache_key(provider: str, model: str, prompt: str, image: bytes) -> str:
    h = hashlib.sha256()
    for part in (provider.encode(), model.encode(), prompt.encode(), image):
        h.update(len(part).to_bytes(8, "big"))  # length-prefix so parts cannot blur together
        h.update(part)
    return h.hexdigest()


def read_cache(cache_dir: Path, key: str) -> LLMResult | None:
    path = cache_dir / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("cached", None)
        return LLMResult(**data, cached=True)
    except (OSError, ValueError, TypeError):
        return None  # missing or damaged entry: treat as a miss


def write_cache(cache_dir: Path, key: str, result: LLMResult) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")


# --- retry and fallback ------------------------------------------------------
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504, 529}  # timeouts, rate limits, server trouble (529: overloaded)
_NETWORK_ERROR = re.compile(r"Timeout|Connect|Read|Remote|Network|Protocol", re.I)


class AllModelsFailed(RuntimeError):
    """Every model (and every retry) failed. The message lists what each one said."""


class EmptyResponse(RuntimeError):
    """The model answered with no text (blocked, cut off, or just empty).

    Treated as a temporary failure: retried, then another model is tried. It is never
    cached and never reaches the checks as if it were a reading.
    """


def _text_or_raise(response, model: str) -> str:
    text = response.text or ""
    if text.strip():
        return text
    try:
        reason = response.candidates[0].finish_reason
    except Exception:  # noqa: BLE001 - the reason is only for the message
        reason = None
    raise EmptyResponse(f"{model} returned no text" + (f" (finish reason: {reason})" if reason else ""))


def classify_error(exc: Exception) -> str:
    """What to do about an error from a model call.

    "retry": a temporary problem, wait and try the same model again.
    "next_model": this model will not work (retired, or not available to this key); skip its retries.
    "fatal": our mistake or a bad key (400, 401, 403, ...); retrying or switching cannot help.
    """
    if isinstance(exc, EmptyResponse):
        return "retry"
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        if code in RETRYABLE_CODES:
            return "retry"
        return "next_model" if code == 404 else "fatal"
    return "retry" if _NETWORK_ERROR.search(type(exc).__name__) else "fatal"


@dataclass
class Outcome:
    result: LLMResult
    model: str  # the model that answered
    calls: int  # calls made altogether
    failures: list[str] = field(default_factory=list)  # one line per failed call


def call_with_fallback(models: list[str], fn, attempts: int = ATTEMPTS_PER_MODEL,
                       base_delay: float = BASE_DELAY, max_delay: float = MAX_DELAY,
                       sleep=time.sleep, jitter=random.random) -> Outcome:
    """Call `fn(model)` for each model in turn, retrying temporary errors with backoff.

    Waits base_delay, 2x, 4x ... (at most max_delay, plus up to 25% jitter) between
    retries of one model. After `attempts` failed calls, or at once for a model that
    cannot work, it moves on to the next model. A fatal error is raised unchanged.
    """
    failures: list[str] = []
    calls = 0
    last: Exception | None = None
    for model in models:
        for attempt in range(attempts):
            calls += 1
            try:
                return Outcome(fn(model), model, calls, failures)
            except Exception as exc:  # noqa: BLE001 - classified below
                kind = classify_error(exc)
                if kind == "fatal":
                    raise
                last = exc
                failures.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
                if kind == "next_model":
                    break
                if attempt < attempts - 1:
                    sleep(min(base_delay * 2 ** attempt, max_delay) * (1 + 0.25 * jitter()))
    raise AllModelsFailed(f"all models failed after {calls} call(s): " + " | ".join(failures)) from last


# --- Gemini ------------------------------------------------------------------
def load_env() -> None:
    """Load a local .env file if python-dotenv is available (never committed)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


class _CachedProvider:
    """What every provider shares: the disk cache, retries with backoff, and fallback models.

    A provider only says how to make one call: `_generate` (read an image) and
    `_generate_structured` (fill a schema). Needs `name`, `model`, `fallbacks`, `attempts`,
    `cache_dir` and `_sleep`.
    """

    name: str

    def transcribe(self, image: bytes, prompt: str) -> LLMResult:
        """Read an image. Retries temporary errors, then falls back to other models.

        The answer is cached under the model that gave it, so a fallback answer is never
        mistaken for the primary model's. `result.fell_back_from` says when it was a fallback.
        """
        mime = sniff_mime(image)
        return self._run(prompt, image, lambda model: self._generate(model, image, mime, prompt))

    def structure(self, text: str, prompt: str, schema: type[BaseModel]) -> LLMResult:
        """Fill `schema` from `text`. The answer is JSON in `result.text`; the caller validates it.

        Same caching, retries and fallback as `transcribe`. The schema is part of the cache key.
        """
        key_prompt = prompt + "\n" + json.dumps(schema.model_json_schema(), sort_keys=True)
        return self._run(key_prompt, text.encode("utf-8"),
                         lambda model: self._generate_structured(model, text, prompt, schema))

    def _run(self, key_prompt: str, payload: bytes, call) -> LLMResult:
        """Cache lookup, then `call(model)` with retries and fallback, cached under the model that answered."""
        def one(model: str) -> LLMResult:
            key = cache_key(self.name, model, key_prompt, payload)
            hit = read_cache(self.cache_dir, key)
            if hit and hit.text.strip():  # an empty answer saved by an older version is not a hit
                return hit
            result = call(model)
            write_cache(self.cache_dir, key, result)
            return result

        outcome = call_with_fallback([self.model, *self.fallbacks], one,
                                     attempts=self.attempts, sleep=self._sleep)
        result = outcome.result
        result.attempts = outcome.calls
        result.failures = outcome.failures
        result.fell_back_from = self.model if outcome.model != self.model else None
        return result


class GeminiClient(_CachedProvider):
    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 cache_dir: Path = CACHE_DIR, fallbacks: tuple[str, ...] | list[str] | None = None,
                 attempts: int = ATTEMPTS_PER_MODEL, timeout: float = REQUEST_TIMEOUT):
        load_env()
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "No Gemini API key found. Put GEMINI_API_KEY=... in a .env file "
                "(see .env.example) or set it in your shell.")
        from google import genai  # imported late so offline tests do not need the SDK
        from google.genai import types

        # The SDK does not retry unless asked to; we retry ourselves (call_with_fallback),
        # and we set a timeout so one call cannot hang for minutes. The SDK takes milliseconds.
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self.fallbacks = [m for m in (fallbacks or []) if m != self.model]
        self.attempts = attempts
        self.cache_dir = cache_dir
        self._sleep = time.sleep

    def list_models(self) -> list[str]:
        return [m.name for m in self._client.models.list()]

    def _generate_structured(self, model: str, text: str, prompt: str, schema: type[BaseModel]) -> LLMResult:
        from google.genai import types

        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=model,
            contents=[prompt, "Transcript:\n" + text],
            config=types.GenerateContentConfig(
                temperature=0, response_mime_type="application/json", response_schema=schema),
        )
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=_text_or_raise(response, model),
            model=model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            seconds=round(time.perf_counter() - start, 2),
            thinking_tokens=getattr(usage, "thoughts_token_count", None),
        )

    def _generate(self, model: str, image: bytes, mime: str, prompt: str) -> LLMResult:
        from google.genai import types

        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=image, mime_type=mime), prompt],
            config=types.GenerateContentConfig(temperature=0),
        )
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=_text_or_raise(response, model),
            model=model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            seconds=round(time.perf_counter() - start, 2),
            thinking_tokens=getattr(usage, "thoughts_token_count", None),
        )


# --- Claude ------------------------------------------------------------------
def _claude_text(response, model: str) -> str:
    """The text of a Claude answer: its text blocks, without any thinking block."""
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text")
    if response.stop_reason == "max_tokens":
        raise EmptyResponse(f"{model} was cut off at max_tokens")
    if not text.strip():
        raise EmptyResponse(f"{model} returned no text (stop reason: {response.stop_reason})")
    return text


class ClaudeClient(_CachedProvider):
    """Claude through the official `anthropic` SDK (an optional extra: pip install -e ".[claude]").

    Credentials are resolved by the SDK: ANTHROPIC_API_KEY (also read from .env), then
    ANTHROPIC_AUTH_TOKEN, then a profile from `ant auth login`.
    """

    name = "claude"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 cache_dir: Path = CACHE_DIR, fallbacks: tuple[str, ...] | list[str] | None = None,
                 attempts: int = ATTEMPTS_PER_MODEL, timeout: float = REQUEST_TIMEOUT):
        load_env()
        try:
            import anthropic  # imported late: only needed when Claude is used
        except ImportError as e:
            raise RuntimeError('The Claude client needs the anthropic package: pip install -e ".[claude]"') from e

        # We retry ourselves (call_with_fallback), so the SDK must not retry as well.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout)
        self.model = model or os.environ.get("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL
        self.fallbacks = [m for m in (fallbacks or []) if m != self.model]
        self.attempts = attempts
        self.cache_dir = cache_dir
        self._sleep = time.sleep

    def list_models(self) -> list[str]:
        return [m.id for m in self._client.models.list()]

    def _message(self, model: str, content: list[dict], effort: str, schema=None, max_tokens: int = 8000) -> LLMResult:
        """One Messages API call. No temperature: the current Claude models do not accept one."""
        output_config: dict = {"effort": effort}
        if schema is not None:
            import anthropic

            # the API needs additionalProperties: false on every object; the SDK adds it
            output_config["format"] = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        start = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=model, max_tokens=max_tokens, output_config=output_config,
                messages=[{"role": "user", "content": content}])
        except TypeError as e:  # the SDK raises this when it finds no credentials at all
            if "authentication" in str(e).lower():
                raise RuntimeError(
                    "No Claude credentials found. Put ANTHROPIC_API_KEY=... in a .env file "
                    "(see .env.example) or set it in your shell.") from e
            raise
        usage = getattr(response, "usage", None)
        return LLMResult(
            text=_claude_text(response, model),
            model=model,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),  # includes any thinking, which is billed as output
            seconds=round(time.perf_counter() - start, 2),
        )

    def _generate(self, model: str, image: bytes, mime: str, prompt: str) -> LLMResult:
        import base64

        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.standard_b64encode(image).decode("ascii")}},
            {"type": "text", "text": prompt},
        ]
        return self._message(model, content, CLAUDE_TRANSCRIBE_EFFORT)

    def _generate_structured(self, model: str, text: str, prompt: str, schema: type[BaseModel]) -> LLMResult:
        content = [{"type": "text", "text": prompt}, {"type": "text", "text": "Transcript:\n" + text}]
        return self._message(model, content, CLAUDE_STRUCTURE_EFFORT, schema=schema, max_tokens=16000)


def make_client(provider: str, model: str | None = None, fallbacks=None) -> GeminiClient | ClaudeClient:
    """The client for `provider` ("gemini" or "claude")."""
    if provider == "gemini":
        return GeminiClient(model=model, fallbacks=fallbacks)
    if provider == "claude":
        return ClaudeClient(model=model, fallbacks=fallbacks)
    raise ValueError(f"unknown provider {provider!r}: expected 'gemini' or 'claude'")
