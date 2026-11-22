"""The search-only operator settings are validated before any database work."""

from pathlib import Path

import pytest

from cairn.web import cli

OPTIONS = [
    "--docs-artifact",
    "/trusted/release.json",
    "--docs-release",
    "a" * 40,
    "--docs-reader-env",
    "DOCS_READER",
]


def test_cli_passes_names_to_factory_without_reading_reader_or_enabling_model(
    tmp_path, monkeypatch
):
    made = {}
    monkeypatch.delenv("DOCS_READER", raising=False)

    def factory(store, **settings):
        made.update(settings)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "create_app", factory)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("server started"))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["serve", "--db", str(tmp_path / "kb.db"), *OPTIONS])
    assert stopped.value.code == 0
    assert made["docs_artifact"] == Path("/trusted/release.json")
    assert made["docs_release"] == "a" * 40
    assert made["docs_reader_env"] == "DOCS_READER"
    assert not any(name.startswith("docs_model") for name in made)


@pytest.mark.parametrize(
    "options",
    [
        OPTIONS[:2],
        OPTIONS[2:4],
        OPTIONS[4:],
        OPTIONS[:4],
        OPTIONS[:2] + OPTIONS[4:],
        OPTIONS[2:],
        [*OPTIONS[:3], "INVALID-SENSITIVE-VALUE", *OPTIONS[4:]],
        [*OPTIONS[:5], "INVALID-SENSITIVE-VALUE"],
        [*OPTIONS[:1], "", *OPTIONS[2:]],
    ],
)
def test_invalid_docs_settings_are_safe_and_rejected_before_db(
    tmp_path, monkeypatch, capsys, options
):
    db = tmp_path / "absent" / "kb.db"
    monkeypatch.setattr(cli.cli_data, "make_folder", lambda *a: pytest.fail("database touched"))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("server started"))
    try:
        code = cli.main(["serve", "--db", str(db), *options])
    except SystemExit as stopped:
        code = stopped.code
    assert code == 2
    assert not db.parent.exists()
    output = capsys.readouterr().err
    assert "--docs-" in output
    assert "INVALID-SENSITIVE-VALUE" not in output


def test_source_sync_namespace_and_runner_receive_no_docs_settings(tmp_path, monkeypatch):
    """A non-serve command keeps its own namespace and source runner contract."""
    import asyncio
    from types import SimpleNamespace

    from cairn.sources.runner import SyncReport

    args = cli._parser().parse_args(
        ["source-sync", "--db", str(tmp_path / "kb.db"), "--name", "example"]
    )
    assert not any(name.startswith("docs_") for name in vars(args))
    calls = []

    async def runner(store, name, *, http, env, gate, plan, plans):
        calls.append(name)
        return SyncReport(off=True)

    monkeypatch.setattr(cli, "run_source", runner)
    store = SimpleNamespace(get_source=lambda name: object())
    assert asyncio.run(cli._sync(args, store, None)) == 0
    assert calls == ["example"]
