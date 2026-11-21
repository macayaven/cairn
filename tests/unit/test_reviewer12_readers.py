"""Read-only commands use the same upgrade advice as other administrative commands."""

import pytest
from test_repair_says_what_committed_unit import clean_31

from cairn.store import SCHEMA_VERSION
from cairn.web import cli, cli_data


@pytest.mark.parametrize("command", ["stats", "audit-verify"])
def test_readers_use_the_common_refusal(tmp_path, capsys, command):
    db = clean_31(tmp_path)
    assert cli.main([command, "--db", str(db)]) == 1
    assert (
        capsys.readouterr().err
        == cli_data.OLDER_SCHEMA.format(db=db, found=31, known=SCHEMA_VERSION, command=command)
        + "\n"
    )
