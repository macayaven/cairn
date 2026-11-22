"""Sentences of src/cairn/guides/sources.md (and the other guides) about the Gmail source, each
pinned here so the guide cannot go false unnoticed: the sentence is in the test's docstring and
in the guide, and the test asks the running code for it, or, for what Google says, the notes the
guide was written from (docs/gmail-api.md and docs/google-auth.md). Google is a stand-in;
nothing leaves the process. Every name, address and key is made up, and the key pair is made at
test time."""

import json
import shlex
from importlib.resources import files

import pytest

from cairn.guard import PatternGuard
from cairn.sources import KINDS, SourceError
from cairn.sources import gmail as g
from cairn.sources import google_auth as auth
from support.google_authorize_guide import own_authorisation
from support.guide_source import guide_source
from support.paths import REPO_ROOT
from support.sources.gmail import (
    CREDENTIAL,
    DAY,
    HOUR,
    LABEL_ID,
    NOW,
    SINCE,
    VAR,
    Stand,
    mail,
    part,
    read,
    refs,
    settings,
    tree,
)
from support.sources.google_auth import key_file, keys  # noqa: F401 (keys is a fixture)

ROOT = REPO_ROOT
RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
NOTES = " ".join((ROOT / "docs" / "gmail-api.md").read_text().split())
AUTH_NOTES = " ".join((ROOT / "docs" / "google-auth.md").read_text().split())
HEADING = "### Mail from Gmail, read by the server"
ANCHOR = "#mail-from-gmail-read-by-the-server"


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


def section() -> str:
    """The Gmail source's section of the guide, to the next section."""
    start = RAW.index(HEADING)
    return RAW[start : RAW.index("\n## ", start)]


def here(sentence: str) -> None:
    """The Gmail source's section says `sentence` (other sections say some alike)."""
    assert " ".join(sentence.split()) in " ".join(section().split()), sentence


def noted(*quotes: str, notes: str = NOTES) -> None:
    for quote in quotes:
        assert " ".join(quote.split()) in notes, quote


def test_the_guides_first_line_says_cairn_signs_in_to_mail_only_through_it():
    """sources.md: "cairn signs in to your mail only through a [Gmail
    source](#mail-from-gmail-read-by-the-server) the operator adds, which reads only the
    messages a person labels, and to your calendar only through a [Google Calendar
    source](...) the operator adds." True too where no Gmail source was added: then cairn does
    not sign in to mail at all."""
    said(
        f"cairn signs in to your mail only through a [Gmail source]({ANCHOR}) the operator adds,"
        " which reads only the messages a person labels, and to your calendar only through a"
        " [Google Calendar source](#events-from-google-calendar-read-by-the-server) the operator"
        " adds."
    )
    assert RAW.splitlines()[2].startswith("cairn signs in to your mail only through a [Gmail")
    assert HEADING in RAW and "gmail" in KINDS
    # a source is only ever added by the operator, and a kind that starts off reads nothing
    assert KINDS["gmail"].starts_off


def test_the_list_of_what_the_server_reads_links_to_the_section():
    """sources.md: "... and [Gmail](#mail-from-gmail-read-by-the-server), where the messages a
    person labels do.\""""
    said(f"and [Gmail]({ANCHOR}), where the messages a person labels do.")


def test_the_guide_says_when_to_use_it_or_the_recipe():
    """sources.md: "When the operator can sign in to Google as a server, they can add a Gmail
    source instead, and each message a person labels arrives as a note of its own without
    anyone asking an agent. Use it to keep what a message said, in its sender's words. Use the
    recipe above when you want an agent to pick out what a thread decided and write it in its
    own words, without copying a body, a signature or an address, or when your mail is not in
    Gmail.\""""
    here(
        "When the operator can sign in to Google as a server, they can add a Gmail source"
        " instead, and each message a person labels arrives as a note of its own without anyone"
        " asking an agent. Use it to keep what a message said, in its sender's words. Use the"
        " recipe above when you want an agent to pick out what a thread decided and write it in"
        " its own words, without copying a body, a signature or an address, or when your mail is"
        " not in Gmail."
    )
    # "the recipe above" is the Mail recipe, which tells the agent not to copy a body
    mail_section = RAW[RAW.index("## Mail\n") : RAW.index(HEADING)]
    assert "Never copy a message's body, a signature, an address or a phone number." in (
        mail_section
    )
    stand = Stand()
    stand.messages = [mail(1), mail(2, SINCE + 2 * HOUR)]
    pulled = stand.pull()
    assert refs(pulled) == ["gmail:unitm0001", "gmail:unitm0002"]  # a note each
    assert "Item 1 is agreed." in pulled.drafts[0].text  # the sender's words


def test_a_person_chooses_with_one_label_of_their_own_and_gmails_own_are_refused():
    """sources.md: "A Gmail source reads only the messages of one mailbox that carry one Gmail
    label: a label a person made in Gmail, which the operator names in `label`. Gmail's own
    labels, such as `INBOX`, `SENT` or a category, are refused. Putting the label on a message
    is what chooses it.\""""
    here(
        "A Gmail source reads only the messages of one mailbox that carry one Gmail label: a"
        " label a person made in Gmail, which the operator names in `label`. Gmail's own labels,"
        " such as `INBOX`, `SENT` or a category, are refused. Putting the label on a message is"
        " what chooses it."
    )
    for own in ("INBOX", "sent", "CATEGORY_PERSONAL", "category_updates"):
        with pytest.raises(ValueError, match="one of Gmail's own labels"):
            g.check_config(settings(label=own))
    stand = Stand()
    stand.messages = [mail(1)]
    stand.pull()
    (listed,) = stand.calls("list")
    assert listed.url.params["labelIds"] == LABEL_ID  # the label's id, found by its name
    stand.labels = [{"id": "SENT", "name": "cairn", "type": "system"}]
    assert "Gmail's own labels" in stand.failed()


def test_it_starts_off_and_reads_nothing_that_arrived_before_it_was_switched_on():
    """sources.md: "The source starts off: the operator adds it, and it reads nothing until an
    owner or an admin switches it on. A message that arrived before the source was switched on
    is never read, even when it is labelled later.\""""
    here(
        "The source starts off: the operator adds it, and it reads nothing until an owner or an"
        " admin switches it on. A message that arrived before the source was switched on is"
        " never read, even when it is labelled later."
    )
    assert KINDS["gmail"].starts_off
    stand = Stand()
    stand.messages = [mail(1, SINCE - HOUR), mail(2, SINCE + HOUR)]  # both labelled now
    assert refs(stand.pull()) == ["gmail:unitm0002"]


def test_a_label_is_noticed_for_lookback_days_after_a_message_arrived():
    """sources.md: "A label is noticed for `lookback_days` after a message arrived (30 unless
    set, from 1 to 365): a message labelled later than that is not read.\""""
    here(
        "A label is noticed for `lookback_days` after a message arrived (30 unless set, from 1 to"
        " 365): a message labelled later than that is not read."
    )
    assert g.check_config(settings())["lookback_days"] == 30
    assert g.check_config(settings(lookback_days=1))["lookback_days"] == 1
    assert g.check_config(settings(lookback_days=365))["lookback_days"] == 365
    for days in (0, 366):
        with pytest.raises(ValueError, match="lookback_days"):
            g.check_config(settings(lookback_days=days))
    stand = Stand()
    stand.now = SINCE + 40 * DAY
    early = mail(1, SINCE + HOUR)  # labelled 40 days after it arrived
    recent = mail(2, stand.now - 29 * DAY)  # labelled 29 days after
    stand.messages = [early, recent]
    assert refs(stand.pull()) == ["gmail:unitm0002"]
    stand.messages = [early]
    assert refs(stand.pull(lookback_days=45)) == ["gmail:unitm0001"]


def test_a_message_is_the_unit_and_drafts_spam_and_trash_are_not_read():
    """sources.md: "A message is the unit, not a conversation: labelling a conversation in
    Gmail labels the messages it holds at that moment, so a reply that arrives later is read
    only when it gets the label itself. Drafts are not read, nor messages in Spam or Trash.\""""
    here(
        "A message is the unit, not a conversation: labelling a conversation in Gmail labels the"
        " messages it holds at that moment, so a reply that arrives later is read only when it"
        " gets the label itself. Drafts are not read, nor messages in Spam or Trash."
    )
    noted(
        "A person who labels a conversation labels only its current messages",
        "`messages.list` excludes `SPAM` and `TRASH` unless `includeSpamTrash=true` (default"
        " `false`)",
    )
    stand = Stand()
    stand.messages = [mail(1), mail(2, SINCE + 2 * HOUR, labels=["DRAFT", LABEL_ID])]
    assert refs(stand.pull()) == ["gmail:unitm0001"]
    (listed,) = stand.calls("list")
    assert "includeSpamTrash" not in listed.url.params
    assert "threads" not in " ".join(str(r.url) for r in stand.requests)


def test_no_setting_reads_a_whole_mailbox():
    """sources.md: "A mailbox holds other people's words, and most of it is not worth keeping.
    So no setting reads every message, a folder or a search: a message is read only because a
    person put the label on it.\""""
    here(
        "A mailbox holds other people's words, and most of it is not worth keeping. So no setting"
        " reads every message, a folder or a search: a message is read only because a person put"
        " the label on it."
    )
    assert g.SETTINGS == (
        "credentials_env",
        "subject",
        "label",
        "lookback_days",
        "projects",
        "base_url",
    )
    for setting in ("query", "q", "all", "folder", "includeSpamTrash"):
        with pytest.raises(ValueError, match=r"has no setting|does not take"):
            g.check_config({**settings(), setting: True})
    with pytest.raises(ValueError, match="needs label"):
        g.check_config({"credentials_env": VAR})


def test_its_note_is_a_heading_the_facts_and_the_body_as_it_was_sent():
    """sources.md: "A Gmail source writes each message it reads as a note into one project. Its
    note has a heading with the subject, then when the message arrived (in UTC), who sent it,
    who it was sent to and copied to, by name, and the names of its attachments, then its body
    as text: its plain text, or its HTML turned into text when it has no plain text. The body
    is written as it was sent, the earlier messages it quotes included. Attachments are never
    read.\""""
    here(
        "A Gmail source writes each message it reads as a note into one project. Its note has a"
        " heading with the subject, then when the message arrived (in UTC), who sent it, who it"
        " was sent to and copied to, by name, and the names of its attachments, then its body as"
        " text: its plain text, or its HTML turned into text when it has no plain text. The body"
        " is written as it was sent, the earlier messages it quotes included. Attachments are"
        " never read."
    )
    with pytest.raises(ValueError, match="at most one project"):
        g.check_config(settings(projects=["mail", "other"]))
    body = "Friday works.\n\nOn Thu, Ada Lovelace wrote:\n> Can we move it to Friday?"
    payload = tree(
        "multipart/mixed",
        tree("multipart/alternative", part("text/plain", body), part("text/html", "<p>x</p>")),
        part("application/pdf", filename="Budget.pdf", attachmentId="unitatt1"),
    )
    headers = {"Subject": "Re: Budget", "To": "Brook Lane <b@example.test>", "Cc": "Cy <c@x.test>"}
    item = mail(1, SINCE + HOUR, headers=headers, payload=payload)
    (draft,) = g.drafts(read(item), "mail")
    arrived = (SINCE + HOUR).strftime("%Y-%m-%d %H:%M")
    assert draft.text == (
        f"# Re: Budget\n\n- Arrived: {arrived} UTC\n- From: Ada Lovelace\n- To: Brook Lane\n"
        f"- Cc: Cy\n- Attachments: Budget.pdf\n\n{body}\n"
    )
    assert draft.project == "mail"
    only_html = mail(2, payload=part("text/html", "<p>Agreed: <b>Friday</b>.</p>"))
    (html_draft,) = g.drafts(read(only_html), "mail")
    assert html_draft.text.endswith("- From: Ada Lovelace\n\nAgreed: Friday.\n")
    stand = Stand()
    stand.messages = [item]
    stand.pull()
    assert not any("attachments" in str(r.url) for r in stand.requests)


def test_a_conversation_shares_a_page_a_month():
    """sources.md: "The messages of one conversation in one month share a page, named by the
    subject without its "Re:" or "Fwd:" and by the month, so a subject that comes back every
    week gets a page a month.\""""
    here(
        "The messages of one conversation in one month share a page, named by the subject"
        ' without its "Re:" or "Fwd:" and by the month, so a subject that comes back every week'
        " gets a page a month."
    )
    from datetime import UTC, datetime

    nov, dec = datetime(2026, 11, 3, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)
    assert g.topic("Budget review", nov) == g.topic("Re: Budget review", nov)
    assert g.topic("Fwd: RE: Budget review", nov) == "Budget review, 2026-11"
    assert g.topic("Budget review", dec) == "Budget review, 2026-12"


def test_a_long_message_is_written_in_parts_and_what_cannot_be_read_is_reported_once():
    """sources.md: "A message longer than 1,500 passages is written in parts. A message with no
    text of its own, one whose answer from Gmail is over 8 MB, and one that would take more
    than 20 parts are not read, and each is reported once. When it is switched on, the page
    proposes the project `mail`.\""""
    here(
        "A message longer than 1,500 passages is written in parts. A message with no text of its"
        " own, one whose answer from Gmail is over 8 MB, and one that would take more than 20"
        " parts are not read, and each is reported once. When it is switched on, the page"
        " proposes the project `mail`."
    )
    assert g.PASSAGES == 1_500 and g.MAX_PARTS == 20 and g.MESSAGE_BYTES == 8 * 1024 * 1024
    assert KINDS["gmail"].default_project == "mail"
    long = mail(1, payload=part("text/plain", "\n\n".join(f"Paragraph {k}." for k in range(1_600))))
    assert len(g.drafts(read(long), "mail")) == 2
    attachment_only = mail(2, payload=part("application/pdf", filename="a.pdf", attachmentId="x"))
    assert g.drafts(read(attachment_only), "mail") == g.NO_TEXT
    stand = Stand()
    stand.messages = [mail(3, payload=part("application/pdf", filename="a.pdf", attachmentId="x"))]
    first = stand.pull()
    assert [s.reason for s in first.skipped] == [g.NO_TEXT]
    again = stand.pull(first.cursor)
    assert again.skipped == [] and again.drafts == []
    for reason in (g.NO_TEXT, g.TOO_LARGE, g.TOO_LONG):
        assert reason.endswith("it is not read again")
    assert "8 MB" in g.TOO_LARGE and "20 notes" in g.TOO_LONG


def test_no_email_address_is_among_the_facts():
    """sources.md: "People are named as their mail names them, never by their address: someone
    with no name, or whose name holds an address, is written as the domain of their address,
    and past ten people the rest are counted ("and 3 more"). The subject and the body are the
    sender's words, written unchanged, so an address in them meets the note guard as in any
    note.\""""
    here(
        "People are named as their mail names them, never by their address: someone with no"
        " name, or whose name holds an address, is written as the domain of their address, and"
        ' past ten people the rest are counted ("and 3 more"). The subject and the body are the'
        " sender's words, written unchanged, so an address in them meets the note guard as in"
        " any note."
    )
    assert g.people("ada@example.test") == "example.test"
    assert g.people('"ada@example.test" <ada@example.test>') == "example.test"
    many = ", ".join(f"Person {k} <p{k}@example.test>" for k in range(13))
    assert g.people(many).endswith("Person 9 and 3 more")
    item = mail(
        1,
        headers={"Subject": "Ask eve@example.test", "From": "ada@example.test", "To": many},
        payload=part("text/plain", "Write to dee@example.test."),
    )
    (draft,) = g.drafts(read(item), "mail")
    assert "@" not in "\n".join(g.facts(read(item)))
    assert draft.title == "Ask eve@example.test"  # the subject unchanged
    assert "Write to dee@example.test." in draft.text


def test_under_secrets_an_address_is_kept_and_under_secrets_personal_it_is_refused():
    """sources.md: "Under the workspace's default guard policy, `secrets`, an address in a body
    is kept. Under `secrets+personal`, a message whose subject or body holds an email address is
    refused by the note guard and not read again, and mail often holds one, in a signature or
    above a quoted reply." That a refused draft is final for its message, through the runner, is
    pinned by tests/acceptance/test_c6_gmail.py
    (test_through_the_runner_the_guard_decides_on_a_reply_as_on_any_note)."""
    here(
        "Under the workspace's default guard policy, `secrets`, an address in a body is kept."
        " Under `secrets+personal`, a message whose subject or body holds an email address is"
        " refused by the note guard and not read again, and mail often holds one, in a"
        " signature or above a quoted reply."
    )
    from cairn.store import DEFAULT_GUARD_POLICY

    assert DEFAULT_GUARD_POLICY == "secrets"
    reply = "Friday works.\n\nOn Thu, Ada <ada@example.test> wrote:\n> Can we move it?\n"
    (draft,) = g.drafts(read(mail(1, payload=part("text/plain", reply))), "mail")
    assert PatternGuard("secrets").check(draft.text) == []
    found = PatternGuard("secrets+personal").check(draft.text)
    assert {f.rule for f in found} == {"email_address"}


def test_each_message_is_read_once_and_switched_on_again_reads_from_then():
    """sources.md: "Each message is read once. Taking the label off afterwards, labelling it
    again, or moving it to Trash changes nothing in cairn. Google says a message cannot be
    changed once it is made, only its labels, so there is nothing later to follow. Switched off
    and on again, the source reads only messages that arrived after it was switched on
    again.\""""
    here(
        "Each message is read once. Taking the label off afterwards, labelling it again, or"
        " moving it to Trash changes nothing in cairn. Google says a message cannot be changed"
        " once it is made, only its labels, so there is nothing later to follow. Switched off and"
        " on again, the source reads only messages that arrived after it was switched on again."
    )
    noted("After you create a message, you can't change it.", "only the `labelIds` might change")
    stand = Stand()
    item = mail(1)
    stand.messages = [item]
    first = stand.pull()
    assert refs(first) == ["gmail:unitm0001"]
    stand.messages = []  # the label taken off, or the message moved to Trash
    gone = stand.pull(first.cursor)
    stand.messages = [item]  # labelled again
    assert refs(stand.pull(gone.cursor)) == []
    # switched on again an hour before now: what arrived while it was off is not read
    stand.messages = [mail(2, NOW - 3 * HOUR), mail(3, NOW - HOUR / 2)]
    assert refs(stand.pull(since=NOW - HOUR)) == ["gmail:unitm0003"]


def test_the_scope_the_api_and_the_credential_in_a_variable(keys):  # noqa: F811
    """sources.md: "The source signs in to Google in one of two ways, and needs the scope
    `https://www.googleapis.com/auth/gmail.readonly` in each, which Google describes as "View
    your email messages and settings." Google classes this scope as Restricted, and a
    Workspace administrator can block it. The Gmail API must be enabled in the Cloud project of
    the service account or the OAuth client. Its credential is the text of a JSON document in a
    variable of the server's environment, never in cairn and never a path to a file: with
    Docker, a line in `deploy/cairn.env` with the JSON on one line between single quotes.\""""
    here(
        "The source signs in to Google in one of two ways, and needs the scope"
        " `https://www.googleapis.com/auth/gmail.readonly` in each, which Google describes as"
        ' "View your email messages and settings." Google classes this scope as Restricted, and a'
        " Workspace administrator can block it. The Gmail API must be enabled in the Cloud"
        " project of the service account or the OAuth client. Its credential is the text of a"
        " JSON document in a variable of the server's environment, never in cairn and never a"
        " path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line"
        " between single quotes."
    )
    noted(
        '| `https://www.googleapis.com/auth/gmail.readonly` | "View your email messages and'
        ' settings." [doc: G-SCOPES] | Restricted',
        "For Gmail, Google Drive, Docs and Chat, access to a fixed list of high-risk OAuth"
        " scopes can be restricted as well (paraphrased). Gmail's list:"
        " `https://mail.google.com/`, `gmail.compose`, `gmail.insert`, `gmail.metadata`,"
        " `gmail.modify`, `gmail.readonly`,",
        '"A Google Cloud project with the Gmail API enabled."',
    )
    assert g.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]
    _, pem = keys
    line = json.dumps(key_file(pem))
    assert "\n" not in line
    assert isinstance(auth.load_credential("V", {"V": line}), auth.ServiceAccount)
    with pytest.raises(SourceError, match="not a path"):
        auth.load_credential("V", {"V": "/run/secrets/google.json"})
    with pytest.raises(ValueError, match="credentials_env holds what looks like a credential"):
        g.check_config(settings(credentials_env=line))
    from cairn.sources.google_api import Reply

    refused = g.refusal(Reply(403, b"", ("forbidden",), False), "x", "V")
    assert "the Gmail API is enabled in its Cloud project" in refused


def test_with_a_service_account_a_super_admin_delegates_and_subject_names_the_person(keys):  # noqa: F811
    """sources.md, the first way: the key, delegation by a super admin with the numeric Client
    ID and the scope, which can take up to 24 hours, the key file in the variable and `subject`
    in the config; a service account without `subject` is refused."""
    here(
        "With a service account acting for one person, for a Google Workspace domain whose super"
        " admin agrees:"
    )
    here(
        "1. In a Google Cloud project, create a service account and a JSON key for it. Google"
        " keeps no copy of the key, and an organization created on or after May 3, 2024 blocks"
        " key creation unless its admin allows it."
    )
    here(
        "2. A super admin of the domain opens the Admin console, then Security, Access and data"
        " control, API Controls, **Manage Domain Wide Delegation**, and adds the service"
        " account's numeric **Client ID** (not its email address) with the scope above. It"
        " usually takes a few minutes and can take up to 24 hours."
    )
    here(
        "3. The variable holds the key file's contents, and `subject` in the source's config is"
        " the address of the person whose mail it reads. A service account without `subject` is"
        " refused: it has no mailbox of its own."
    )
    noted(
        'Google "does not keep a copy of your private keys"',
        "organization was created on or after May 3, 2024, this constraint is enforced by default.",
        "Main menu > Security > Access and data control > API Controls",
        "Using the client email instead of the numeric ID, or a Google Group, gives"
        " `unauthorized_client`",
        '"usually takes a few minutes ... might take up to 24 hours"',
        notes=AUTH_NOTES,
    )
    _, pem = keys
    env = {"V": json.dumps(key_file(pem))}
    config = g.check_config(settings(credentials_env="V", subject="ada@example.test"))
    assert g.open_source("mail", config, http=None, env=env).subject == "ada@example.test"
    assert g.mailbox("ada@example.test") == "ada%40example.test"
    with pytest.raises(SourceError, match="no mailbox of its own"):
        g.open_source("mail", g.check_config(settings(credentials_env="V")), http=None, env=env)


def test_with_ones_own_authorisation_the_variable_holds_a_refresh_token_and_no_subject():
    """sources.md, the second way: a Desktop app client, `google-authorize` with `--kind gmail`
    on the person's computer, the file's one line in the variable without `subject`, whose
    mailbox is read, and where the guide says how long the token lasts (pinned with the command
    in test_guide_facts_google_authorize_unit.py)."""
    here("With one person's own authorisation, when there is no super admin to ask:")
    for sentence in own_authorisation("gmail", section(), g.SCOPES):
        here(sentence)
    here(
        '3. The variable holds the file\'s one line, `{"type": "authorized_user", "client_id":'
        ' "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no'
        " `subject`: the source reads the mailbox of the person who authorised it. Then delete"
        " the file."
    )
    env = {VAR: CREDENTIAL}
    with pytest.raises(SourceError, match="remove subject"):
        g.open_source(
            "mail", g.check_config(settings(subject="ada@example.test")), http=None, env=env
        )
    assert g.open_source("mail", g.check_config(settings()), http=None, env=env).subject is None
    assert g.mailbox(None) == "me"
    assert "authorise again" in auth._AUTHORIZED_USER_SAID["invalid_grant"]


def test_the_command_adds_it_off_and_the_settings_are_as_the_guide_lists_them():
    """sources.md: "Then the person makes the label in Gmail, and the operator adds the source,
    which starts off:" and the command, then "`credentials_env` and `label` are the settings it
    needs; `subject` (with a service account), `lookback_days` (30), `base_url` and `projects`
    are optional." ... "The Sources page shows whose mailbox it reads, the label, and how long a
    label is noticed, and never the credential.\""""
    here("Then the person makes the label in Gmail, and the operator adds the source, which")
    (block,) = [
        part.split("```", 1)[0] for part in section().split("```text\n")[1:] if "source-add" in part
    ]
    argv = shlex.split(block.strip())
    assert argv[:5] == ["python", "-m", "cairn.server", "source-add", "--db"]
    assert argv[argv.index("--kind") + 1] == "gmail"
    config = json.loads(argv[argv.index("--config") + 1])
    checked = g.check_config(config)
    assert checked["credentials_env"] == "GMAIL_CREDENTIAL"
    assert checked["projects"] == [] and checked["lookback_days"] == 30
    assert config["subject"].endswith("@example.com")
    here(
        "`credentials_env` and `label` are the settings it needs; `subject` (with a service"
        " account), `lookback_days` (30), `base_url` and `projects` are optional."
    )
    for needed in ("credentials_env", "label"):
        with pytest.raises(ValueError, match=f"needs {needed}"):
            g.check_config({k: v for k, v in config.items() if k != needed})
    here(
        "The Sources page shows whose mailbox it reads, the label, and how long a label is"
        " noticed, and never the credential."
    )
    shown = g.describe(checked)
    assert config["subject"] in shown and config["label"] in shown and "30 days" in shown
    assert "GMAIL_CREDENTIAL" not in shown and "credentials_env" not in shown


def test_the_guide_says_google_does_not_state_how_a_nested_label_is_named():
    """sources.md: "`label` is the label's name as Gmail shows it. Google's pages do not say how
    the API names a label nested under another; cairn expects the parent's name in front,
    `parent/child`, and when no label has the name given, a run says so.\""""
    here(
        "`label` is the label's name as Gmail shows it. Google's pages do not say how the API"
        " names a label nested under another; cairn expects the parent's name in front,"
        " `parent/child`, and when no label has the name given, a run says so."
    )
    noted('**Nested labels:** the Gmail UI offers "Nest label under" [doc: HELP-LABELS]. How a')
    noted("**[cairn]** To confirm against a real mailbox: that `labels.list` names a nested label")
    assert g.check_config(settings(label="Clients/Acme"))["label"] == "Clients/Acme"
    stand = Stand()
    stand.labels = [{"id": LABEL_ID, "name": "Clients/Acme", "type": "user"}]
    stand.messages = [mail(1)]
    assert refs(stand.pull(label="Clients/Acme")) == ["gmail:unitm0001"]
    said_ = stand.failed(label="Clients/Other")
    assert said_ == g.NOT_FOUND and "parent/child" in said_ and "Clients/Other" not in said_


def test_the_other_guides_list_it_among_the_sources_the_server_reads():
    """run.md: "A source (Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail,
    Linear, GitHub or a connector of your own) is read by the server itself" ... README.md:
    "Google Calendar events, the Gmail messages a person labels, Linear issues". deploy/README.md:
    "A Gmail source starts off too, and then reads only the messages that arrive from then on
    and that a person puts its one label on, each once; its variable holds a Google
    credential's JSON in the same way.\""""
    run = " ".join(files("cairn.guides").joinpath("run.md").read_text().split())
    readme = " ".join((ROOT / "README.md").read_text().split())
    deploy = " ".join((ROOT / "deploy" / "README.md").read_text().split())
    assert "Google Drive, Google Calendar, Gmail, Linear, GitHub or a connector of your own)" in run
    assert "Google Calendar events, the Gmail messages a person labels, Linear issues" in readme
    assert (
        "A Gmail source starts off too, and then reads only the messages that arrive from then on"
        " and that a person puts its one label on, each once; its variable holds a Google"
        " credential's JSON in the same way." in deploy
    )
    assert KINDS["gmail"].starts_off and KINDS["gmail"].switching_on() == g.SWITCH_ON
