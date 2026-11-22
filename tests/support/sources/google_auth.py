"""Shared test helpers extracted from test_google_auth_unit."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

GOOGLE = "https://oauth2.googleapis.com/token"

EMAIL = "unit-reader@unit-project.iam.example.test"

KEY_ID = "u" + "1a2b3c4d" * 5  # made up

SA_CLIENT_ID = "2" + "13" * 10

CLIENT_ID = "unit-" + "client" + ".apps.example.test"

SECRET = "GOCSPX-" + "unit" + "MadeUpSecret9f"

REFRESH = "1//" + "0unit" + "MadeUpRefresh" + "Token3c"


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private, pem


def key_file(pem: str, **changes) -> dict:
    doc = {
        "type": "service_account",
        "project_id": "unit-project",
        "private_key_id": KEY_ID,
        "private_key": pem,
        "client_email": EMAIL,
        "client_id": SA_CLIENT_ID,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": GOOGLE,
    }
    doc.update(changes)
    return {k: v for k, v in doc.items() if v is not None}


def user(**changes) -> dict:
    doc = {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
    doc.update(changes)
    return {k: v for k, v in doc.items() if v is not None}
