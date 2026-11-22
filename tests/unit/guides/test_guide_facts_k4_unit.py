"""The sources guide's sentence about K4 (tests/acceptance/test_k4_notes_fit_a_page.py): a note
a source writes is cut or split to fit a page, and a cut note says where it was cut. Pinned
here so the guide cannot go false unnoticed: found in the guide as a person reads it
(guide_source), with the product's name as the guide's marker writes it, then asked of cairn."""

from importlib.resources import files

import cairn.store.memory
from cairn.sources import connector, fit
from cairn.store import FACTS_MAX, PAGE_CHARS_MAX, PAGE_CLAIMS_MAX
from cairn.web.brand import product_of
from support.guide_source import guide_source

SECTION = "## What every recipe shares"
SENTENCES = (
    "**A note fits a page.** A page holds at most 2,000 claims and 400,000 characters of"
    " claims, and every source {{name}} reads writes notes that fit one. A note that would not"
    " fit is cut, and the note says where it was cut; a source that writes a long item in"
    " parts, such as a document, a message or a transcript, makes each part fit, and the parts"
    " hold all of the item between them. A connector's item is not cut: one whose text is"
    " longer than a page holds is skipped, and the run's report says so. A note with more than"
    " 30,000 facts is not cut either: it is skipped as `facts past their bound`."
)


def flat(text: str) -> str:
    return " ".join(text.split())


def section(text: str) -> str:
    start = text.index(SECTION)
    return text[start : text.index("\n## ", start + len(SECTION))]


def test_the_sources_guide_says_it_once_in_what_every_recipe_shares():
    raw = files("cairn.guides").joinpath("sources.md").read_text(encoding="utf-8")
    assert flat(SENTENCES) in flat(section(raw))  # the marker, as the file writes it
    read = flat(guide_source("sources"))
    filled = flat(SENTENCES.replace("{{name}}", product_of(None).name))
    assert read.count(filled) == 1
    assert flat(guide_source("sources")).count("**A note fits a page.**") == 1


def test_what_the_sentence_says_is_what_cairn_holds(monkeypatch):
    assert (PAGE_CLAIMS_MAX, PAGE_CHARS_MAX, FACTS_MAX) == (2_000, 400_000, 30_000)
    line = "The rest is left out."
    cut = fit.fitted("# Long\n\n" + "\n\n".join(["word " * 100] * 10) + "\n", line)
    assert cut.count("word") == 1_000  # it fits: nothing cut
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 2_000)
    cut = fit.fitted("# Long\n\n" + "\n\n".join(["word " * 100] * 10) + "\n", line)
    assert fit.fits(cut) and cut.rstrip().endswith(line)  # cut, and says where
    assert "longer than a page holds" in connector.too_long()
    assert "full" not in connector.too_long()
