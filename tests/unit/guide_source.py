"""A guide's Markdown as it reads without a brand: its markers (B3, cairn.web.markdown.MARKER)
filled with the software's name, as /docs/<name> says it. The guide-fact tests pin sentences
of this text, so a sentence reads as a person reads it, not as the file spells it."""

from importlib.resources import files

from cairn.web.brand import product_of
from cairn.web.markdown import fill


def guide_source(name: str) -> str:
    """The Markdown of the guide `name` (connect, sources, run...), its markers filled."""
    text = files("cairn.guides").joinpath(f"{name}.md").read_text(encoding="utf-8")
    return fill(text, product_of(None))
