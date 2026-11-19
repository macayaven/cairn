"""What `serve` says when it starts, one line each on its log (stderr), without stopping:
an https public URL with no --trusted-proxy (every caller then shares one count in the
limits), and sign-in or a public URL with no --operator-notes (the privacy guide then shows
no operator, country or contact). Every name is made up."""

import pytest

from cairn.web import cli

HTTPS = "https://cairn.example.test"


def test_an_https_public_url_without_a_trusted_proxy_says_every_caller_shares_one_count():
    line = cli.no_trusted_proxy(HTTPS, [])
    assert line == cli.NO_TRUSTED_PROXY
    assert "\n" not in line and "--trusted-proxy" in line and "one count" in line
    assert cli.no_trusted_proxy(HTTPS, ["172.30.0.2"]) is None
    assert cli.no_trusted_proxy("http://127.0.0.1:8765", []) is None
    assert cli.no_trusted_proxy(None, []) is None


def test_sign_in_or_a_public_url_without_operator_notes_says_the_privacy_guide_is_bare():
    line = cli.no_operator_notes(True, None, None)
    assert line == cli.NO_OPERATOR_NOTES
    assert "\n" not in line and "--operator-notes" in line
    assert all(word in line for word in ("operator", "country", "contact"))
    assert cli.no_operator_notes(False, HTTPS, None) == line
    assert cli.no_operator_notes(True, HTTPS, "Run by Fable Works, in Ireland.") is None
    assert cli.no_operator_notes(False, None, None) is None


@pytest.fixture
def listened(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append(k))
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "gh-made-up")
    return calls


def test_serve_says_both_lines_once_and_still_listens(tmp_path, capsys, listened):
    db = tmp_path / "cairn.db"
    argv = ["serve", "--db", str(db), "--public-url", HTTPS, "--sign-in", "github"]
    assert cli.main(argv) == 0
    err = capsys.readouterr().err.splitlines()
    assert err.count(cli.NO_TRUSTED_PROXY) == 1 and err.count(cli.NO_OPERATOR_NOTES) == 1
    assert len(listened) == 1


def test_serve_with_both_given_says_neither(tmp_path, capsys, listened):
    notes = tmp_path / "operator.md"
    notes.write_text("Run by Fable Works.\n")
    db = tmp_path / "cairn.db"
    argv = ["serve", "--db", str(db), "--public-url", HTTPS, "--sign-in", "github",
            "--trusted-proxy", "172.30.0.2", "--operator-notes", str(notes)]  # fmt: skip
    assert cli.main(argv) == 0
    err = capsys.readouterr().err
    assert cli.NO_TRUSTED_PROXY not in err and cli.NO_OPERATOR_NOTES not in err
    assert len(listened) == 1


def test_serve_on_this_machine_alone_says_neither(tmp_path, capsys, listened):
    assert cli.main(["serve", "--db", str(tmp_path / "cairn.db")]) == 0
    assert capsys.readouterr().err == ""
