"""Unit tests for the B1 settings of `serve` and `create_app`: the wall, the brand and how long
a sign-in lasts. They pin what tests/acceptance/test_b1_wall.py leaves open: each refusal has
its own rule behind it (not argparse's ignorance of the option), the settings reach the app
as given, and the logo is read before anything is opened."""

import httpx
import pytest
import uvicorn

from cairn import server
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web import cli

BASE = "https://kb.example.test"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32">'
    b'<text x="4" y="22" fill="#1b2a24">Juniper Field</text></svg>\n'
)
ENV = {"CAIRN_GITHUB_CLIENT_ID": "github-client-id", "CAIRN_GITHUB_CLIENT_SECRET": "made-up"}


def serve(monkeypatch, tmp_path, argv, *, sign_in=True):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    made = {}

    def note(store, **settings):
        made.update(settings)
        raise SystemExit(0)  # the app is not needed: what it was given is

    monkeypatch.setattr(cli, "create_app", note)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    db = tmp_path / "kb.db"
    providers = ["--sign-in", "github"] if sign_in else []
    try:
        code = server.main(["serve", "--db", str(db), "--public-url", BASE, *providers, *argv])
    except SystemExit as stopped:
        code = stopped.code
    return code, made, db


def test_serve_hands_the_settings_to_the_app(tmp_path, monkeypatch):
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    argv = ["--wall", "--brand-name", "Knowledge Base", "--brand-logo", str(logo),
            "--brand-logo-alt", "Juniper Field", "--session-days", "30"]  # fmt: skip
    code, made, _ = serve(monkeypatch, tmp_path, argv)
    assert code == 0
    assert made["wall"] is True and made["session_days"] == 30
    assert made["brand_name"] == "Knowledge Base" and made["brand_logo_alt"] == "Juniper Field"
    assert made["brand_logo"].data == SVG  # read once, before the database was opened


def test_without_the_settings_serve_hands_the_app_its_defaults(tmp_path, monkeypatch):
    code, made, _ = serve(monkeypatch, tmp_path, [])
    assert code == 0
    assert (made["wall"], made["brand_name"], made["brand_logo"]) == (False, None, None)
    assert (made["brand_logo_alt"], made["session_days"]) == (None, None)


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["--brand-name", "Knowledge Base", "--brand-logo", "{svg}", "--brand-logo-alt", ""],
         "--brand-logo-alt is 1 to 200 characters"),
        (["--brand-name", "Knowledge Base", "--brand-logo", "{svg}", "--brand-logo-alt", "a\tb"],
         "--brand-logo-alt has no control character"),
        (["--brand-name", "   "], "--brand-name is 1 to 60 characters, not blank"),
        (["--brand-name", "Base‮"], "--brand-name has no control character"),
        (["--session-days", "-3"], "--session-days"),
        (["--session-days", "1e3"], "--session-days"),
    ],
)  # fmt: skip
def test_a_value_that_cannot_be_used_exits_2_and_says_its_rule(
    tmp_path, monkeypatch, capsys, argv, said
):
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    argv = [str(logo) if value == "{svg}" else value for value in argv]
    code, made, db = serve(monkeypatch, tmp_path, argv)
    err = capsys.readouterr().err
    assert code == 2 and not made and not db.exists()
    assert said in err.strip().splitlines()[-1]


@pytest.mark.parametrize(
    ("argv", "sign_in", "said"),
    [
        (["--wall"], False, "--wall needs --sign-in"),
        (["--brand-name", "Knowledge Base"], False, "--brand-name needs --sign-in"),
        (["--session-days", "30"], False, "--session-days needs --sign-in"),
        (["--brand-logo", "{svg}"], True, "--brand-logo needs --brand-name"),
        (["--brand-name", "K", "--brand-logo-alt", "J"], True,
         "--brand-logo-alt needs --brand-logo"),
    ],
)  # fmt: skip
def test_a_setting_that_would_do_nothing_exits_2_with_its_own_sentence(
    tmp_path, monkeypatch, capsys, argv, sign_in, said
):
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    argv = [str(logo) if value == "{svg}" else value for value in argv]
    code, made, db = serve(monkeypatch, tmp_path, argv, sign_in=sign_in)
    assert code == 2 and not made and not db.exists()
    assert said in capsys.readouterr().err.strip().splitlines()[-1]


def test_the_help_of_serve_names_each_setting(capsys):
    with pytest.raises(SystemExit):
        cli.main(["serve", "--help"])
    shown = " ".join(capsys.readouterr().out.split())
    for option in ("--wall", "--brand-name NAME", "--brand-logo FILE", "--brand-logo-alt TEXT",
                   "--session-days N"):  # fmt: skip
        assert option in shown, option


# create_app refuses what serve refuses, for a caller that builds the app itself


def sign_in() -> SignIn:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    return SignIn(providers=(GitHub("id", "secret"),), http=http)


@pytest.mark.parametrize(
    ("settings", "said"),
    [
        ({"wall": True}, "--wall needs --sign-in"),
        ({"brand_name": "Knowledge Base"}, "--brand-name needs --sign-in"),
        ({"session_days": 30}, "--session-days needs --sign-in"),
    ],
)
def test_create_app_refuses_a_setting_that_needs_sign_in(tmp_path, settings, said):
    store = Store(tmp_path / "kb.db")
    try:
        with pytest.raises(ValueError, match=said):
            create_app(store, public_url=BASE, **settings)
    finally:
        store.close()


@pytest.mark.parametrize("days", [0, 366, 7.5, "30", True])
def test_create_app_takes_a_whole_number_of_days_from_1_to_365(tmp_path, days):
    store = Store(tmp_path / "kb.db")
    try:
        with pytest.raises(ValueError, match="--session-days is a whole number from 1 to 365"):
            create_app(store, public_url=BASE, sign_in=sign_in(), session_days=days)
        create_app(store, public_url=BASE, sign_in=sign_in(), session_days=365)
    finally:
        store.close()
