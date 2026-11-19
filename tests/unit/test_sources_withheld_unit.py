"""Unit tests for SourceKind.withheld and cairn.sources.withheld_via: which kinds' notes a
model does not read unless the operator allows it (tests/acceptance/test_m2_withheld_notes.py).
Fixtures are synthetic."""

import importlib

from cairn import sources
from cairn.sources import KINDS, SourceKind

GOOGLE = ("google_meet", "google_calendar", "google_drive", "gmail")
# Every withheld kind: since Privacy 4, every kind the server reads.
WITHHELD = (*GOOGLE, "connector", "langfuse", "granola", "linear", "github")


def test_a_kind_is_not_withheld_unless_it_says_so():
    assert SourceKind(check=dict, open=dict, via="plain").withheld is False
    assert SourceKind(check=dict, open=dict, via="held", withheld=True).withheld is True


def test_every_kind_the_server_reads_is_withheld():
    assert sorted(name for name, kind in KINDS.items() if kind.withheld) == sorted(WITHHELD)


def test_withheld_via_are_the_withheld_kinds_via_values_and_nothing_else():
    said = sources.withheld_via()
    assert isinstance(said, frozenset)
    assert said == frozenset(KINDS[name].via for name in WITHHELD)
    assert "gmail" in said and "langfuse" in said and "granola" in said


def test_withheld_via_reads_kinds_when_called(monkeypatch):
    before = sources.withheld_via()
    monkeypatch.setitem(
        KINDS, "unit_held", SourceKind(check=dict, open=dict, via="u-held", withheld=True)
    )
    assert sources.withheld_via() == before | {"u-held"}
    monkeypatch.delitem(KINDS, "unit_held")
    monkeypatch.setitem(KINDS, "gmail", SourceKind(check=dict, open=dict, via="gmail"))
    assert "gmail" not in sources.withheld_via()


def test_each_withheld_kind_s_module_says_it_is_withheld_and_why():
    for name in GOOGLE:
        module = importlib.import_module(f"cairn.sources.{name}")
        doc = " ".join((module.__doc__ or "").split())
        assert "withheld" in doc and "Google" in doc and "model" in doc, name
