"""Unit tests for writing from the web view, beyond the frozen M1 contract.

They pin what tests/acceptance/test_m1_mobile.py leaves open: what a browser actually sends
(every field, blank or not, and CRLF line ends), a note that compiles into no page, how a
shared link joins shared text, and what an error keeps in the form.
"""

import re

import httpx
import pytest
from starlette.testclient import TestClient
from test_guide_facts_m3_unit import SIX, answer

from cairn import sources
from cairn.arrange import arrange_page
from cairn.compiler import GateResult
from cairn.identity import GitHub
from cairn.llm import FakeModel
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web import cli_arrange
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
NOTE = "# Retry policy\n\nQuill retries 3 times before failing.\n"


def _client(tmp_path, **options):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("phone")
    store.create_project("alpha")
    client = TestClient(create_app(store, public_url=BASE, **options), base_url=BASE)
    r = client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    return store, client


@pytest.fixture
def env(tmp_path):
    store, client = _client(tmp_path)
    with client:
        yield store, client
    store.close()


def post(client, path, **data):
    return client.post(path, data=data, headers=SAME_ORIGIN, follow_redirects=False)


def notes(store, project="alpha"):
    return store.list_notes(store.get_project(project).id)


def textarea(text: str) -> str:
    return re.search(r"<textarea\b[^>]*>(.*?)</textarea>", text, re.S).group(1)


def test_blank_optional_fields_are_left_out_like_the_note_tool_does(env):
    store, client = env
    r = post(client, "/b/alpha/notes", text=NOTE, title="", topic="  ", source_ref="")
    assert r.status_code == 303 and r.headers["location"] == "/b/alpha/p/retry-policy"
    (note,) = notes(store)
    assert (note.topic, note.source_ref) == (None, None)


def test_a_browsers_crlf_line_ends_are_stored_as_lf(env):
    store, client = env
    r = post(client, "/b/alpha/notes", text=NOTE.replace("\n", "\r\n"))
    assert r.status_code == 303
    (note,) = notes(store)
    assert note.text == NOTE


def test_a_note_with_no_passage_is_refused_and_nothing_is_stored(env):  # review 7.6
    store, client = env
    r = post(client, "/b/alpha/notes", text="# Only a heading\n")
    assert r.status_code == 400 and "no body text" in r.text
    assert len(notes(store)) == 0


def test_the_project_is_named_in_the_redirect_even_when_posted_by_id(env):
    store, client = env
    project = store.get_project("alpha")
    r = post(client, f"/b/{project.id}/notes", text=NOTE)
    assert r.headers["location"] == "/b/alpha/p/retry-policy"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"url": "https://example.test/"}, "https://example.test/"),
        ({"text": "See https://example.test/", "url": "https://example.test/"},
         "See https://example.test/"),
        ({"text": "Plain words"}, "Plain words"),
        ({}, ""),
    ],
)  # fmt: skip
def test_a_shared_link_joins_the_text_once(env, params, expected):
    _, client = env
    assert textarea(client.get("/new", params=params).text).strip() == expected


def test_quick_note_keeps_the_chosen_project_and_the_text_on_an_error(env):
    store, client = env
    store.create_project("beta")
    r = post(client, "/notes", project="beta", text="  ", title="Kept <title>")
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert re.search(r'<option value="beta" selected>', r.text)
    assert 'value="Kept &lt;title&gt;"' in r.text


def test_a_rejected_quick_note_lists_the_violations(tmp_path):
    def refuse(page, spans):
        return GateResult(passed=False, violations=("lint.something: <x>",))

    store, client = _client(tmp_path, gate=refuse)
    with client:
        r = post(client, "/notes", project="alpha", text=NOTE)
        assert r.status_code == 422 and "lint.something: &lt;x&gt;" in r.text
        assert "Quill retries 3 times before failing." in textarea(r.text)
    store.close()


def test_a_refused_project_keeps_what_was_typed(env):
    _, client = env
    r = post(client, "/projects", name="not a <name>", description="for <field> notes")
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert 'value="not a &lt;name&gt;"' in r.text
    field = re.compile(r'<input id="project-name"[^>]*>')
    assert "autofocus" in field.search(r.text).group(0)  # the field and its message in view
    assert "autofocus" not in field.search(client.get("/").text).group(0)
    assert 'value="for &lt;field&gt; notes"' in r.text
    assert "<name>" not in r.text and "<field>" not in r.text


def test_the_manifest_is_served_as_a_manifest(env):
    _, client = env
    r = client.get("/static/manifest.webmanifest")
    assert r.headers["content-type"].startswith("application/manifest+json")


# The Source field (R1.5): what its forms do, and a near miss caught before storing

# What a form does, as the hint beside the field says it after the forms: no more than is
# true whether or not a model is connected and whatever the operator set.
MARKED = (
    " keeps its page from a model the operator connects to arrange pages, unless the operator"
    " chose to send such notes; the assistants people connect read it like any note they reach"
    " and send it to their own provider."
)
MORE_THAN_TRUE = ("no model", "every model")  # what neither the hint nor the alert may promise


def test_beside_the_source_field_the_forms_are_named_from_the_codes_list(env):
    from cairn.sources import withheld_forms

    _, client = env
    named = sorted(withheld_forms())  # the recipe's forms and the kinds' own words (R1.6)
    assert {"mail", "gmail"} <= set(named)
    forms = [f"<code>{form}:</code>" for form in named]
    listed = f"{', '.join(forms[:-1])} or {forms[-1]}"
    said = f'<p class="hint">A note whose source begins with {listed}{MARKED}</p>'
    for page in (client.get("/b/alpha/new").text, client.get("/new").text):
        assert said in page
        assert not [words for words in MORE_THAN_TRUE if words in page]
        assert 'name="as_written"' not in page  # the tick box comes with a near miss only


def test_what_the_hint_says_of_a_marked_note_is_what_happens_to_it(env):
    """The hint's sentence (MARKED), run: a note filed through the form under each start it
    names has a page the arranger does not send to its model by default and does send when
    the operator's setting says send; an agent with a key reads the note's claims either way."""
    store, client = env
    project = store.get_project("alpha")
    hint = re.search(
        r'<p class="hint">(A note whose source begins with .*?)</p>',
        client.get("/b/alpha/new").text,
        re.S,
    ).group(1)
    assert hint.endswith(MARKED)
    named = re.findall(r"<code>([^<]+):</code>", hint)
    assert len(named) > 1 and "mail" in named
    # "the operator chose to send such notes": <PREFIX>_SOURCE_NOTES=send, which makes the
    # arranger withhold nothing (cli_arrange.serve_arranger); unset, it withholds these.
    assert cli_arrange.sends_source_notes("ARRANGER", {}) is False
    assert cli_arrange.sends_source_notes("ARRANGER", {"ARRANGER_SOURCE_NOTES": "send"}) is True
    key = store.create_key("assistant")
    for start in named:
        pages = []
        for n in range(2):  # a page for each setting: a page not sent is tidied instead
            title = f"Shelf {start} {n}"
            filed = post(
                client, "/b/alpha/notes", text=f"# {title}\n\n{SIX}", source_ref=f"{start}:x-7"
            )
            assert filed.status_code == 303, start
            pages.append(store.get_page(project.id, filed.headers["location"].rsplit("/", 1)[1]))
        kept, sent = pages
        model = FakeModel([])
        held = arrange_page(store, project.id, kept.slug, model, withhold=sources.withheld_via())
        assert held.status == "withheld" and model.requests == [], start
        model = FakeModel([answer(sent)])
        gone = arrange_page(store, project.id, sent.slug, model, withhold=())
        assert gone.status == "arranged", start
        assert sent.claims[0].text in model.requests[0][1]["content"], start
        read = client.get(f"/api/projects/alpha/pages/{kept.slug}", headers={"x-api-key": key})
        assert kept.claims[0].text in [claim["text"] for claim in read.json()["claims"]], start


@pytest.mark.parametrize("path", ["/b/alpha/notes", "/notes"])
@pytest.mark.parametrize(
    ("typed", "form"),
    [("email:thread-7", "mail"), ("ma\N{ZERO WIDTH SPACE}il:thread-7", "mail"), ("docs", "doc")],
)
def test_a_near_miss_comes_back_with_everything_typed_and_nothing_stored(env, path, typed, form):
    store, client = env
    fields = {"text": NOTE, "title": "Kept <title>", "topic": "Retry", "source_ref": typed}
    r = post(client, path, project="alpha", **fields)
    assert r.status_code == 400 and notes(store) == []
    assert store.list_pages(store.get_project("alpha").id) == []
    alert = re.search(r'<div class="alert" role="alert">(.*?)</div>', r.text, re.S).group(1)
    said = (
        f"The source is close to <code>{form}:</code>, the start that marks a note the way the"
        f" hint under Source says: begin it with <code>{form}:</code> to mark this note, or tick"
        " Save it as written to store the source as it is."
    )
    assert said in alert
    assert MARKED in r.text.split(alert, 1)[1]  # the hint the alert names, further down
    assert not [words for words in MORE_THAN_TRUE if words in r.text]
    assert "Quill retries 3 times before failing." in textarea(r.text)
    assert 'value="Kept &lt;title&gt;"' in r.text and 'value="Retry"' in r.text
    assert f'name="source_ref" type="text" value="{typed}"' in r.text
    form_html = r.text.split('class="stack writing composer"', 1)[1].split("</form>", 1)[0]
    box = re.search(r'<input [^>]*name="as_written"[^>]*>', form_html).group(0)  # posted with it
    assert 'type="checkbox"' in box and 'id="as_written"' in box and "checked" not in box
    assert re.search(r'<label [^>]*for="as_written"[^>]*>.*?Save it as written</label>', form_html)
    assert '<details class="more" open>' in r.text  # the field and its box in view
    ticked = post(client, path, project="alpha", **fields, as_written="1")
    assert ticked.status_code == 303
    (note,) = notes(store)
    assert note.source_ref == typed  # stored unchanged


def test_a_ticked_box_stays_ticked_when_the_note_is_refused_for_something_else(env):
    store, client = env
    token = "ghp_" + "Zq7xR2" * 6  # the note guard refuses it
    text = f"# Keys\n\nThe token is {token}.\n"
    r = post(client, "/b/alpha/notes", text=text, source_ref="email:thread-7", as_written="1")
    assert r.status_code == 422 and notes(store) == []
    assert "Refused." in r.text and "The source is close to" not in r.text
    box = re.search(r'<input [^>]*name="as_written"[^>]*>', r.text).group(0)
    assert "checked" in box


def test_a_source_withheld_as_written_or_far_from_a_form_is_stored_at_once(env):
    store, client = env
    typed = ("mail:thread-1", " MAIL :thread-2", "session:abc", "https://example.test/mail")
    for n, source_ref in enumerate(typed):
        r = post(client, "/b/alpha/notes", text=f"# N{n}\n\nFact {n}.\n", source_ref=source_ref)
        assert r.status_code == 303, source_ref
    assert len(notes(store)) == 4


def test_rest_stores_a_near_miss_as_it_is(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("agent")
    store.create_project("alpha")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        body = {"text": NOTE, "source_ref": "email:thread-7"}
        r = client.post("/api/projects/alpha/notes", headers={"x-api-key": key}, json=body)
        assert r.status_code == 201
    (note,) = notes(store)
    assert note.source_ref == "email:thread-7"
    store.close()


# A note form sent after the sign-in ended (R1.7): the draft comes back, nothing is stored

MARK = "Zephyr-41 <b>draft</b> & more"
TYPED = {
    "project": "alpha",
    "title": f"T {MARK}",
    "text": f"# Retry policy\r\n\r\nQuill retries 3 times. {MARK}\r\n",
    "topic": f"Topic {MARK}",
    "source_ref": "email:thread-9",
    "as_written": "1",
}
SIGNED_OUT = "Your sign-in has ended, so this note was not saved."
SIGN_IN_THERE = "Sign in there, then send this form again."


@pytest.fixture(params=[False, True], ids=["open", "walled"])
def signed_out(tmp_path, request):
    """A browser whose session has ended, with and without the wall, and a session it gets
    by signing in again (in another tab: the same browser's cookie jar)."""
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    walled = request.param
    sign_in = None
    if walled:  # the wall needs a provider to sign in with; none is called here
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    app = create_app(store, public_url=BASE, wall=walled, sign_in=sign_in)
    with TestClient(app, base_url=BASE) as client:
        token = store.sign_in("sso", "a", "a@example.test", "Ada", issuer="https://id.test",
                              join="default").token  # fmt: skip
        yield store, client, token, walled
    store.close()


@pytest.mark.parametrize("path", ["/b/alpha/notes", "/notes"])
def test_a_note_form_sent_signed_out_comes_back_whole_and_goes_through_once_signed_in(
    signed_out, path, caplog
):
    store, client, token, _ = signed_out
    caplog.set_level("DEBUG")
    r = post(client, path, **TYPED)
    assert r.status_code == 401 and notes(store) == []
    assert r.headers["cache-control"] == "no-store" and "set-cookie" not in r.headers
    alert = re.search(r'<div class="alert" role="alert">(.*?)</div>', r.text, re.S).group(1)
    assert SIGNED_OUT in alert and SIGN_IN_THERE in alert
    link = re.search(r'<a [^>]*href="/login"[^>]*>', alert).group(0)
    assert 'target="_blank"' in link and 'rel="noopener"' in link
    escaped = "Zephyr-41 &lt;b&gt;draft&lt;/b&gt; &amp; more"
    assert MARK not in r.text and escaped in r.text
    assert f'value="T {escaped}"' in r.text and f'value="Topic {escaped}"' in r.text
    assert f"Quill retries 3 times. {escaped}" in textarea(r.text)
    assert 'name="source_ref" type="text" value="email:thread-9"' in r.text
    box = re.search(r'<input [^>]*name="as_written"[^>]*>', r.text).group(0)
    assert "checked" in box
    form = r.text.split('class="stack writing composer"', 1)[1].split("</form>", 1)[0]
    assert f'action="{path}"' in r.text.split('class="stack writing composer"', 1)[0][-200:]
    if path == "/notes":
        assert '<option value="alpha" selected>alpha</option>' in form
    assert "Zephyr" not in caplog.text  # nothing typed reaches a log
    # Signed in in another tab: the session cookie is in the browser's jar, and the form
    # posted from this same-origin page carries it (SameSite=Strict sends it on a
    # same-origin request).
    client.cookies.set(session_cookie(), token)
    again = post(client, path, **TYPED)
    assert again.status_code == 303
    (note,) = notes(store)
    assert (note.title, note.topic, note.source_ref) == (
        TYPED["title"], TYPED["topic"], "email:thread-9"
    )  # fmt: skip


def test_under_a_brand_behind_the_wall_the_kept_draft_names_only_the_brand(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    base = "https://kb.example.test"
    app = create_app(store, public_url=base, sign_in=sign_in, wall=True, brand_name="Ledger")
    with TestClient(app, base_url=base) as client:
        r = post(client, "/notes", **TYPED)
    store.close()
    assert r.status_code == 401 and SIGNED_OUT in r.text
    assert "cairn" not in r.text.lower() and "<title>" in r.text and "Ledger" in r.text
    assert r.headers["x-robots-tag"] == "noindex"


def test_only_the_note_form_keeps_its_draft(signed_out):
    store, client, _, walled = signed_out
    bare = (401, "Sign in first.") if walled else (303, "")
    for path, kwargs in (
        ("/projects", {"data": {"name": "beta"}}),  # another form
        ("/notes", {"json": TYPED}),  # not a form post
        ("/notes", {}),  # nothing sent
        ("/b/alpha/notes/x", {"data": TYPED}),  # another address
    ):
        r = client.post(path, headers=SAME_ORIGIN, follow_redirects=False, **kwargs)
        if path == "/b/alpha/notes/x" and r.status_code == 404:
            continue  # no such page, as before
        assert (r.status_code, r.text if walled else "") == bare, (path, kwargs)
        assert "Zephyr" not in r.text
    api = client.post("/api/projects/alpha/notes", json={"text": TYPED["text"]})
    assert api.status_code == 401 and api.headers["content-type"] == "application/json"
    assert notes(store) == []


def test_a_cross_site_note_form_is_refused_and_shows_nothing_typed(signed_out):
    store, client, _, _ = signed_out
    for headers in ({"sec-fetch-site": "cross-site"}, {"origin": "https://elsewhere.test"}):
        r = client.post("/notes", data=TYPED, headers=headers, follow_redirects=False)
        assert r.status_code == 403 and "Zephyr" not in r.text
    assert notes(store) == []


def test_a_draft_past_the_body_cap_is_refused_before_anything_is_shown(signed_out):
    from cairn.server import MAX_BODY

    store, client, _, _ = signed_out
    r = post(client, "/notes", **{**TYPED, "text": "Zephyr " * (MAX_BODY // 7 + 1)})
    assert r.status_code == 413 and "Zephyr" not in r.text
    assert notes(store) == []
