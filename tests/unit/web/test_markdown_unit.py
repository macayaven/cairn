"""Unit tests: claims shown as Markdown in the web view, and dates a person can read.

A note is somebody's text, and often an agent's. Rendering it must never turn any of it
into markup of its own, a script, a remote image or a link that runs code.
"""

import re

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store
from cairn.web.markdown import MAX_RENDERED, day, guide, render, when

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def test_code_lists_tables_and_quotes_become_structure():
    code = render("```bash\nquill run --max-retries 3 --backoff 30s\n```")
    assert code.startswith("<pre><code") and "quill run --max-retries 3" in code
    assert "```" not in code
    bullet = render("- **Run:** `drift sync --src ./a`.")
    assert "<li>" in bullet and "<strong>Run:</strong>" in bullet
    assert "<code>drift sync --src ./a</code>" in bullet
    table = render("| Flag | Meaning |\n|---|---|\n| --dry-run | list only |")
    assert "<table>" in table and '<td data-label="Flag">--dry-run</td>' in table
    assert "<blockquote>" in render("> Keep `--delete` off.")
    assert render("Plain words.") == "<p>Plain words.</p>\n"


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        "<a href='javascript:alert(1)'>x</a>",
        "<iframe src=https://example.test></iframe>",
        "</div></section><h1>Not the page's own heading</h1>",
        "```\n</code></pre><script>alert(1)</script>\n```",
        '<style>body{display:none}</style><p onclick="x()">hi</p>',
    ],
)
def test_markup_in_a_note_is_shown_as_text(hostile):
    out = render(hostile)
    assert not re.search(r"<(script|img|iframe|style|h1|a|div|section)\b", out, re.I), out
    assert not re.search(r"\son\w+=", re.sub(r"&\w+;", "", out).split(">", 1)[0]), out
    assert "&lt;" in out  # it is there, as text


def test_only_ordinary_links_become_links_and_they_open_safely():
    out = render("See [the runbook](https://example.test/run?a=1&b=2) or <https://example.test/x>.")
    links = re.findall(r"<a ([^>]*)>", out)
    assert len(links) == 2
    for attributes in links:
        assert 'rel="noopener noreferrer nofollow"' in attributes
        assert re.search(r'href="https://example\.test/', attributes)
    for scheme in (
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "vbscript:x",
        "file:///etc/passwd",
    ):
        assert "<a " not in render(f"[click]({scheme})"), scheme
    assert "<a " in render("[mail](mailto:ops@example.test)")


def test_images_and_headings_stay_text():
    assert "<img" not in render("![chart](https://example.test/pixel.png)")
    assert not re.search(r"<h[1-6]", render("# Looks like a heading\n\nSetext\n======"))


def test_a_table_cell_carries_its_column_name_and_no_style():
    out = render("| Flag | | Count |\n|---|---|--:|\n| --dry-run | x | 3 |\n| `-v` | y | 12 |")
    cells = re.findall(r"<td ([^>]*)>", out)
    assert (
        cells
        == [
            'data-label="Flag"',
            'data-label="Column 2"',
            'class="align-right" data-label="Count"',
        ]
        * 2
    )
    assert "style=" not in out and '<th class="align-right">Count</th>' in out
    named = render("| **Who** | `flag` |\n|---|---|\n| a | b |")
    assert '<td data-label="Who">a</td>' in named and '<td data-label="flag">b</td>' in named


def test_a_guide_has_its_title_its_sections_and_anchors_for_them():
    text = (
        "# Connect an agent\n\nIntro with <b>raw</b> HTML and "
        "[patterns](patterns.md#pick-a-pattern)."
        "\n\n## Claude Code\n\n```bash\nclaude mcp add cairn\n```\n\n### Sign in\n\nText.\n\n"
        "## VS Code (GitHub Copilot agent mode)\n\n# A second title\n\n[out](https://example.test/a)"
    )
    shown = guide(text, known=("connect", "patterns"))
    assert shown.title == "Connect an agent"
    assert shown.sections == [
        ("claude-code", "Claude Code"),
        ("vs-code-github-copilot-agent-mode", "VS Code (GitHub Copilot agent mode)"),
        ("a-second-title", "A second title"),
    ]
    html = str(shown.html)
    assert "<h1" not in html and "Connect an agent" not in html  # the page's own h1 holds it
    assert '<h2 id="claude-code">Claude Code</h2>' in html and '<h3 id="sign-in">' in html
    assert '<h2 id="a-second-title">' in html  # one h1 a page
    assert "<pre><code" in html and "```" not in html and "&lt;b&gt;raw&lt;/b&gt;" in html
    assert 'href="/docs/patterns#pick-a-pattern"' in html
    assert re.search(r'<a href="https://example\.test/a" rel="noopener noreferrer nofollow">', html)


def test_a_guides_repeated_headings_get_distinct_anchors():
    shown = guide("# T\n\n## Notes\n\nA\n\n## Notes\n\nB\n", known=())
    assert [anchor for anchor, _ in shown.sections] == ["notes", "notes-1"]


def test_a_very_long_claim_is_not_parsed():
    huge = "*x* " * (MAX_RENDERED // 4 + 10)
    out = render(huge)
    assert out.startswith("<pre>") and "<em>" not in out
    assert "<em>" in render("*x* " * 10)


def test_a_date_reads_like_a_date():
    assert when("2026-11-10T22:39:07.632098+00:00") == "10 November 2026, 22:39 UTC"
    assert when("2026-03-01T07:05:00+02:00") == "1 March 2026, 05:05 UTC"
    assert when("not a date") == "not a date"
    assert when("") == ""
    assert day("2026-11-10T22:39:07.632098+00:00") == "10 Nov 2026"
    assert day("2026-03-01T00:30:00+02:00") == "28 Feb 2026"  # the UTC day
    assert day("soon") == "soon"


def test_the_page_view_renders_a_claim_and_keeps_its_source_word_for_word(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    project = store.create_project("ops")
    note = (
        "# Retry policy\n\nQuill retries **3 times**. <script>alert('x')</script>\n\n"
        "```bash\nquill run --max-retries 3\n```\n\n- See [docs](javascript:alert(2)).\n"
    )
    stored, _ = store.add_note(project.id, note)
    assert compile_note(store, stored).status == "saved"
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        html = client.get("/b/ops/p/retry-policy").text
        listing = client.get("/b/ops").text
    store.close()
    assert re.search(r'<time datetime="2\d{3}-[^"]+">\d{1,2} [A-Z][a-z]{2} 2\d{3}</time>', listing)

    claims = html.split('aria-label="Claims"', 1)[1].split("<h2>Checks</h2>", 1)[0]
    assert "<strong>3 times</strong>" in claims
    assert re.search(r"<pre><code[^>]*>quill run --max-retries 3\n</code></pre>", claims)
    # The fence is markup now, not text. A claim's proof shows the note's own lines, fence
    # included, as the list of sources does.
    assert "```" not in re.sub(r'<details class="proof">.*?</details>', "", claims, flags=re.S)
    assert "<script" not in html.lower() and 'href="javascript:' not in html.lower()
    assert "&lt;script&gt;" in claims
    # The source below the claims is the note's own text, untouched.
    sources = html.split("<h2>Sources</h2>", 1)[1]
    assert "```bash" in sources and "Quill retries **3 times**." in sources
    # And the page says when it was written in words.
    assert re.search(
        r'<time datetime="2\d{3}-[^"]+">\d{1,2} \w+ 2\d{3}, \d\d:\d\d UTC</time>', html
    )
