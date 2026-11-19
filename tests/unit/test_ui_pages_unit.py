"""Unit tests for the pages' wave of fixes (review: UI and UX 2, 3, 5, 6, 7, 8, 9): one click
ends nothing, the first-run pages, getting around (skip link, breadcrumbs, citations), the
set-up page's address and narrow screens."""

import re
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web.brand import Brand
from cairn.web.templating import _page

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
SKIP = '<a class="skip-link" href="#main">Skip to the main content</a>'
NOTE = "# Deploy\n\nRun verify-backup.sh before tagging a release.\n"


@pytest.fixture
def env(tmp_path):
    """A key session in the default workspace, with the project "ops" holding one note."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        client.post("/projects", data={"name": "ops"}, headers=SAME_ORIGIN)
        saved = client.post("/b/ops/notes", data={"text": NOTE}, headers=SAME_ORIGIN)
        assert saved.status_code in (200, 303)
        yield client, store
    store.close()


def main_of(html: str) -> str:
    return html.split('<main id="main">', 1)[1].split("</main>", 1)[0]


# 4. Every page starts with a link to its main content


def test_every_page_starts_with_a_skip_link_to_its_main_landmark(env):
    client, _ = env
    for path in ("/", "/b/ops", "/search", "/new", "/workspace", "/keys", "/setup", "/start"):
        html = client.get(path).text
        body = html.split("<body", 1)[1].split(">", 1)[1].lstrip()
        assert body.startswith(SKIP), path
        assert html.count('<main id="main">') == 1, path


def test_a_signed_out_page_has_the_skip_link_too(env):
    client, _ = env
    client.post("/logout", headers=SAME_ORIGIN)
    html = client.get("/login").text
    assert SKIP in html and '<main id="main">' in html


def test_a_branded_page_has_the_skip_link_too():
    html = _page("brand_retry.html", brand=Brand("Fable Works"), url="/").body.decode()
    body = html.split("<body", 1)[1].split(">", 1)[1].lstrip()
    assert body.startswith(SKIP) and '<main id="main">' in html


def test_the_skip_link_shows_only_when_it_has_the_keyboards_focus():
    from cairn.web.templating import HERE

    css = (HERE / "static" / "cairn.css").read_text("utf-8")
    hidden = re.search(r"\.skip-link \{([^}]*)\}", css.replace(".visually-hidden, ", ""))
    shown = re.search(r"\.skip-link:focus \{([^}]*)\}", css)
    assert hidden and "clip-path: inset(50%)" in hidden.group(1)
    assert shown and "clip-path: none" in shown.group(1)


# 5. Breadcrumbs, one way; citations with a name

BREADCRUMB = '<nav class="crumbs" aria-label="Breadcrumb">'


def crumbs_of(html: str) -> list[tuple[str, str]]:
    """The breadcrumb's links, as (href, text); fails when the page has no breadcrumb."""
    block = html.split(BREADCRUMB, 1)[1].split("</nav>", 1)[0]
    return re.findall(r'<a href="([^"]+)">([^<]*)</a>', block)


def test_every_breadcrumb_is_a_navigation_landmark_written_one_way(env):
    client, store = env
    (project,) = store.list_projects()
    (note,) = store.list_notes(project.id)
    (page,) = store.list_pages(project.id)
    trails = {
        "/b/ops": [("/", "Projects")],
        f"/b/ops/p/{page.slug}": [("/", "Projects"), ("/b/ops", "ops")],
        "/b/ops/new": [("/", "Projects"), ("/b/ops", "ops")],
        f"/b/ops/notes/{note.id}/forget": [("/", "Projects"), ("/b/ops", "ops")],
        "/b/ops/delete": [("/", "Projects"), ("/b/ops", "ops")],
        "/b/ops/graph": [("/b/ops", "ops")],
        "/keys": [("/workspace", "Workspace")],
        "/audit": [("/workspace", "Workspace")],
        "/docs/connect": [("/docs", "Guides")],
    }
    for path, trail in trails.items():
        html = client.get(path).text
        assert html.count(BREADCRUMB) == 1, path
        assert crumbs_of(html) == trail, path
        assert 'class="crumbs"' not in html.replace(BREADCRUMB, ""), path


def test_no_template_writes_a_breadcrumb_its_own_way():
    from cairn.web.templating import HERE

    for template in sorted((HERE / "templates").glob("*.html")):
        if template.name in ("_components.html", "landing.html"):  # the macro; an illustration
            continue
        text = template.read_text("utf-8")
        assert 'class="crumbs"' not in text, template.name
        assert '<p class="meta"><a href="/' not in text, template.name


def test_a_citation_link_is_named_source_n(env):
    client, store = env
    (project,) = store.list_projects()
    (page,) = store.list_pages(project.id)
    html = client.get(f"/b/ops/p/{page.slug}").text
    cites = html.split('class="in-margin claim-cites">', 1)[1].split("</div>", 1)[0]
    links = re.findall(r"<a [^>]*>", cites)
    assert links and all('aria-label="Source 1"' in link for link in links)


# 6. The set-up page shows this server's own address


def test_the_setup_page_shows_the_servers_own_address(env):
    client, _ = env
    html = client.get("/setup").text
    assert f"CAIRN_URL={BASE}\n" in html and "your-host" not in html


def test_without_a_public_url_the_setup_page_shows_a_placeholder(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    with TestClient(create_app(store), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        assert "CAIRN_URL=https://&lt;your-host&gt;" in client.get("/setup").text
    store.close()


# 1. One click ends nothing; 2. a repeated button says which row it acts on

ISSUER = "https://id.example.test/pool-1"
REDIRECT = "http://127.0.0.1:33418/callback"


def dump(db) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def team(tmp_path):
    """Ada owns Fable Works's default workspace, Brook administers it, Quill is a member.
    Brook made the key "brook-ci", Quill approved the agent "quill-agent", there is a set
    "work", and an open invitation for zed@example.test."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    people = {}
    for name in ("Ada", "Brook", "Quill"):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        people[name.lower()] = store.for_account(signed.account_id, signed.user.id)
    people["ada"].set_role(people["brook"].user_id, "admin")
    people["brook"].create_key("brook-ci")
    work = people["ada"].create_set("work", [])
    invite = people["ada"].invite("zed@example.test")
    agent = store.register_client("quill-agent", [REDIRECT])
    code = people["quill"].create_code(agent.id, REDIRECT, "c" * 43)
    assert store.redeem_code(code, agent.id)
    (grant,) = store.list_grants()
    sessions = {name: view.create_session() for name, view in people.items()}
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield SimpleNamespace(
            client=client, store=store, db=db, sessions=sessions, work=work, invite=invite,
            grant=grant, **people,
        )  # fmt: skip
    store.close()


def as_(team, who: str) -> dict[str, str]:
    return {"cookie": f"cairn_session={team.sessions[who]}"}


def endings(team) -> list[dict]:
    """Each ending: the list it is on, its address, what its row's link is named, and the
    words its page must say of what stops working with it."""
    return [
        {
            "list": "/keys",
            "path": "/keys/brook-ci/revoke",
            "named": "Revoke brook-ci",
            "says": "Every script and agent that sends this key stops working at once, and every"
            " web session opened with it ends.",
        },
        {
            "list": "/members",
            "path": f"/members/{team.brook.user_id}/remove",
            "named": "Remove Brook",
            "says": "Their sessions here end, and the agents they approved and the keys they"
            " made stop working, at once.",
        },
        {
            "list": "/members",
            "path": f"/members/invites/{team.invite.id}/revoke",
            "named": "Revoke the invitation for zed@example.test",
            "says": "The link stops working: zed@example.test cannot join with it.",
        },
        {
            "list": "/agents",
            "path": f"/agents/{team.grant.id}/revoke",
            "named": "Disconnect quill-agent",
            "says": "It stops working at once: its tokens are refused from then on.",
        },
        {
            "list": "/sets",
            "path": f"/sets/{team.work.id}/delete",
            "named": "Delete the set work",
            "says": "Every key and agent that reaches the set stops reaching its projects.",
        },
    ]


def test_each_ending_on_a_list_is_a_link_to_its_page_not_a_form(team):
    for ending in endings(team):
        listed = team.client.get(ending["list"], headers=as_(team, "ada")).text
        assert f'action="{ending["path"]}"' not in listed, ending["path"]
        link = re.search(rf'<a [^>]*href="{re.escape(ending["path"])}"[^>]*>', listed)
        assert link, ending["path"]
        assert f'aria-label="{ending["named"]}"' in link.group(), ending["path"]


def test_each_endings_page_names_what_ends_and_holds_the_one_form(team):
    for ending in endings(team):
        before = dump(team.db)
        page = team.client.get(ending["path"], headers=as_(team, "ada"))
        assert page.status_code == 200, ending["path"]
        main = main_of(page.text)
        assert ending["says"] in main, ending["path"]
        forms = re.findall(r'<form method="post" action="([^"]+)"', main)
        assert forms == [ending["path"]], ending["path"]
        back = re.search(r'<a class="keep" href="([^"]+)">', main)
        assert back and back.group(1) == ending["list"], ending["path"]
        assert main.count("<h1>") == 1 and "style=" not in page.text
        assert dump(team.db) == before, ending["path"]  # asking writes nothing


def test_each_endings_form_does_it(team):
    for ending in endings(team):
        page = main_of(team.client.get(ending["path"], headers=as_(team, "ada")).text)
        (action,) = re.findall(r'<form method="post" action="([^"]+)"', page)
        done = team.client.post(
            action, headers={**as_(team, "ada"), **SAME_ORIGIN}, follow_redirects=False
        )
        assert done.status_code == 303 and done.headers["location"] == ending["list"]
    assert [key.label for key in team.store.list_keys()] == []
    assert team.store.list_invites() == [] and team.store.list_grants() == []
    assert team.store.list_sets() == []
    assert "Brook" not in [member.name for member in team.store.list_members()]


def test_each_ending_refuses_a_post_from_another_site(team):
    for ending in endings(team):
        cross = {**as_(team, "ada"), "sec-fetch-site": "cross-site"}
        before = dump(team.db)
        refused = team.client.post(ending["path"], headers=cross, follow_redirects=False)
        assert refused.status_code == 403, ending["path"]
        assert dump(team.db) == before, ending["path"]


def test_an_endings_page_answers_as_its_form_would(team):
    quill = as_(team, "quill")
    for path in (
        "/keys/brook-ci/revoke",
        f"/members/{team.brook.user_id}/remove",
        f"/members/invites/{team.invite.id}/revoke",
        f"/sets/{team.work.id}/delete",
    ):
        assert team.client.get(path, headers=quill).status_code == 403, path
    ada = as_(team, "ada")
    for path in (
        "/keys/no-such-key/revoke",
        "/members/usr_000000000000/remove",
        "/members/invites/inv_000000000000/revoke",
        "/agents/grt_000000000000/revoke",
        "/sets/set_000000000000/delete",
    ):
        assert team.client.get(path, headers=ada).status_code == 404, path
    brook_agent = f"/agents/{team.grant.id}/revoke"  # Quill's agent: Brook sees it, as admin
    assert team.client.get(brook_agent, headers=as_(team, "brook")).status_code == 200


def test_a_key_that_made_things_offers_both_on_its_one_page(team):
    team.client.post(
        "/login", data={"key": team.brook.create_key("brook-laptop")}, headers=SAME_ORIGIN
    )
    team.client.post("/keys", data={"label": "made-later"}, headers=SAME_ORIGIN)
    main = main_of(team.client.get("/keys/brook-laptop/revoke").text)
    assert "made-later" in main
    actions = re.findall(r'<form method="post" action="([^"]+)"', main)
    assert actions == ["/keys/brook-laptop/revoke"] * 2
    assert 'value="keep"' in main and 'value="take"' in main


def test_every_repeated_row_action_names_its_row(team):
    members = main_of(team.client.get("/members", headers=as_(team, "ada")).text)
    assert 'aria-label="Change the role of Brook"' in members
    assert 'aria-label="Change the role of Quill"' in members
    assert 'aria-label="Remove Quill"' in members


def test_forget_this_note_names_its_note(env):
    client, store = env
    (project,) = store.list_projects()
    (page,) = store.list_pages(project.id)
    html = client.get(f"/b/ops/p/{page.slug}").text
    links = re.findall(r'<a [^>]*href="/b/ops/notes/[^"]+/forget"[^>]*>', html)
    assert links and all('aria-label="Forget the note Deploy"' in link for link in links)


# 3. The first-run pages tell the truth about Personal notes


def test_the_personal_notes_card_says_who_else_reaches_it_as_the_new_project_page_does(env):
    from cairn.web.templating import PRIVATE_REACH as SAID

    client, _ = env
    assert SAID.startswith("Only you, the people you name, and the workspace's owners")
    assert "keys and sources that belong to whoever runs the server" in SAID
    PRIVATE_REACH = SAID  # our own words, shown as written
    assert PRIVATE_REACH in client.get("/").text  # the new-project form, where it was
    start = main_of(client.get("/start").text)
    cards = re.findall(r'<li class="use">(.*?)</li>', start, re.DOTALL)
    personal = [card for card in cards if 'value="personal"' in card]
    others = [card for card in cards if 'value="personal"' not in card]
    assert len(personal) == 1 and PRIVATE_REACH in personal[0]
    assert others and all(PRIVATE_REACH not in card for card in others)


def test_each_cards_name_field_has_a_name_of_its_own(env):
    from cairn.kinds import KINDS

    client, _ = env
    start = main_of(client.get("/start").text)
    named = {}
    for kind_id in re.findall(r'<input id="name-([a-z]+)"', start):
        label = re.search(rf'<label class="use-name" for="name-{kind_id}">(.*?)</label>', start)
        named[kind_id] = re.sub(r"<[^>]+>", "", label.group(1))
    assert len(named) > 1 and len(set(named.values())) == len(named)
    for kind_id, words in named.items():
        assert words == f"Project name for {KINDS[kind_id].title}"


def test_the_first_run_choice_of_projects_is_under_projects(env):
    """/start makes projects, so it is under Projects. Its next step, /start/connect, is
    also the workspace's Setup, which the workspace page lists, so it stays under
    Workspace and leads back there."""

    def current(html: str) -> list[str]:
        block = html.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
        return re.findall(r'href="([^"]+)" aria-current="page"', block)

    client, _ = env
    for path, section, trail in (
        ("/start", "/", [("/", "Projects")]),
        ("/start/connect", "/workspace", [("/workspace", "Workspace")]),
    ):
        html = client.get(path).text
        assert current(html) == [section], path
        assert html.count(BREADCRUMB) == 1 and crumbs_of(html) == trail, path


# 8. Narrow screens: 44 by 44 to touch, and no label on a node's icon


def phone_rules(css: str) -> dict[str, str]:
    """The phone block's rules (max-width: 34rem), each selector of a list on its own."""
    start = css.index("{", css.index("@media (max-width: 34rem)"))
    depth, at = 0, start
    while True:
        depth += {"{": 1, "}": -1}.get(css[at], 0)
        if depth == 0:
            break
        at += 1
    block = re.sub(r"/\*.*?\*/", "", css[start + 1 : at], flags=re.S)
    rules: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block):
        for selector in selectors.split(","):
            rules[" ".join(selector.split())] = rules.get(" ".join(selector.split()), "") + body
    return rules


def test_at_phone_width_the_small_links_are_44_by_44_to_touch():
    from cairn.web.templating import HERE

    css = (HERE / "static" / "cairn.css").read_text("utf-8")
    main = phone_rules(css)
    explorer = phone_rules((HERE / "static" / "graph-view.css").read_text("utf-8"))
    for selector in (".claim-cites a", ".versions a", ".ending a", ".choices a.quiet"):
        assert "min-height: 44px" in main[selector], selector
        assert "min-width: 44px" in main[selector], selector
    assert "min-height: 44px" in main[".rows td.act a"]
    narrow = re.search(r"--margin-narrow: (\d+)px", css)
    assert narrow and int(narrow.group(1)) >= 44 + 8  # the cites' column holds a 44px link
    assert "min-height: 44px" in explorer[".graph-explorer .graph-views a"]
    assert "min-width: 44px" in explorer[".graph-explorer .graph-views a"]


def boxes(html: str, *, explorer: bool) -> tuple[list, list]:
    """The node icons' and labels' boxes in a drawing, as (left, top, right, bottom)."""
    icons, labels = [], []
    if explorer:
        for x, y in re.findall(r'data-x="(-?\d+)" data-y="(-?\d+)"', html):
            x, y = int(x), int(y)
            icons.append((x - 7, y - 7, x + 7, y + 7))
            labels.append((x + 9, y - 8, x + 9 + 190, y - 8 + 22))
    else:
        for x, y in re.findall(r'<circle cx="(-?\d+)" cy="(-?\d+)" r="5"', html):
            x, y = int(x), int(y)
            icons.append((x - 5, y - 5, x + 5, y + 5))
            labels.append((x + 8, y + 4, x + 8 + 210, y + 4 + 24))
    return icons, labels


def test_the_server_drawn_graph_keeps_every_label_off_every_icon():
    from cairn.web.graph_view import render_explorer, render_graph

    nodes = [{"id": "project:1", "kind": "project", "label": "alpha-ops", "focus": "project"}]
    edges = []
    for n in range(4):
        nodes.append({"id": f"page:{n}", "kind": "page", "label": f"deploy-{n}" * 3,
                      "slug": f"deploy-{n}"})  # fmt: skip
        nodes.append({"id": f"claim:{n}", "kind": "claim", "label": "Run it." * 9,
                      "slug": f"deploy-{n}", "claim_id": f"c{n}"})  # fmt: skip
        nodes.append({"id": f"passage:{n}", "kind": "passage", "label": "lines 1-2"})
        nodes.append({"id": f"note:{n}", "kind": "note", "label": "Quill's note" * 4,
                      "note_id": f"n{n}", "source_ref": None, "via": "mcp"})  # fmt: skip
        edges.append({"kind": "has page", "from": "project:1", "to": f"page:{n}"})
    graph = {"nodes": nodes, "edges": edges, "truncated": False, "focus": "project",
             "next": None}  # fmt: skip
    drawn = [
        (render_graph("alpha-ops", graph, brand=None, person=None), False),
        (render_explorer("alpha-ops", graph, brand=None, person=None), True),
    ]
    for response, explorer in drawn:
        icons, labels = boxes(response.body.decode(), explorer=explorer)
        assert len(icons) == len(nodes)
        for label in labels:
            for icon in icons:
                apart = (
                    label[2] <= icon[0] or icon[2] <= label[0]
                    or label[3] <= icon[1] or icon[3] <= label[1]
                )  # fmt: skip
                assert apart, (label, icon)
