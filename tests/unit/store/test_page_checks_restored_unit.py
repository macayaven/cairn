"""A page's checks, whatever an archive brought (fifth report, must-fix 5). An import takes a
version's metrics as a short flat map of names to whole numbers of any size, finite floats
and short words (portable._metrics), so the page shows each without failing: a ratio as a
percentage only when it is a number from 0 to 1, any other value as it is, and the rows in
one order whatever order the archive held them in. Each case goes the public way: an export,
the archive edited, an import into another store, and the page read in a browser's session.
Every note is made up."""

import contextlib
import io
import json
import re
import zipfile
from html import unescape

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Claim, Store

BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
NOTE = "# Quill runbook\n\nQuill restarts the queue after 3 failures.\n\nQuill logs to stderr.\n"
LATER = "# Quill runbook\n\nQuill pages Brook after 5 failures.\n"
PAGE = "/b/ops/p/quill-runbook"
PAGES = "projects/ops/pages.jsonl"
# The checks the view knows, in the order it shows them: counts, then the ratios made of them.
KNOWN = (
    "claims", "passages_cited", "retention", "code_line_retention", "passage_coverage",
    "omission_rate", "arranged_by",
)  # fmt: skip
MOST_DIGITS = 4300  # the longest whole number Python's JSON reader takes, so an import does


@pytest.fixture(scope="module")
def archive(tmp_path_factory) -> bytes:
    """An export of one project whose one page has two versions."""
    source = Store(tmp_path_factory.mktemp("source") / "source.db")
    try:
        ops = source.create_project("ops")
        for text in (NOTE, LATER):
            note, _ = source.add_note(ops.id, text)
            assert compile_note(source, note).status == "saved"
        out = io.BytesIO()
        source.export(out)
        return out.getvalue()
    finally:
        source.close()


def rewritten(archive: bytes, change) -> bytes:
    """The archive with the lines of the project's page versions passed through `change`."""
    source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, change(data) if name == PAGES else data)
    return out.getvalue()


def edited(archive: bytes, metrics: dict, *, version: int | None = None) -> bytes:
    """The archive with `metrics` as the checks of the page's latest version, or of `version`,
    written as JSON writes them."""

    def change(data: bytes) -> bytes:
        lines = [json.loads(line) for line in data.splitlines()]
        (line,) = [n for n in lines if n["version"] == (version or len(lines))]
        line["metrics"] = metrics
        return "".join(json.dumps(n) + "\n" for n in lines).encode()

    return rewritten(archive, change)


@pytest.fixture
def restored(tmp_path):
    """restored(archive) imports it into a new store and answers `get`, a signed-in read."""
    with contextlib.ExitStack() as opened:

        def restore(archive: bytes):
            target = Store(tmp_path / "target.db")
            opened.callback(target.close)
            target.import_export(io.BytesIO(archive))
            app = create_app(target, public_url=BASE)
            client = opened.enter_context(TestClient(app, base_url=BASE))
            login = client.post(
                "/login", data={"key": target.create_key("laptop")}, headers=SAME_ORIGIN
            )
            assert login.status_code == 200  # the key is no part of what a failure prints
            return client.get

        yield restore


def rows(html: str) -> list[tuple[str, str]]:
    """The rows of the page's checks, as (name, value shown), in the order shown."""
    checks = html.split("<h2>Checks</h2>", 1)[1].split("<h2>Sources</h2>", 1)[0]
    found = re.findall(
        r'<tr data-metric="([^"]+)">.*?<td data-label="Value" class="num">([^<]*)</td>',
        checks,
        re.S,
    )
    return [(unescape(name), unescape(value)) for name, value in found]


def never_overflowed(html: str) -> bool:
    values = [value for _, value in rows(html)]
    return not any(re.search(r"\b(inf|nan)\b", value, re.I) for value in values)


# The reviewer's two values


def test_a_restored_ratio_that_is_a_whole_number_out_of_range_is_shown_as_that_number(
    archive, restored
):
    get = restored(edited(archive, {"claims": 1, "retention": 10**400}))
    shown = get(PAGE)
    assert shown.status_code == 200
    assert rows(shown.text) == [("claims", "1"), ("retention", "1" + "0" * 400)]


def test_a_restored_ratio_past_what_a_float_times_100_holds_is_never_shown_as_inf(
    archive, restored
):
    get = restored(edited(archive, {"claims": 1, "retention": 1e308}))
    shown = get(PAGE)
    assert shown.status_code == 200
    assert never_overflowed(shown.text)
    assert rows(shown.text) == [("claims", "1"), ("retention", "1e+308")]


def test_the_checks_are_shown_in_one_order_whatever_order_they_were_stored_in(archive, restored):
    stored = {
        "zeta": 2,
        "arranged_by": "quill-small",
        "omission_rate": 0.0,
        "passage_coverage": 1.0,
        "code_line_retention": 0.875,
        "retention": 0.5,
        "Alpha": 3,
        "passages_cited": 1,
        '"><b>': 4,
        "claims": 1,
    }
    get = restored(edited(archive, stored))
    html = get(PAGE).text
    shown = rows(html)
    # A name the view does not know comes after the known ones, by name.
    assert [name for name, _ in shown] == [*KNOWN, '"><b>', "Alpha", "zeta"]
    assert "<b>" not in html
    assert dict(shown) == {
        "claims": "1",
        "passages_cited": "1",
        "retention": "50%",
        "code_line_retention": "87.5%",
        "passage_coverage": "100%",
        "omission_rate": "0%",
        "arranged_by": "quill-small",
        '"><b>': "4",
        "Alpha": "3",
        "zeta": "2",
    }


# Every value an import takes


@pytest.mark.parametrize(
    ("value", "said"),
    [
        (0, "0%"),
        (1, "100%"),
        (0.0, "0%"),
        (-0.0, "0%"),
        (1.0, "100%"),
        (20 / 21, "95.2%"),
        (5e-324, "0%"),
        (2, "2"),
        (-1, "-1"),
        (-(10**400), "-1" + "0" * 400),
        (int("9" * MOST_DIGITS), "9" * MOST_DIGITS),
        (1.5, "1.5"),
        (1.0000000000000002, "1.0000000000000002"),
        (-5e-324, "-5e-324"),
        (-0.25, "-0.25"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (-1e308, "-1e+308"),
        ("most", "most"),
        ("", ""),
        ("<b>9</b> & 1e400", "<b>9</b> & 1e400"),
    ],
    ids=lambda value: repr(value)[:24],
)
@pytest.mark.parametrize("name", ["retention", "omission_rate"])
def test_a_restored_ratio_is_a_percentage_only_from_0_to_1_and_else_the_value_it_is(
    archive, restored, name, value, said
):
    get = restored(edited(archive, {"claims": 1, name: value}))
    shown = get(PAGE)
    assert shown.status_code == 200
    assert rows(shown.text) == [("claims", "1"), (name, said)]
    assert "<b>" not in shown.text


@pytest.mark.parametrize(
    ("value", "said"),
    [
        (10**400, "1" + "0" * 400),
        (-7, "-7"),
        (0.5, "0.5"),
        (1e308, "1e+308"),
        (-0.0, "-0.0"),
        ("three", "three"),
    ],
    ids=lambda value: repr(value)[:24],
)
@pytest.mark.parametrize("name", ["claims", "passages_cited", "arranged_by", "kept"])
def test_a_restored_check_that_is_no_ratio_is_shown_as_the_value_it_is(
    archive, restored, name, value, said
):
    get = restored(edited(archive, {name: value}))
    shown = get(PAGE)
    assert shown.status_code == 200
    assert rows(shown.text) == [(name, said)]


def test_an_older_version_s_restored_checks_are_shown_the_same_way(archive, restored):
    odd = {"retention": 10**400, "omission_rate": 1e308, "claims": "two"}
    get = restored(edited(archive, odd, version=1))
    older = get(PAGE, params={"version": "1"})
    assert older.status_code == 200
    assert rows(older.text) == [
        ("claims", "two"),
        ("retention", "1" + "0" * 400),
        ("omission_rate", "1e+308"),
    ]
    assert get(PAGE).status_code == 200  # the latest version, as it was written


def test_the_most_checks_an_archive_brings_are_all_shown(archive, restored):
    from cairn.store import portable

    names = [f"m{n:02}" for n in range(portable.METRICS_MAX - len(KNOWN))]
    stored = dict.fromkeys([*reversed(names), *reversed(KNOWN)], 10**400)
    get = restored(edited(archive, stored))
    shown = get(PAGE)
    assert shown.status_code == 200
    assert [name for name, _ in rows(shown.text)] == [*KNOWN, *names]


# What an import refuses never reaches the page


@pytest.mark.parametrize(
    "value",
    [True, False, None, [1], {"nested": 1}, float("nan"), float("inf"), -float("inf")],
    ids=lambda value: repr(value)[:24],
)
def test_an_import_refuses_a_check_that_is_no_number_and_no_word(archive, restored, value):
    with pytest.raises(ValueError, match="metrics"):
        restored(edited(archive, {"retention": value}))


def test_an_import_refuses_a_whole_number_longer_than_json_reads(archive, restored):
    """One digit past the longest the page is shown with above: JSON cannot write it either,
    so the digits are put in by hand."""
    marked = edited(archive, {"retention": 7})
    held, longer = b'"retention": 7', b'"retention": ' + b"9" * (MOST_DIGITS + 1)
    assert zipfile.ZipFile(io.BytesIO(marked)).read(PAGES).count(held) == 1
    with pytest.raises(ValueError, match="not valid JSON"):
        restored(rewritten(marked, lambda data: data.replace(held, longer)))


# What only the store's own callers can save does not fail the page either


def test_a_check_no_import_brings_does_not_fail_the_page(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        project = store.create_project("ops")
        note, _ = store.add_note(project.id, "# Tools\n\nQuill retries.\n")
        (span,) = [s for s in store.note_spans(note.id) if s.kind == "paragraph"]
        claims = [Claim(id="cl_" + span.id[3:], text=span.text, span_ids=(span.id,))]
        odd = {
            "retention": True,
            "omission_rate": None,
            "passage_coverage": {"nested": [1]},
            "code_line_retention": [0.5],
            "claims": False,
            "arranged_by": 7,
        }
        store.save_page(project.id, "tools", "Tools", "# Tools\n", claims, odd)
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            shown = client.get("/b/ops/p/tools")
        assert shown.status_code == 200
        assert rows(shown.text) == [
            ("claims", "False"),
            ("retention", "True"),
            ("code_line_retention", "[0.5]"),
            ("passage_coverage", "{'nested': [1]}"),
            ("omission_rate", "None"),
            ("arranged_by", "7"),
        ]
    finally:
        store.close()
