"""RT1: the faults the generated search found, each shrunk to a small plan (the seed it was
shrunk from is named). Each test asserts what the plain loop keeps, so it fails while the
fault stands. Made-up notes."""

from test_rt1_harness import Note, Plan, Run, Step

RUNS16 = "\n\n".join(
    [
        "    `````\n    sundial kqbbbb",
        "    ~~~\n    lantern kqbbbc",
        "    ~~~text\n    harbour kqbbbd",
        "    ~~~\n    lantern kqbbbf",
        "    ```text\n    almanac kqbbbg",
        "    ````\n    meadow kqbbbh",
        "    ~~~text\n    meadow kqbbbj",
        "    ~~~~~py\n    meadow kqbbbk",
        "    ~~~~\n    compass kqbbbl",
        "    ~~~~~\n    meadow kqbbbm",
        "    ~~~\n    almanac kqbbbn",
        "    ```py\n    sundial kqbbbp",
        "    ~~~text\n    almanac kqbbbq",
        "    ~~~\n    sundial kqbbbr",
        "    ~~~text\n    sundial kqbbbt",
        "    ~~~~~text\n    sundial kqbbbv",
        "  End kqbbbx.",
    ]
)


def faults(plan: Plan) -> list[tuple[int, str, str]]:
    run = Run(plan).run()
    try:
        return run.faults
    finally:
        run.close()


def test_seed_159_a_title_that_changes_on_the_first_render_takes_an_innocent_sibling():
    """Rediscovered by the generic search (seed 159), not written for it: the fault of the
    reviewer's report on 27aff19f, finding 1, by another road. The oldest note (titled
    "FIELD NOTES") is a list sibling filed after the notes before it. Forgetting the
    separator, the first render is titled "FIELD NOTES": the "## Field notes" section stands
    after the sixteen spent fences, and the oldest note's sibling is named. The forget takes
    the sibling before it too (its fence covers the tail where that body writes it). The
    plain loop: without the oldest note the title is "Field notes", the section goes to the
    top before the spent fences, the sibling before keeps its fence closed by the first
    closer, and the sixteenth spent fence is named instead."""
    oldest = Note(
        "under15",
        "## Field notes\n\n    *  ~~~text\n       meadow kqbbcx",
        topic="FIELD NOTES",
        inner=[
            Note("tail5", "      lantern kqbbch", topic="Field notes"),
            Note("parent7", "## Field notes\n\n-   parent kqbbck", topic="FIELD NOTES"),
            Note(
                "under14",
                "## Field notes\n\n    *   ```text\n        harbour kqbbcw",
                topic="FIELD NOTES",
            ),
        ],
    )
    plan = Plan(
        [
            oldest,
            Note("separator19", "Between kqbbdt.", topic="FIELD NOTES"),
            Note("tail22", "## Field notes\n\n        orchard kqbbdx", topic="FIELD NOTES"),
            Note("parent23", "- item kqbbbw", topic="Field notes"),
            Note("under24", RUNS16, topic="Field notes"),
        ],
        [Step("forget", "separator19")],
    )
    assert faults(plan) == []
