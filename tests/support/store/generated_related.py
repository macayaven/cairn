"""Shared test helpers extracted from test_generated_related_unit."""

import random

import support.compiler.generated_copy_passes_fact_check as shapes
from support.fuzz import fuzz_budget

SEED, PROJECTS = 20261120, fuzz_budget(60, 300)

TITLES = [
    "Quill",
    "Quill log",
    "Ada",
    "Ada keeps",
    "Nightly log",
    "Fable Works",
    "Port",
    "Brook",
    "The log",
    "Works",
]


def passage(rng: random.Random, held: str) -> str:
    """A passage that holds `held` where the fact generator puts a fact (its `mixed`)."""
    drawn = shapes.fact
    shapes.fact = lambda _rng: held
    try:
        return shapes.mixed(rng)[1]
    finally:
        shapes.fact = drawn


def spelled(rng: random.Random, held: str) -> str:
    """`held` as one page writes it: as drawn, in capitals, or without its commas. In
    capitals it is another term that the index reads as the same words; without its commas
    it can be the same term (a thousands separator is left out of the comparison) that the
    index reads as other words."""
    return rng.choice([held, held, held.upper(), held.replace(",", "")])


def naming(rng: random.Random, pages: dict[str, str]) -> dict[str, str]:
    """`pages`, most of them with one passage more that writes another page's title: in a
    sentence of the generator's words, where it names that page (on its one line, or on the
    second of two, or indented under a list item), or inside a fence, a path (one no other
    page writes), an indented block or code in backticks across a line's end, where it does
    not."""
    titles = list(pages)
    made = {}
    for n, (title, text) in enumerate(pages.items()):
        other = rng.choice([t for t in titles if t != title])
        where = rng.choice(["prose", "lines", "fence", "path", None, "item", "indented", "split"])
        added = {
            "prose": f"{shapes.words(rng)} {other} {shapes.words(rng)}.",
            "lines": f"{shapes.words(rng)}\n{shapes.words(rng)} {other} {shapes.words(rng)}.",
            "fence": f"```\n{shapes.words(rng)} {other}\n```",
            "path": f"{shapes.words(rng)} /srv/p{n}/{other.replace(' ', '/')}",
            None: None,
            "item": f"- {shapes.words(rng)}.\n\n"
            f"    {shapes.words(rng)} {other} {shapes.words(rng)}.",
            "indented": f"    {shapes.words(rng)} {other} {shapes.words(rng)}",
            "split": f"{shapes.words(rng)} `{shapes.words(rng)}\n{other}` {shapes.words(rng)}.",
        }[where]
        made[title] = text if added is None else f"{text}\n\n{added}"
    return made


def projects() -> list[dict[str, str]]:
    """Each project: its pages, title to the text of the page's one note."""
    rng = random.Random(SEED)
    titled = random.Random(SEED + 1)  # the passages that write titles (`naming`)
    made = []
    for _ in range(PROJECTS):
        pool = [shapes.fact(rng) for _ in range(rng.randint(1, 4))]
        titles = rng.sample(TITLES, rng.randint(2, 9))
        made.append(
            naming(
                titled,
                {
                    title: "\n\n".join(
                        passage(rng, spelled(rng, rng.choice(pool)))
                        for _ in range(rng.randint(1, 4))
                    )
                    for title in titles
                },
            )
        )
    return made
