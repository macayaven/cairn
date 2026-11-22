"""File generated Slack messages through the message shortcut, as a person does, and hold
what the page shows to what Slack displayed (test_rt3_slack_model): the note read whole, each
claim rendered alone as the page view renders it (cairn.web.markdown.render, the `md`
filter), and the page's assembled body. Every fault is shrunk to its smallest message.

The checks, by severity:
- link: every link any view shows is Slack's own, to Slack's own address, with Slack's own
  words; or the note's source link. No image, ever.
- heading: one heading, the author's, under the passage splitter and CommonMark; none in a
  claim alone; in the body, the title and the author's heading only.
- code: each line Slack displayed as code is shown as code, byte for byte; each line the
  page shows as code is a line Slack displayed (as cairn's guide writes it: a link as
  `[words](address)`), less only indentation at its start.
- text: the characters a person reads in the claims are those Slack displayed, less only
  marks Markdown may take as structure or emphasis.
- source: the note's source link is a link on the page.
- refused: a message with text is filed, or refused for a stated reason.
- reply: the reply in Slack names the note by the first line Slack displayed, with no
  mark doubled. Where the reply says `"mrkdwn": false` its title is read as plain text, in
  its plain-text section and in the fallback text, with `&amp;`, `&lt;` and `&gt;` read as
  the characters they write. Nobody has checked against Slack how it decodes entities in
  plain text, or whether it honours `"mrkdwn": false` on a reply sent to a `response_url`.
  Else the title is read as Slack reads mrkdwn: no Slack markup, nothing Slack showed as
  characters styled.
- title: the body's heading of the title holds no code, link or image, and displays the
  start of the first line Slack displayed, with no mark doubled.
- author: the author line is the author's name, as the note writes it for Markdown.

What the connect guide states is not counted (LIMIT_* below, and its stated rules: an
address removed, a table's bars, the fence exception); the note's own code is not checked,
since no page renders a note whole (its links and headings are).
"""

import os
import random
import time
from collections import Counter

import pytest

from cairn.slack import text as slack_text
from support.rt3_slack_model import a_message, an_author, display
from support.slack.rt3_slack_pages import Fault, Filer, faults, names

SEVERITY = [
    "link", "image", "heading", "code", "source", "refused", "reply", "title", "text", "author"
]  # fmt: skip


# Filing, as a person does: the shortcut on a message, the note, its page


# What a view shows


# The checks. A fault is (class, kind, view, detail); its signature, (class, kind), is what
# a shrink keeps.


def _code_texts(raw: str) -> list[str]:
    return [p.shown for p in display(raw, names).pieces if p.kind in ("code", "block")]


# The limits the connect guide states (src/cairn/guides/connect.md), which the generated run
# does not count; each is pinned in test_rt3_slack_found as the guide says it.


def signatures(found: list[Fault]) -> set[tuple[str, str]]:
    return {(kind, sub) for kind, sub, _, _ in found}


# Shrinking


def shrink(filer: Filer, raw: str, signature: tuple[str, str], author: str = "Cedar Quill",
           budget: int = 400) -> str:  # fmt: skip
    """The smallest message (lines, then characters) that still has the fault `signature`."""
    tries = 0

    def keeps(text: str) -> bool:
        nonlocal tries
        tries += 1
        if not text.strip():
            return False
        return signature in signatures(faults(filer(text, author)))

    best = raw
    for unit in ("\n", None):
        size = max(1, len(best.split("\n")) // 2) if unit else max(1, len(best) // 2)
        while size >= 1 and tries < budget:
            parts = best.split("\n") if unit else list(best)
            changed = False
            at = 0
            while at < len(parts) and tries < budget:
                candidate = parts[:at] + parts[at + size :]
                text = ("\n" if unit else "").join(candidate)
                if candidate and keeps(text):
                    best, parts, changed = text, candidate, True
                else:
                    at += size
            if not changed:
                size //= 2
    return best


# The campaign

COUNT = int(os.environ.get("RT3_MESSAGES", "600"))
SEED = int(os.environ.get("RT3_SEED", "20261120"))


def campaign(filer: Filer, count: int, seed: int, keep: int = 3) -> dict:
    """`count` messages from `seed`: the faults by signature (up to `keep` messages each),
    how often each was seen and in which views, and what each conversion cost."""
    found: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    seen: Counter[tuple[str, str]] = Counter()
    views: dict[tuple[str, str], set[str]] = {}
    cost: list[tuple[int, int, float, str]] = []
    parse, split = slack_text._PARSER.parse, slack_text.chunk
    counted: Counter[str] = Counter()

    def counting_parse(*args, **kwargs):
        counted["parse"] += 1
        return parse(*args, **kwargs)

    def counting_chunk(*args, **kwargs):
        counted["chunk"] += 1
        return split(*args, **kwargs)

    for n in range(count):
        rng = random.Random(seed * 100003 + n)
        raw = a_message(rng)
        author = an_author(rng) if rng.random() < 0.1 else "Cedar Quill"
        counted.clear()
        slack_text._PARSER.parse, slack_text.chunk = counting_parse, counting_chunk
        start = time.process_time()
        try:
            filed = filer(raw, author)
        finally:
            slack_text._PARSER.parse, slack_text.chunk = parse, split
        spent = time.process_time() - start
        cost.append((counted["parse"] + counted["chunk"], len(raw), spent, raw))
        for signature in signatures(got := faults(filed)):
            seen[signature] += 1
            views.setdefault(signature, set()).update(
                v for c, k, v, _ in got if (c, k) == signature
            )
            detail = next(d for c, k, _, d in got if (c, k) == signature)
            if len(found.setdefault(signature, [])) < keep:
                found[signature].append((raw, author, detail))
    return {"found": found, "seen": seen, "views": views, "cost": cost, "count": count}


def report(filer: Filer, result: dict, shrink_budget: int = 300) -> list[str]:
    lines = [f"{result['count']} messages"]
    order = sorted(result["found"], key=lambda sig: (SEVERITY.index(sig[0]), sig))
    for signature in order:
        smallest = set()
        for raw, author, _ in result["found"][signature]:
            small = shrink(filer, raw, signature, author, shrink_budget)
            got = faults(filer(small, author))
            detail = next((d for c, k, _, d in got if (c, k) == signature), "?")
            smallest.add((small, author, detail))
        views = ",".join(sorted(result["views"][signature]))
        lines.append(f"{signature[0]}: {signature[1]} x{result['seen'][signature]} in {views}")
        for small, author, detail in sorted(smallest, key=lambda s: len(s[0])):
            who = "" if author == "Cedar Quill" else f" author={author!r}"
            lines.append(f"    {small!r}{who} :: {detail[:200]}")
    worst = max(result["cost"], key=lambda c: c[0])
    slowest = max(result["cost"], key=lambda c: c[2] / max(c[1], 1))
    lines.append(f"most readings: {worst[0]} for {worst[1]} characters: {worst[3][:100]!r}")
    lines.append(f"slowest per character: {slowest[2]:.3f}s for {slowest[1]} characters")
    return lines


@pytest.fixture
def filer(tmp_path):
    return Filer(tmp_path)


def test_rt3_generated_messages_show_what_slack_displayed(filer):
    """RT3_MESSAGES messages from RT3_SEED (600 from 20261120 by default), filed through the
    shortcut; each fault shrunk to its smallest message. The first run, on 0d4e71a8 (the
    candidate as the seventh report found it), rediscovered both of its Slack faults by
    itself: a Slack code block whose text the claim alone renders as a link while the note
    reads it as code ("link: from a Slack code block", seen in the claim and not the note,
    e.g. '```\\n```i\\n\\t```\\n[](h)```'), and white space lost at the end of a line both
    readers read as code ("code: trailing white space lost (Slack text read as code)",
    e.g. '___\\n\\tt ')."""
    result = campaign(filer, COUNT, SEED, keep=1)
    lines = report(filer, result, shrink_budget=150)
    print("\n".join(lines))
    assert not result["found"], "\n" + "\n".join(lines)
