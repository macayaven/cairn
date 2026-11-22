"""Self-tests for tests/support/rt2_github.py."""

from __future__ import annotations

import sys

import pytest

from support import rt2_github
from support.rt2_github import (
    TAGS,
    Config,
    Hub,
    Hung,
    Scheduler,
    Script,
    body_text,
    current_run,
    pinned_run,
    run_attempt,
    run_files,
    step_in_history,
)


def test_the_listing_is_behind_a_write_by_the_looks_its_kind_is_given():
    hub = Hub(Config(lag={"create": 2, "upload": 0, "publish": 0, "person": 0}), Scheduler())
    hub.write(
        "create",
        (
            "create",
            7,
            {
                "id": 7,
                "tag_name": "v1.2.3",
                "draft": True,
                "prerelease": False,
                "name": "v1.2.3",
                "body": "x",
                "assets": [],
            },
        ),
        "step",
    )
    seen = []
    for _ in range(3):
        _, out, _ = hub.listing(100, ".[] | .id")
        seen.append(out.split())
    assert seen == [[], [], ["7"]]
    assert hub.by_id(7, ".id")[1].split() == ["7"]


def test_a_view_skips_a_write_to_a_release_it_does_not_show_yet():
    hub = Hub(Config(lag={"create": 1, "upload": 0, "publish": 0, "person": 0}), Scheduler())
    hub.write(
        "create",
        (
            "create",
            7,
            {
                "id": 7,
                "tag_name": "v1.2.3",
                "draft": True,
                "prerelease": False,
                "name": None,
                "body": None,
                "assets": [],
            },
        ),
        "step",
    )
    hub.write("publish", ("set", 7, {"draft": False}), "step")
    assert hub.view()["releases"] == {}
    hub.listings += 1
    assert hub.view()["releases"][7]["draft"] is False


def test_the_shell_reaches_the_model_and_an_unknown_call_is_loud():
    run = 'gh api --paginate "repos/$GH_REPO/releases?per_page=100" --jq ".[] | .id"; gh frob'
    hub = Hub(Config(), Scheduler())
    hub.attempt = 1
    done = run_attempt(run, hub, run_files())
    assert done.code == 99 and hub.unmodelled == ["gh frob"]


def test_an_attempt_that_does_not_end_is_stopped_whole_and_said(monkeypatch):
    """A subshell left waiting on the answer pipe, as in the one full run that hung: the
    attempt's time runs out, its whole process group is killed (the subshell held the output
    pipes), and the explorer gets Hung, with the server let go, within seconds."""
    monkeypatch.setattr(rt2_github, "ATTEMPT_SECONDS", 2)
    run = 'sleep 3; x="$(read -r y < "$RT2_DIR/resp")"'
    hub = Hub(Config(), Scheduler())
    hub.attempt = 1
    with pytest.raises(Hung, match="did not end"):
        run_attempt(run, hub, run_files())


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [("fate of POST", "reply lost")],
        [("fate of upload cairn_memory-1.2.3.tar.gz", "cut")],
        [("fate of PATCH", "reply lost")],
        [("person, before GET releases/88", "edits the notes")],
        [("person, before GET releases page", "makes a second release for the tag, a draft")],
    ],
    ids=["nothing", "creation lost", "upload cut", "publication lost", "notes", "second"],
)
def test_the_two_clients_give_the_same_execution(steps):
    """The real subprocess (`path`) against the in-process runner (`inprocess`),
    on the same schedule, three attempts: the same requests, answers and exits."""
    seen = []
    for client in ("path", "inprocess"):
        hub = Hub(Config(lag={"create": 1, "upload": 1, "publish": 1, "person": 1}), Script(steps))
        codes = []
        for n in (1, 2, 3):
            hub.attempt, hub.command = n, 0
            hub.catch_up()
            done = run_attempt(current_run(), hub, hub.files, client=client)
            codes.append((done.code, done.errors))
        seen.append(
            (
                codes,
                [(e.attempt, e.what, e.detail, e.fate) for e in hub.events],
                hub.truth,
                hub.unmodelled,
            )
        )
    assert seen[0] == seen[1]


def test_the_pinned_step_is_the_step_of_its_commit():
    """The file the pinned step is read from is the step at that commit, wherever the
    checkout holds the commit; where it does not, there is nothing to compare it with."""
    in_history = step_in_history("27aff19f")
    if in_history is None:
        pytest.skip("this checkout does not hold the commit the step was taken from")
    assert pinned_run("27aff19f") == in_history


def test_the_pinned_step_needs_no_history(tmp_path, monkeypatch):
    """GitHub's run checked out one commit and the pinned step could not be read: a test
    reads no history. With git unable to find any repository, the step is still read."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-repository"))
    assert step_in_history("27aff19f") is None
    assert "gh release create" in pinned_run("27aff19f")


_IS_THE_STREAM_AT_ITS_END = """
import os, sys
stream = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK)
try:
    read = os.read(stream, 1)
except BlockingIOError:
    raise SystemExit(0)  # a writer holds it open and nothing waits in it: a read would wait
raise SystemExit(3 if read == b"" else 4)  # 3: no writer is left, so a read meets the end
"""


@pytest.mark.parametrize("client", ["function", "path"])
def test_the_stream_of_answers_does_not_end_between_two_answers(tmp_path, client):
    """A call reads its exit status from the stream of answers. On `main`'s first run of the
    sharded suite two jobs failed here: the stand-in opened that stream for each answer and
    closed it after, a call opened it while the writer of the answer before was still closing,
    and its read met the end. The status was empty, the shell said `return: : numeric argument
    required`, and the step said the releases could not be listed. So between two answers the
    stream has a writer, whoever looks: a read there waits and does not end."""
    probe = tmp_path / "probe.py"
    probe.write_text(_IS_THE_STREAM_AT_ITS_END)
    run = f'sleep 1; "{sys.executable}" -I "{probe}" "$RT2_DIR/resp"; sleep 1'
    hub = Hub(Config(), Scheduler())
    hub.attempt = 1
    done = run_attempt(run, hub, run_files(), client=client)
    assert done.code == 0, f"the probe ended at {done.code}: {done.stdout}{done.stderr}"
    assert not hub.unmodelled
    assert [event.what for event in hub.events] == ["sleep", "sleep"]


@pytest.mark.parametrize("tag", TAGS.values())
@pytest.mark.parametrize("which", ["pinned", "current"])
def test_nothing_wrong_ends_in_one_public_release_of_the_run(tag, which):
    run = pinned_run("27aff19f") if which == "pinned" else current_run()
    hub = Hub(Config(tag=tag), Scheduler())
    hub.attempt = 1
    done = run_attempt(run, hub, hub.files)
    assert done.code == 0, done.stdout + done.stderr
    assert not hub.unmodelled
    (r,) = hub.releases_for(hub.truth)
    assert (r["draft"], r["prerelease"], r["body"], r["name"]) == (
        False,
        "-rc." in tag,
        body_text(tag),
        tag,
    )
    assert {a["name"]: a["data"] for a in r["assets"]} == run_files()
