"""Sentences of src/cairn/guides/sources.md (and deploy/README.md) about the Google Drive
source, each pinned here so the guide cannot go false unnoticed: the sentence is in the
test's docstring and in the guide, and the test asks the running code for it, or, for what
Google says, the notes the guide was written from (docs/google-drive-api.md and
docs/google-auth.md). Google is a stand-in; nothing leaves the process. Every name, address
and key is made up, and the key pair is made at test time."""

import json
import shlex
from pathlib import Path

import pytest
from google_authorize_guide import own_authorisation
from guide_source import guide_source
from test_google_auth_unit import key_file, keys, user  # noqa: F401 (keys is a fixture)
from test_google_drive_unit import (
    DAY,
    FOLDER,
    HOUR,
    NOW,
    SINCE,
    Stand,
    document,
    paragraphs,
    refs,
    settings,
)

from cairn.sources import KINDS, SourceError
from cairn.sources import google_auth as auth
from cairn.sources import google_drive as d

ROOT = Path(__file__).parents[2]
RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
NOTES = " ".join((ROOT / "docs" / "google-drive-api.md").read_text().split())
AUTH_NOTES = " ".join((ROOT / "docs" / "google-auth.md").read_text().split())
HEADING = "### Documents from Google Drive, read by the server"


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


def section() -> str:
    """The Drive section of the guide, to its last line."""
    start = RAW.index(HEADING)
    end = RAW.find("\n## ", start)
    return RAW[start:end]


def noted(*quotes: str, notes: str = NOTES) -> None:
    for quote in quotes:
        assert " ".join(quote.split()) in notes, quote


def test_the_guide_says_when_to_use_the_drive_source_or_an_agent():
    """sources.md: "When a team keeps its documents in Google Drive, the operator can add a
    Google Drive source for one folder, and each document put into that folder arrives as a
    note without anyone asking an agent. Use it to keep the documents themselves, word for
    word. Use the recipe above when you want an agent to pick out the decisions and
    constraints and write them in its own words, when a document's later versions matter as
    much as its first, or when your documents live where cairn has no source.\""""
    said(
        "When a team keeps its documents in Google Drive, the operator can add a Google Drive"
        " source for one folder, and each document put into that folder arrives as a note"
        " without anyone asking an agent. Use it to keep the documents themselves, word for"
        " word. Use the recipe above when you want an agent to pick out the decisions and"
        " constraints and write them in its own words, when a document's later versions matter"
        " as much as its first, or when your documents live where cairn has no source."
    )
    assert RAW.index("## Documents\n") < RAW.index(HEADING) < RAW.index("## To-dos")
    assert "google_drive" in KINDS


def test_it_reads_the_docs_and_text_files_directly_in_one_folder_into_one_project():
    """sources.md: "A Google Drive source reads the Google Docs, Markdown files and text files
    directly in the one folder the operator names, and writes each document as a note into
    one project: a Doc as Google exports it to Markdown, a file as it is. It does not look into
    subfolders, follow shortcuts or read any other kind of file, such as a Sheet, a
    presentation or a PDF.\""""
    said(
        "A Google Drive source reads the Google Docs, Markdown files and text files directly"
        " in the one folder the operator names, and writes each document as a note into one"
        " project: a Doc as Google exports it to Markdown, a file as it is."
    )
    said(
        "It does not look into subfolders, follow shortcuts or read any other kind of file,"
        " such as a Sheet, a presentation or a PDF."
    )
    stand = Stand()
    one = stand.add(1, SINCE + HOUR)
    two = stand.add(2, SINCE + 2 * HOUR, name="Plan.md", mimeType=d.MARKDOWN)
    three = stand.add(3, SINCE + 3 * HOUR, name="Ideas.txt", mimeType=d.TEXT)
    for n, mime in enumerate(
        [
            "application/vnd.google-apps.spreadsheet",
            "application/vnd.google-apps.presentation",
            "application/pdf",
            d.FOLDER_TYPE,
            "application/vnd.google-apps.shortcut",
        ],
        start=4,
    ):
        stand.add(n, SINCE + n * HOUR, mimeType=mime)
    got = stand.pull()
    assert refs(got) == [f"drive:{one}", f"drive:{two}", f"drive:{three}"]
    assert got.skipped == [] and {draft.project for draft in got.drafts} == {"specs"}
    (listing,) = stand.of("list")
    assert listing.url.params["q"] == f"'{FOLDER}' in parents and trashed = false"
    (export,) = stand.of("export")
    assert export.url.params["mimeType"] == "text/markdown"
    assert len(stand.of("media")) == 2


def test_its_note_is_a_heading_the_facts_and_the_text_and_no_email_address():
    """sources.md: "Its note has a heading with the document's name, then when it was last
    modified, who last modified it, by name, and a link to open it in Google Drive, then the
    document's text, unchanged but for embedded images, which are left out with their alt
    text kept. No email address is written among these facts.\""""
    said(
        "Its note has a heading with the document's name, then when it was last modified, who"
        " last modified it, by name, and a link to open it in Google Drive, then the document's"
        " text, unchanged but for embedded images, which are left out with their alt text kept."
    )
    said("No email address is written among these facts.")
    text = "Intro ![Chart](data:image/png;base64,iVBORw0KGgo) and the rest.\n"
    (draft,) = d.drafts(document(), d.clean(text.encode()), "specs")
    assert draft.text == (
        "# Ledger design\n\n"
        "- Last modified: 2026-11-12 09:30 UTC\n"
        "- Last modified by: Ada Park\n"
        "- Open it in Google Drive: https://docs.google.com/document/d/1UnitDoc/edit\n\n"
        "Intro Chart and the rest.\n"
    )
    assert d.facts(document(editor="ada@example.test")) == [
        "- Last modified: 2026-11-12 09:30 UTC",
        "- Open it in Google Drive: https://docs.google.com/document/d/1UnitDoc/edit",
    ]


def test_each_document_gets_a_page_named_by_its_name_and_a_long_one_is_written_in_parts():
    """sources.md: "Each document gets a page of its own, named by its name. A document longer
    than 1,500 passages is written in parts.\""""
    said("Each document gets a page of its own, named by its name.")
    said("A document longer than 1,500 passages is written in parts.")
    assert d.PASSAGES == 1_500
    made = d.drafts(document(), "\n\n".join(paragraphs(1_600)), "specs")
    assert [m.topic for m in made] == ["Ledger design", "Ledger design, part 2"]


def test_what_cannot_be_read_is_reported_once():
    """sources.md: "A document that may not be downloaded, one over 10 MB, and one that would
    take more than 20 parts are not read, and each is reported once.\""""
    said(
        "A document that may not be downloaded, one over 10 MB, and one that would take more"
        " than 20 parts are not read, and each is reported once."
    )
    assert (d.MAX_BYTES, d.MAX_PARTS) == (10 * 1024 * 1024, 20)
    stand = Stand()
    stand.add(1, SINCE + HOUR, capabilities={"canDownload": False})
    stand.add(2, SINCE + 2 * HOUR, mimeType=d.TEXT, size=str(d.MAX_BYTES + 1))
    stand.add(3, SINCE + 3 * HOUR, "\n\n".join(paragraphs(20 * 1_500)))
    got = stand.pull()
    assert got.drafts == []
    assert [s.reason for s in got.skipped] == [d.NO_DOWNLOAD, d.TOO_LARGE, d.TOO_LONG]
    stand.now += HOUR
    again = stand.pull(got.cursor)
    assert again.drafts == [] and again.skipped == []


def test_switched_on_the_page_proposes_the_project_documents():
    """sources.md: "When it is switched on, the page proposes the project `documents`.\""""
    said("When it is switched on, the page proposes the project `documents`.")
    assert KINDS["google_drive"].default_project == "documents"


def test_a_person_chooses_a_document_by_putting_it_into_the_folder():
    """sources.md: "The source starts off: the operator adds it, and it reads nothing until an
    owner or an admin switches it on. From then on, putting a document into the folder is what
    chooses it. A document already in the folder when the source is switched on is not read,
    unless `existing` is set; one put in or changed after is. A document is read once it has
    rested, not modified for `settle_hours` (24 unless set, from 1 to 720), so one still being
    written waits.\""""
    said(
        "The source starts off: the operator adds it, and it reads nothing until an owner or an"
        " admin switches it on. From then on, putting a document into the folder is what"
        " chooses it."
    )
    said(
        "A document already in the folder when the source is switched on is not read, unless"
        " `existing` is set; one put in or changed after is."
    )
    said(
        "A document is read once it has rested, not modified for `settle_hours` (24 unless set,"
        " from 1 to 720), so one still being written waits."
    )
    assert KINDS["google_drive"].starts_off and d.SETTLE_HOURS == (24, 1, 720)
    stand = Stand()
    old = stand.add(1, SINCE - 30 * DAY)
    changed = stand.add(2, SINCE + HOUR)
    writing = stand.add(3, NOW - 2 * HOUR)
    got = stand.pull()
    assert refs(got) == [f"drive:{changed}"]
    later = stand.add(4, SINCE - 300 * DAY)  # put in after the switch-on, however old
    stand.now += DAY
    assert refs(stand.pull(got.cursor)) == [f"drive:{later}", f"drive:{writing}"]
    assert f"drive:{old}" in refs(stand.pull(existing=True))


def test_each_document_is_read_once_and_put_back_to_be_read_again():
    """sources.md: "Each document is read once. An edit, a rename, a move or a deletion
    afterwards changes nothing in cairn. cairn keeps every note it is given, so reading a
    document again at each change would pile near-copies onto its page, and Google gives a
    reader no dependable sign that a Doc's text has changed. To have a newer version read,
    take the document out of the folder, leave it out until the server has synced once, and
    put it back: it is read again once it has rested, and if it changed, its page gets a
    second note.\""""
    said(
        "Each document is read once. An edit, a rename, a move or a deletion afterwards changes"
        " nothing in cairn. cairn keeps every note it is given, so reading a document again at"
        " each change would pile near-copies onto its page, and Google gives a reader no"
        " dependable sign that a Doc's text has changed."
    )
    said(
        "To have a newer version read, take the document out of the folder, leave it out until"
        " the server has synced once, and put it back: it is read again once it has rested,"
        " and if it changed, its page gets a second note."
    )
    noted(
        "not populated for Docs Editors or shortcut files",
        "To access the revision history, a user must have the `role` of `owner`, `organizer`,"
        " `fileOrganizer`, or `writer`.",
    )
    stand = Stand()
    one = stand.add(1, SINCE + HOUR)
    got = stand.pull()
    stand.files[one].update(name="Renamed", modifiedTime="2026-11-13T13:00:00.000Z")
    stand.texts[one] = b"A later edit.\n"
    stand.now += 2 * DAY
    kept = stand.pull(got.cursor)
    assert kept.drafts == []
    taken = stand.files.pop(one)
    stand.files[one] = taken  # out and back with no sync between: never seen to leave
    assert stand.pull(kept.cursor).drafts == []
    stand.files.pop(one)
    out = stand.pull(kept.cursor)
    stand.files[one] = taken
    back = stand.pull(out.cursor)
    assert refs(back) == [f"drive:{one}"] and "A later edit." in back.drafts[0].text


def test_switched_off_and_on_it_takes_stock_again():
    """sources.md: "Switched off and on again, the source takes stock again: what was put in
    or changed while it was off is not read.\""""
    said(
        "Switched off and on again, the source takes stock again: what was put in or changed"
        " while it was off is not read."
    )
    stand = Stand()
    got = stand.pull()
    meanwhile = stand.add(1, NOW + HOUR)  # changed while it was off
    stand.now += 3 * DAY
    again = stand.pull(got.cursor, since=stand.now)
    assert again.drafts == []
    stand.files[meanwhile]["modifiedTime"] = "2026-11-17T09:00:00.000Z"  # changed since
    stand.now += 2 * DAY
    assert refs(stand.pull(again.cursor, since=NOW + 3 * DAY)) == [f"drive:{meanwhile}"]


def test_the_scope_is_drive_readonly_which_google_classes_restricted():
    """sources.md: "The source signs in to Google in one of three ways, and needs the scope
    `https://www.googleapis.com/auth/drive.readonly` in each. Google classes this scope as
    Restricted, and a Workspace administrator can block it.\""""
    said(
        "The source signs in to Google in one of three ways, and needs the scope"
        " `https://www.googleapis.com/auth/drive.readonly` in each. Google classes this scope as"
        " Restricted, and a Workspace administrator can block it."
    )
    assert d.SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]
    noted(
        '| `drive.readonly` | Restricted | "View and download all your Drive files." |',
        "restrict the high-risk Drive scopes (`drive.readonly` among them)",
    )


def test_the_credential_is_json_in_a_variable_and_the_api_is_enabled(keys):  # noqa: F811
    """sources.md: "Its credential is the text of a JSON document in a variable of the
    server's environment, never in cairn and never a path to a file: with Docker, a line in
    `deploy/cairn.env` with the JSON on one line between single quotes. The Google Drive API
    is enabled in the Google Cloud project the credential comes from.\""""
    said(
        "Its credential is the text of a JSON document in a variable of the server's"
        " environment, never in cairn and never a path to a file: with Docker, a line in"
        " `deploy/cairn.env` with the JSON on one line between single quotes."
    )
    said("The Google Drive API is enabled in the Google Cloud project the credential comes from.")
    noted('enabling "Google Drive API"')
    _, pem = keys
    line = json.dumps(key_file(pem))
    assert isinstance(auth.load_credential("V", {"V": line}), auth.ServiceAccount)
    with pytest.raises(SourceError, match="not a path"):
        auth.load_credential("V", {"V": "/run/secrets/google.json"})
    with pytest.raises(ValueError, match="credentials_env holds what looks like a credential"):
        d.check_config(settings(credentials_env=line))
    reply = d.Reply(403, reasons=frozenset({"accessNotConfigured"}))
    assert "the Google Drive API is enabled in its Cloud project" in d.refusal(reply, "x", "V")


def test_a_service_account_the_folder_is_shared_with_reads_as_itself(keys):  # noqa: F811
    """sources.md, the shared folder's three steps: a key as for delegation; the folder shared
    with the service account's address as Viewer, Notify people unticked, sharing with the
    whole domain not reaching it; the key file in the variable and no `subject`."""
    said(
        "With a service account the folder is shared with, when no administrator needs to be asked:"
    )
    said(
        "1. In a Google Cloud project, create a service account and a JSON key for it. Google"
        " keeps no copy of the key, and an organization created on or after May 3, 2024 blocks"
        " key creation unless its admin allows it."
    )
    said(
        "2. Someone who may share the folder shares it with the service account's email"
        " address as Viewer, and unticks Notify people: a service account has no inbox."
        " Sharing with the whole domain does not reach a service account, which is not part of"
        " the domain."
    )
    said(
        "3. The variable holds the key file's contents, and the config has no `subject`: the"
        " service account reads as itself, with no domain-wide delegation."
    )
    noted(
        "you can directly share individual files with the service account's email address"
        " using the standard UI.",
        "(such as Editor or Viewer)",
        "since service accounts don't have inboxes",
        "If you share Google Workspace assets, like documents or events, with your entire"
        " Google Workspace domain, they're not shared with service accounts.",
    )
    noted(
        'Google "does not keep a copy of your private keys"',
        "organization was created on or after May 3, 2024, this constraint is enforced by default.",
        notes=AUTH_NOTES,
    )
    _, pem = keys
    env = {"V": json.dumps(key_file(pem))}
    source = d.open_source(
        "specs", d.check_config(settings(credentials_env="V")), http=None, env=env
    )
    assert source.subject is None
    signed = auth.assertion(auth.load_credential("V", env), d.SCOPES, None, 1_700_000_000)
    claims = json.loads(segment(signed.split(".")[1]))
    assert "sub" not in claims and claims["scope"] == d.SCOPE


def segment(part: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_a_service_account_acting_for_a_person_needs_a_super_admins_delegation(keys):  # noqa: F811
    """sources.md, delegation's three steps: the key as above; delegation by a super admin with
    the numeric Client ID and the scope, which can take up to 24 hours; the key file in the
    variable and `subject` in the config, a person who can see the folder."""
    said(
        "With a service account acting for a person, for a Google Workspace domain whose super"
        " admin agrees:"
    )
    said("1. In a Google Cloud project, create a service account and a JSON key for it, as above.")
    said(
        "2. A super admin of the domain opens the Admin console, then Security, Access and data"
        " control, API Controls, **Manage Domain Wide Delegation**, and adds the service"
        " account's numeric **Client ID** (not its email address) with the scope above. It"
        " usually takes a few minutes and can take up to 24 hours."
    )
    said(
        "3. The variable holds the key file's contents, and `subject` in the source's config is"
        " the address of the person it reads as. The folder must be one that person can see."
    )
    noted(
        "Main menu > Security > Access and data control > API Controls",
        "Using the client email instead of the numeric ID, or a Google Group, gives"
        " `unauthorized_client`",
        '"usually takes a few minutes ... might take up to 24 hours"',
        notes=AUTH_NOTES,
    )
    noted("The named folder must be visible to that person.")
    _, pem = keys
    signed = auth.assertion(
        auth.load_credential("V", {"V": json.dumps(key_file(pem))}),
        d.SCOPES,
        "ada@example.test",
        1_700_000_000,
    )
    claims = json.loads(segment(signed.split(".")[1]))
    assert claims["sub"] == "ada@example.test" and claims["scope"] == d.SCOPE


def test_with_ones_own_authorisation_the_variable_holds_a_refresh_token_and_no_subject():
    """sources.md, one person's own authorisation: a Desktop app client, `google-authorize`
    with `--kind google_drive` on the person's computer, the file's one line in the variable
    without `subject`, and where the guide says how long the token lasts (pinned with the
    command in test_guide_facts_google_authorize_unit.py)."""
    said("With one person's own authorisation, when the source should read as that person:")
    own_authorisation("google_drive", section(), d.SCOPES)  # each step in this section
    step3 = (
        '3. The variable holds the file\'s one line, `{"type": "authorized_user", "client_id":'
        ' "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no'
        " `subject`. Then delete the file."
    )
    assert " ".join(step3.split()) in " ".join(section().split())  # here, not another section
    env = {"V": json.dumps(user())}
    config = d.check_config(settings(credentials_env="V", subject="ada@example.test"))
    with pytest.raises(SourceError, match="remove subject"):
        d.open_source("specs", config, http=None, env=env)
    assert "authorise again" in auth._AUTHORIZED_USER_SAID["invalid_grant"]


def test_the_command_adds_it_off_and_the_settings_are_as_the_guide_lists_them():
    """sources.md: the source-add command, then "`folder_id` is the last part of the folder's
    address, after `/folders/`. `credentials_env` and `folder_id` are the settings it needs;
    `subject` (with delegation), `settle_hours` (24), `existing` (false), `base_url` and
    `projects` are optional.\""""
    (block,) = [
        part.split("```", 1)[0] for part in section().split("```text\n")[1:] if "source-add" in part
    ]
    argv = shlex.split(block.strip())
    assert argv[:5] == ["python", "-m", "cairn.server", "source-add", "--db"]
    assert argv[argv.index("--kind") + 1] == "google_drive"
    config = json.loads(argv[argv.index("--config") + 1])
    checked = d.check_config(config)
    assert checked["credentials_env"] == "GOOGLE_DRIVE_CREDENTIAL" and checked["projects"] == []
    assert (checked["settle_hours"], checked["existing"]) == (24, False)
    said("`folder_id` is the last part of the folder's address, after `/folders/`.")
    with pytest.raises(ValueError, match="after /folders/"):
        d.check_config({**config, "folder_id": f"https://drive.google.com/drive/folders/{FOLDER}"})
    said(
        "`credentials_env` and `folder_id` are the settings it needs; `subject` (with"
        " delegation), `settle_hours` (24), `existing` (false), `base_url` and `projects` are"
        " optional."
    )
    assert set(d.SETTINGS) == {
        "credentials_env",
        "folder_id",
        "subject",
        "settle_hours",
        "existing",
        "base_url",
        "projects",
    }
    for needed in ("credentials_env", "folder_id"):
        with pytest.raises(ValueError, match=f"needs {needed}"):
            d.check_config({k: v for k, v in config.items() if k != needed})
    assert KINDS["google_drive"].starts_off


def test_the_page_shows_which_folder_it_reads_and_as_whom_and_never_the_credential():
    """sources.md: "The Sources page shows which folder it reads and as whom, and never the
    credential.\""""
    said("The Sources page shows which folder it reads and as whom, and never the credential.")
    config = d.check_config(settings(subject="ada@example.test"))
    shown = KINDS["google_drive"].describe(config)
    assert FOLDER in shown and "ada@example.test" in shown
    assert config["credentials_env"] not in shown


def test_the_list_of_what_the_server_reads_links_to_the_section():
    """sources.md: "... [Google Drive](#documents-from-google-drive-read-by-the-server), where
    the documents put into one folder do; and ...\""""
    said(
        "[Google Drive](#documents-from-google-drive-read-by-the-server), where the documents"
        " put into one folder do;"
    )
    assert HEADING in RAW


def test_the_deploy_guide_says_a_drive_source_starts_off_and_reads_what_is_put_in():
    """deploy/README.md: "A Google Drive source starts off too, and then reads the documents
    put into its one folder from then on, each once; its variable holds a Google credential
    in the same way.\""""
    deploy = " ".join((ROOT / "deploy" / "README.md").read_text().split())
    assert (
        "A Google Drive source starts off too, and then reads the documents put into its one"
        " folder from then on, each once; its variable holds a Google credential in the same"
        " way." in deploy
    )
    assert (
        "on its own: Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail,"
        " Linear, GitHub or a connector of your own." in deploy
    )
    assert KINDS["google_drive"].starts_off
