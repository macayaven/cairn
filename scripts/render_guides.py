"""Write each guide as a server without a brand shows it: uv run python scripts/render_guides.py

The guides in src/cairn/guides hold markers ({{name}}, {{slug}}, {{SLUG}}) that the server
fills from its product, so that an operator's brand can take cairn's name. On GitHub the
source shows them raw. This script writes docs/guides/<name>.md for each guide the server
knows (cairn.web.public.GUIDES): one HTML comment line that says where the text comes from,
then the source with its markers filled by cairn.web.markdown.fill, the function the server's
parser calls, for the product without a brand. Links between the guides (`<name>.md#anchor`)
are left as they are: on GitHub they lead to the rendered copy beside it, and its headings'
ids are made from the filled text, as the server's are.

tests/unit/test_rendered_guides_unit.py fails when a copy is not what this script writes.
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files
from pathlib import Path

from cairn.web.brand import product_of
from cairn.web.markdown import fill
from cairn.web.public import GUIDES

OUT = Path(__file__).resolve().parents[1] / "docs" / "guides"
HEADER = (
    "<!-- Generated from src/cairn/guides/{name}.md by scripts/render_guides.py."
    " Edit that file, not this one, and run uv run python scripts/render_guides.py. -->\n"
)


def rendered(name: str) -> str:
    """The guide `name` (one of GUIDES) as docs/guides/<name>.md holds it."""
    source = (files("cairn") / "guides" / f"{name}.md").read_text(encoding="utf-8")
    return HEADER.format(name=name) + fill(source, product_of(None))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or check generated docs/guides/*.md.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail with exit status 1 if any rendered guide is missing or stale",
    )
    args = parser.parse_args(argv)
    if args.check:
        stale = [
            f"docs/guides/{name}.md"
            for name in GUIDES
            if not (OUT / f"{name}.md").exists()
            or (OUT / f"{name}.md").read_text(encoding="utf-8") != rendered(name)
        ]
        if stale:
            print(
                f"stale rendered guides: {', '.join(stale)} "
                "(run: uv run python scripts/render_guides.py)",
                file=sys.stderr,
            )
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name in GUIDES:
        path = OUT / f"{name}.md"
        text = rendered(name)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(OUT.parents[1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
