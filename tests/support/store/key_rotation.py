"""Shared test helpers extracted from test_key_rotation_unit."""

import base64
import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cairn import server
from cairn.store import Store
from cairn.store.hosted import GoogleCustody, Sealer, associated

CURRENT = bytes(range(32))

EARLIER = bytes(range(1, 33))

OLDEST = bytes(range(2, 34))

TOKEN = "1//rotation-refresh-token-ada-0123456789"

TIED = associated("acct_fable", "gmail.ada.0123456789", "user_ada")

CLIENT_SECRET = "rotation-web-secret-0123456789"

CLIENT_JSON = json.dumps(
    {"web": {"client_id": "rotation-client.apps.example.test", "client_secret": CLIENT_SECRET}}
)

GOOGLE = "CAIRN_GOOGLE_CONNECT_KEY"

SLACK = "CAIRN_SLACK_KEY"

VARIABLES = (GOOGLE, f"{GOOGLE}_PREVIOUS", SLACK, f"{SLACK}_PREVIOUS")


def holds(ok: object) -> None:
    """Fail, when `ok` is not True, with the file, line and source text of the check only."""
    if ok is not True:
        caller = inspect.getframeinfo(inspect.stack()[1].frame)
        line = (caller.code_context or [""])[0].strip()
        pytest.fail(f"{caller.filename}:{caller.lineno}: {line}", pytrace=False)


def b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


def b64s(*keys: bytes) -> str:
    return ",".join(b64(key) for key in keys)


def google_env(**more: str) -> dict[str, str]:
    return {"CAIRN_GOOGLE_CONNECT_CLIENT": CLIENT_JSON, GOOGLE: b64(CURRENT), **more}


def slack_env(**more: str) -> dict[str, str]:
    return {SLACK: b64(CURRENT), **more}


def opens_earlier(sealer: Sealer, *earlier: bytes) -> bool:
    return all(sealer.open(Sealer(key).seal(TOKEN, TIED), TIED) == TOKEN for key in earlier)


ISSUER = "https://sso.example.test"

NOW = datetime(2099, 11, 17, 9, tzinfo=UTC)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

SIGNING = "slack-signing-" + "0e" * 8

BOT = "xoxb-" + "rotation-" + "0123456789"


def under(store: Store, key: bytes, *previous: bytes) -> None:
    """The keys this process holds from now on, as a restart with them would give."""
    store.google_custody = GoogleCustody(
        Sealer(key, previous), client_id="rotation-client.apps.example.test",
        client_secret=CLIENT_SECRET,
    )  # fmt: skip
    store.slack_sealer = Sealer(key, previous)


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=lambda: NOW)
    under(store, EARLIER)
    ada = store.sign_in("sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="personal")
    w = SimpleNamespace(store=store, db=db, account=ada.account_id, ada=ada.user.id)
    w.view = lambda: store.for_account(w.account, w.ada)  # a view copies the keys held now
    yield w
    store.close()


def connect(w, address: str, token: str = TOKEN) -> str:
    subject = "1000" + str(sum(map(ord, address)))
    return w.view().connect_google("gmail", address, [GMAIL_SCOPE], token, subject=subject).name


def slack_ready(w) -> str:
    view = w.view()
    made = view.make_slack_install()
    view.ready_slack_install(
        made.id, signing_secret=SIGNING, bot_token=BOT, team_id="T0FABLE01",
        team_domain="fable", bot_user_id="U0BOT0001",
    )  # fmt: skip
    return made.id


def google_opens(w) -> dict[str, bool]:
    view = w.view()
    found = {}
    for account in view.google_accounts():
        try:
            found[account.name] = view.google_token(account.name) == TOKEN
        except Exception:  # TokenUnusable: it does not open
            found[account.name] = False
    for revocation, _subject, token in w.store.pending_google_revocations():
        found[revocation] = token == TOKEN
    return found


def reseal(monkeypatch, db, *options: str, **env: str) -> int:
    for name in (*VARIABLES, "CAIRN_GOOGLE_CONNECT_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return server.main(["reseal", "--db", str(db), *options])


BOTH_ROTATED = {
    GOOGLE: b64(CURRENT), f"{GOOGLE}_PREVIOUS": b64(EARLIER),
    SLACK: b64(CURRENT), f"{SLACK}_PREVIOUS": b64(EARLIER),
}  # fmt: skip
