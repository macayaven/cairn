"""The pages on cairn's design system, as far as a test can see it.

- A compiled page shows each claim with its citation numbers in the margin (`in-margin
  claim-cites`), and each source with the source's own line numbers in the margin,
  `is-cited` on exactly the lines the source span holds, on the number and on the line.
- Only cited source lines and the words a search matched carry the highlight: the
  stylesheet uses `--mark` for nothing else, and a page has no other `<mark>`.
- Every actor shown on the audit, keys and agents pages carries its kind as a word:
  person, key, agent or cairn.
- An agent's and a key's scope ends with what is out of reach.
- No template and no stylesheet names another origin: no font, stylesheet, script or
  image is fetched from another host. The two typefaces lead the font stacks, and the
  stylesheet's only `url(` addresses are their files, relative to it, under fonts/.
- The stylesheet's colour tokens meet WCAG AA in both themes for the pairs the design
  system's usage notes name: 4.5:1 for text, 3:1 for `rule-strong` and `mark-edge`.
"""

import re
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store
from support.web.design_system import CSS, PACKAGE, Node, rule, tree

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
# Two sections, and one line said in both: the synthetic note the design system's
# previews use.
NOTE = (
    "# Drift sync\n"
    "\n"
    "Drift mirrors two buckets.\n"
    "\n"
    "## Flags\n"
    "\n"
    "- `--retries 3` helps on flaky links.\n"
    "- `--dry-run` lists the changes first.\n"
    "\n"
    "## Limits\n"
    "\n"
    "Drift mirrors two buckets.\n"
)
KINDS = {"person", "key", "agent", "cairn"}


# A small tree of the page, enough to ask where an element sits


def margin_lines(figure: Node) -> list[tuple[int, str, bool, bool]]:
    """A Source's lines: (number in the margin, the line, number cited, line cited)."""
    (grid,) = figure.all(cls="source-lines")
    cells = grid.kids()
    pairs = list(zip(cells[::2], cells[1::2], strict=True))
    shown = []
    for number, line in pairs:
        assert "in-margin" in number.classes and "source-line" in line.classes
        shown.append(
            (
                int(number.text()),
                line.text(),
                "is-cited" in number.classes,
                "is-cited" in line.classes,
            )
        )
    return shown


# The world


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, NOTE, source_ref="session 2026-11-13")
    assert compile_note(store, note).status == "saved"
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        r = client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
        assert r.status_code == 303
        yield store, client, project, note
    store.close()


def connect_agent(store, name, projects=None, access="write"):
    client = store.register_client(name, [REDIRECT])
    code = store.create_code(client.id, REDIRECT, CHALLENGE, projects=projects, access=access)
    assert store.redeem_code(code, client.id) is not None


# The page: citations in the margin, a source's own line numbers


def test_a_compiled_page_numbers_its_citations_and_its_sources_lines(env):
    store, client, project, note = env
    (page,) = store.list_pages(project.id)
    html = client.get(f"/b/alpha/p/{page.slug}").text
    doc = tree(html)
    lines = note.text.splitlines()

    (listed,) = doc.all("ol", "sources")
    order = [li.attrs["id"] for li in listed.kids()]
    spans = store.get_spans(order)

    claims = doc.all("article", "claim")
    assert len(claims) == len(page.claims) >= 3
    for article, claim in zip(claims, page.claims, strict=True):
        assert article.attrs["id"] == claim.id
        assert "margin-grid" in article.classes
        margin, body = article.kids()
        assert {"in-margin", "claim-cites"} <= margin.classes
        cites = margin.all("a")
        assert [a.attrs["href"] for a in cites] == [f"#{i}" for i in dict.fromkeys(claim.span_ids)]
        for a in cites:  # the number is the source's place in the list of sources
            assert int(a.text()) == order.index(a.attrs["href"][1:]) + 1
        assert claim.text.split("`")[0].strip("- ") in body.text()
        (proof,) = body.all("details", "proof")
        assert proof.all("summary")[0].text().startswith("See the ")
        shown = proof.all("figure", "source")
        assert len(shown) == len(claim.span_ids)
        assert all("id" not in figure.attrs for figure in shown)  # the list holds the ids

    figures = [li.all("figure", "source")[0] for li in listed.kids()]
    proofs = [f for a in claims for f in a.all("figure", "source")]
    for figure in figures + proofs:
        numbered = margin_lines(figure)
        assert numbered and [n for n, *_ in numbered] == sorted({n for n, *_ in numbered})
        for number, text, number_cited, line_cited in numbered:
            assert text == lines[number - 1]  # the source's own line, at its own number
            assert number_cited == line_cited
    for li, figure in zip(listed.kids(), figures, strict=True):
        span = spans[li.attrs["id"]]
        cited = [n for n, _, c, _ in margin_lines(figure) if c]
        assert cited == list(range(span.start_line, span.end_line + 1))
        assert "\n".join(t for _, t, c, _ in margin_lines(figure) if c) == span.text
    assert any(n == 1 for figure in figures for n, *_ in margin_lines(figure))  # context too


def test_only_cited_lines_and_search_matches_carry_the_highlight(env):
    store, client, project, _ = env
    (page,) = store.list_pages(project.id)
    doc = tree(client.get(f"/b/alpha/p/{page.slug}").text)
    assert doc.all("mark") == []
    marked = [n for n in doc.walk() if "is-cited" in n.classes]
    assert marked and all(n.within("source-lines") for n in marked)

    found = tree(client.get("/search", params={"q": "Retries flaky"}).text)
    marks = found.all("mark")
    assert {m.text().casefold() for m in marks} == {"retries", "flaky"}
    assert all(m.within("hit") for m in marks)
    assert all("is-cited" not in n.classes for n in found.walk())

    css = CSS.read_text(encoding="utf-8")
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"var\(--mark\)", body):
            for one in selector.split(","):
                assert "is-cited" in one or re.search(r"(^|\s)mark\b", one.strip()), one


def test_a_search_match_is_marked_in_the_words_and_never_in_the_markup(env):
    store, client, project, _ = env
    note, _ = store.add_note(project.id, "# Amp\n\nAmp & volts: <b>amp</b> code.\n")
    assert compile_note(store, note).status == "saved"
    html = client.get("/search", params={"q": "amp b"}).text
    hit = html.split('<li class="hit">', 1)[1].split("</li>", 1)[0]
    assert "&<mark>" not in hit and "<mark>amp</mark>;" not in hit
    assert "&lt;<mark>b</mark>&gt;<mark>amp</mark>&lt;/<mark>b</mark>&gt;" in hit


# Who did it, and what an agent can see


def test_every_actor_on_the_audit_keys_and_agents_pages_carries_its_kind(env):
    store, client, project, _ = env
    connect_agent(store, "Claude Code", projects=(project.id,), access="read")
    store.create_key("ci-runner", (project.id,), "read")
    client.post("/b/alpha/notes", data={"text": "# Drift sync\n\nIt runs nightly.\n"},
                headers=SAME_ORIGIN)  # fmt: skip
    seen = {}
    for path in ("/audit", "/keys", "/agents"):
        doc = tree(client.get(path).text)
        actors = doc.all(cls="actor")
        assert actors, path
        for actor in actors:
            kind = actor.all(cls="actor-kind")
            assert len(kind) == 1 and kind[0].text() in KINDS, (path, actor.text())
            assert actor.all(cls="actor-name")[0].text().strip(), path
            seen.setdefault(path, set()).add(kind[0].text())
    assert "key" in seen["/audit"]  # a session opened with a key is that key
    assert "agent" in seen["/agents"] and "key" in seen["/keys"]


def consent_page(client, name: str = "Claude Code") -> str:
    """The real consent page: an agent registers, then asks the signed-in browser."""
    client_id = client.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
        },
    ).json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "s",
    }
    sent = client.get("/authorize?" + urlencode(params), follow_redirects=False)
    page = client.get(sent.headers["location"])
    assert page.status_code == 200
    return page.text


def test_the_consent_page_names_the_agent_as_an_actor_and_says_what_is_out_of_reach(env):
    _, client, _, _ = env
    doc = tree(consent_page(client))
    named = {a.all(cls="actor-name")[0].text(): a.all(cls="actor-kind")[0].text()
             for a in doc.all(cls="actor")}  # fmt: skip
    assert named.get("Claude Code") == "agent"
    assert set(named.values()) <= KINDS
    # The choice reads in the Scope's words, and ends with what no choice opens.
    (choice,) = doc.all(cls="scope-choice")
    (form,) = [f for f in doc.all("form") if f.attrs.get("action") == "/oauth/consent"]
    assert any(node is choice for node in form.walk())
    modes = [m.text().strip() for m in choice.all(cls="scope-mode")]
    assert modes[-1] == "No access"
    beyond = " ".join(choice.all(cls="scope-beyond")[0].text().split())
    assert "left unticked" in beyond and "you cannot reach yourself" in beyond
    # Nothing on the page is a cited line or a search match.
    assert doc.all("mark") == [] and all("is-cited" not in n.classes for n in doc.walk())


def test_every_page_that_shows_the_connect_commands_wraps_them_on_a_phone(env):
    # The phone rule is `.setup pre`: /start/connect had the class, /setup and /welcome,
    # which include the same _connect.html, did not, and their commands ran off the screen.
    _, client, _, _ = env
    for path in ("/setup", "/welcome", "/start/connect"):
        doc = tree(client.get(path).text)
        blocks = doc.all("pre")
        assert blocks, path
        assert all(b.within("setup") for b in blocks), path


def test_a_pages_running_text_keeps_to_the_measure():
    # /join had paragraphs a full 60rem wide beside a lede held to 40rem.
    css = CSS.read_text(encoding="utf-8")
    assert "max-width: var(--measure)" in rule(css, "main > p, main > section > p")


def test_a_long_error_sentence_is_a_heading_not_a_headline(env):
    _, client, _, _ = env
    long = tree(client.get("/oauth/consent", params={"request": "gone"}).text)
    (title,) = long.all("h1")
    assert len(title.text()) > 80 and "is-long" in title.classes
    (short,) = tree(client.get("/no-such-page").text).all("h1")
    assert "is-long" not in short.classes
    css = CSS.read_text(encoding="utf-8")
    assert "font-size: 22px" in rule(css, "h1.is-long")


def test_the_consent_rows_each_put_their_name_above_their_choice():
    css = CSS.read_text(encoding="utf-8")
    beyond = rule(css, ".scope-beyond")
    assert "display: grid" in beyond and "grid-template-columns: minmax(0, 1fr)" in beyond


def test_on_a_phone_an_options_hint_sits_under_its_words_not_under_its_control():
    css = CSS.read_text(encoding="utf-8")
    phone = _block(css, css.index("@media (max-width: 34rem)"))
    hint = rule(phone, ".option > .hint")
    assert "flex-basis: 100%" in hint and "padding-left: 26px" in hint


def test_a_workspace_with_no_people_says_so_and_what_to_do(env):
    _, client, _, _ = env
    doc = tree(client.get("/members").text)
    (empty,) = [
        s
        for s in doc.all("section", "empty-state")
        if s.attrs.get("aria-labelledby") == "no-people"
    ]
    assert "No one has joined yet" in empty.text()
    assert "Invite someone above" in empty.text()
    assert not [t for t in doc.all("table") if t.attrs.get("aria-labelledby") == "people"]


def test_an_agents_and_a_keys_scope_says_what_is_out_of_reach(env):
    store, client, project, _ = env
    beta = store.create_project("beta")
    store.create_project("gamma")
    connect_agent(store, "Claude Code", projects=(project.id,), access="read")
    connect_agent(store, "Codex")  # every project, to write
    store.create_key("ci-runner", (beta.id,), "write")
    for path, rows in (("/agents", 2), ("/keys", 2)):
        doc = tree(client.get(path).text)
        scopes = doc.all("ul", "scope")
        assert len(scopes) == rows, path
        for scope in scopes:
            items = scope.kids()
            assert len(items) >= 2, path
            last = items[-1]
            assert last.all(cls="scope-none"), (path, last.text())
            assert all(not i.all(cls="scope-none") for i in items[:-1])
    agents = tree(client.get("/agents").text)
    narrow = next(s for s in agents.all("ul", "scope") if "alpha" in s.text())
    assert "read only" in narrow.text().lower()
    assert "every other project" in narrow.kids()[-1].text() and "2" in narrow.kids()[-1].text()
    wide = next(s for s in agents.all("ul", "scope") if "alpha" not in s.text())
    assert wide.kids()[-1].text().strip()


# No other origin


def foreign_urls(css: str) -> list[str]:
    """Each `url(...)` of a stylesheet that is not a relative path under fonts/: one with a
    scheme, a `//`, a leading `/`, a `..` or any other directory names what is not cairn's
    own font file. An empty list when every address is one."""
    own = re.compile(r"fonts/[A-Za-z0-9][A-Za-z0-9._-]*")
    found = re.findall(r"url\(\s*(\"[^\"]*\"|'[^']*'|[^)]*?)\s*\)", css, flags=re.I)
    seen = len(re.findall(r"url\(", css, flags=re.I))
    addresses = [address.strip("\"'") for address in found]
    bad = [a for a in addresses if not own.fullmatch(a) or ".." in a]
    return bad + ["url( unparsed"] * (seen - len(found))


def test_no_template_and_no_stylesheet_names_another_origin(env):
    _, client, _, _ = env
    css = CSS.read_text(encoding="utf-8")
    assert "@import" not in css
    # A url( names only cairn's own font files, beside the stylesheet: never another origin.
    assert foreign_urls(css) == []
    assert re.findall(r"url\(", css)  # the typefaces' files are named, so the rule is used
    for doctored in (
        'url("https://fonts.example.test/a.ttf")',
        "url(//fonts.example.test/a.ttf)",
        "url('/static/fonts/a.ttf')",
        'url("fonts/../../a.ttf")',
        "url(data:font/ttf;base64,AAAA)",
        'URL("images/a.png")',
    ):
        assert foreign_urls(css + f"\na {{ background: {doctored}; }}"), doctored
    sans = re.search(r"--font-sans:\s*([^;]+);", css)
    mono = re.search(r"--font-mono:\s*([^;]+);", css)
    assert sans and sans[1].startswith('"Atkinson Hyperlegible Next", system-ui')
    assert mono and mono[1].startswith('"Atkinson Hyperlegible Mono", ui-monospace')
    outside = re.compile(
        r"""(\bsrc\s*=\s*["']?\s*(https?:)?//)"""
        r"""|(<link\b[^>]*\bhref\s*=\s*["']?\s*(https?:)?//)"""
        r"""|@import|url\(""",
        re.I,
    )
    for template in sorted((PACKAGE / "templates").glob("*.html")):
        assert not outside.search(template.read_text(encoding="utf-8")), template.name
    for path in ("/", "/b/alpha", "/search?q=drift", "/docs", "/agents"):
        assert not outside.search(client.get(path).text), path


# What the screenshots showed: a phone's cards, its targets, a token that must not break


def test_a_source_head_names_the_note_once_then_the_section(env):
    store, client, project, _ = env
    (page,) = store.list_pages(project.id)
    heads = [
        " ".join(span.text().split())
        for figure in tree(client.get(f"/b/alpha/p/{page.slug}").text).all("figure", "source")
        for span in figure.all("figcaption", "source-head")[0].kids()
        if span.tag == "span" and "id" not in span.classes and not span.text().startswith("written")
    ]
    within = " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} "
    assert f"Drift sync{within}Flags" in heads and f"Drift sync{within}Limits" in heads
    assert all("Drift sync · Drift sync" not in head for head in heads), heads


def phone_cards(css: str) -> str:
    return _block(css, css.index("@media (max-width: 40rem)"))


def test_a_cards_value_flows_beside_its_label_as_one_line_of_text():
    # A grid would make each piece of a value (an actor and its id, a detail's name and its
    # words) a cell of its own; the label takes a gutter and the value stays in flow.
    cards = phone_cards(CSS.read_text(encoding="utf-8"))
    cell = rule(cards, ".rows td, .prose td, .md td")
    assert "display: grid" not in cell and "display: flex" not in cell
    assert "padding: var(--space-1) 0 var(--space-1) calc(7rem" in cell
    label = rule(cards, ".rows td::before, .prose td::before, .md td::before")
    assert "position: absolute" in label and "width: 7rem" in label


def test_a_cards_title_link_is_a_target_on_a_phone():
    cards = phone_cards(CSS.read_text(encoding="utf-8"))
    title = rule(cards, ".rows td:first-child a")
    assert "min-height: 44px" in title and "display: inline-flex" in title


def test_the_access_pages_list_form_is_a_form_section_and_its_notice_a_notice(env):
    """/b/<project>/access: "Add several people" keeps to the width of the other forms (a 60rem
    textarea and select read as a different page), and the addresses that are no member's
    are listed in a plain notice, a long one wrapping inside it on a phone; nothing carries
    the highlight or a style."""
    _, client, _, _ = env
    css = CSS.read_text(encoding="utf-8")
    widths = ".composer, .new-project, .new-key, .new-set, .set, .guard, .invite, .share"
    assert "max-width: 34rem" in rule(css, widths)
    assert "overflow-wrap: anywhere" in rule(css, ".notice ul")
    # A fieldset is as wide as its widest control unless told otherwise: the third level's
    # words pushed "People named", and the form with it, past a phone's edge.
    assert "min-width: 0" in rule(css, "fieldset.reach")
    page = client.get("/b/alpha/access")
    doc = tree(page.text)
    (form,) = [f for f in doc.all("form") if f.attrs.get("action") == "/b/alpha/share"]
    assert form.within("share")
    typed = "nobody@elsewhere.example.test, other@elsewhere.example.test"
    told = client.post(
        "/b/alpha/share", data={"emails": typed, "level": "read"}, headers=SAME_ORIGIN
    )
    assert told.status_code == 200
    doc = tree(told.text)
    listed = [n for n in doc.walk() if "email" in n.classes]
    assert [n.text() for n in listed] == typed.split(", ")
    assert all(n.within("notice") for n in listed)
    for html in (page.text, told.text):
        assert " style=" not in html and "<mark" not in html and "is-cited" not in html


def test_each_source_has_its_destination_as_a_scope_its_state_in_words_and_one_main_button(
    env, monkeypatch
):
    """/sources (S2): a source is a section of its own; where it writes is a Scope; its state
    is words; an off source says that switching it on reads only what happens from then on;
    the new project's name is a Field (a label above the control, the rule under it); and each
    source has one main button. No style, no script."""
    from cairn.sources import KINDS, SourceKind

    store, client, _, _ = env
    kind = SourceKind(check=dict, open=lambda *a, **k: None, via="m", starts_off=True)
    monkeypatch.setitem(KINDS, "minutes", kind)
    store.add_source(
        "traces",
        "langfuse",
        {
            "base_url": "https://langfuse.example.test",
            "projects": ["alpha"],
            "public_key_env": "DS_PUBLIC",
            "secret_key_env": "DS_SECRET",
        },
    )
    store.add_source("standup", "minutes", {"projects": []})
    html = client.get("/sources").text
    assert " style=" not in html and "<script" not in html and "<mark" not in html
    doc = tree(html)
    sections = {s.attrs["id"]: s for s in doc.all("section", "integration")}
    assert set(sections) == {"source-traces", "source-standup"}
    for section in sections.values():
        (state,) = section.all(cls="state")
        assert " ".join(state.text().split()).lower().startswith(("on since", "off"))
        (scope,) = section.all("ul", "scope")
        assert scope.all(cls="scope-none")  # it ends with where it does not write
        buttons = section.all("button")
        main = [b for b in buttons if not b.classes & {"quiet", "link", "danger"}]
        assert len(main) == 1, [b.text() for b in buttons]
    off = sections["source-standup"]
    assert "reads only what happens from then on" in " ".join(off.text().split())
    (field,) = [i for i in off.all("input") if i.attrs.get("name") == "new_project"]
    labels = [lab for lab in off.all("label") if lab.attrs.get("for") == field.attrs["id"]]
    assert labels and field.attrs.get("aria-describedby")
    (hint,) = [p for p in off.all("p") if p.attrs.get("id") == field.attrs["aria-describedby"]]
    assert "private" in hint.text()


def test_a_form_section_keeps_its_distance_from_what_follows():
    # On /sets the empty state came straight after the "Create the set" button.
    css = CSS.read_text(encoding="utf-8")
    assert "margin-bottom: var(--space-8)" in rule(css, ".new-set, .new-key")


def main_buttons(html: str) -> list[str]:
    """The text of each main button on the page: a submit button or a.button that is not
    quiet, a link or a stop."""
    doc = tree(html)
    found = [*doc.all("button"), *doc.all("a", "button")]
    return [
        " ".join(b.text().split())
        for b in found
        if not b.classes & {"quiet", "is-quiet", "link", "danger", "is-stop"}
    ]


def test_the_access_page_has_one_main_button_and_it_saves_the_rule(env):
    """/b/<project>/access holds two forms. The page is for the rule, so saving it is the one
    main button; adding several people is quiet."""
    _, client, _, _ = env
    page = client.get("/b/alpha/access")
    assert page.status_code == 200 and "Add them" in page.text
    assert main_buttons(page.text) == ["Save who reaches it"]


def test_code_in_a_guides_table_stays_whole_on_a_wide_screen():
    css = CSS.read_text(encoding="utf-8")
    wide = _block(css, css.index("@media (min-width: 40.01rem)"))
    assert "overflow-wrap: normal" in rule(wide, ".prose td code, .md td code")


def test_the_landings_connect_commands_show_whole_on_a_wide_screen():
    # With the shipped mono face (wider than Menlo), the Cursor line ran under the edge of
    # its 46rem box at 1280 px, its closing braces hidden behind a scroll a Mac hides.
    css = CSS.read_text(encoding="utf-8")
    assert "max-width: var(--page)" in rule(css, ".commands")
    shown = rule(css, ".commands pre")
    assert "white-space: pre-wrap" in shown and "overflow-wrap: anywhere" in shown


def test_an_audit_action_and_an_actors_id_never_break_inside(env):
    _, client, _, _ = env
    audit = client.get("/audit").text
    assert re.search(r'<td data-label="What" class="mono">note\.saved</td>', audit)
    css = CSS.read_text(encoding="utf-8")
    kept = rule(css, '.rows td.mono[data-label="What"], .rows td[data-label="Who"] > .hint')
    assert "white-space: nowrap" in kept


def test_on_a_phone_a_claims_number_sits_on_its_first_line_and_every_link_is_a_target():
    css = CSS.read_text(encoding="utf-8")
    phone = _block(css, css.index("@media (max-width: 34rem)"))
    cites = rule(phone, ".claim-cites a")
    assert "min-height: 44px" in cites and "line-height: 27px" in cites  # the body's line
    # The links of running text: 44px high to touch, and a link that wraps (a title in it)
    # keeps the text's own line, not 44px a line; an inline block, so its words and the
    # title flow as one text (tests/browser/test_browser_wrapped_links.py).
    ((targets, said),) = [
        (sel, body)
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", phone)
        if ".crumbs a" in sel
    ]
    assert ".sources .back a" in targets and ".source-head a" in targets
    assert "display: inline-block" in said and "align-content: center" in said
    assert "min-height: 44px" in said and "flex" not in said
    assert "line-height" not in said


# Contrast, from the stylesheet's own tokens

TEXT = 4.5
NON_TEXT = 3.0
PAIRS = [
    *(("ink", ground, TEXT) for ground in ("paper", "sheet", "well", "lichen-soft", "stop-soft")),
    *(("ink-soft", ground, TEXT) for ground in ("paper", "sheet", "well")),
    *(("lichen", ground, TEXT) for ground in ("paper", "sheet", "well", "lichen-soft")),
    *(("stop", ground, TEXT) for ground in ("paper", "sheet", "stop-soft")),
    ("on-lichen", "lichen", TEXT),
    ("on-mark", "mark", TEXT),
    ("on-stop", "stop", TEXT),
    ("paper", "ink", TEXT),  # the main button
    ("paper", "ink-soft", TEXT),  # the main button under the pointer
    ("ink-soft", "mark", TEXT),  # a cited line's number
    *(("rule-strong", ground, NON_TEXT) for ground in ("paper", "sheet", "well")),
    *(("mark-edge", ground, NON_TEXT) for ground in ("paper", "sheet")),
    *(("focus", ground, NON_TEXT) for ground in ("paper", "sheet", "well", "mark")),
]
COLOURS = {name for pair in PAIRS for name in pair[:2]}


def _block(css: str, start: int) -> str:
    """The text inside the braces that open at or after `start`."""
    opened = css.index("{", start)
    depth, at = 0, opened
    while True:
        depth += {"{": 1, "}": -1}.get(css[at], 0)
        if depth == 0:
            return css[opened + 1 : at]
        at += 1


def themes(css: str) -> dict[str, dict[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    light = dict(re.findall(r"--([\w-]+):\s*([^;]+);", _block(css, css.index(":root"))))
    media = css.index("@media (prefers-color-scheme: dark)")
    dark_block = _block(css, media)
    dark = light | dict(re.findall(r"--([\w-]+):\s*([^;]+);", _block(dark_block, 0)))
    return {"light": light, "dark": dark}


def colour(tokens: dict[str, str], name: str) -> str:
    value = tokens[name].strip()
    while found := re.fullmatch(r"var\(--([\w-]+)\)", value):
        value = tokens[found[1]].strip()
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (name, value)
    return value


def luminance(hex_colour: str) -> float:
    def component(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * component(r) + 0.7152 * component(g) + 0.0722 * component(b)


def ratio(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_stylesheet_defines_every_colour_for_both_themes():
    css = CSS.read_text(encoding="utf-8")
    found = themes(css)
    dark_only = dict(
        re.findall(r"--([\w-]+):\s*([^;]+);", _block(css, css.index("prefers-color-scheme: dark")))
    )
    for name in COLOURS - {"focus"}:
        assert name in found["light"] and name in dark_only, name
    assert colour(found["light"], "paper").lower() == "#f5f6f3"  # stone white, not cream
    assert colour(found["dark"], "paper").lower() == "#101316"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fore, ground, least", PAIRS)
def test_every_named_pair_is_legible(theme, fore, ground, least):
    tokens = themes(CSS.read_text(encoding="utf-8"))[theme]
    measured = ratio(colour(tokens, fore), colour(tokens, ground))
    assert measured >= least, f"{theme}: {fore} on {ground} is {measured:.2f}:1"
