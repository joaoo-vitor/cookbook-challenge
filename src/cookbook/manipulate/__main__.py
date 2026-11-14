"""CLI: python -m cookbook.manipulate name.recipe.json --servings 6 [--base-servings 4]

Takes the structured recipe written by `python -m cookbook.extract ... --structure`, applies the
requested rules and writes the adapted recipe and the change log next to it (or to --out).
"""

import argparse
import json
import sys
from pathlib import Path

from cookbook.models import Recipe

from .engine import manipulate
from .scaling import NeedsBaseServings

_MARK = {"changed": "changed  ", "unchanged": "unchanged", "warning": "WARNING  "}


def main() -> int:
    ap = argparse.ArgumentParser(description="Change a structured recipe (for now: the number of servings).")
    ap.add_argument("recipe", help="a .recipe.json file written by `python -m cookbook.extract --structure`")
    ap.add_argument("--servings", type=int, help="the number of servings wanted")
    ap.add_argument("--base-servings", type=int,
                    help="how many servings the recipe makes, if you know it better than the recipe "
                         "(it replaces the recipe's own number, stated or estimated)")
    ap.add_argument("--out", help="folder for the results (default: next to the recipe)")
    args = ap.parse_args()

    path = Path(args.recipe)
    try:
        recipe = Recipe.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"error: cannot read {path} as a structured recipe: {str(e)[:200]}", file=sys.stderr)
        return 1
    if args.servings is None:
        ap.error("nothing to do: give --servings (more rules are coming)")

    try:
        result = manipulate(recipe, servings=args.servings, base_servings=args.base_servings)
    except (NeedsBaseServings, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else path.parent
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{path.name.removesuffix('.recipe.json')}.servings{args.servings}"
    (out / f"{stem}.recipe.json").write_text(result.recipe.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{stem}.changes.json").write_text(
        json.dumps([c.model_dump() for c in result.changes], indent=2, ensure_ascii=False), encoding="utf-8")

    estimate = " (the base is an ESTIMATE)" if result.base_is_estimate else ""
    print(f"servings: {result.base_servings} -> {result.target_servings}{estimate}")
    for kind in ("warning", "changed", "unchanged"):
        for c in (c for c in result.changes if c.kind == kind):
            what = f"{c.before} -> {c.after}" if c.kind == "changed" and c.field != "text" else (c.after or "")
            print(f"  {_MARK[c.kind]} {c.target} {c.field}: {what[:90]}\n             {c.reason}")
    counts = {k: sum(1 for c in result.changes if c.kind == k) for k in ("changed", "unchanged", "warning")}
    print(f"\n{counts['changed']} changed, {counts['unchanged']} left as they are, {counts['warning']} warning(s)")
    print(f"-> {out / (stem + '.recipe.json')}\n-> {out / (stem + '.changes.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
