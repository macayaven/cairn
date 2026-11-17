"""Unit tests for the line `serve` prints when it listens in clear text where others can reach
it (H1, item 5; the acceptance test is tests/acceptance/test_h1_hardening.py): which hosts
are loopback, which public URLs say a proxy ends TLS, and where the line comes in serve's
output. Every name is made up."""

import pytest

from cairn.web import cli


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.8.9.10", "localhost", "LOCALHOST", "::1", "[::1]", "0:0:0:0:0:0:0:1"]
)
def test_a_loopback_host_is_quiet(host):
    assert cli.clear_text(host, None) is None
    assert cli.clear_text(host, "http://cairn.example.test") is None


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.0.2.10", "2001:db8::1", "cairn.example.test", "localhost.example.test"],
)
def test_any_other_host_without_https_says_so(host):
    for public_url in (None, "http://cairn.example.test", "http://127.0.0.1:8765"):
        assert cli.clear_text(host, public_url) == cli.CLEAR_TEXT.format(host=host)
    assert cli.clear_text(host, "https://cairn.example.test") is None
    assert cli.clear_text(host, "HTTPS://cairn.example.test") is None


def test_the_line_is_one_line_naming_what_to_do():
    line = cli.CLEAR_TEXT.format(host="0.0.0.0")
    assert "\n" not in line
    assert "clear text" in line and "TLS" in line and "--public-url https://" in line


def test_it_comes_after_every_refusal_and_once(tmp_path, monkeypatch, capsys):
    """A serve that stops on a bad option says only why; one that listens says it once, last,
    just before it listens."""
    listened = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: listened.append(k))
    db = tmp_path / "cairn.db"
    assert cli.main(["serve", "--db", str(db), "--host", "0.0.0.0", "--wall"]) == 2
    assert "clear text" not in capsys.readouterr().err and listened == []
    assert cli.main(["serve", "--db", str(db), "--host", "0.0.0.0"]) == 0
    assert capsys.readouterr().err == cli.CLEAR_TEXT.format(host="0.0.0.0") + "\n"
    assert len(listened) == 1
