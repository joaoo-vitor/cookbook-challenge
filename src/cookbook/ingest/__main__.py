"""CLI: python -m cookbook.ingest path/to/file.pdf [--json] [--show-text]"""

import argparse
import json
import sys

from .pdf import analyze_pdf


def main() -> int:
    ap = argparse.ArgumentParser(description="Decide the reading strategy for each PDF page.")
    ap.add_argument("pdf")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--show-text", action="store_true", help="also print extracted text")
    args = ap.parse_args()

    try:
        pages = analyze_pdf(args.pdf)
    except Exception as e:  # unreadable / encrypted / not a PDF
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([p.to_dict() for p in pages], ensure_ascii=False, indent=2))
        return 0

    for p in pages:
        s = p.signals
        q = "n/a" if s.text_quality is None else f"{s.text_quality:.2f}"
        print(f"page {p.page}: {p.strategy.value.upper()}")
        print(f"  signals: chars={s.n_chars} image={s.image_coverage:.0%} "
              f"invisible_text={s.has_invisible_text} ocr_producer={s.ocr_producer} "
              f"ocr_font={s.ocr_font} quality={q}")
        for r in p.reasons:
            print(f"  - {r}")
        for i, t in enumerate(p.tasks):
            print(f"  extra task [{i}]: image {t.bbox} -> vision")
        if args.show_text and p.text.strip():
            print("  text:\n" + "\n".join("    " + line for line in p.text.splitlines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
