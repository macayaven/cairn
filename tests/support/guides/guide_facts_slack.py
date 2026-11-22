"""Shared test helpers extracted from test_guide_facts_slack_unit."""

from support.guide_source import guide_source
from support.slack.slack_store import KEY, SECRET, TOKEN, a_note, ready, world  # noqa: F401


def flat(text: str) -> str:
    return " ".join(text.split())


def section(guide: str, heading: str) -> str:
    """The section `heading` of the guide as a person reads it, to the next section."""
    raw = guide_source(guide)
    start = raw.index(f"\n{heading}\n")
    end = raw.find("\n## ", start + len(heading) + 2)
    return raw[start : end if end != -1 else None]


def says(guide: str, heading: str, *sentences: str) -> None:
    """Each sentence is in that section of the guide, as a person reads it, once."""
    text = flat(section(guide, heading))
    for sentence in sentences:
        assert flat(sentence) in text, sentence
        assert flat(guide_source(guide)).count(flat(sentence)) == 1, sentence


RUN = ("run", "## Slack")

PRIVACY = ("privacy", "## Slack")
