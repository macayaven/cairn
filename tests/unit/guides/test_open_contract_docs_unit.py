"""Guide and docstring contract checks for open policies and workspace-list."""

from cairn.compiler import own_claims
from cairn.web import cli
from support.paths import REPO_ROOT

# --- test_reviewer12_addresses.py ---


def test_the_guide_distinguishes_the_current_page_from_a_removed_version():
    guides = REPO_ROOT / "src/cairn/guides"
    text = (guides / "run.md").read_text()
    assert "current address of every page" in text and "removed version" in text


# --- test_reviewer12_claims.py ---


def test_own_claims_accounts_for_a_writer_open_across_the_upgrade():
    words = " ".join(own_claims.__doc__.split())
    assert "older writer" in words and "across the upgrade" in words


# --- test_reviewer12_contract.py ---


def test_open_contract_names_library_writers_and_private_creation():
    guide = (REPO_ROOT / "src/cairn/guides/run.md").read_text()
    assert "older library writers" in guide
    description = " ".join(cli.__doc__.split())
    assert "refuse one in their own words" not in description
    assert "folded into it by SQLite as the connection closes" not in description
    assert "publish a new database" in description


# --- test_reviewer12_workspace_list.py ---


def test_workspace_list_documents_its_read_only_open():
    description = " ".join(cli.__doc__.split()).partition("`workspace-list --db PATH`")[2]
    words = description.partition("No database:")[0]
    assert "refuses an older schema" in words
