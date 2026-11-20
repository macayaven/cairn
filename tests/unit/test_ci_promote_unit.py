"""From the one build to the registry: .github/workflows/ci-cd.yml's image_build, the step that
receives its archive (image_scan and image), `publish` and `aliases`, run as the runner runs
them (bash, -e and pipefail), with stand-ins for oras and gh and a registry kept as files.

- image_build builds once, both platforms, with nothing but a read token, and hands on one
  archive with its SHA-256 and its index's digest.
- The scan and the image check push that archive, checked, by that digest, to a registry on
  their runner, with the command `publish` uses, and read it back.
- `publish` checks the archive and pushes it by that digest. A tag's full version is pushed
  once: already there as the same digest, it is carried on; as another, the job stops in one
  line; when the registry's tags cannot be listed, it stops too (only a listing that succeeded
  and lacks the version pushes it).
- `aliases`, after the release, points latest and every X.Y at the highest released version
  whose full version is in the registry, whichever run moves them and in whatever order:
  release_check.aliases says what they must be, and the step is run against the same cases.

Everything here is made up: the bytes of a manifest stand for an index."""

import hashlib
import importlib.util
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml
from test_ci_workflow_unit import JOBS, RAW_WORKFLOW, job_comment, needs, reaches_the_token

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "release_check_promote", REPO / "scripts" / "release_check.py"
)
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)

RECEIVE = "Push the build to a registry on the runner, as publish pushes it"
PUSH = "Push the scanned build"
ALIAS = "Point latest and each X.Y at the highest release"


def step(job: str, name: str) -> dict:
    (found,) = [s for s in JOBS[job]["steps"] if s.get("name") == name]
    return found


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --- the stand-ins ------------------------------------------------------------------------------

ORAS = r"""#!/bin/bash
# oras against a registry kept as files: $REG/<tag> holds the manifest's bytes.
printf '%s\n' "$*" >> "$CALLS"
by_digest() {
  for f in "$REG"/*; do
    [ -e "$f" ] || continue
    sum="$(sha256sum < "$f")"
    if [ "sha256:${sum%% *}" = "$1" ]; then printf '%s\n' "$f"; return 0; fi
  done
  return 1
}
args=()
for a in "$@"; do
  case "$a" in
    --plain-http|--to-plain-http|--from-oci-layout|--to-oci-layout) ;;
    *) args+=("$a") ;;
  esac
done
case "${args[0]} ${args[1]}" in
  "repo tags")
    [ -z "$TAGS_FAIL" ] || exit 1
    ls "$REG" ;;
  "manifest fetch-config")
    printf '%s' "$IMAGE_CONFIG" ;;
  "manifest fetch")
    ref="${args[2]}"
    if [ -n "$IMAGE_MANIFEST" ] && [ "$ref" = "$IMAGE" ]; then
      printf '%s' "$IMAGE_MANIFEST"; exit 0
    fi
    if [[ $ref == *@* ]]; then f="$(by_digest "${ref#*@}")" || exit 1; cat "$f"
    elif [ -n "$FETCH_OTHER" ]; then printf 'other bytes'
    else [ -e "$REG/${ref##*:}" ] || exit 1; cat "$REG/${ref##*:}"; fi ;;
  "cp "*)
    if [[ " $* " == *" --to-oci-layout "* ]]; then
      # registry -> a layout directory DIR:REF holding the source's bytes as its one entry
      f="$(by_digest "${args[1]#*@}")" || exit 1
      dir="${args[2]%:*}"
      mkdir -p "$dir/blobs/sha256"
      sum="$(sha256sum < "$f")"
      cp "$f" "$dir/blobs/sha256/${sum%% *}"
      printf '{"imageLayoutVersion":"1.0.0"}' > "$dir/oci-layout"
      printf '{"manifests":[{"digest":"sha256:%s"}]}' "${sum%% *}" > "$dir/index.json"
    else
      printf '%s' "$BUILT" > "$REG/${args[2]##*:}"
    fi ;;
  "tag "*)
    f="$(by_digest "${args[1]#*@}")" || exit 1
    cp "$f" "$REG/${args[2]}" ;;
  *) exit 2 ;;
esac
"""

GH = r"""#!/bin/bash
printf 'gh %s\n' "$*" >> "$CALLS"
cat "$RELEASED"
"""

SHA256SUM = (
    '#!/bin/sh\nif [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; fi\n'
    'exec shasum -a 256 "$@"\n'
)


class Runner:
    """A runner's temp directory, a registry as files, and the stand-ins on PATH."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.reg = tmp_path / "registry"
        self.reg.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, text in (("oras", ORAS), ("gh", GH), ("sha256sum", SHA256SUM)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        for name in ("docker", "curl", "sleep"):  # the local registry is the files above
            (self.bin / name).write_text(
                '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$CALLS"\n'
            )
            (self.bin / name).chmod(0o755)
        self.calls = tmp_path / "calls"
        self.calls.write_text("")
        self.built = b'{"manifests":"the build"}'
        image = tmp_path / "image"
        image.mkdir()
        (tmp_path / "index.json").write_text("{}")
        with tarfile.open(image / "cairn.oci.tar", "w") as archive:
            archive.add(tmp_path / "index.json", arcname="index.json")
        self.archive = hashlib.sha256((image / "cairn.oci.tar").read_bytes()).hexdigest()

    def put(self, tag: str, data: bytes) -> None:
        (self.reg / tag).write_bytes(data)

    def tags(self) -> dict[str, str]:
        return {path.name: digest(path.read_bytes()) for path in self.reg.iterdir()}

    def oras_calls(self) -> list[str]:
        return [
            line
            for line in self.calls.read_text().splitlines()
            if not line.startswith(("gh ", "docker ", "curl ", "sleep "))
        ]

    def run(self, run: str, **env: str) -> subprocess.CompletedProcess:
        output = self.tmp / "github_output"
        output.write_text("")
        base = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "RUNNER_TEMP": str(self.tmp),
            "REG": str(self.reg),
            "CALLS": str(self.calls),
            "BUILT": self.built.decode(),
            "DIGEST": digest(self.built),
            "ARCHIVE": self.archive,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "Owner/Repo",
        }
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run],
            cwd=self.tmp,
            env={**base, **env},
            capture_output=True,
            text=True,
            check=False,
        )


# --- image_build: one build, read-only ------------------------------------------------------------


def test_image_build_builds_once_reads_only_and_hands_on_one_archive():
    job = JOBS["image_build"]
    assert job["permissions"] == {"contents": "read"}
    assert "if" not in job and "needs" not in job and "environment" not in job
    configuration = yaml.safe_dump(job)
    assert "secrets." not in configuration and "login" not in configuration
    # The read token reaches the checkout, and the build action by its default (for a Git
    # context; this build's context is the checked-out directory).
    assert [s["name"] for s in job["steps"] if reaches_the_token(s)] == [
        "Check out",
        "Build linux/amd64 and linux/arm64",
    ]
    archive = next(s for s in job["steps"] if s.get("id") == "archive")
    assert archive["run"] == (
        'python3 scripts/oci_archive.py "$RUNNER_TEMP/image/cairn.oci.tar"'
        ' --github-output "$GITHUB_OUTPUT"'
    )
    assert job["outputs"] == {
        "digest": "${{ steps.archive.outputs.digest }}",
        "archive": "${{ steps.archive.outputs.archive }}",
        "amd64": "${{ steps.archive.outputs.amd64 }}",
        "arm64": "${{ steps.archive.outputs.arm64 }}",
    }
    upload = job["steps"][-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"] == {
        "name": "image",
        "path": "${{ runner.temp }}/image/cairn.oci.tar",
        "if-no-files-found": "error",
        "retention-days": 5,
        "overwrite": True,
        "compression-level": 0,
    }
    # The only image build in the workflow: the rehearsals take its amd64 image too.
    builders = {
        name
        for name, other in JOBS.items()
        for s in other["steps"]
        if s.get("uses", "").startswith("docker/build-push") or "docker build" in s.get("run", "")
    }
    assert builders == {"image_build"}


# --- the scan and the image check receive it as publish does -------------------------------------


def test_the_scan_and_the_image_check_receive_the_build_with_one_step():
    receive = step("image_scan", RECEIVE)
    assert receive == step("image", RECEIVE)
    assert receive["env"] == {
        "DIGEST": "${{ needs.image_build.outputs.digest }}",
        "ARCHIVE": "${{ needs.image_build.outputs.archive }}",
        "NATIVE": "${{ runner.arch == 'ARM64' && 'linux/arm64' || 'linux/amd64' }}",
    }
    assert step("image_scan", CHECK) == step("image", CHECK)
    for job in ("image_scan", "image"):
        names = [s.get("name") for s in JOBS[job]["steps"]]
        assert names.index("Fetch the build") < names.index(RECEIVE) < names.index(CHECK)
        assert "image_build" in needs(JOBS[job])
        # This leg's platform is named by its own manifest digest, which holds one image.
        assert JOBS[job]["env"]["IMAGE"] == (
            "localhost:5000/cairn@${{ needs.image_build.outputs[matrix.arch] }}"
        )
        assert JOBS[job]["env"]["PLATFORM"] == "${{ matrix.platform }}"
        assert JOBS[job]["env"]["ARCH"] == "${{ matrix.arch }}"
        arches = {entry["arch"] for entry in JOBS[job]["strategy"]["matrix"]["include"]}
        assert arches == {"amd64", "arm64"} <= set(JOBS["image_build"]["outputs"])
    # Only the image check runs the image, so only it pulls one into the engine.
    assert not any("docker pull" in s.get("run", "") for s in JOBS["image_scan"]["steps"])
    pull = step("image", "Pull this platform of the build")
    assert lines(pull["run"]) == [
        'docker pull --platform "$PLATFORM" "$IMAGE"',
        'docker tag "$IMAGE" "$LOCAL"',
        'test "$(docker image inspect --format \'{{.Architecture}}\' "$LOCAL")" = "$ARCH"',
    ]
    names = [s.get("name") for s in JOBS["image"]["steps"]]
    assert names.index(CHECK) < names.index("Pull this platform of the build")


CHECK = "Check that this platform's image is built for it"
IMAGE_INDEX = "application/vnd.oci.image.index.v1+json"
IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def check_platform(runner: Runner, media_type: str, os_arch: tuple[str, str]):
    image = "localhost:5000/cairn@sha256:" + "b" * 64
    return runner.run(
        step("image_scan", CHECK)["run"],
        IMAGE=image,
        PLATFORM="linux/arm64",
        IMAGE_MANIFEST=f'{{"mediaType": "{media_type}"}}',
        IMAGE_CONFIG=f'{{"os": "{os_arch[0]}", "architecture": "{os_arch[1]}"}}',
    )


def test_the_platform_check_reads_the_registry_and_not_the_engine(tmp_path):
    """The check reads the manifest and config of exactly IMAGE from the registry, so its answer
    does not depend on the runner's own platform."""
    runner = Runner(tmp_path)
    done = check_platform(runner, IMAGE_MANIFEST, ("linux", "arm64"))
    assert done.returncode == 0, done.stdout + done.stderr
    image = "localhost:5000/cairn@sha256:" + "b" * 64
    assert runner.oras_calls() == [
        f"manifest fetch --plain-http {image}",
        f"manifest fetch-config --plain-http {image}",
    ]
    assert "docker " not in runner.calls.read_text()


@pytest.mark.parametrize(
    ("media_type", "os_arch", "said"),
    [
        (IMAGE_MANIFEST, ("linux", "amd64"), "is built for linux/amd64, not linux/arm64"),
        (IMAGE_INDEX, ("linux", "arm64"), f"is a {IMAGE_INDEX}, not one image"),
    ],
)
def test_an_image_of_another_platform_or_an_index_stops_the_job_in_one_line(
    tmp_path, media_type, os_arch, said
):
    done = check_platform(Runner(tmp_path), media_type, os_arch)
    assert done.returncode == 1
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert said in error


def test_no_step_takes_one_platform_by_the_index_digest_and_a_flag():
    """A platform is never selected from the index by a flag: what is pulled, tagged, inspected,
    scanned or run for one platform is named by that platform's own digest."""
    acting = re.compile(
        r"\b(docker (pull|tag|run|image inspect|inspect)|trivy image|smoke_container)"
    )
    for name, job in JOBS.items():
        for s in job["steps"]:
            for line in lines(s.get("run", "")):
                if not acting.search(line):
                    continue
                assert "$DIGEST" not in line or "--platform" not in line, (name, line)
                if name in ("image_scan", "image"):
                    assert "$DIGEST" not in line, (name, line)


def lines(run: str) -> list[str]:
    return [line.strip() for line in run.replace("\\\n", " ").splitlines()]


def test_the_rehearsal_pushes_with_publish_s_own_lines():
    """The archive's check, its unpacking and the push by digest are publish's, word for word,
    except for the registry: a pull request runs every line of the push a tag runs."""
    receive, push = lines(step("image_scan", RECEIVE)["run"]), lines(step("publish", PUSH)["run"])
    # publish copies from the archive only when the registry lacks the digest; those lines then
    # are the rehearsal's.
    unpack = push.index('cd "$RUNNER_TEMP/image"')
    assert receive[0] == push[0] == "set -o pipefail"
    assert receive[1:5] == push[unpack : unpack + 4]
    rehearsed = next(line for line in receive if line.startswith("oras cp "))
    published = next(line for line in push if line.startswith("oras cp "))
    assert rehearsed == 'oras cp --to-plain-http --from-oci-layout "layout@$DIGEST" "$image:ci"'
    assert published == 'oras cp --from-oci-layout "layout@$DIGEST" "$IMAGE:$FIRST"'
    assert (
        "registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
        in (" ".join(receive))
    )
    assert '--platform "$NATIVE"' in " ".join(receive)


def test_the_rehearsal_pushes_the_build_and_reads_back_its_digest(tmp_path):
    runner = Runner(tmp_path)
    done = runner.run(step("image_scan", RECEIVE)["run"])
    assert done.returncode == 0, done.stdout + done.stderr
    built = digest(runner.built)
    assert runner.tags() == {"ci": built, "rehearsed": built}
    assert runner.oras_calls() == [
        f"cp --to-plain-http --from-oci-layout layout@{built} localhost:5000/cairn:ci",
        "repo tags --plain-http localhost:5000/cairn",
        f"tag --plain-http localhost:5000/cairn@{built} rehearsed",
        "manifest fetch --plain-http localhost:5000/cairn:ci",
        "manifest fetch --plain-http localhost:5000/cairn:rehearsed",
    ]


def test_an_archive_that_is_not_the_build_s_is_never_pushed(tmp_path):
    runner = Runner(tmp_path)
    done = runner.run(step("image_scan", RECEIVE)["run"], ARCHIVE="0" * 64)
    assert done.returncode != 0 and runner.oras_calls() == [] and runner.tags() == {}


def test_a_push_that_reads_back_another_digest_stops_the_job(tmp_path):
    runner = Runner(tmp_path)
    done = runner.run(step("image_scan", RECEIVE)["run"], DIGEST="sha256:" + "a" * 64)
    # oras cannot tag a digest the registry does not hold, and the job stops there.
    assert done.returncode != 0
    assert not any(call.startswith("manifest fetch") for call in runner.oras_calls())


def test_a_tag_that_reads_back_other_bytes_stops_the_rehearsal_in_one_line(tmp_path):
    runner = Runner(tmp_path)
    done = runner.run(step("image_scan", RECEIVE)["run"], FETCH_OTHER="1")
    assert done.returncode == 1
    other = digest(b"other bytes")
    built = digest(runner.built)
    assert done.stdout.strip().splitlines()[-1] == (
        f"::error::localhost:5000/cairn:ci is {other}, not the build's {built}"
    )


# --- publish: promote, never build --------------------------------------------------------------


def test_publish_fetches_checks_and_pushes_and_nothing_more():
    job = JOBS["publish"]
    kinds = [s.get("uses", "run").split("@")[0] for s in job["steps"]]
    assert kinds == [
        "run",
        "docker/login-action",
        "docker/metadata-action",
        "run",
        "actions/download-artifact",
        "run",
    ]
    assert job["steps"][4]["with"] == {"name": "image", "path": "${{ runner.temp }}/image"}
    assert step("publish", DECIDE)["env"] == {
        "DIGEST": "${{ needs.image_build.outputs.digest }}",
        "TAGS": "${{ steps.meta.outputs.tags }}",
    }
    assert job["outputs"] == {
        "digest": "${{ steps.push.outputs.digest }}",
        "amd64": "${{ needs.image_build.outputs.amd64 }}",
    }
    assert "image_build" in needs(job)


DECIDE = "Look for this build in the registry"


def publish(runner: Runner, tags: list[str], **env: str) -> subprocess.CompletedProcess:
    """Job `publish` as the runner runs it: the decision, then the push with its outputs (the
    archive is on the runner here whenever it is asked for). The push's result, with the
    decision's output first; or the decision's, when it stopped the job."""
    lines_ = "\n".join(f"ghcr.io/owner/repo:{tag}" for tag in tags)
    env = {
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF_NAME": "v1.2.3",
        "GITHUB_SHA": "c" * 40,
        "TAGS": lines_,
        **env,
    }
    decided = runner.run(step("publish", DECIDE)["run"], **env)
    if decided.returncode != 0:
        return decided
    out = dict(
        line.split("=", 1) for line in (runner.tmp / "github_output").read_text().splitlines()
    )
    pushed = runner.run(
        step("publish", PUSH)["run"],
        **env,
        IMAGE=out["image"],
        NAMES=out["names"],
        FIRST=out["first"],
        COPY=out["copy"],
        FROM_ARCHIVE=out["archive"],
    )
    return subprocess.CompletedProcess(
        pushed.args, pushed.returncode, decided.stdout + pushed.stdout, pushed.stderr
    )


def test_a_new_version_is_pushed_by_digest_and_read_back(tmp_path):
    runner = Runner(tmp_path)
    runner.put("1.2.2", b"an earlier release")
    done = publish(runner, ["1.2.3"])
    assert done.returncode == 0, done.stdout + done.stderr
    built = digest(runner.built)
    assert runner.tags()["1.2.3"] == built
    assert runner.oras_calls() == [
        "repo tags ghcr.io/owner/repo",
        # The registry lacks the digest, so the archive is copied.
        f"manifest fetch ghcr.io/owner/repo@{built}",
        f"cp --from-oci-layout layout@{built} ghcr.io/owner/repo:1.2.3",
        "manifest fetch ghcr.io/owner/repo:1.2.3",
    ]
    assert (tmp_path / "github_output").read_text() == f"digest={built}\n"


def test_a_candidate_is_pushed_as_its_version_only(tmp_path):
    runner = Runner(tmp_path)
    done = publish(runner, ["1.2.3-rc.1"], GITHUB_REF_NAME="v1.2.3-rc.1")
    assert done.returncode == 0, done.stdout + done.stderr
    built = digest(runner.built)
    assert runner.tags() == {"1.2.3-rc.1": built}


def test_a_re_run_of_the_same_build_carries_on_and_pushes_nothing(tmp_path):
    runner = Runner(tmp_path)
    runner.put("1.2.3", runner.built)
    done = publish(runner, ["1.2.3"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert not any(call.startswith(("cp ", "tag ")) for call in runner.oras_calls())
    assert "is this build already" in done.stdout


def test_a_full_version_already_published_as_another_digest_stops_the_job_in_one_line(tmp_path):
    runner = Runner(tmp_path)
    runner.put("1.2.3", b"the build an earlier attempt published")
    before = runner.tags()
    done = publish(runner, ["1.2.3"])
    assert done.returncode == 1
    assert runner.tags() == before
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert errors == [
        f"::error::ghcr.io/owner/repo:1.2.3 is {before['1.2.3']} already, not this run's build"
        f" {digest(runner.built)}: a full version is published once; to finish the run that"
        " published it, re-run only its failed jobs"
    ]


def test_a_long_listing_with_the_version_first_still_finds_it(tmp_path):
    """Every push to main adds a tag; a listing longer than a pipe's buffer, the version at its
    top, must still read as "there", never as absent (which would push the version again)."""
    runner = Runner(tmp_path)
    runner.put("1.2.3", b"the build an earlier attempt published")
    listing = tmp_path / "listing"
    listing.write_text("1.2.3\n" + "".join(f"sha-{n:07x}\n" for n in range(20000)))
    oras = runner.bin / "oras"
    oras.write_text(oras.read_text().replace('    ls "$REG" ;;', f'    cat "{listing}" ;;'))
    done = publish(runner, ["1.2.3"])
    assert done.returncode == 1 and "a full version is published once" in done.stdout
    assert not any(call.startswith("cp ") for call in runner.oras_calls())


def test_a_version_that_reads_back_other_bytes_stops_publish_in_one_line(tmp_path):
    runner = Runner(tmp_path)
    done = publish(runner, ["1.2.3"], FETCH_OTHER="1")
    assert done.returncode == 1
    other = digest(b"other bytes")
    assert done.stdout.strip().splitlines()[-1] == (
        f"::error::ghcr.io/owner/repo:1.2.3 is {other}, not the build's {digest(runner.built)}"
    )
    assert (tmp_path / "github_output").read_text() == ""


def test_tags_that_cannot_be_listed_push_nothing(tmp_path):
    runner = Runner(tmp_path)
    done = publish(runner, ["1.2.3"], TAGS_FAIL="1")
    assert done.returncode == 1 and runner.tags() == {}
    assert "could not be listed" in done.stdout


def test_main_is_pushed_as_its_commit_and_main_moves(tmp_path):
    """sha-<commit> is written once, as a version is, so main asks for the tags too; `main`
    itself moves with every push."""
    runner = Runner(tmp_path)
    runner.put("main", b"yesterday's main")
    done = publish(
        runner, ["main", "sha-ccccccc"], GITHUB_REF_TYPE="branch", GITHUB_REF_NAME="main"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    built = digest(runner.built)
    assert runner.tags() == {"main": built, "sha-ccccccc": built}
    calls = runner.oras_calls()
    assert calls[0] == "repo tags ghcr.io/owner/repo"
    assert f"cp --from-oci-layout layout@{built} ghcr.io/owner/repo:sha-ccccccc" in calls


# --- aliases: decided when they move, from what is released ---------------------------------------

ALIAS_CASES = {
    # name: (released tags, full versions in the registry, the aliases they give)
    "one release": (["v0.1.0"], ["0.1.0"], {"latest": "0.1.0", "0.1": "0.1.0"}),
    "a patch of an older line": (
        ["v0.2.0", "v0.1.1", "v0.1.0"],
        ["0.1.0", "0.1.1", "0.2.0"],
        {"latest": "0.2.0", "0.1": "0.1.1", "0.2": "0.2.0"},
    ),
    "numbers, not text": (
        ["v0.10.0", "v0.9.12", "v0.9.2"],
        ["0.9.2", "0.9.12", "0.10.0"],
        {"latest": "0.10.0", "0.9": "0.9.12", "0.10": "0.10.0"},
    ),
    "a release whose image is not in the registry": (
        ["v0.2.0", "v0.1.0"],
        ["0.1.0"],
        {"latest": "0.1.0", "0.1": "0.1.0"},
    ),
    "an image with no release (its run failed after publish)": (
        ["v0.1.0"],
        ["0.1.0", "0.2.0"],
        {"latest": "0.1.0", "0.1": "0.1.0"},
    ),
    "other tags": (
        ["v0.1.0", "nightly", "v0.2"],
        ["0.1.0", "main"],
        {"latest": "0.1.0", "0.1": "0.1.0"},
    ),
    "nothing released": ([], ["main"], {}),
}


@pytest.mark.parametrize("case", ALIAS_CASES)
def test_release_check_says_where_the_aliases_go(case):
    released, published, expected = ALIAS_CASES[case]
    assert release_check.aliases(released, published) == expected


def run_aliases(runner: Runner, released: list[str]) -> subprocess.CompletedProcess:
    listed = runner.tmp / "released"
    listed.write_text("".join(f"{tag}\n" for tag in released))
    return runner.run(step("aliases", ALIAS)["run"], RELEASED=str(listed))


def manifest(version: str) -> bytes:
    return f"the index of {version}".encode()


@pytest.mark.parametrize(("listed", "moves"), [(999, True), (1000, False)])
def test_the_aliases_listing_s_cap_at_its_boundary(tmp_path, listed, moves):
    """gh is asked for at most 1,000 stable releases: 999 are all of them and the aliases move;
    at 1,000 a higher release may be missing, so the job stops, says why, and moves nothing."""
    runner = Runner(tmp_path)
    released = [f"v0.{n}.0" for n in range(listed)]
    runner.put("0.0.0", manifest("0.0.0"))
    runner.put(f"0.{listed - 1}.0", manifest(f"0.{listed - 1}.0"))
    runner.put("latest", manifest("0.0.0"))
    before = runner.tags()
    done = run_aliases(runner, released)
    if moves:
        assert done.returncode == 0, done.stdout + done.stderr
        assert runner.tags()["latest"] == digest(manifest(f"0.{listed - 1}.0"))
    else:
        assert done.returncode == 1
        assert runner.tags() == before
        assert done.stdout.strip().splitlines()[-1] == (
            "::error::gh listed 1000 releases, the most it asks for, so a higher one may be missing"
        )
    assert "--limit 1000 " in step("aliases", ALIAS)["run"]


@pytest.mark.parametrize("case", ALIAS_CASES)
def test_the_step_moves_the_aliases_where_release_check_says(tmp_path, case):
    released, published, expected = ALIAS_CASES[case]
    runner = Runner(tmp_path)
    for version in published:
        runner.put(version, manifest(version))
    runner.put("latest", manifest("an old latest")) if expected else None
    done = run_aliases(runner, released)
    assert done.returncode == 0, done.stdout + done.stderr
    tags = runner.tags()
    for alias, version in expected.items():
        assert tags[alias] == digest(manifest(version)), alias
    if not expected:
        assert "stay where they are" in done.stdout


def test_overlapping_releases_end_at_the_highest_in_either_order(tmp_path):
    """Two releases, v0.1.1 and v0.2.0, made about the same time; each run's aliases job runs
    after its own release, in either order, and before or after the other's release exists.
    Wherever it starts, the last to run leaves latest on 0.2.0 and 0.1 on 0.1.1."""
    final = {"latest": "0.2.0", "0.1": "0.1.1", "0.2": "0.2.0"}
    for first, second in (("v0.1.1", "v0.2.0"), ("v0.2.0", "v0.1.1")):
        (tmp_path / first).mkdir()
        runner = Runner(tmp_path / first)
        for version in ("0.1.0", "0.1.1", "0.2.0"):
            runner.put(version, manifest(version))
        # The first run's job runs when only its own release (and the old one) exists.
        assert run_aliases(runner, ["v0.1.0", first]).returncode == 0
        # The second runs after both; then the first runs again (a re-run, or one queued).
        for released in (["v0.1.0", first, second], ["v0.1.0", second, first]):
            assert run_aliases(runner, released).returncode == 0
            tags = runner.tags()
            assert {alias: tags[alias] for alias in final} == {
                alias: digest(manifest(version)) for alias, version in final.items()
            }
    assert (
        release_check.aliases(["v0.1.1", "v0.2.0", "v0.1.0"], ["0.1.0", "0.1.1", "0.2.0"]) == final
    )


def test_an_alias_already_right_is_left_alone(tmp_path):
    runner = Runner(tmp_path)
    runner.put("0.1.0", manifest("0.1.0"))
    runner.put("latest", manifest("0.1.0"))
    runner.put("0.1", manifest("0.1.0"))
    done = run_aliases(runner, ["v0.1.0"])
    assert done.returncode == 0
    assert not any(call.startswith("tag ") for call in runner.oras_calls())
    assert "latest is 0.1.0 already" in done.stdout


def test_the_aliases_job_asks_gh_for_releases_only_and_runs_one_at_a_time():
    job = JOBS["aliases"]
    run = step("aliases", ALIAS)["run"]
    assert (
        "gh release list --limit 1000 --exclude-drafts --exclude-pre-releases --json tagName"
        " --jq '.[].tagName'" in run
    )
    assert job["concurrency"] == {"group": "cairn-release-aliases", "cancel-in-progress": False}
    assert needs(job) == {"release_check", "release"}
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert [s.get("uses", "run").split("@")[0] for s in job["steps"]] == [
        "run",
        "docker/login-action",
        "run",
    ]
    comment = job_comment("aliases")
    for sentence in (
        "It decides from what exists when it runs, not from the tag that started it",
        "a newer one waiting replaces an older one waiting, which GitHub cancels, and the newer"
        " one, deciding later, covers its release too.",
        "A version's own release is made before this job, so a cancelled one leaves no version"
        " without its release.",
    ):
        assert sentence in comment, sentence


def test_the_new_shell_steps_say_pipefail_first():
    """GitHub's default shell is `bash -e {0}`, without pipefail: a failed oras before a pipe
    would go unseen."""
    for job, name in (("image_scan", RECEIVE), ("publish", PUSH), ("aliases", ALIAS)):
        assert step(job, name)["run"].startswith("set -o pipefail\n"), (job, name)
    assert "shell:" not in RAW_WORKFLOW
    assert re.search(r"oras manifest fetch .*\| sha256sum", step("aliases", ALIAS)["run"])
