"""What each Google section of src/cairn/guides/sources.md says about one person's own
authorisation, and the code each sentence rests on.

Not a test module: the guide-fact tests of the four Google sources import it, so the four
sections say the same steps and each is checked against `google-authorize` the same way. The
shared section they point to is pinned in test_guide_facts_google_authorize_unit.py.
"""

import shlex

from cairn.web import cli, cli_google
from cairn.web.markdown import anchor

SHARED = "One person's own Google authorisation"
LINK = f"[{SHARED}](#{anchor(SHARED)})"


def flat(text: str) -> str:
    return " ".join(text.split())


def own_authorisation(kind: str, section: str, scopes: list[str]) -> list[str]:
    """The sentences `section` says for `kind`'s path with one person's own authorisation,
    after checking each is there and that the command they name takes `--kind kind` and asks
    for `scopes`, the source's own."""
    sentences = [
        "1. In a Google Cloud project, create an OAuth client of type **Desktop app** and"
        " download its JSON file.",
        "2. On the computer whose browser the person uses, run `google-authorize` with"
        f" `--kind {kind}`, as {LINK} shows. The person grants the scope above in that browser,"
        " and the command writes the credential to a new file.",
        f"How long the refresh token lasts is under {LINK}; when it stops working, the source"
        " says to authorise again.",
    ]
    for sentence in sentences:
        assert flat(sentence) in flat(section), sentence
    assert "7 days" not in section, "the 7-day rule is said once, in the shared section"
    # The command takes the kind, and asks Google for the scopes the source itself needs.
    argv = shlex.split(f"google-authorize --client c.json --kind {kind} --out o.json")
    assert cli._parser().parse_args(argv).kinds == [kind]
    assert kind in cli_google.KIND_NAMES
    assert cli_google.scopes_for([kind]) == list(scopes)
    return sentences
