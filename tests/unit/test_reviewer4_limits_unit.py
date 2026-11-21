"""Operator instructions for wheel provenance and interrupted database rewrites."""

import re
import shlex
from itertools import pairwise
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
STABLE = "refs/tags/vX.Y.Z"


@pytest.mark.parametrize("document", ["README.md", "deploy/README.md"])
def test_wheel_verification_pins_the_tag(document):
    refs = []
    for block in MarkdownIt("commonmark").parse((ROOT / document).read_text()):
        if block.type not in {"fence", "code_block"}:
            continue
        for line in block.content.replace("\\\n", " ").splitlines():
            if not line.lstrip().startswith("gh attestation verify "):
                continue
            args = shlex.split(line, comments=True)
            if not any(arg.endswith(".whl") for arg in args):
                continue
            options = [part for arg in args for part in arg.split("=", 1)]
            refs.append(
                next((ref for flag, ref in pairwise(options) if flag == "--source-ref"), None)
            )
    assert STABLE in refs, (document, refs)
    # An additional candidate example can pin its own tag. Every wheel command needs a pin.
    assert all(ref == STABLE or (ref and ref.startswith(STABLE + "-rc.")) for ref in refs)


def test_spec_drops_unconditional_rewrite_promise():
    spec = " ".join((ROOT / "docs/spec-v1.md").read_text().casefold().split())
    clauses = {clause.strip() for clause in re.split(r"[.;|]", spec)}
    obsolete = {
        "a rewrite that succeeded is not done again",
        "a successful rewrite is never repeated",
    }
    assert obsolete.isdisjoint(clauses)
