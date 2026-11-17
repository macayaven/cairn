"""Sentences of src/cairn/guides/sources.md's section "One person's own Google authorisation",
where the four Google sources point for `google-authorize`, each pinned here so the guide
cannot go false unnoticed: the test asks the command for it, or, for what Google says, the
notes the guide was written from (docs/google-oauth-installed.md and docs/google-auth.md).
Nothing listens for long and nothing leaves the process."""

import shlex
from pathlib import Path

import pytest
from google_authorize_guide import LINK, SHARED, flat
from guide_source import guide_source

from cairn.sources import gmail, google_drive
from cairn.web import cli, cli_google
from cairn.web.markdown import anchor, guide

ROOT = Path(__file__).parents[2]
RAW = guide_source("sources")
HEADING = f"### {SHARED}"
OAUTH_NOTES = flat((ROOT / "docs" / "google-oauth-installed.md").read_text())
AUTH_NOTES = flat((ROOT / "docs" / "google-auth.md").read_text())
GOOGLE_SECTIONS = (
    "### Meeting transcripts from Google Meet, read by the server",
    "### Mail from Gmail, read by the server",
    "### Events from Google Calendar, read by the server",
    "### Documents from Google Drive, read by the server",
)


def section() -> str:
    """The shared section, to the next heading or the guide's end."""
    start = RAW.index(f"\n{HEADING}\n")
    rest = RAW[start + 1 :]
    ends = [i for i in (rest.find("\n## ", 1), rest.find("\n### ", 1)) if i > 0]
    return rest[: min(ends)] if ends else rest


def said(sentence: str) -> None:
    assert flat(sentence) in flat(section()), sentence


def noted(*quotes: str, notes: str = OAUTH_NOTES) -> None:
    for quote in quotes:
        assert flat(quote) in notes, quote


def command_line() -> str:
    (line,) = [
        line.strip()
        for line in section().splitlines()
        if line.strip().startswith("python -m cairn.server google-authorize ")
    ]
    return line


def test_the_four_google_sections_point_to_the_one_section_that_says_it():
    assert section().startswith(HEADING)
    assert anchor(SHARED) == "one-persons-own-google-authorisation"
    page = guide(RAW, ["sources"])
    assert f'id="{anchor(SHARED)}"' in page.html
    for heading in GOOGLE_SECTIONS:
        start = RAW.index(heading)
        text = RAW[start : RAW.index("\n## ", start)]
        assert flat(text).count(LINK) == 2, heading  # step 2 and step 3
    assert flat(RAW).count("7 days") == 1  # said once, here


def test_the_sources_that_read_as_one_person_are_the_kinds_the_command_takes():
    """sources.md: "The Google Meet, Gmail, Google Calendar and Google Drive sources can each
    read as one person who authorised them, with no super admin." """
    said(
        "The Google Meet, Gmail, Google Calendar and Google Drive sources can each read as one"
        " person who authorised them, with no super admin. `google-authorize` obtains that"
        " person's credential, once for each credential:"
    )
    assert cli_google.KIND_NAMES == ("gmail", "google_calendar", "google_drive", "google_meet")


def test_step_one_a_desktop_app_client_whose_secret_is_downloaded_once(tmp_path):
    """sources.md: create an OAuth client of type Desktop app and download its file when you
    create it, since Google's pages say the secret can be downloaded only then. A Web
    application client's file is refused, saying to create a Desktop app client."""
    said(
        "1. In a Google Cloud project, enable the API of each source the credential is for, set"
        " up the consent screen, and create an OAuth client of type **Desktop app**. Download"
        " its JSON file when you create the client: Google's pages say its client secret can be"
        " downloaded only then."
    )
    noted(
        "Any application that calls Google APIs needs to enable those APIs",
        'Clients > Create client > "Desktop app"',
        "The Google Cloud Console shows an OAuth 2.0 client's secret, and lets it be"
        " downloaded, only when the client is created",
    )
    web = tmp_path / "web.json"
    web.write_text('{"web": {"client_id": "x.example.test", "client_secret": "s"}}')
    with pytest.raises(cli_google.Refused) as refused:
        cli_google.read_client(web)
    assert "create a Desktop app client" in str(refused.value)
    assert refused.value.exit == 2


def test_step_two_the_command_as_the_guide_shows_it_parses_with_one_kind_each():
    """sources.md: the command with the client's file, one `--kind` for each source, and a
    file that does not exist yet; the four kinds it names are the ones it takes."""
    said(
        "2. On the computer whose browser the person uses, run the command with the client's"
        " file, the kind of each source the credential is for (`gmail`, `google_calendar`,"
        " `google_drive` or `google_meet`, one `--kind` each) and a file that does not exist"
        " yet:"
    )
    words = shlex.split(command_line())
    assert words[:3] == ["python", "-m", "cairn.server"]
    args = cli._parser().parse_args(words[3:])
    assert args.command == cli_google.COMMAND
    assert args.kinds == ["gmail"] and args.client == Path("client_secret.json")
    assert args.out == Path("gmail-credential.json") and args.wait == cli_google.WAIT
    two = cli._parser().parse_args([*words[3:], "--kind", "google_drive"])
    assert two.kinds == ["gmail", "google_drive"]


@pytest.mark.parametrize("seconds", ["29", "901"])
def test_it_waits_300_seconds_unless_wait_gives_from_30_to_900(seconds, capsys):
    """sources.md: "It prints an address and waits for the person, 300 seconds unless `--wait`
    gives from 30 to 900." """
    said(
        "It prints an address and waits for the person, 300 seconds unless `--wait` gives from"
        " 30 to 900."
    )
    assert (cli_google.WAIT, cli_google.WAIT_MIN, cli_google.WAIT_MAX) == (300, 30, 900)
    words = shlex.split(command_line())[3:]
    for fine in ("30", "900"):
        assert cli._parser().parse_args([*words, "--wait", fine]).wait == int(fine)
    with pytest.raises(SystemExit) as refused:
        cli._parser().parse_args([*words, "--wait", seconds])
    assert refused.value.code == 2
    assert "from 30 to 900" in capsys.readouterr().err


def test_google_sends_the_browser_back_to_127_0_0_1_on_the_same_computer():
    """sources.md: "Google then sends the browser back to the command on 127.0.0.1, which is
    that same computer, so a browser on another computer cannot finish it." """
    said(
        "The person opens the address in that browser, signs in and grants every scope it asks"
        " for. Google then sends the browser back to the command on 127.0.0.1, which is that"
        " same computer, so a browser on another computer cannot finish it."
    )
    noted("the HTTP request never leaves the device")
    with cli_google.Listener("s", deadline=0.0) as listener:
        assert listener.server.server_address[0] == "127.0.0.1"
    url = cli_google.authorization_url(
        cli_google.GOOGLE_AUTH_URL,
        client_id="x.example.test",
        redirect_uri=f"http://127.0.0.1:{listener.port}",
        scopes=cli_google.scopes_for(["gmail"]),
        state="s",
        challenge="c",
    )
    assert f"redirect_uri=http%3A%2F%2F127.0.0.1%3A{listener.port}&" in url


def test_the_file_is_new_readable_by_its_owner_only_and_never_overwritten(tmp_path):
    """sources.md: "The command writes the credential on one line to the file `--out` names,
    readable by its owner only. It never overwrites a file, never opens a browser itself and
    opens no database." """
    said(
        "The command writes the credential on one line to the file `--out` names, readable by"
        " its owner only. It never overwrites a file, never opens a browser itself and opens no"
        " database."
    )
    out = tmp_path / "gmail-credential.json"
    fd = cli_google.reserve(out)
    try:
        assert out.stat().st_mode & 0o777 == 0o600
    finally:
        cli_google.discard(fd, out)
    out.write_text("kept")
    with pytest.raises(cli_google.Refused):
        cli_google.reserve(out)
    assert out.read_text() == "kept"
    assert "webbrowser" not in Path(cli_google.__file__).read_text()
    command = cli._parser()._subparsers._group_actions[0].choices[cli_google.COMMAND]
    assert "--db" not in command._option_string_actions


def test_step_three_is_what_the_command_prints_when_it_is_done():
    """sources.md: "Put the file's one line in the server's environment under the name the
    source's config gives as `credentials_env`, then delete the file: it holds the client
    secret and the refresh token." The command's last line says the same."""
    said(
        "3. Put the file's one line in the server's environment under the name the source's"
        " config gives as `credentials_env`, then delete the file: it holds the client secret"
        " and the refresh token."
    )
    line = cli_google.DONE[1]
    assert (
        "put the file's one line in the server's environment under the name the source's"
        " config gives as credentials_env, then delete"
    ) in line
    written = cli_google.credential_line(
        cli_google.Client("x.example.test", "s"), "1//refresh-made-up"
    )
    assert '"client_secret": "s"' in written and '"refresh_token": "1//refresh-made-up"' in written


def test_how_long_it_lasts_is_what_google_says_and_no_more():
    """sources.md, "How long it lasts": the reasons a refresh token stops working, the 7-day
    rule of an External client in "Testing", its test users, and that Google's pages do not say
    whether a restricted scope works for an unverified client one person uses "In production";
    cairn promises nothing about it."""
    said(
        "**How long it lasts.** A refresh token stops working if the person revokes it, after"
        " six months unused, and after 7 days while the client's consent screen is External and"
        ' in "Testing"; the source then says to authorise again.'
    )
    noted(
        "The refresh token has not been used for six months.",
        "The user has revoked your app's access.",
        'a publishing status of "Testing" is issued a refresh token expiring in 7 days',
        notes=AUTH_NOTES,
    )
    said(
        'While the consent screen is in "Testing", the person must be listed among its test users.'
    )
    noted("the operator must have added the person's address under Audience > Test users")
    said(
        "Google's pages do not say whether a restricted scope, such as Gmail's or Google"
        " Drive's, can be granted to an unverified client that one person uses once its consent"
        ' screen is "In production". Try it before you rely on it: cairn promises nothing about'
        " it."
    )
    noted(
        "Whether restricted scopes (Gmail, Drive) can be granted at all by an unverified"
        ' "In production" app for personal use is **[not stated]**',
        "Restricted Gmail scopes include `gmail.readonly`; restricted Drive scopes include"
        " `drive.readonly`",
    )
    assert gmail.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert google_drive.SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]


def test_it_is_not_run_inside_the_servers_container_and_the_guide_says_so():
    """sources.md: "Do not run it inside the server's container or on the server: there the
    browser could not come back to it, and the file would be written where the person cannot
    reach it. It needs cairn on that computer and nothing else: no database and no running
    server.\""""
    said(
        "Do not run it inside the server's container or on the server: there the browser could"
        " not come back to it, and the file would be written where the person cannot reach it."
        " It needs cairn on that computer and nothing else: no database and no running server."
    )
    # no database: the command has no --db; no server: it listens only on this machine
    parser = cli._parser()
    with pytest.raises(SystemExit) as refused:
        parser.parse_args(["google-authorize", "--db", "x.db", "--client", "c", "--kind", "gmail"])
    assert refused.value.code == 2
    pre_release = flat((ROOT / "docs" / "pre-release.md").read_text())
    assert (
        "Run it on your own computer, never with `docker exec` on the VM or inside the"
        " server's container" in pre_release
    )


def test_the_address_is_for_this_run_and_this_person_only():
    """sources.md: the address is for this run and this person only, since whoever signs in
    through it first chooses the account; the command's line before the address says so."""
    said(
        "The address is for this run and this person only: do not pass it on, since whoever"
        " signs in through it first chooses the Google account the source reads."
    )
    assert cli_google.OPEN.startswith("Open this address in your browser:")
    assert "do not pass it on" in cli_google.OPEN


def test_out_goes_in_a_folder_only_the_person_can_write_to():
    """sources.md: someone who can write to the folder could swap the file once written; the
    command's --out help says the same, and the command refuses no common setup over it."""
    said(
        "Give `--out` a file in a folder only you can write to: someone who can write to the"
        " folder could swap the file after the command has written it."
    )
    command = cli._parser()._subparsers._group_actions[0].choices[cli_google.COMMAND]
    assert "only you can write to" in command._option_string_actions["--out"].help


def test_step_three_says_to_check_the_account_that_signed_in():
    """sources.md: check the Google account the person signed in with before putting the line
    in the server's environment; the command's next steps say so too."""
    said(
        "Check first that the Google account the person signed in with is the one meant: the"
        " source reads as that account."
    )
    assert "check that the Google account" in " ".join(cli_google.DONE)
