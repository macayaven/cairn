from cairn.web import cli


def test_workspace_list_documents_its_read_only_open():
    description = " ".join(cli.__doc__.split()).partition("`workspace-list --db PATH`")[2]
    words = description.partition("No database:")[0]
    assert "refuses an older schema" in words
