"""A person who is not an owner is refused an export and an import in the export's own words
(NOT_AN_EXPORTER). The store's people part and its export part each defined
`_require_owner`, and the people part's hid the export part's, so the refusal named making
and removing owners instead. Over the store, and over the web for an admin's key (the web
page refuses a member before the store is asked). Made-up people."""

import io

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.store.people import NOT_AN_OWNER
from cairn.store.portable import NOT_AN_EXPORTER

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def signed(store: Store, who: str) -> str:
    """A person of the default workspace, by id: the first is its owner, the next members."""
    return store.sign_in(
        "sso", f"subject-{who}", f"{who}@example.test", who.title(), issuer=ISSUER, join="default"
    ).user.id


@pytest.fixture
def people(store) -> dict[str, str]:
    owner = signed(store, "ada")
    found = {"owner": owner, "member": signed(store, "brook"), "admin": signed(store, "quill")}
    store.for_account(DEFAULT_ACCOUNT_ID, owner).set_role(found["admin"], "admin")
    return found


def archive(store: Store) -> bytes:
    out = io.BytesIO()
    store.export(out)
    return out.getvalue()


@pytest.mark.parametrize("role", ["member", "admin"])
def test_a_person_who_is_not_an_owner_is_refused_in_the_exports_words(store, people, role):
    made = archive(store)
    view = store.for_account(DEFAULT_ACCOUNT_ID, people[role])
    with pytest.raises(PermissionError) as exporting:
        view.export(io.BytesIO())
    with pytest.raises(PermissionError) as importing:
        view.import_export(io.BytesIO(made))
    assert str(exporting.value) == NOT_AN_EXPORTER
    assert str(importing.value) == NOT_AN_EXPORTER


def test_an_owner_still_exports(store, people):
    view = store.for_account(DEFAULT_ACCOUNT_ID, people["owner"])
    assert view.export(io.BytesIO()) is not None


def test_owners_are_still_made_and_removed_by_owners_only(store, people):
    """The people part keeps its own check and its own words."""
    admin = store.for_account(DEFAULT_ACCOUNT_ID, people["admin"])
    with pytest.raises(PermissionError) as raised:
        admin.set_role(people["member"], "owner")
    assert str(raised.value) == NOT_AN_OWNER


def test_an_admins_key_is_refused_an_export_over_the_api_in_the_exports_words(store, people):
    key = store.for_account(DEFAULT_ACCOUNT_ID, people["admin"]).create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        r = client.get("/api/account/export", headers={"x-api-key": key})
    assert r.status_code == 403
    assert r.json() == {"error": NOT_AN_EXPORTER}
