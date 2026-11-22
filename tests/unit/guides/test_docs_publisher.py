"""Offline release publication: repository bytes and real Memory behind fake HTTP."""

import importlib
import importlib.util
import json
import subprocess

import httpx
import pytest

GUIDES = ("connect", "patterns", "sources", "security", "run", "privacy")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def release(tmp_path, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic Author")
    git(repo, "config", "user.email", "author@example.test")
    git(repo, "remote", "add", "origin", "https://github.com/macayaven/cairn.git")
    guides = repo / "src/cairn/guides"
    guides.mkdir(parents=True)
    for name in GUIDES:
        text = f"# {name}\n\nRead the {name} guide.\n"
        if getattr(request, "param", None) == "duplicates":
            text += f"\nRead the {name} guide.\n"
        if getattr(request, "param", None) == "multichunk" and name == "connect":
            text = "# Connect\n\n" + "".join(f"- Instruction {i}.\n" for i in range(170))
        (guides / f"{name}.md").write_text(text)
    (repo / "private").mkdir()
    (repo / "private/work-order.md").write_text("Private: never publish.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic release")
    return repo, git(repo, "rev-parse", "HEAD")


def test_release_artifact_preserves_only_approved_committed_lines(release):
    # Publishing arbitrary repository paths or rewriting lines breaks provenance.
    repo, sha = release
    assert importlib.util.find_spec("cairn.docs_publisher") is not None, "publisher is missing"
    publisher = importlib.import_module("cairn.docs_publisher")
    artifact = publisher.build_release(repo, sha)
    assert artifact["schema"] == "cairn-docs-release-v1"
    assert artifact["release"] == sha
    assert artifact["repository"] == "https://github.com/macayaven/cairn"
    assert artifact["project"] == {
        "name": f"cairn-docs-{sha}",
        "description": f"cairn documentation release {sha}; cairn-docs-release-v1",
    }
    assert [entry["path"] for entry in artifact["notes"]] == [
        f"src/cairn/guides/{name}.md" for name in GUIDES
    ]
    first = artifact["notes"][0]
    assert (first["start_line"], first["end_line"]) == (1, 3)
    assert first["page"] == "docs-connect-0001"
    assert first["payload"] == {
        "text": "# connect\n\nRead the connect guide.\n",
        "title": "docs-connect-0001",
        "topic": "docs-connect-0001",
        "label": "cairn-docs-release-v1",
        "source_ref": f"https://github.com/macayaven/cairn/blob/{sha}/src/cairn/guides/connect.md#L1-L3",
    }
    assert "Private: never publish." not in json.dumps(artifact)


@pytest.mark.parametrize("mutation", ["dirty", "staged", "release", "origin", "symlink", "parent"])
def test_refuses_unverified_repository_bytes(release, tmp_path, mutation):
    from cairn import docs_publisher as publisher

    repo, sha = release
    path = repo / "src/cairn/guides/connect.md"
    if mutation in {"dirty", "staged"}:
        path.write_text("Changed after release.\n")
        if mutation == "staged":
            git(repo, "add", ".")
    elif mutation == "release":
        sha = "f" * 40
    elif mutation == "origin":
        git(repo, "remote", "set-url", "origin", "https://example.test/other/repo.git")
    elif mutation == "symlink":
        outside = tmp_path / "outside.md"
        outside.write_text(path.read_text())
        path.unlink()
        path.symlink_to(outside)
    elif mutation == "parent":
        guides = path.parent
        guides.rename(repo / "elsewhere")
        guides.symlink_to(repo / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError):
        publisher.build_release(repo, sha)


@pytest.mark.parametrize(
    "content",
    [
        "# Guide\n\npassword = SyntheticCredentialValue123456\n",
        "# Guide\n\nhttps://synthetic-host.synthetic-tail.ts.net/path\n",
        "# Guide\n\n" + "x" * 17000 + "\n",
        "# Guide\n" + "Read safely.\n" * 6000,
        "# Guide only\n",
    ],
)
def test_rejects_secret_shapes_and_unpublishable_content_without_echo(release, content):
    from cairn import docs_publisher as publisher

    repo, _ = release
    (repo / "src/cairn/guides/connect.md").write_text(content)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic invalid guide")
    with pytest.raises(ValueError) as error:
        publisher.build_release(repo, git(repo, "rev-parse", "HEAD"))
    assert content not in str(error.value)
    assert "SyntheticCredential" not in str(error.value)
    assert "synthetic-host" not in str(error.value)


def test_chunks_keep_every_original_line_and_bound_note_payload(release):
    from cairn import docs_publisher as publisher

    repo, _ = release
    text = "# Connect\n\n" + "- Read this guide.\n" * 170
    (repo / "src/cairn/guides/connect.md").write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic long guide")
    artifact = publisher.build_release(repo, git(repo, "rev-parse", "HEAD"))
    notes = [entry for entry in artifact["notes"] if entry["path"].endswith("/connect.md")]
    assert [(entry["start_line"], entry["end_line"]) for entry in notes] == [
        (1, 80),
        (81, 160),
        (161, 172),
    ]
    assert "".join(entry["payload"]["text"] for entry in notes) == text
    assert notes[-1]["payload"]["source_ref"].endswith("/connect.md#L161-L172")


@pytest.fixture
def destination(release, tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from cairn import docs_publisher as publisher
    from cairn.gate import lint_gate
    from cairn.memory import Memory
    from cairn.server import create_app
    from cairn.store import Store

    repo, sha = release
    artifact = publisher.build_release(repo, sha)
    store = Store(tmp_path / "docs.db")
    project = store.create_project(**artifact["project"], kind="documents")
    store.set_project_models(project.id, "never")
    writer = store.create_key("synthetic-publisher", [project.id], "write")
    reader = store.create_key("synthetic-reader", [project.id], "read")
    monkeypatch.setenv("DOCS_WRITER", writer)
    monkeypatch.setenv("DOCS_READER", reader)
    requests = []
    with TestClient(create_app(store), base_url="https://cairn.example.test") as server:

        def handle(request):
            requests.append((request.method, request.url.path))
            answer = server.request(
                request.method,
                str(request.url),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(answer.status_code, content=answer.content)

        yield {
            "repo": repo,
            "sha": sha,
            "store": store,
            "project": project,
            "transport": httpx.MockTransport(handle),
            "requests": requests,
            "memory": Memory(store, lint_gate),
            "artifact": artifact,
        }
    store.close()


def publish(
    destination, key_env="DOCS_WRITER", transport=None, base_url="https://cairn.example.test"
):
    from cairn import docs_publisher as publisher

    assert callable(getattr(publisher, "publish_release", None)), "publication is missing"
    return publisher.publish_release(
        destination["repo"],
        destination["sha"],
        base_url=base_url,
        key_env=key_env,
        transport=transport or destination["transport"],
    )


def test_existing_api_publication_is_idempotent_and_keeps_reader_separate(destination):
    first = publish(destination)
    assert first == {"status": "published", "notes": 6, "created": 6}
    store, project = destination["store"], destination["project"]
    assert store.count_notes(project.id) == 6
    assert store.page_versions(project.id, "docs-connect-0001") == [1]
    again = publish(destination)
    assert again == {"status": "published", "notes": 6, "created": 0}
    assert store.count_notes(project.id) == 6
    assert store.page_versions(project.id, "docs-connect-0001") == [1]
    # An existing read credential goes through the normal server's permission checks.
    with pytest.raises(ValueError):
        publish(destination, key_env="DOCS_READER")
    assert store.count_notes(project.id) == 6


@pytest.mark.parametrize("mutation", ["purpose", "models", "page", "foreign", "modified"])
def test_destination_preflight_prevents_any_write_to_wrong_content(destination, mutation):
    d = destination
    store, project, memory = d["store"], d["project"], d["memory"]
    if mutation == "purpose":
        with store._write() as con:
            con.execute("UPDATE projects SET description = ? WHERE id = ?", ("Other", project.id))
    elif mutation == "models":
        store.set_project_models(project.id, "allowed")
    else:
        payload = dict(d["artifact"]["notes"][0]["payload"])
        if mutation == "page":
            payload["topic"] = "another-page"
        elif mutation == "foreign":
            payload["source_ref"] = "https://example.test/customer-note"
        elif mutation == "modified":
            payload["text"] = "# connect\n\nOther content.\n"
        memory.note(project.name, **payload)
    before = store.count_notes(project.id)
    with pytest.raises(ValueError):
        publish(d)
    assert not any(method == "POST" for method, _ in d["requests"])
    assert store.count_notes(project.id) == before


@pytest.mark.parametrize("kind", ["redirect", "http_error", "malformed", "large", "transport"])
def test_network_errors_are_bounded_and_never_echo_server_or_credentials(destination, kind):
    def answer(request):
        if kind == "transport":
            raise httpx.ConnectError("SyntheticCredentialValue123456", request=request)
        if kind == "redirect":
            return httpx.Response(307, headers={"location": "https://other.example.test/collect"})
        if kind == "http_error":
            return httpx.Response(403, text="SyntheticCredentialValue123456")
        if kind == "large":
            return httpx.Response(200, content=b"x" * (256 * 1024 + 1))
        return httpx.Response(200, text="not JSON SyntheticCredentialValue123456")

    with pytest.raises(ValueError) as error:
        publish(destination, transport=httpx.MockTransport(answer))
    assert "SyntheticCredential" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://cairn.example.test",
        "https://user:pass@cairn.example.test",
        "https://cairn.example.test/api",
        "https://cairn.example.test?token=value",
        "https://cairn.example.test/#fragment",
    ],
)
def test_invalid_destination_makes_no_request(destination, url):
    with pytest.raises(ValueError):
        publish(destination, base_url=url)
    assert not destination["requests"]


def test_entire_release_is_scanned_before_first_request(destination):
    d = destination
    path = d["repo"] / "src/cairn/guides/privacy.md"
    path.write_text("# Privacy\n\npassword = SyntheticCredentialValue123456\n")
    git(d["repo"], "add", ".")
    git(d["repo"], "commit", "-qm", "Synthetic unsafe last guide")
    d["sha"] = git(d["repo"], "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        publish(d)
    assert not d["requests"]


def test_empty_approved_guide_is_not_silently_left_out(release):
    from cairn import docs_publisher as publisher

    repo, _ = release
    (repo / "src/cairn/guides/privacy.md").write_text("")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic empty guide")
    with pytest.raises(ValueError):
        publisher.build_release(repo, git(repo, "rev-parse", "HEAD"))


def test_default_cli_writes_private_artifact_without_credentials_or_content_output(
    release, tmp_path, capsys
):
    from cairn import docs_publisher as publisher

    assert callable(getattr(publisher, "main", None)), "dry-run CLI is missing"
    repo, sha = release
    output = tmp_path / "release.json"
    assert publisher.main(["--repo", str(repo), "--release", sha, "--output", str(output)]) == 0
    artifact = json.loads(output.read_text())
    assert artifact["release"] == sha
    assert artifact["notes"][0]["payload"]["text"] == "# connect\n\nRead the connect guide.\n"
    assert output.stat().st_mode & 0o777 == 0o600
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout) == {"status": "dry-run", "notes": 6}
    assert stderr == ""


def test_a_second_run_accepts_the_same_output_and_refuses_a_different_one(
    release, tmp_path, capsys
):
    from cairn import docs_publisher as publisher

    repo, sha = release
    output = tmp_path / "release.json"
    args = ["--repo", str(repo), "--release", sha, "--output", str(output)]
    assert publisher.main(args) == 0
    written = output.read_bytes()
    capsys.readouterr()
    assert publisher.main(args) == 0
    assert output.read_bytes() == written
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout) == {"status": "dry-run", "notes": 6}
    assert stderr == ""
    other = tmp_path / "other.json"
    other.write_text("{}\n")
    assert publisher.main([*args[:-1], str(other)]) == 2
    assert other.read_text() == "{}\n"
    stdout, stderr = capsys.readouterr()
    assert stdout == ""
    assert stderr == (
        f"cairn docs: {other} already exists and holds something other than this release;"
        " remove it or choose another --output\n"
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--key", "SyntheticCredentialValue123456"],
        ["--publish"],
        [
            "--repo",
            "/missing/SyntheticCredentialValue123456",
            "--release",
            "x",
            "--output",
            "unused",
        ],
    ],
)
def test_cli_errors_never_echo_arguments(args, capsys):
    from cairn import docs_publisher as publisher

    assert callable(getattr(publisher, "main", None)), "dry-run CLI is missing"
    assert publisher.main(args) == 2
    stdout, stderr = capsys.readouterr()
    assert "SyntheticCredential" not in stdout + stderr
    assert "Traceback" not in stderr


def test_interrupted_publication_can_resume_without_new_versions(destination):
    calls = 0
    original = destination["transport"]

    def interrupt(request):
        nonlocal calls
        if request.method == "POST":
            calls += 1
            if calls == 3:
                return httpx.Response(503, text="unavailable")
        return original.handle_request(request)

    with pytest.raises(ValueError):
        publish(destination, transport=httpx.MockTransport(interrupt))
    assert destination["store"].count_notes(destination["project"].id) == 2
    assert publish(destination) == {"status": "published", "notes": 6, "created": 4}
    assert destination["store"].page_versions(destination["project"].id, "docs-connect-0001") == [1]


def test_injected_redirect_cannot_forward_writer_header(destination):
    seen = []

    def redirect(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://other.example.test/collect"})

    with pytest.raises(ValueError):
        publish(destination, transport=httpx.MockTransport(redirect))
    assert seen == ["cairn.example.test"]


def test_release_work_bound_is_checked_before_http(destination):
    d = destination
    for name in GUIDES:
        (d["repo"] / f"src/cairn/guides/{name}.md").write_text(
            f"# {name}\n\n" + "- A separate instruction.\n" * 300
        )
    git(d["repo"], "add", ".")
    git(d["repo"], "commit", "-qm", "Synthetic excessive passage count")
    d["sha"] = git(d["repo"], "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        publish(d)
    assert not d["requests"]


def test_network_request_cap_stops_partial_publication(destination, monkeypatch):
    from cairn import docs_publisher as publisher

    monkeypatch.setattr(publisher, "MAX_REQUESTS", 3)
    with pytest.raises(ValueError):
        publish(destination)
    assert len(destination["requests"]) == 3
    assert destination["store"].count_notes(destination["project"].id) == 1


def test_missing_writer_variable_makes_no_request(destination):
    with pytest.raises(ValueError):
        publish(destination, key_env="UNSET_SYNTHETIC_WRITER")
    assert not destination["requests"]


@pytest.mark.parametrize("release", ["duplicates"], indirect=True)
def test_default_server_tidy_does_not_make_release_unpublishable(destination):
    from cairn.arrange import tidy_page

    original = destination["transport"]

    def tidy_after_note(request):
        answer = original.handle_request(request)
        if request.method == "POST" and answer.status_code == 201:
            data = answer.json()
            tidy_page(destination["store"], destination["project"].id, data["page"])
        return answer

    transport = httpx.MockTransport(tidy_after_note)
    assert publish(destination, transport=transport)["created"] == 6
    versions = destination["store"].page_versions(destination["project"].id, "docs-connect-0001")
    assert versions == [1, 2]
    assert publish(destination, transport=transport)["created"] == 0
    assert (
        destination["store"].page_versions(destination["project"].id, "docs-connect-0001")
        == versions
    )


@pytest.mark.parametrize("marker", [None, "synthetic-model", "third"])
def test_only_existing_no_model_tidy_second_version_is_accepted(destination, marker):
    d = destination
    for entry in d["artifact"]["notes"]:
        d["memory"].note(d["project"].name, via="api", **entry["payload"])
    page = d["store"].get_page(d["project"].id, "docs-connect-0001")
    metrics = {**page.metrics, "arranged_by": "cairn" if marker == "third" else marker}
    for _ in range(2 if marker == "third" else 1):
        d["store"].save_page(
            d["project"].id, page.slug, page.title, page.body, page.claims, metrics
        )
    with pytest.raises(ValueError):
        publish(d)
    assert not any(method == "POST" for method, _ in d["requests"])


@pytest.mark.parametrize("release", ["multichunk"], indirect=True)
def test_multi_chunk_release_publishes_unique_pages_with_original_line_provenance(destination):
    assert publish(destination) == {"status": "published", "notes": 8, "created": 8}
    d = destination
    assert d["store"].page_versions(d["project"].id, "docs-connect-0001") == [1]
    assert d["store"].page_versions(d["project"].id, "docs-connect-0002") == [1]
    third = d["memory"].page(d["project"].name, "docs-connect-0003")
    evidence = d["memory"].evidence(d["project"].name, third["slug"], third["claims"][0]["id"])
    passage = evidence["passages"][0]
    assert passage["source_ref"].endswith("/connect.md#L161-L172")
    assert passage["start_line"] == 1
    assert passage["text"] == "- Instruction 158."
    assert publish(destination)["created"] == 0


def passage_signature(text, offset=0):
    from cairn.spans import chunk

    return [
        (span.kind, span.text, span.start_line + offset, span.end_line + offset)
        for span in chunk("original", text)
    ]


@pytest.mark.parametrize(
    "block",
    [
        "```python\n" + "print('synthetic')\n" * 8 + "```\n",
        "- An instruction\n" + "  continued instruction\n" * 9,
        "A paragraph line.\n" * 10,
        "| synthetic | value |\n" * 10,
        "> A quoted instruction.\n" * 10,
    ],
)
def test_markdown_boundary_preserves_original_passage_kinds_text_and_ranges(release, block):
    from cairn import docs_publisher as publisher

    repo, _ = release
    # The indivisible passage begins at L79 and crosses the former L80 cut.
    text = "# Before\n\n" + "".join(f"- Before {i}.\n" for i in range(75)) + "\n"
    text += block + "\n# After\n\nThe following prose stays prose.\n"
    (repo / "src/cairn/guides/connect.md").write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic Markdown boundary")
    artifact = publisher.build_release(repo, git(repo, "rev-parse", "HEAD"))
    entries = [entry for entry in artifact["notes"] if entry["path"].endswith("/connect.md")]
    emitted = [
        passage
        for entry in entries
        for passage in passage_signature(entry["payload"]["text"], entry["start_line"] - 1)
    ]
    assert emitted == passage_signature(text)
    assert "".join(entry["payload"]["text"] for entry in entries) == text
    assert [(entry["start_line"], entry["end_line"]) for entry in entries] == [(1, 78), (79, 92)]


def test_indivisible_passage_past_line_bound_is_refused(release):
    from cairn import docs_publisher as publisher

    repo, _ = release
    text = "# Guide\n\n```python\n" + "print('synthetic')\n" * 79 + "```\n"
    (repo / "src/cairn/guides/connect.md").write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic indivisible long code")
    with pytest.raises(ValueError):
        publisher.build_release(repo, git(repo, "rev-parse", "HEAD"))


def test_all_public_guides_keep_full_document_passage_structure(release):
    from importlib.resources import files

    from cairn import docs_publisher as publisher

    repo, _ = release
    originals = {}
    for name in GUIDES:
        text = files("cairn.guides").joinpath(f"{name}.md").read_text()
        originals[f"src/cairn/guides/{name}.md"] = text
        (repo / f"src/cairn/guides/{name}.md").write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Public guides in offline temporary release")
    artifact = publisher.build_release(repo, git(repo, "rev-parse", "HEAD"))
    for path, text in originals.items():
        entries = [entry for entry in artifact["notes"] if entry["path"] == path]
        assert "".join(entry["payload"]["text"] for entry in entries) == text
        emitted = [
            passage
            for entry in entries
            for passage in passage_signature(entry["payload"]["text"], entry["start_line"] - 1)
        ]
        assert emitted == passage_signature(text), path


def test_the_codex_key_sentences_name_the_variable_and_still_publish():
    from importlib.resources import files

    text = files("cairn.guides").joinpath("connect.md").read_text()
    assert "With an API key held in `CAIRN_API_KEY`, sent as `Authorization: Bearer`:\n" in text
    assert (
        "In the `env_http_headers` line, write `CAIRN_API_KEY` in place of"
        " `<ENVIRONMENT_VARIABLE_NAME>`: the header takes the variable's name, never the key.\n"
    ) in text


def test_a_guide_has_room_to_grow_under_the_file_bound():
    """The longest guide was 17 bytes under a 64 KiB bound; a release must not start
    failing because a guide gained a sentence. The bound is 128 KiB, and the longest real
    guide leaves at least a quarter of it free."""
    from pathlib import Path

    from cairn import docs_publisher

    guides = Path(docs_publisher.__file__).parent / "guides"
    longest = max(path.stat().st_size for path in guides.glob("*.md"))
    assert docs_publisher.MAX_FILE_BYTES == 128 * 1024
    assert longest <= docs_publisher.MAX_FILE_BYTES * 3 // 4
