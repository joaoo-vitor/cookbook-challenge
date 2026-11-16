"""CLI: python -m cookbook.output name.recipe.json [--issues name.issues.json] [--changes name.changes.json] [--out name.md]

Turns a structured recipe (as read, or changed by `python -m cookbook.manipulate`) into Markdown a person
can cook from: the ingredients and steps as they are now, the servings with how sure they are, what
replaced what, and what to check before cooking. Next to a manipulated recipe it finds the change log
(`<stem>.changes.json`) and the original's issues (`<name>.issues.json`) by itself.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from cookbook.manipulate.changes import Change
from cookbook.models import Issue, Recipe

from .render import render_markdown


def _stem(path: Path) -> str:
    return path.name.removesuffix(".recipe.json")


def _original_stem(stem: str) -> str:
    """The stem of the recipe the manipulations started from: 'x.vegan.servings6' -> 'x'."""
    return re.sub(r"(\.vegan)?(\.servings\d+)?$", "", stem)


def _read(path: Path, kind):
    return [kind.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write a recipe as Markdown for a cook.")
    ap.add_argument("recipe", help="a .recipe.json file")
    ap.add_argument("--issues", help="the .issues.json of the extraction (default: found next to the recipe)")
    ap.add_argument("--changes", help="the .changes.json of the manipulations (default: found next to the recipe)")
    ap.add_argument("--out", help="the Markdown file to write (default: next to the recipe)")
    args = ap.parse_args(argv)

    path = Path(args.recipe)
    try:
        recipe = Recipe.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"error: cannot read {path} as a structured recipe: {str(e)[:200]}", file=sys.stderr)
        return 1

    stem = _stem(path)
    changes_path = Path(args.changes) if args.changes else path.with_name(f"{stem}.changes.json")
    issues_path = Path(args.issues) if args.issues else path.with_name(f"{_original_stem(stem)}.issues.json")
    try:
        changes = _read(changes_path, Change) if changes_path.exists() else []
        issues = _read(issues_path, Issue) if issues_path.exists() else []
    except (OSError, ValueError) as e:
        print(f"error: cannot read the issues or the changes: {str(e)[:200]}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else path.with_name(f"{stem}.md")
    out.write_text(render_markdown(recipe, issues=issues, changes=changes), encoding="utf-8")
    print(f"{recipe.title or stem}: {len(issues)} issue(s) and {len(changes)} change(s) read"
          f" -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
