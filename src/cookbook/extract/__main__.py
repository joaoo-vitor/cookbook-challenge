"""CLI to try a model on a PDF: python -m cookbook.extract Recipe.pdf [--pages 1,2] [--model NAME]

Writes each page's transcript and the token/latency numbers to --out (default
data/experiments, which is gitignored) so candidate models can be compared.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from .transcribe import number_lines, transcribe_pdf


def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe a PDF with a vision model.")
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--provider", default="gemini", choices=["gemini"])
    ap.add_argument("--model", help="model name (default: GEMINI_MODEL or the built-in alias)")
    ap.add_argument("--pages", help="comma-separated 1-based pages, e.g. 1,2")
    ap.add_argument("--out", default="data/experiments")
    ap.add_argument("--list-models", action="store_true", help="list models the key can use")
    args = ap.parse_args()

    from cookbook.llm import GeminiClient

    try:
        llm = GeminiClient(model=args.model)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.list_models:
        print("\n".join(llm.list_models()))
        return 0
    if not args.pdf:
        ap.error("pdf is required unless --list-models is given")

    pages = {int(p) for p in args.pages.split(",")} if args.pages else None
    try:
        transcripts = transcribe_pdf(args.pdf, llm, pages=pages)
    except Exception as e:  # API errors, unreadable PDF: short message, no traceback
        print(f"error ({type(e).__name__}): {str(e)[:400]}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(args.pdf).stem}_{re.sub(r'[^A-Za-z0-9._-]+', '-', llm.model)}"
    for t in transcripts:
        numbered = "\n".join(number_lines(t.page, t.text))
        (out / f"{stem}_p{t.page}.txt").write_text(numbered + "\n", encoding="utf-8")
        usage = [{"model": c.model, "input_tokens": c.input_tokens,
                  "output_tokens": c.output_tokens, "thinking_tokens": c.thinking_tokens,
                  "seconds": c.seconds, "cached": c.cached}
                 for c in t.calls]
        (out / f"{stem}_p{t.page}.usage.json").write_text(
            json.dumps(usage, indent=2), encoding="utf-8")
        print(f"page {t.page} [{t.strategy.value}, {t.source}] -> {out / (stem + f'_p{t.page}.txt')}")
        for c in t.calls:
            print(f"  tokens in/out/thinking: {c.input_tokens}/{c.output_tokens}/{c.thinking_tokens}, {c.seconds}s"
                  f"{' (cached)' if c.cached else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
