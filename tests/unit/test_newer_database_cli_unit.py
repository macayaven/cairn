"""Every command of `python -m cairn.server` answers a database a newer cairn wrote and this one
may not run on (its `PRAGMA user_version` above SCHEMA_VERSION, and its floor too) with exit 1
and one line on stderr: the word SchemaTooNew, the versions and what to do. Nothing on
stdout, no traceback, nothing written, and `serve` never listens. The commands are read from
the parser, so one added later is covered as soon as it has an entry in ARGS, or is left out by
name in NO_DATABASE when it opens no database; the store's own refusal stays as it is."""

import sqlite3
from pathlib import Path

import pytest
from cli_commands import (
    ALL_COMMANDS,
    ARGS,
    COMMANDS,
    NO_DATABASE,
    PLANS_YAML,
    VARIANT_IDS,
    VARIANTS,
    db_options,
)

from cairn.store import DEFAULT_ACCOUNT_ID, SCHEMA_VERSION, SchemaTooNew, Store
from cairn.web import cli

NEWER = SCHEMA_VERSION + 1
REFUSAL = (
    f"the database has schema version {NEWER} and needs a cairn of schema version {NEWER} or"
    f" later; this cairn knows up to {SCHEMA_VERSION}"
)


def newer_database(directory: Path) -> Path:
    """A database as a newer cairn leaves it after a step that changes what this one relies
    on: made by this one, then stamped one version up, with its floor raised to it (a step
    that only adds would leave the floor, and this cairn would run on it)."""
    db = directory / "cairn.db"
    Store(db).close()
    con = sqlite3.connect(db)
    try:
        con.execute(f"PRAGMA user_version = {NEWER}")
        con.execute("UPDATE oldest_build SET version = ?", (NEWER,))
        con.commit()
    finally:
        con.close()
    (directory / "archive.zip").write_bytes(b"not read: the database is refused first")
    (directory / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    return db


@pytest.fixture
def listened(monkeypatch) -> list:
    """uvicorn.run as `serve` would call it, recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def refused_in_one_line(tmp_path, capsys, listened, command: str, extra: list[str]) -> str:
    db = newer_database(tmp_path)
    before, files = db.read_bytes(), sorted(tmp_path.iterdir())
    argv = [command, "--db", str(db), *(arg.format(dir=tmp_path) for arg in extra)]
    code = cli.main(argv)
    out, err = capsys.readouterr()
    assert code == 1, argv
    assert out == "", argv
    assert "Traceback" not in err
    (line,) = err.splitlines()  # exactly one line
    assert err == line + "\n"
    assert db.read_bytes() == before, "the database was written"
    assert sorted(tmp_path.iterdir()) == files, "a file was made or removed beside it"
    assert listened == [], "serve started listening"
    return line


def test_every_command_of_the_parser_has_an_entry_here():
    missing = sorted(set(COMMANDS) - set(ARGS))
    assert not missing, (
        f"no arguments for {missing} in ARGS: add the smallest list that makes each open the"
        " database, so a newer database is checked there too"
    )
    assert sorted(ARGS) == COMMANDS, (
        f"ARGS names commands the parser has not: {set(ARGS) - set(COMMANDS)}"
    )


def test_a_command_is_left_out_of_the_database_tests_only_by_name_and_only_without_db():
    """Every command of the parser is in exactly one of ARGS and NO_DATABASE; each in ARGS
    takes exactly one --db, and each in NO_DATABASE takes none. A command added without --db
    and not named in NO_DATABASE fails here, and so does one named there that takes --db."""
    assert not set(ARGS) & set(NO_DATABASE)
    assert sorted([*ARGS, *NO_DATABASE]) == ALL_COMMANDS
    without = sorted(command for command in ALL_COMMANDS if not db_options(command))
    assert without == sorted(NO_DATABASE), (
        f"commands without --db: {without}; name each in NO_DATABASE with why, or give it --db"
    )
    assert all(len(db_options(command)) == 1 for command in ARGS)
    assert set(NO_DATABASE) == {"google-authorize", "connector-check"}


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_refuses_a_newer_database_in_one_line(tmp_path, capsys, listened, command):
    if command not in ARGS:
        pytest.fail(
            f"{command!r} has no entry in ARGS: give it the arguments that open the database"
        )
    line = refused_in_one_line(tmp_path, capsys, listened, command, ARGS[command])
    assert line == cli.NEWER_DATABASE.format(refusal=REFUSAL)
    assert "SchemaTooNew" in line
    assert f"schema version {NEWER}" in line and f"knows up to {SCHEMA_VERSION}" in line
    assert f"needs a cairn of schema version {NEWER} or later" in line
    assert "the cairn that wrote it or a newer one" in line
    assert "restore the database from before the upgrade" in line
    assert str(tmp_path) not in line  # no path at all, so never one twice


@pytest.mark.parametrize(("command", "extra"), VARIANTS, ids=VARIANT_IDS)
def test_the_options_that_print_notices_still_leave_one_line(
    tmp_path, capsys, listened, command, extra
):
    line = refused_in_one_line(tmp_path, capsys, listened, command, extra)
    assert line == cli.NEWER_DATABASE.format(refusal=REFUSAL)


def test_the_store_itself_still_raises_with_its_own_message(tmp_path):
    db = newer_database(tmp_path)
    before = db.read_bytes()
    with pytest.raises(SchemaTooNew) as refused:
        Store(db)
    assert str(refused.value) == REFUSAL
    assert db.read_bytes() == before


def test_a_missing_database_and_a_bad_option_keep_their_own_exits(tmp_path, capsys):
    missing = tmp_path / "none.db"
    assert cli.main(["usage", "--db", str(missing), "--account", DEFAULT_ACCOUNT_ID]) == 1
    assert capsys.readouterr().err == f"no database at {missing}\n"
    assert not missing.exists()
    with pytest.raises(SystemExit) as bad:
        cli.main(["usage", "--db", str(missing), "--no-such-option"])
    assert bad.value.code == 2


def test_an_import_whose_archive_cannot_be_read_says_both_lines_as_before(tmp_path, capsys):
    """The notice that no plan is applied comes first, as it always did, when the database
    is fine and the archive is what fails."""
    db = tmp_path / "cairn.db"
    Store(db).close()
    gone = tmp_path / "gone.zip"
    argv = ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(gone)]
    assert cli.main(argv) == 1
    assert capsys.readouterr().err.splitlines() == [
        cli.cli_data.NO_PLAN,
        f"cannot read {gone}: No such file or directory",
    ]


def flat(text: str) -> str:
    return " ".join(text.split())


# The line as the docstring and the guides quote it, with the two versions as placeholders.
QUOTED = cli.NEWER_DATABASE.format(
    refusal="the database has schema version <found> and needs a cairn of schema version"
    " <floor> or later; this cairn knows up to <known>"
)


def test_the_docstring_quotes_the_line_main_prints():
    doc = flat(cli.__doc__)
    assert f'"{QUOTED}"' in doc
    assert "stops every command with exit 1 and one line on stderr" in doc
    assert "nothing on stdout" in doc and "`serve` never starts listening" in doc
    printed = QUOTED.replace("<found>", str(NEWER)).replace("<known>", str(SCHEMA_VERSION))
    printed = printed.replace("<floor>", str(NEWER))
    assert printed == cli.NEWER_DATABASE.format(refusal=REFUSAL)


# --- what the guides tell an operator who rolled back onto a newer database ---------------

REPO = Path(__file__).resolve().parents[2]
DEPLOY_GUIDE = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")
INFRA_GUIDE = (REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8")
QUOTED_BLOCK = f"\n```text\n{QUOTED}\n```\n"


def part(guide: str, heading: str) -> str:
    """From `heading` to the next heading of the same or a higher level, outside code."""
    level = len(heading.split(" ", 1)[0])
    lines = guide[guide.index(f"\n{heading}\n") + 1 :].splitlines(keepends=True)
    kept, fenced = lines[:1], False
    for line in lines[1:]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#") and len(line.split(" ", 1)[0]) <= level:
            break
        kept.append(line)
    return "".join(kept)


def test_the_compose_guide_quotes_the_line_where_docker_compose_logs_shows_it():
    update = part(DEPLOY_GUIDE, "## Update")
    assert QUOTED_BLOCK in update
    said = flat(update)
    assert "cairn exits at start, before it listens, with one line and no traceback" in said
    assert "leaves the database as it was; `docker compose logs cairn` shows it" in said
    assert "Docker starts the container again (`restart: unless-stopped`)" in said
    compose = (REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    service = compose[compose.index("\n  cairn:\n") :]
    service = service[: service.index("\n\n")]
    assert "\n    restart: unless-stopped\n" in service
    # What the container runs is serve, through main, which prints the line.
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["python", "-m", "cairn.server"]' in dockerfile
    assert 'CMD ["serve", ' in dockerfile


def test_the_vm_guide_quotes_the_line_where_journalctl_shows_it_every_five_seconds():
    rollback = part(INFRA_GUIDE, "## Rollback")
    assert QUOTED_BLOCK in rollback
    said = flat(rollback)
    assert "with one line and no traceback; `sudo journalctl -u cairn` on the VM shows it" in said
    assert "systemd starts cairn again every five seconds" in said
    assert "(`Restart=always`, `RestartSec=5` in `cairn.service`)" in said
    template = (REPO / "infra" / "gcp" / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
    unit = template[template.index("path: /etc/systemd/system/cairn.service\n") :]
    unit = unit[: unit.index("\n  - path: ")]
    assert "\n      Restart=always\n      RestartSec=5\n" in unit
    assert "ExecStart=/bin/bash /etc/cairn/host.sh run-cairn\n" in unit
    # run-cairn runs the container attached, so its stderr is cairn.service's journal.
    host = (REPO / "infra" / "gcp" / "files" / "host.sh").read_text(encoding="utf-8")
    run = host[host.index("cmd_run_cairn() {") :]
    run = run[: run.index("\n}\n")]
    assert "exec docker run --rm --name cairn " in run
    assert " -d " not in run and "--detach" not in run
    assert '"$image" serve --db /data/cairn.db ' in run
