"""A model that carries a secret in clear never shows it in its repr.

A token is handed out once, in a model, and a `repr` of that model is one logging call or
one failed assertion away from a log. Every field of cairn.store.models whose name says it
holds a token keeps itself out of the repr.
"""

import inspect

from pydantic import BaseModel

from cairn.store import models
from cairn.store.models import TokenPair

SECRET_NAMES = ("token", "access_token", "refresh_token", "claim_token", "secret")


def test_a_token_pair_does_not_print_its_tokens():
    pair = TokenPair(access_token="at-plain-value", refresh_token="rt-plain-value", expires_in=3600)
    shown = repr(pair) + str(pair)
    assert "at-plain-value" not in shown and "rt-plain-value" not in shown
    assert "3600" in shown  # what is not secret still shows
    assert pair.access_token == "at-plain-value"  # and the value is still there to hand out


def test_no_model_shows_a_field_that_holds_a_token():
    shown = []
    for name, model in inspect.getmembers(models, inspect.isclass):
        if not issubclass(model, BaseModel) or model is BaseModel:
            continue
        for field_name, field in model.model_fields.items():
            if field_name in SECRET_NAMES and field.repr:
                shown.append(f"{name}.{field_name}")
    assert shown == [], shown
