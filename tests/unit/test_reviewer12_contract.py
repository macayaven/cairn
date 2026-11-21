"""The operator and library descriptions agree with the actual open policies."""

from pathlib import Path

from cairn.web import cli


def test_open_contract_names_library_writers_and_private_creation():
    guide = (Path(__file__).resolve().parents[2] / "src/cairn/guides/run.md").read_text()
    assert "older library writers" in guide
    description = " ".join(cli.__doc__.split())
    assert "refuse one in their own words" not in description
    assert "folded into it by SQLite as the connection closes" not in description
    assert "publish a new database" in description
