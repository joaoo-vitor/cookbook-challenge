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
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

CACHE_DIR = Path(".cache") / "llm"  # gitignored
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"  # alias, unverified: override with GEMINI_MODEL


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    seconds: float
    cached: bool = False
    thinking_tokens: int | None = None  # billed as output by some providers


class VisionLLM(Protocol):
    name: str

    def transcribe(self, image: bytes, prompt: str) -> LLMResult: ...


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


# --- Gemini ------------------------------------------------------------------
def load_env() -> None:
    """Load a local .env file if python-dotenv is available (never committed)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


class GeminiClient:
    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 cache_dir: Path = CACHE_DIR):
        load_env()
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "No Gemini API key found. Put GEMINI_API_KEY=... in a .env file "
                "(see .env.example) or set it in your shell.")
        from google import genai  # imported late so offline tests do not need the SDK

        self._client = genai.Client(api_key=key)
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self.cache_dir = cache_dir

    def list_models(self) -> list[str]:
        return [m.name for m in self._client.models.list()]

    def transcribe(self, image: bytes, prompt: str) -> LLMResult:
        mime = sniff_mime(image)
        key = cache_key(self.name, self.model, prompt, image)
        hit = read_cache(self.cache_dir, key)
        if hit:
            return hit

        from google.genai import types

        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image, mime_type=mime), prompt],
            config=types.GenerateContentConfig(temperature=0),
        )
        usage = getattr(response, "usage_metadata", None)
        result = LLMResult(
            text=response.text or "",
            model=self.model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            seconds=round(time.perf_counter() - start, 2),
            thinking_tokens=getattr(usage, "thoughts_token_count", None),
        )
        write_cache(self.cache_dir, key, result)
        return result
