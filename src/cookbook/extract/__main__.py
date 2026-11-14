"""CLI: python -m cookbook.extract Recipe.pdf [--pages 1,2] [--model NAME] [--second-model NAME]

Reads every page with a vision model. By default a second, different model reads
the same images and the differences are flagged (use --single to skip that).
Writes the transcript, the second reading, the flags and the token/latency numbers
to --out (default data/experiments, which is gitignored).
"""

import argparse
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

from .transcribe import number_lines, transcribe_pdf


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name)


def _usage(calls) -> list[dict]:
    return [{"model": c.model, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
             "thinking_tokens": c.thinking_tokens, "seconds": c.seconds, "cached": c.cached,
             "attempts": c.attempts, "fell_back_from": c.fell_back_from, "failures": c.failures}
            for c in calls]


def _note(c) -> str:
    notes = []
    if c.cached:
        notes.append("cached")
    if c.attempts > 1:
        notes.append(f"{c.attempts} calls")
    if c.fell_back_from:
        notes.append(f"FALLBACK from {c.fell_back_from}")
    return f" ({', '.join(notes)})" if notes else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe a PDF with two vision models and flag differences.")
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--provider", default="gemini", choices=["gemini"])
    ap.add_argument("--model", help="primary model (default: GEMINI_MODEL, then a built-in name)")
    ap.add_argument("--second-model", help="second reader (default: GEMINI_SECOND_MODEL, then a built-in name)")
    ap.add_argument("--fallback-models", help="comma-separated models to try when a reader's own model "
                    "keeps failing (default: GEMINI_FALLBACK_MODELS, then a built-in name)")
    ap.add_argument("--no-fallback", action="store_true", help="never switch to another model")
    ap.add_argument("--single", action="store_true", help="one model only, no comparison")
    ap.add_argument("--pages", help="comma-separated 1-based pages, e.g. 1,2")
    ap.add_argument("--out", default="data/experiments")
    ap.add_argument("--list-models", action="store_true", help="list models the key can use")
    args = ap.parse_args()

    from cookbook.llm import DEFAULT_FALLBACK_MODELS, DEFAULT_SECOND_MODEL, GeminiClient

    raw = args.fallback_models or os.environ.get("GEMINI_FALLBACK_MODELS")
    fallbacks = [] if args.no_fallback else (
        [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_FALLBACK_MODELS))

    try:
        llm = GeminiClient(model=args.model, fallbacks=fallbacks)
        second = None if args.single else GeminiClient(
            model=args.second_model or os.environ.get("GEMINI_SECOND_MODEL") or DEFAULT_SECOND_MODEL,
            fallbacks=fallbacks)
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
        transcripts = transcribe_pdf(args.pdf, llm, pages=pages, second=second)
    except Exception as e:  # API errors, unreadable PDF: short message, no traceback
        print(f"error ({type(e).__name__}): {str(e)[:400]}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(args.pdf).stem}_{_safe(llm.model)}"
    print(f"primary: {llm.model}" + (f" | second reader: {second.model}" if second else " | single read")
          + (f" | fallback: {', '.join(llm.fallbacks)}" if llm.fallbacks else " | no fallback"))
    for t in transcripts:
        base = out / f"{stem}_p{t.page}"
        base.with_name(base.name + ".txt").write_text(
            "\n".join(number_lines(t.page, t.text)) + "\n", encoding="utf-8")
        base.with_name(base.name + ".usage.json").write_text(
            json.dumps({"primary": _usage(t.calls), "second": _usage(t.second_calls)}, indent=2),
            encoding="utf-8")
        if t.second_text is not None:
            base.with_name(base.name + ".second.txt").write_text(
                "\n".join(number_lines(t.page, t.second_text)) + "\n", encoding="utf-8")
        if t.comparisons or t.warnings:
            base.with_name(base.name + ".flags.json").write_text(
                json.dumps({"comparisons": [dataclasses.asdict(c) for c in t.comparisons],
                            "warnings": t.warnings}, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"\npage {t.page} [{t.strategy.value}, {t.source}] -> {base.with_name(base.name + '.txt')}")
        for who, calls in (("primary", t.calls), ("second ", t.second_calls)):
            for c in calls:
                print(f"  {who} tokens in/out/thinking: {c.input_tokens}/{c.output_tokens}/{c.thinking_tokens}, "
                      f"{c.seconds}s{_note(c)}")
                for failure in c.failures:
                    print(f"    failed call: {failure}")
        for cmp in t.comparisons:
            verdict = "UNRELIABLE: the two reads mostly disagree" if cmp.unreliable else "reads agree"
            print(f"  {cmp.label}: agreement {cmp.agreement:.2f} ({verdict}), {len(cmp.flags)} flag(s)")
            for f in cmp.flags:
                print(f"    [{f.severity}] {f.line} {f.kind}: {f.primary!r} vs {f.secondary!r}")
        for w in t.warnings:
            print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
