"""Job `release` makes the release as a draft, compares the draft's title, notes and files with
the ones this run wrote and attested, publishes it with a request of its own and looks again; a
retry starts from whatever an earlier attempt of the same run left, and nothing the job runs
takes a release back.

The release's text carries a mark, `<!-- cairn-ci-cd run RUN_ID -->`, an HTML comment GitHub
does not show and its API returns in the release's `body`; a re-run of a job keeps its run's id.
The step lists every release of the repository, drafts included, and takes the one for its tag
as this run's only when it carries this run's mark and has this tag's pre-release flag. None:
it makes a draft with the files and looks again. This run's draft: when its title and notes
are the run's (test_ci_release_notes_title_unit) and it holds exactly the files this run
attested, names and bytes, the step publishes it, by its id, and looks again; when its files
are not those (an upload was cut) the step stops in one line that says what a person does:
delete it on the releases page and re-run the job. A published one must carry that title and
those notes and hold exactly those files, byte for byte, and is left as it is. A download
that fails is not a difference: the step says so and a re-run compares again. Anything else
stops the job in one sentence.

The third report's must-fix 2: the step used to delete its draft by id, and a person who
published it between the step's last look and that delete lost a public release. The fourth
report's must-fix 1: `gh release create`, given files and not asked for a draft, published its
own draft and deleted it by id when the publication's reply was an error, though GitHub had
published it. So the job's text issues no delete of a release or of a release's file, asks
gh for a draft, and publishes with one request that sets `draft` to false and says whether
the release is GitHub's latest (test_ci_release_latest_unit says which);
test_generated_release_retry_unit runs the orderings and the requests that fail. gh is a
stand-in that keeps the releases as a JSON file, runs the step's own --jq expression with jq,
performs a delete it is asked for (so one would show), can publish the draft between two
listings, and can list what was there before a write for some looks after it
(test_ci_release_listing_lag_unit). `sleep` is a stand-in too, so no test waits."""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_release_unit import files_run
from test_ci_workflow_unit import JOBS, rerun, stops

(CREATE,) = [s for s in JOBS["release"]["steps"] if s.get("name") == "Create the release"]
RUN_ID = "4242"
MARK = f"<!-- cairn-ci-cd run {RUN_ID} -->"
COMMIT = "1a2b3c4d" * 5  # the run's commit, which the tag names
MAIN_HEAD = "9f8e7d6c" * 5  # the default branch's head, another commit
OTHER = "5e5e5e5e" * 5  # a commit a person moves the tag to

GH = r"""#!/bin/bash
printf 'gh %s\n' "$*" >> "$CALLS"
# A person changes every release: $1 is what, as jq writes it for one release. A read by a
# release's id shows the change at once; with $PERSON_LISTED_FROM_LOOK the listing keeps what
# was there before it until that look after it.
by_hand() {
  if [ -n "$PERSON_LISTED_FROM_LOOK" ]; then
    cp "$EXISTING/releases.json" "$EXISTING/stale.json"
    echo "$PERSON_LISTED_FROM_LOOK" > "$EXISTING/stale.from"
    echo 0 > "$EXISTING/stale.looks"
  fi
  jq "map($1)" "$EXISTING/releases.json" > "$EXISTING/next.json"
  mv "$EXISTING/next.json" "$EXISTING/releases.json"
  # ... and with $PERSON_SHOWS_BEHIND a listing that is behind this job's write shows it too
  if [ -n "$PERSON_SHOWS_BEHIND" ] && [ -e "$EXISTING/behind.json" ]; then
    jq "map($1)" "$EXISTING/behind.json" > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/behind.json"
  fi
}
# A write is about to be done. GitHub's listing can lag behind it: when $1 is a number, the
# listing shows what is there now, before the write, until the $1-th look after it.
writing() {
  touch "$EXISTING/written"
  [ -n "$1" ] || return 0
  cp "$EXISTING/releases.json" "$EXISTING/behind.json"
  echo "$1" > "$EXISTING/behind.from"
  echo 0 > "$EXISTING/behind.looks"
}
# The bytes of release $1's file $2: $EXISTING/held/$1/$2 when a test gave that release its
# own, or else $EXISTING/assets/$2.
bytes_of() {
  if [ -e "$EXISTING/held/$1/$2" ]; then
    echo "$EXISTING/held/$1/$2"
  else
    echo "$EXISTING/assets/$2"
  fi
}
# The releases as GitHub answers a read by id: each asset with an id (its own, or ten times its
# release's id and then its place), a state (`uploaded` unless given), its size (the bytes it
# holds) and the time it was last updated (a fixed one unless given).
enriched() {
  local sizes='{}' rid name file
  while IFS=$'\t' read -r rid name; do
    file="$(bytes_of "$rid" "$name")"
    [ -e "$file" ] || continue
    sizes="$(jq -c --arg k "$rid/$name" --argjson n "$(wc -c < "$file" | tr -d ' ')" \
      '. + {($k): $n}' <<< "$sizes")"
  done < <(jq -r '.[] | .id as $r | .assets[] | [$r, .name] | @tsv' "$EXISTING/releases.json")
  jq --argjson sizes "$sizes" 'map(.id as $r | .assets |= (to_entries | map(.value + {
      id: (.value.id // ($r * 10 + .key)), state: (.value.state // "uploaded"),
      size: ($sizes["\($r)/\(.value.name)"] // .value.size),
      updated_at: (.value.updated_at // "2026-11-20T12:00:00Z")})))' "$EXISTING/releases.json"
}
# A person replaces the source archive of every release: a new asset, id 999, a new time, and
# other bytes of the same size.
replaced() {
  local name
  name="$(jq -r '[.[].assets[].name | select(endswith(".tar.gz"))][0] // empty' \
    "$EXISTING/releases.json")"
  [ -n "$name" ] || return 0
  jq --arg n "$name" 'map(.assets |= map(if .name == $n then
    {name: $n, size: .size, id: 999, updated_at: "2026-11-20T12:59:59Z"} else . end))' \
    "$EXISTING/releases.json" > "$EXISTING/next.json"
  mv "$EXISTING/next.json" "$EXISTING/releases.json"
  tr 'a-z' 'A-Z' < "$EXISTING/assets/$name" > "$EXISTING/next.bytes"
  mv "$EXISTING/next.bytes" "$EXISTING/assets/$name"
  rm -rf "$EXISTING"/held/*/"$name"
}
# The git tag: $EXISTING/tag holds the commit it names, nothing once it is gone, and
# $EXISTING/tag.object the id of the tag object when it is annotated. Just before the
# $TAG_AT_READ-th read of it a person sets it to $TAG_BECOMES (`gone` deletes it).
retag() {
  if [ "$1" = gone ]; then : > "$EXISTING/tag"; else printf '%s' "$1" > "$EXISTING/tag"; fi
  rm -f "$EXISTING/tag.object"
}
case "$1 $2" in
  "api -H")  # a read of the tag, with the header that asks for an id alone
    [ "$3" = "Accept: application/vnd.github.sha" ] && [ $# = 4 ] || exit 2
    read=$(($(cat "$EXISTING/tag.reads" 2>/dev/null || echo 0) + 1))
    echo "$read" > "$EXISTING/tag.reads"
    [ "$read" != "$TAG_FAILS_AT_READ" ] || {
      echo '{"message":"Server Error"}'; echo "gh: Server Error (HTTP 502)" >&2; exit 1
    }
    [ "$read" != "$TAG_AT_READ" ] || retag "$TAG_BECOMES"
    commit="$(cat "$EXISTING/tag")"
    case "$4" in
      # GitHub's "Get a commit": the commit a tag names, an annotated tag's too
      "repos/owner/repo/commits/tags/$GITHUB_REF_NAME") answer="$commit" ;;
      # "Get a reference": what the ref points to, an annotated tag's object
      "repos/owner/repo/git/ref/tags/$GITHUB_REF_NAME")
        answer="$(cat "$EXISTING/tag.object" 2>/dev/null || echo "$commit")" ;;
      *) exit 2 ;;
    esac
    if [ -z "$commit" ]; then
      echo "{\"message\":\"No commit found for SHA: tags/$GITHUB_REF_NAME\",\"status\":\"422\"}"
      echo "gh: No commit found for SHA: tags/$GITHUB_REF_NAME (HTTP 422)" >&2
      exit 1
    fi
    printf '%s' "$answer" ;;
  "api --paginate")
    [ -z "$LIST_FAILS" ] || { echo "HTTP 502" >&2; exit 1; }
    if [ -n "$LIST_FAILS_AFTER_A_WRITE" ] && [ -e "$EXISTING/written" ]; then
      echo "HTTP 502" >&2; exit 1
    fi
    listed="$(cat "$EXISTING/listed" 2>/dev/null || echo 0)"
    echo $((listed + 1)) > "$EXISTING/listed"
    jq_expr=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--jq" ]; then jq_expr="$2"; fi
      shift
    done
    shown="$EXISTING/releases.json"
    if [ -e "$EXISTING/behind.json" ]; then
      look=$(($(cat "$EXISTING/behind.looks") + 1))
      echo "$look" > "$EXISTING/behind.looks"
      [ "$look" -ge "$(cat "$EXISTING/behind.from")" ] || shown="$EXISTING/behind.json"
    fi
    # ... and behind a person's change: the test's own stale.json, or by_hand's.
    if [ -e "$EXISTING/stale.json" ]; then
      look=$(($(cat "$EXISTING/stale.looks") + 1))
      echo "$look" > "$EXISTING/stale.looks"
      [ "$look" -ge "$(cat "$EXISTING/stale.from")" ] || shown="$EXISTING/stale.json"
    fi
    # The listing can be behind the uploads too: the draft this attempt made is listed with
    # the file names $NAMES_LISTED_AT_FIRST (a JSON list), at every look after its creation
    # or, with $NAMES_LISTED_FOR_LOOKS, at that many and then with the names it holds. And a
    # person can change the draft just before the $AT_LOOK-th look after its creation.
    if [ -e "$EXISTING/made.looks" ]; then
      look=$(($(cat "$EXISTING/made.looks") + 1))
      echo "$look" > "$EXISTING/made.looks"
      [ "$look" != "$AT_LOOK" ] || by_hand "$AT_LOOK_CHANGE"
    fi
    if [ -n "$NAMES_LISTED_AT_FIRST" ] && [ -e "$EXISTING/made.looks" ]; then
      if [ -z "$NAMES_LISTED_FOR_LOOKS" ] || [ "$look" -le "$NAMES_LISTED_FOR_LOOKS" ]; then
        jq --argjson names "$NAMES_LISTED_AT_FIRST" 'map(if .id == 88 then
          .assets = ($names | map({name: ., size: 1})) else . end)' "$shown" \
          > "$EXISTING/partial.json"
        shown="$EXISTING/partial.json"
      fi
    fi
    jq -r "$jq_expr" "$shown"
    if [ -n "$PUBLISHED_AFTER_FIRST_LISTING" ]; then  # a person publishes the draft meanwhile
      jq 'map(.draft = false)' "$EXISTING/releases.json" > "$EXISTING/next.json"
      mv "$EXISTING/next.json" "$EXISTING/releases.json"
    fi ;;
  "api repos/"*)  # a release read by its id: what is there now, whatever the listing shows
    id="${2##*/}"
    read=$(($(cat "$EXISTING/reads" 2>/dev/null || echo 0) + 1))
    echo "$read" > "$EXISTING/reads"
    [ "$read" != "$FAILS_AT_READ" ] || { echo "gh: Bad Gateway (HTTP 502)" >&2; exit 1; }
    [ "$read" != "$DELETED_BEFORE_READ" ] || echo "[]" > "$EXISTING/releases.json"
    [ "$read" != "$AT_READ" ] || by_hand "$AT_READ_CHANGE"
    if ! jq -e "any(.id == $id)" "$EXISTING/releases.json" > /dev/null; then
      echo '{"message":"Not Found","status":"404"}'  # as gh does: the reply, then its own line
      echo "gh: Not Found (HTTP 404)" >&2
      exit 1
    fi
    [ "$3" = --jq ] && [ $# = 4 ] || exit 2
    enriched | jq -r "map(select(.id == $id)) | .[0] | $4" ;;
  "api --allow-escape-sequences")  # a release's file by its asset id: the bytes it holds now
    [ "$3 $4" = "-H Accept: application/octet-stream" ] && [ $# = 5 ] || exit 2
    aid="${5##*/}"
    fetch=$(($(cat "$EXISTING/fetches" 2>/dev/null || echo 0) + 1))
    echo "$fetch" > "$EXISTING/fetches"
    [ "$fetch" != "$FETCH_FAILS" ] || { echo "gh: Bad Gateway (HTTP 502)" >&2; exit 1; }
    [ "$fetch" != "$REPLACED_BEFORE_FETCH" ] || replaced
    found="$(enriched | jq -r --argjson aid "$aid" \
      'first(.[] | . as $r | .assets[] | select(.id == $aid) | [$r.id, $r.draft, .name] | @tsv)')"
    IFS=$'\t' read -r rid draft name <<< "$found"
    if [ -z "$found" ] || [ ! -e "$(bytes_of "$rid" "$name")" ]; then
      echo '{"message":"Not Found","status":"404"}'
      echo "gh: Not Found (HTTP 404)" >&2
      exit 1
    fi
    # DOWNLOAD_FAILS names the release whose files cannot be had: the draft, or the published
    if { [ "$DOWNLOAD_FAILS" = draft ] && [ "$draft" = true ]; } ||
      { [ "$DOWNLOAD_FAILS" = published ] && [ "$draft" = false ]; }; then
      echo "gh: Bad Gateway (HTTP 502)" >&2
      exit 1
    fi
    cat "$(bytes_of "$rid" "$name")"
    [ "$fetch" != "$REPLACED_AFTER_FETCH" ] || replaced ;;
  "api -X")
    [ "$3" = DELETE ] || exit 2
    id="${4##*/}"
    jq "map(select(.id != $id))" "$EXISTING/releases.json" > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/releases.json" ;;
  "api --method")  # the publication: PATCH of a release's id, draft=false
    [ "$3" = PATCH ] && [ "$5 $6 $7" = "--silent -F draft=false" ] || exit 2
    id="${4##*/}"
    # a person changes it after the job compared it, before the request arrives
    [ -z "$CHANGED_BEFORE_PUBLICATION" ] || by_hand "$CHANGED_BEFORE_PUBLICATION"
    [ -z "$TAG_BEFORE_PUBLICATION" ] || retag "$TAG_BEFORE_PUBLICATION"
    # ... or publishes a release of another tag (a JSON release), by hand
    if [ -n "$ADDED_BEFORE_PUBLICATION" ]; then
      jq --argjson r "$ADDED_BEFORE_PUBLICATION" '. + [$r]' "$EXISTING/releases.json" \
        > "$EXISTING/next.json"
      mv "$EXISTING/next.json" "$EXISTING/releases.json"
    fi
    jq -e "any(.id == $id)" "$EXISTING/releases.json" > /dev/null || exit 1  # HTTP 404
    writing "$PUBLISHED_SHOWN_FROM_LOOK"
    jq "map(if .id == $id then .draft = false else . end)" "$EXISTING/releases.json" \
      > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/releases.json"
    # A release published while its tag is gone gets the tag again, where the creation's
    # target says, or on the default branch's head ("Default: the repository's default
    # branch", GitHub's REST reference; that it is the target the draft was made with is
    # assumed, not measured)
    if [ ! -s "$EXISTING/tag" ]; then
      if [ -s "$EXISTING/target" ]; then
        cp "$EXISTING/target" "$EXISTING/tag"
      else
        printf '%s' "$MAIN_HEAD" > "$EXISTING/tag"
      fi
      touch "$EXISTING/tag.made"
    fi
    [ -z "$TAG_ONCE_PUBLIC" ] || retag "$TAG_ONCE_PUBLIC"
    # a person deletes it as soon as it is public, or changes it
    [ -z "$DELETED_ONCE_PUBLIC" ] || echo "[]" > "$EXISTING/releases.json"
    [ -z "$CHANGED_ONCE_PUBLIC" ] || by_hand "$CHANGED_ONCE_PUBLIC" ;;
  "release download")  # what the step did before, kept to show it: the files of the tag's
    # published release, or else of its newest draft (shared/fetch.go: "a published repository
    # release by its tagName, or a draft release by its pending tag name")
    dir=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--dir" ]; then dir="$2"; fi
      shift
    done
    [ ! -e "$dir" ] || exit 1  # gh keeps a file that is there, and the step gives a new place
    mkdir -p "$dir"
    rid="$(jq -r --arg t "$GITHUB_REF_NAME" '[.[] | select(.tag_name == $t)] |
      ((map(select(.draft | not)) | .[0]) // (sort_by(.id) | .[-1])) | .id // empty' \
      "$EXISTING/releases.json")"
    [ -n "$rid" ] || exit 1
    jq -r --argjson r "$rid" '.[] | select(.id == $r) | .assets[].name' "$EXISTING/releases.json" |
      while read -r name; do
        [ ! -e "$(bytes_of "$rid" "$name")" ] || cp "$(bytes_of "$rid" "$name")" "$dir/$name"
      done ;;
  "release create")  # the release asked for, as GitHub would list it, and its files
    tag="$3"; draft=false; pre=false; title=null; names=()
    # --verify-tag: the CLI asks GitHub whether the tag exists, and stops when it does not
    if [[ " $* " = *" --verify-tag "* ]] && [ ! -s "$EXISTING/tag" ]; then
      echo "tag $tag doesn't exist in the repo owner/repo, aborting due to --verify-tag flag" >&2
      exit 1
    fi
    shift 3
    while [ $# -gt 0 ]; do
      case "$1" in
        --draft) draft=true ;;
        --prerelease) pre=true ;;
        --target) printf '%s' "$2" > "$EXISTING/target"; shift ;;
        --notes-file) cp "$2" "$EXISTING/body"; shift ;;
        --title) title="$(jq -n --arg title "$2" '$title')"; shift ;;  # none given: no name
        --*) ;;
        *) cp "$1" "$EXISTING/assets/"; names+=("$1") ;;
      esac
      shift
    done
    writing "$MADE_SHOWN_FROM_LOOK"
    echo 0 > "$EXISTING/made.looks"
    jq --arg tag "$tag" --rawfile body "$EXISTING/body" --argjson draft "$draft" \
      --argjson pre "$pre" --argjson title "$title" '. + [{id: 88, tag_name: $tag, name: $title,
        draft: $draft, prerelease: $pre,
        body: $body, assets: ($ARGS.positional | map({name: ., size: 1}))}]' \
      "$EXISTING/releases.json" --args "${names[@]}" > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/releases.json"
    # a person changes the draft as soon as it is made, deletes it, or makes one more
    [ -z "$CHANGED_ONCE_MADE" ] || by_hand "$CHANGED_ONCE_MADE"
    [ -z "$DELETED_ONCE_MADE" ] || echo "[]" > "$EXISTING/releases.json"
    [ -z "$TAG_ONCE_MADE" ] || retag "$TAG_ONCE_MADE"
    if [ -n "$ANOTHER_ONCE_MADE" ]; then
      jq '. + [.[-1] + {id: 99}]' "$EXISTING/releases.json" > "$EXISTING/next.json"
      mv "$EXISTING/next.json" "$EXISTING/releases.json"
    fi ;;
  *) exit 2 ;;
esac
"""


def files_of(version: str) -> dict[str, bytes]:
    """What `package` hands on: the two files, the text, and SHA256SUMS over the three, as its
    step writes it."""
    made = {
        f"cairn_memory-{version}-py3-none-any.whl": b"a wheel",
        f"cairn_memory-{version}.tar.gz": b"a source archive",
        "notes.md": b"the notes\n",
    }
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in made.items())
    return {**made, "SHA256SUMS": sums.encode()}


def setup(tmp_path: Path, tag: str, existing, assets: dict | None = None):
    version = tag[1:].split("-rc.")[0]
    release = tmp_path / "release"
    release.mkdir()
    for name, data in files_of(version).items():
        (release / name).write_bytes(data)
    there = tmp_path / "existing"
    (there / "assets").mkdir(parents=True)
    listed = [] if existing is None else existing if isinstance(existing, list) else [existing]
    (there / "releases.json").write_text(json.dumps(listed))
    for name, data in (assets or {}).items():
        (there / "assets" / name).write_bytes(data)
    (there / "tag").write_text(COMMIT)  # the tag names the run's commit
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(GH)
    (bin_ / "gh").chmod(0o755)
    # No test waits: `sleep` is the test's, keeps its call with gh's and returns at once.
    (bin_ / "sleep").write_text('#!/bin/sh\nprintf \'sleep %s\\n\' "$*" >> "$CALLS"\n')
    (bin_ / "sleep").chmod(0o755)
    return bin_, there, release


def attempt(tmp_path: Path, tag: str, candidate: str, stale=None, **env: str):
    """One attempt of the step on what `setup` left and the attempts before it changed; the
    calls are this attempt's. STALE is (releases, look): what the listing shows in place of
    what is there, until that look of this attempt (None: at every look)."""
    calls = tmp_path / "calls"
    calls.write_text("")
    for downloaded in ("draft", "published"):  # a re-run's runner is a new one
        shutil.rmtree(tmp_path / downloaded, ignore_errors=True)
    # ... and it comes later, when the listing has caught up with every write
    there = tmp_path / "existing"
    for lag in ("written", "behind.json", "behind.from", "behind.looks", "made.looks", "reads"):
        (there / lag).unlink(missing_ok=True)
    (there / "tag.reads").unlink(missing_ok=True)
    (there / "fetches").unlink(missing_ok=True)
    for lag in ("stale.json", "stale.from", "stale.looks"):
        (there / lag).unlink(missing_ok=True)
    if stale is not None:
        listed, look = stale
        (there / "stale.json").write_text(json.dumps(listed))
        (there / "stale.from").write_text(str(10**6 if look is None else look))
        (there / "stale.looks").write_text("0")
    done = run_step(
        CREATE["run"],
        cwd=tmp_path / "release",
        env={
            "PATH": f"{tmp_path / 'bin'}:{SYSTEM}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": tag,
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_SHA": COMMIT,
            "MAIN_HEAD": MAIN_HEAD,
            "CANDIDATE": candidate,
            "CALLS": str(calls),
            "EXISTING": str(tmp_path / "existing"),
            "GH_REPO": "owner/repo",
            **env,
        },
    )
    return done, calls.read_text().splitlines()


def run(tmp_path: Path, tag: str, candidate: str, existing=None, assets=None, **env: str):
    setup(tmp_path, tag, existing, assets)
    return attempt(tmp_path, tag, candidate, **env)


def held(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / "existing" / "releases.json").read_text())


def by_hand(tmp_path: Path, what: str) -> None:
    """A person, on the releases page: publishes every draft (GitHub keeps its id) or deletes
    every release."""
    after = [{**r, "draft": False} for r in held(tmp_path)] if what == "publish" else []
    (tmp_path / "existing" / "releases.json").write_text(json.dumps(after))


def made(calls: list[str]) -> list[str]:
    return [line for line in calls if line.startswith("gh release create")]


def writes(calls: list[str]) -> list[str]:
    kinds = ("gh api -X", "gh api --method", "gh release create")
    return [line for line in calls if line.startswith(kinds)]


def made_earlier(tag: str, prerelease: bool, draft: bool = False, names=None, body=None) -> dict:
    version = tag[1:].split("-rc.")[0]
    attached = [n for n in files_of(version) if n != "notes.md"] if names is None else names
    # As GET repos/OWNER/REPO/releases lists one (GitHub's REST reference), with the title
    # and the text the step gives the release it makes.
    return {
        "id": 77,
        "tag_name": tag,
        "name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "body": f"the notes\n\n{MARK}\n" if body is None else body,
        "assets": [{"name": n, "size": 1} for n in attached],
    }


def attached(tag: str) -> dict[str, bytes]:
    version = tag[1:].split("-rc.")[0]
    return {n: d for n, d in files_of(version).items() if n != "notes.md"}


def the_error(done) -> str:
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert error.count(". ") == 0
    return error


LISTING = "gh api --paginate repos/owner/repo/releases?per_page=100 --jq "
READ = "gh api repos/owner/repo/releases/{id} --jq "  # one release, by its id
# The tag, read as the commit it names (test_ci_release_tag_unit has why and where).
TAG_READ = "gh api -H Accept: application/vnd.github.sha repos/owner/repo/commits/tags/{tag}"
# One file of a release, by its asset id (test_ci_release_assets_unit has why).
FETCH = (
    "gh api --allow-escape-sequences -H Accept: application/octet-stream"
    " repos/owner/repo/releases/assets/{aid}"
)


def fetches(rid: int) -> list[str]:
    """The three fetches of release RID's files, in the order the step names the files: the
    stand-in gives an asset the id ten times its release's and then its place."""
    return [FETCH.format(aid=rid * 10 + n) for n in range(3)]


BOTH = [
    ("v1.2.3", "", "", " -f make_latest=true"),
    ("v1.2.3-rc.1", "true", " --prerelease --latest=false", " -f make_latest=false"),
]


def untagged(calls: list[str]) -> list[str]:
    """CALLS without the reads of the tag, for a test of what the step does with the release:
    test_ci_release_tag_unit says where the tag's reads stand."""
    return [line for line in calls if not line.startswith(TAG_READ.split("{")[0])]


def looks(calls: list[str]) -> list[str]:
    """CALLS, each listing and each read by id cut to where its --jq expression starts."""
    cut = []
    for line in calls:
        if line.startswith(LISTING):
            line = LISTING
        elif line.startswith("gh api repos/owner/repo/releases/"):
            line = line[: line.index(" --jq ") + len(" --jq ")]
        cut.append(line)
    return cut


@pytest.mark.parametrize(("tag", "candidate", "flags", "latest"), BOTH)
def test_no_release_yet_makes_a_draft_compares_it_publishes_it_and_looks_again(
    tmp_path, tag, candidate, flags, latest
):
    done, calls = run(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    read, tag_read = READ.format(id=88), TAG_READ.format(tag=tag)
    assert looks(calls) == [
        tag_read,
        LISTING,
        f"gh release create {tag} --draft --verify-tag --target {COMMIT} --title {tag}"
        f" --notes-file {tmp_path}/body.md{flags} cairn_memory-1.2.3-py3-none-any.whl"
        " cairn_memory-1.2.3.tar.gz SHA256SUMS",
        LISTING,
        read,
        *fetches(88),
        read,
        tag_read,
        f"gh api --method PATCH repos/owner/repo/releases/88 --silent -F draft=false{latest}",
        LISTING,
        read,
        *fetches(88),
        read,
        tag_read,
    ]
    (made_,) = held(tmp_path)
    assert (made_["draft"], made_["prerelease"]) == (False, candidate == "true")
    assert (made_["name"], made_["body"]) == (tag, f"the notes\n\n{MARK}\n")
    # notes.md itself is one of the attested files, so it is left as it is.
    assert (tmp_path / "release" / "notes.md").read_bytes() == b"the notes\n"
    assert f"The GitHub release {tag} is this run's and is published" in done.stdout


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
def test_this_run_s_published_release_is_recognised_and_left_as_it_is(tmp_path, tag, candidate):
    existing = made_earlier(tag, candidate == "true")
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == []
    assert f"The GitHub release {tag} is this run's" in done.stdout
    assert [line for line in calls if line.startswith(FETCH[:20])] == fetches(77)


DRAFT = (
    "::error::read by its id, the GitHub release for {tag} is this run's draft and does not"
    " hold exactly the files this run attested: {why}; this job neither deletes nor publishes"
    " it; delete it on the repository's releases page and re-run this job, which makes the"
    " release again"
)


def unfinished(error: str, tag: str) -> bool:
    """ERROR is the line of this run's draft without exactly its files, whichever file or names
    it gives (test_ci_release_lines_unit has the two kinds)."""
    head, tail = DRAFT.format(tag=tag, why="\0").split("\0")
    return error.startswith(head) and error.endswith(tail)


CUT_OFF = {"cairn_memory-1.2.3.tar.gz": b"a sou"}


@pytest.mark.parametrize(("tag", "candidate", "flags", "latest"), BOTH)
def test_this_run_s_draft_with_exactly_this_run_s_files_is_published_by_its_id(
    tmp_path, tag, candidate, flags, latest
):
    """What a retry does with the draft an attempt left whole, and what the first attempt does
    with the draft it has just made: compare, publish with the one request, look again."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    read, tag_read = READ.format(id=77), TAG_READ.format(tag=tag)
    assert looks(calls) == [
        tag_read,
        LISTING,
        read,
        *fetches(77),
        read,
        tag_read,
        f"gh api --method PATCH repos/owner/repo/releases/77 --silent -F draft=false{latest}",
        LISTING,
        read,
        *fetches(77),
        read,
        tag_read,
    ]
    assert held(tmp_path) == [{**existing, "draft": False}]


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize(
    "names",
    [["SHA256SUMS"], [], ["cairn_memory-1.2.3.tar.gz"], [*attached("v1.2.3"), "other.txt"]],
    ids=["one", "none", "cut-off", "one more"],
)
def test_this_run_s_draft_with_other_names_stops_the_job_which_reads_and_writes_nothing(
    tmp_path, tag, candidate, names
):
    """A file cut off mid-upload, which GitHub may not even serve, is never downloaded: the
    job looks, reads the draft by its id for the names it holds, stops, and leaves the draft
    as it is."""
    existing = made_earlier(tag, candidate == "true", draft=True, names=names)
    done, calls = run(tmp_path, tag, candidate, existing, CUT_OFF)
    assert done.returncode == 1
    assert looks(calls) == [TAG_READ.format(tag=tag), LISTING, READ.format(id=77)]
    assert unfinished(the_error(done), tag)
    assert held(tmp_path) == [existing]


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize(
    ("assets", "fetched"),
    [
        ({**attached("v1.2.3"), **CUT_OFF}, 1),
        ({**attached("v1.2.3"), "SHA256SUMS": attached("v1.2.3")["SHA256SUMS"][:70]}, 2),
        ({**attached("v1.2.3"), "cairn_memory-1.2.3.tar.gz": b"A SOURCE ARCHIVE"}, 2),
    ],
    ids=["a file cut off", "the checksums cut off", "other bytes of the same size"],
)
def test_this_run_s_draft_with_the_names_and_other_bytes_is_not_published(
    tmp_path, tag, candidate, assets, fetched
):
    """The names read are this run's, and a file is not: of another size, which the read by id
    shows, so it is not fetched, or of other bytes, which its fetch shows. The step fetches the
    files in order up to that one and no further; the draft stays a draft."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    done, calls = run(tmp_path, tag, candidate, existing, assets)
    assert done.returncode == 1
    assert looks(calls) == [
        TAG_READ.format(tag=tag),
        LISTING,
        READ.format(id=77),
        *fetches(77)[:fetched],
    ]
    assert unfinished(the_error(done), tag)
    assert held(tmp_path) == [existing]


def test_a_draft_a_person_publishes_while_the_job_compares_it_is_what_the_job_would_make_it(
    tmp_path,
):
    """The listing showed a draft and a person publishes it a moment later: the read by its id
    shows it published, and the job ends as on a release it found published, with nothing to
    write. (A draft published between that read and the publication, which then changes
    nothing, and one deleted or changed: test_generated_release_retry_unit.)"""
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), PUBLISHED_AFTER_FIRST_LISTING="1"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == []
    assert held(tmp_path) == [{**existing, "draft": False}]


# --- what the line tells a person to do, done; and what the job does if they publish instead ----


def left_a_draft(tmp_path, tag: str, candidate: str, assets: dict[str, bytes]) -> None:
    existing = made_earlier(tag, candidate == "true", draft=True, names=list(assets))
    done, calls = run(tmp_path, tag, candidate, existing, assets)
    assert done.returncode == 1 and writes(calls) == []
    assert unfinished(the_error(done), tag)


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
def test_the_draft_a_person_deletes_is_made_again_by_the_re_run(tmp_path, tag, candidate):
    left_a_draft(tmp_path, tag, candidate, CUT_OFF)
    by_hand(tmp_path, "delete")
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    create, publish = writes(calls)
    assert create.startswith(f"gh release create {tag} --draft --verify-tag")
    assert create.endswith(
        "cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz SHA256SUMS"
    )
    assert publish.startswith("gh api --method PATCH repos/owner/repo/releases/88 ")
    (made_,) = held(tmp_path)
    assert (made_["id"], made_["draft"]) == (88, False)


SUMS_CUT = attached("v1.2.3")["SHA256SUMS"].splitlines(keepends=True)


@pytest.mark.parametrize(
    ("assets", "said"),
    [
        (
            {**attached("v1.2.3"), **CUT_OFF},
            "cairn_memory-1.2.3.tar.gz is not the file this run attested",
        ),
        (
            {n: d for n, d in attached("v1.2.3").items() if n != "SHA256SUMS"},
            "its files are [cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz], not",
        ),
        # Both files whole and SHA256SUMS cut off after their two lines: a person who checked
        # each file against it would have seen nothing wrong.
        (
            {**attached("v1.2.3"), "SHA256SUMS": b"".join(SUMS_CUT[:2])},
            "SHA256SUMS is not the file this run attested",
        ),
    ],
    ids=["a file cut off", "a file missing", "the checksums cut off"],
)
def test_a_draft_published_by_hand_that_is_not_whole_is_a_public_release_the_job_cannot_finish(
    tmp_path, assets, said
):
    """Why the row says not to publish it: the job's comparison is refused in the sentence every
    release this run cannot finish gets, the release is public by then, and nothing is deleted."""
    left_a_draft(tmp_path, "v1.2.3", "", assets)
    by_hand(tmp_path, "publish")
    before = held(tmp_path)
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == before
    error = the_error(done)
    assert error.startswith(REFUSED.format(tag="v1.2.3")) and error.endswith(NEXT)
    assert said in error


# --- the job's own text issues no delete, and one write to a release that exists ----------------

FORBIDDEN = {
    "the API's delete": r"DELETE",
    "gh release delete or delete-asset": r"\bgh\s+release\s+delete",
    "an upload that replaces a file": r"--clobber",
    "an upload onto a release that exists": r"\bgh\s+release\s+upload\b",
    "an edit of a release": r"\bgh\s+release\s+edit\b",
    "another program that speaks HTTP": r"\b(curl|wget)\b",
}
# Every line of the job that names gh, whole, in the order the text has them: the first step's
# four, which find gh and read its version, and say so when it is missing or too old; then the
# listing, the read of the tag as the commit it names and the line it stops on, which points
# at gh's own words, the read of one release by its id and the fetch of one of its files by
# its asset id (reads: no method, field or input, the tag's and the file's a header), the
# creation of a draft, and the publication, the one write to a release that exists. Any other
# line that names it is one too many.
GH_LINES = [
    "if ! command -v gh > /dev/null; then",
    'echo "::error::gh is not on this runner, so the GitHub release for $GITHUB_REF_NAME cannot'
    " be made; install GitHub CLI 2.102.0 or later on the runner CAIRN_RELEASE_RUNNER names, or"
    " unset it to use GitHub's\"",
    "version=\"$(gh --version | sed -n 's/^gh version"
    " \\([0-9][0-9]*\\.[0-9][0-9]*\\.[0-9][0-9]*\\)\\( .*\\)\\{0,1\\}$/\\1/p')\"",
    'echo "::error::gh on this runner is ${version:-of a version it does not print as three'
    " plain numbers}, before"
    " 2.102.0, the version in whose source this job's handling of a draft was read, so it is not"
    " trusted with the release; update GitHub CLI on the runner CAIRN_RELEASE_RUNNER names, or"
    " unset it to use GitHub's\"",
    'gh api --paginate "repos/$GH_REPO/releases?per_page=100" --jq ".[] | if .tag_name =='
    ' \\"$GITHUB_REF_NAME\\" then ($fields) elif .draft or .prerelease then empty else'
    ' \\"published \\" + .tag_name end"',
    "if ! at=\"$(gh api -H 'Accept: application/vnd.github.sha'"
    ' "repos/$GH_REPO/commits/tags/$GITHUB_REF_NAME" 2> "$RUNNER_TEMP/tag.err")" || ! [[ "$at"'
    " =~ ^[0-9a-f]{40}$ ]]; then",
    'echo "::error::the tag $GITHUB_REF_NAME could not be read as a commit of the repository (it'
    " is gone, or GitHub did not answer: gh's own words, if any, are above), so this job writes"
    " nothing more for its release and does not call it finished; re-run this job if the"
    " repository's tags page shows $GITHUB_REF_NAME at this run's commit $GITHUB_SHA, and"
    ' otherwise publish nothing for it"',
    'if ! now="$(gh api "repos/$GH_REPO/releases/$id" --jq "select(.tag_name =='
    ' \\"$GITHUB_REF_NAME\\") | ($fields), $details" 2> "$RUNNER_TEMP/read.err")"; then',
    "gh api --allow-escape-sequences -H 'Accept: application/octet-stream'"
    ' "repos/$GH_REPO/releases/assets/$asset" > "$RUNNER_TEMP/$2/$name" || unread',
    'gh release create "$GITHUB_REF_NAME" --draft --verify-tag --target "$GITHUB_SHA" --title'
    ' "$GITHUB_REF_NAME" --notes-file "$RUNNER_TEMP/body.md" "${candidate[@]}" "${files[@]}"',
    'gh api --method PATCH "repos/$GH_REPO/releases/$id" --silent -F draft=false "${latest[@]}"',
]
NAMES_GH = re.compile(r"(^|[^\w.-])gh(?![\w.-])")


def commands(job: dict) -> list[str]:
    """The lines of JOB's shell steps, comments too, a line continued with a backslash made
    one line."""
    text = "\n".join(re.sub(r"\s*\\\n\s*", " ", s.get("run", "")) for s in job["steps"])
    return [line.strip() for line in text.splitlines()]


def can_delete(job: dict) -> list[str]:
    lines = commands(job)
    text = "\n".join(lines)
    found = [what for what, shape in FORBIDDEN.items() if re.search(shape, text)]
    return found + [
        f"another line names gh: {line}"
        for line in lines
        if NAMES_GH.search(line) and line not in GH_LINES
    ]


def test_the_text_of_release_issues_no_delete_and_one_write_to_a_release_that_exists():
    """On the job's text, so the delete cannot come back unseen: no DELETE, no gh release
    delete, delete-asset, upload or edit, no --clobber, no curl; the lines that name gh are the
    listing, the read by id, the read of the tag, the fetch of a file by its asset id, the
    creation and the publication, each exactly as written here; and
    it runs no file. The publication sends `draft=false` and `make_latest`, false for a
    candidate and for a stable version what `is_latest` says, and nothing else, to the id the
    listing gave."""
    release = JOBS["release"]
    assert can_delete(release) == []
    assert [line for line in commands(release) if NAMES_GH.search(line)] == GH_LINES
    assert files_run(release) == []
    assert [s["uses"].split("@")[0] for s in release["steps"] if "uses" in s] == [
        "actions/download-artifact"
    ]
    run_ = CREATE["run"]
    assert re.findall(r"^ *(.*latest=.*)", run_, re.M) == [
        "latest=()",
        "candidate=(--prerelease --latest=false)",
        "latest=(-f make_latest=false)",
        '[ "$prerelease" = true ] || latest=(-f "make_latest=$(is_latest)")',
        'said="${latest[1]#make_latest=}"',
    ]
    assert (
        re.findall(r"\bid=", run_) == []
        and "read -r id draft pre ours titled notes names <<<" in run_
    )
    # What the check sees, each added to the job as it is: the delete the job had, in the
    # ways gh takes a method, and the other ways to remove or replace what a release holds.
    other = "another line names gh: "
    for added, seen in {
        'gh api -X DELETE "repos/$GH_REPO/releases/$id"': ["the API's delete", other],
        'gh api -XDELETE "repos/$GH_REPO/releases/$id"': ["the API's delete", other],
        'gh api --method=delete "repos/$GH_REPO/releases/$id"': [other],
        'gh api "repos/$GH_REPO/releases/$id" \\\n  -F draft=false': [other],
        'gh api --method PATCH "repos/$GH_REPO/releases/$id" --silent -F draft=false'
        ' -F prerelease=true "${latest[@]}"': [other],
        'gh api --method PATCH "repos/$GH_REPO/releases/$id/assets" --silent -F draft=false'
        ' "${latest[@]}"': [other],
        'gh api --paginate "repos/$GH_REPO/releases?per_page=100" --input body.json': [other],
        'curl -X DELETE "$url"': ["the API's delete", "another program that speaks HTTP"],
        'wget --method=delete "$url"': ["another program that speaks HTTP"],
        'gh release delete "$GITHUB_REF_NAME" --yes': ["gh release delete or delete-asset", other],
        'gh  release \\\n  delete-asset "$GITHUB_REF_NAME" SHA256SUMS': [
            "gh release delete or delete-asset",
            other,
        ],
        'gh release upload "$GITHUB_REF_NAME" SHA256SUMS': [
            "an upload onto a release that exists",
            other,
        ],
        'gh release create "$GITHUB_REF_NAME" --clobber': ["an upload that replaces a file", other],
        'gh release create "$GITHUB_REF_NAME" --verify-tag --title "$GITHUB_REF_NAME"'
        ' --notes-file "$RUNNER_TEMP/body.md" "${candidate[@]}" "${files[@]}"': [other],
        'gh release edit "$GITHUB_REF_NAME" --draft=false': ["an edit of a release", other],
        '"$(command -v gh)" release view "$GITHUB_REF_NAME"': [other],
        "# then gh removes it": [other],
    }.items():
        job = {"steps": [*release["steps"], {"name": "added", "run": added}]}
        got = can_delete(job)
        assert [other if what.startswith(other) else what for what in got] == seen, (added, got)


def test_gh_is_asked_for_a_draft_so_the_release_it_makes_is_never_its_own_to_delete():
    """Why the creation carries --draft. gh's source (cli/cli at v2.102.0, read on 2026-11-20,
    pkg/cmd/release/create/create.go) says of `gh release create`, lines 497 to 512:
    `hasAssets := len(opts.Assets) > 0`, `draftWhileUploading := false`,
    `if hasAssets && !opts.Draft {` ... `// Save the release initially as draft and publish it
    after all assets have finished uploading`, `draftWhileUploading = true`,
    `params["draft"] = true`; lines 533 to 541: `cleanupDraftRelease := func(err error) error {`
    `if !draftWhileUploading {` `return err` `}` and only then `deleteRelease(httpClient,
    baseRepo.RepoHost(), safeurl.NewImmutableSafeURL(newRelease.APIURL))`; lines 550 to 560:
    `err = shared.ConcurrentUpload(...)`, `if err != nil {` `return cleanupDraftRelease(err)`,
    and `if draftWhileUploading {` `rel, err := publishRelease(...)`, `if err != nil {`
    `return cleanupDraftRelease(err)`. `deleteRelease` (create/http.go, 208 to 213) is an HTTP
    DELETE of the address GitHub gave the release, which names its id, whatever its state by
    then. So without --draft a publication GitHub made and whose reply was an error was
    followed by the delete of the published release (the fourth report's must-fix 1); with
    --draft, `draftWhileUploading` stays false, nothing is published and the cleanup returns
    the error and deletes nothing, so a failed upload leaves the draft with the files that
    arrived. The publication is then the job's: `publishRelease` (http.go, 182 to 206) sends
    PATCH to the release's address with `"draft": false` and, when --latest was given,
    `"make_latest"`, and the job's line sends the same and has nothing after it to undo.
    The upload (pkg/cmd/release/shared/upload.go): `ConcurrentUpload` runs
    `uploadWithDelete` for each file (111 to 128), which deletes a file only under
    `if a.ExistingURL != nil && a.ExistingURL.String() != "" {` (141 to 146) and then
    `backoff.Retry(...)` of `uploadAsset` with `backoff.WithMaxRetries(bo, 3)`, again only when
    `shouldRetry`: a network error or `httpError.StatusCode >= 500` (130 to 155);
    `AssetsFromArgs`, which `create` takes its files from (create.go, 172), builds each
    `AssetForUpload` with Open, Size, Name, Label and MIMEType and no `ExistingURL` (upload.go,
    62 to 70), and nothing in create.go or create/http.go sets one. No line of upload.go
    deletes a release. So a failed upload leaves the draft, and leaves it public when a person
    published it meanwhile (test_generated_release_retry_unit, "a draft a person publishes
    while its files upload"). The download (pkg/cmd/release/download/download.go, 167):
    `shared.FetchRelease(ctx, httpClient, baseRepo, opts.TagName)`, which is "FetchRelease
    finds a published repository release by its tagName, or a draft release by its pending
    tag name" (shared/fetch.go, 182): beside `releases/tags/TAG` it runs `fetchDraftRelease`
    (237 to 270), a GraphQL query `release(tagName: $tagName)` for `DatabaseID` and `IsDraft`
    and then REST `releases/<DatabaseID>`, and it takes "a release found by either lookup"
    (204 to 218). It lists nothing, and needs a token GraphQL shows the draft to; the source
    says only that the draft lookup fails "when unauthenticated". Every asset is then fetched
    by its own API address with `Accept: application/octet-stream` (download.go, 198 to 208,
    243, 313), a draft's like any other. Nothing here can run gh's code: the test holds the
    flag on the job's line, the README's sentence, the step's own sentences, and that the
    sentence about gh deleting its release is gone."""
    create, publish = GH_LINES[-2:]
    assert re.search(r"^gh release create \S+ --draft ", create)
    assert create.endswith('"${files[@]}"')
    assert publish.startswith('gh api --method PATCH "repos/$GH_REPO/releases/$id" ')
    readme = " ".join(README.split())
    assert (
        "The job's own text issues no delete of a release or of a release's file and replaces no"
        " file, and `gh release create`, asked for a draft, publishes nothing and does not"
        " delete the release it made when an upload fails. The job's one write to a release"
        " that exists is the publication, which sets `draft` to false and says whether the"
        " release is GitHub's latest, and nothing else: a candidate never is, and a stable"
        " version is only when it is the highest stable version among the published releases"
        " the job's last look listed and itself, compared as job `aliases` compares versions,"
        " so a backport below a released version does not take GitHub's latest from it. On a"
        " draft a person published meanwhile it changes nothing but which release is GitHub's"
        " latest, and on one a person deleted it fails."
    ) in readme
    assert "it deletes the release it made" not in readme
    assert "can be deleted with it" not in readme
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert (
        "Asked for a draft it publishes nothing, and its cleanup of the release it made is only"
        " for a draft it meant to publish itself. It sends a file again, three times at most,"
        " only after a failed connection or a 5xx, and removes a file only when given the"
        " address of one already there, which a creation never gives it. So an upload that"
        " fails leaves this draft, with the files that arrived, and leaves it public if a"
        " person published it meanwhile."
    ) in comment
    assert "names and bytes, each fetched by its asset id, and a draft that does not" in comment


REFUSED = (
    "::error::read by its id, a GitHub release for {tag} exists and is not one this run can"
    " finish: "
)
NEXT = (
    "; look at it on the repository's releases page, delete it if nobody should publish it,"
    " and re-run this job"
)


@pytest.mark.parametrize(
    ("existing", "assets", "said"),
    [
        (
            made_earlier("v1.2.3", False, draft=True, names=[], body="hand-made"),
            {},
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", False, body=None) | {"body": None},
            attached("v1.2.3"),
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", False, draft=True, body="<!-- cairn-ci-cd run 42420 -->"),
            {},
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", False, draft=True, body="<!-- cairn-ci-cd run 424 -->"),
            {},
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", prerelease=True),
            attached("v1.2.3"),
            "its pre-release flag is true, not false",
        ),
        (
            made_earlier("v1.2.3", prerelease=True, draft=True),
            {},
            "its pre-release flag is true, not false",
        ),
        (
            made_earlier("v1.2.3", False, names=["SHA256SUMS", "cairn_memory-1.2.3.tar.gz"]),
            attached("v1.2.3"),
            "its files are [SHA256SUMS cairn_memory-1.2.3.tar.gz], not [SHA256SUMS"
            " cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz]",
        ),
        (
            made_earlier("v1.2.3", False, names=[*attached("v1.2.3"), "other.txt"]),
            attached("v1.2.3"),
            "its files are [",
        ),
        (
            made_earlier("v1.2.3", False),
            {**attached("v1.2.3"), "cairn_memory-1.2.3.tar.gz": b"another source archive"},
            "cairn_memory-1.2.3.tar.gz is not the file this run attested",
        ),
    ],
)
def test_a_release_this_run_cannot_finish_stops_the_job_in_one_sentence(
    tmp_path, existing, assets, said
):
    done, calls = run(tmp_path, "v1.2.3", "", existing, assets)
    assert done.returncode == 1
    assert writes(calls) == []
    error = the_error(done)
    assert error.startswith(REFUSED.format(tag="v1.2.3")) and error.endswith(NEXT)
    assert said in error


UNREAD = (
    "::error::the files of the GitHub release for {tag}, {state} as read by its id, could not be"
    " downloaded, so"
    " they were not compared and nothing shows that they differ; re-run this job, and delete"
    " nothing unless the releases page shows a file of the draft missing or unfinished"
)


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
def test_a_download_that_fails_before_the_publication_is_not_a_draft_that_differs(
    tmp_path, tag, candidate, existing
):
    """A download that fails says nothing about the files. The step used to answer it with the
    line of a release this run cannot finish, which tells a person to delete the release, when
    a plain re-run finishes. Now it says so in a line of its own, publishes nothing, and the
    re-run compares the draft and publishes it."""
    held_ = made_earlier(tag, candidate == "true", draft=True) if existing else None
    assets = attached(tag) if existing else None
    done, calls = run(tmp_path, tag, candidate, held_, assets, DOWNLOAD_FAILS="draft")
    assert done.returncode == 1
    assert the_error(done) == UNREAD.format(tag=tag, state="a draft")
    assert [line.split()[1:3] for line in writes(calls)] == (
        [] if existing else [["release", "create"]]
    )
    (draft,) = held(tmp_path)
    assert draft["draft"] is True and calls[-1].startswith(FETCH[:20])
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [line.split()[1:3] for line in writes(calls)] == [["api", "--method"]]
    assert held(tmp_path) == [{**draft, "draft": False}]


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize("existing", [None, "draft", "published"])
def test_a_download_that_fails_after_the_publication_is_not_a_release_that_differs(
    tmp_path, tag, candidate, existing
):
    """The same after the publication, and on a release an earlier attempt published: the line
    says the release is public, nothing is written after the failure, and the re-run compares
    the published release and succeeds, unwritten."""
    held_ = None
    if existing:
        held_ = made_earlier(tag, candidate == "true", draft=existing == "draft")
    assets = attached(tag) if existing else None
    done, calls = run(tmp_path, tag, candidate, held_, assets, DOWNLOAD_FAILS="published")
    assert done.returncode == 1
    assert the_error(done) == UNREAD.format(tag=tag, state="public")
    assert [line.split()[1:3] for line in writes(calls)] == {
        None: [["release", "create"], ["api", "--method"]],
        "draft": [["api", "--method"]],
        "published": [],
    }[existing]
    (public,) = held(tmp_path)
    assert public["draft"] is False and calls[-1].startswith(FETCH[:20])
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == [] and held(tmp_path) == [public]


def test_the_line_of_a_download_that_fails_tells_nobody_to_delete_and_has_its_row():
    """Sixth report, must-fix 5: a download that fails again and again shows only that the
    files stay unreadable. The row and the line ask for what a person can see on the releases
    page before anything is deleted."""
    for state in ("a draft", "public"):
        line = UNREAD.format(tag="v1.2.3", state=state)
        assert "delete it" not in line and ". " not in line
        assert line.endswith(
            "re-run this job, and delete nothing unless the releases page shows a file of the"
            " draft missing or unfinished"
        )
    means, next_step = cells(
        "the files of the GitHub release for …, … as read by its id, could not be downloaded, so"
        " they were not compared and nothing shows that they differ"
    )
    assert means == (
        "GitHub did not give the job the release's files to compare, before the publication"
        " (the line says a draft) or after it (public): that says nothing about the files"
    )
    assert next_step == (
        "Re-run the job, which starts from what is listed then: it compares this run's draft and"
        " publishes it, or compares the published release. When it fails at every re-run, look"
        " at the draft's files on the releases page: delete the draft only if a file is missing"
        " or unfinished there, and re-run the job, which makes the release again; otherwise the"
        " failure is GitHub's or the network's: delete nothing."
    )


@pytest.mark.parametrize("seen", ["a file unfinished", "the three files"])
def test_a_draft_whose_download_fails_at_every_re_run_is_looked_at_before_it_is_deleted(
    tmp_path, seen
):
    """What the row says, done. The line repeats and nothing is written, which shows only
    that the files stay unreadable. A person who sees a file unfinished on the releases page
    deletes the draft, and the re-run makes the release again; one who sees the three files
    deletes nothing, and the re-run that gets the files publishes the same draft."""
    existing = made_earlier("v1.2.3", False, draft=True)
    setup(tmp_path, "v1.2.3", existing, attached("v1.2.3"))
    for _ in range(3):
        done, calls = attempt(tmp_path, "v1.2.3", "", DOWNLOAD_FAILS="draft")
        assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [existing]
        assert the_error(done) == UNREAD.format(tag="v1.2.3", state="a draft")
    if seen == "a file unfinished":
        by_hand(tmp_path, "delete")
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    made_again = 88 if seen == "a file unfinished" else 77
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(made_again, False)]


def test_a_release_of_another_tag_is_none_of_this_one_s(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", made_earlier("v1.2.4", False), attached("v1.2.3"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(made(calls)) == 1


def test_a_listing_that_fails_is_not_taken_for_no_release(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", LIST_FAILS="1")
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == (
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one is"
        " not known; re-run this job"
    )


def test_the_listing_takes_every_page_and_drafts_by_their_tag():
    run_ = CREATE["run"]
    assert 'gh api --paginate "repos/$GH_REPO/releases?per_page=100"' in run_
    assert CREATE["env"]["GH_REPO"] == "${{ github.repository }}"
    assert JOBS["release"]["permissions"] == {"contents": "write"}  # drafts are listed to it
    assert "--clobber" not in run_ and "gh release upload" not in run_


README = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")


def test_the_retry_is_said_in_the_readme_with_its_condition():
    """Each sentence, and the test that runs it: the four requests in their order and a re-run
    from each thing an attempt can leave (the tests at the top of this file); the reply lost
    (test_generated_release_retry_unit, "a publication done whose reply is lost"); the draft
    that is not whole, deleted and made again (below the draft's tests); the draft changed
    between the comparison and the publication (test_generated_release_retry_unit, "a draft
    changed between")."""
    readme = " ".join(README.split())
    assert (
        "The release's text ends in a mark, an HTML comment GitHub does not show, with the id of"
        " the run that made it. Job `release` makes the release with separate requests and takes"
        " none back: it makes a draft with the files (`gh release create --draft`), compares the"
        " draft's title and notes with what it wrote and its files with the ones this run"
        " attested, names and bytes, publishes the draft with a request of its own, to the"
        " draft's id, and looks again; it succeeds only on a published release that carries"
        " that title and those notes and holds exactly those files, byte for byte, as its last"
        " read of the release by its id and the files that read names show them. Each look"
        " lists the"
        " repository's releases, drafts included (a listing that fails stops the job), and takes"
        " the one for its tag as its own only when, read by its id, it carries this run's mark"
        " and this tag's pre-release flag."
    ) in readme
    # (Between the two, what is read by the release's id: test_ci_release_fresh_read_unit.)
    assert (
        "So a re-run of `release` starts from what an earlier attempt left:"
        " no release, and it makes the draft; this run's draft with this run's title and notes"
        " and exactly this run's files, and it publishes it; a published release with the same,"
        " and"
        " it leaves it as it is and succeeds, so that a release tag's `aliases` runs (a"
        " candidate's run has"
        " none). A publication GitHub made while its reply was lost fails the job and loses"
        " nothing: the re-run finds the release published. A draft that does not hold exactly"
        " this run's files, as when an upload was cut, stops the job, and a person deletes it on"
        " the releases page and re-runs the job, which makes the release again (the table below"
        " has the line)."
    ) in readme
    # (The sentences on the notes and the title that follow: test_ci_release_notes_title_unit.)
    # A release for the tag the listing does not show yet is not seen (the independent
    # tester's finding, decision 6): a stated limit, said with what the job still guarantees.
    assert "the table has the line for both). Any other release for the tag stops the job." not in (
        readme
    )
    assert (
        " (the table has the line for both). Any other release for the tag that a look lists"
        " stops the job, but one a person makes while the job runs, or the draft of an earlier"
        " attempt just before a re-run, can be missing from the listing, which can be seconds"
        " behind a write, so the job does not see it and can make or publish its own release"
        " beside it: that is a stated limit, and a re-run can be the job that makes that second"
        " release as much as a first run can; what the job still guarantees is that the release"
        " it publishes and succeeds on is its own, judged by its id, with this run's mark,"
        " title, notes and files."
    ) in readme
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert (
        "any other a look lists stops the job. A look judges its row only to stop early: two"
        " rows for the tag stop the job on the listing, which its line says, and a row that does"
        " not show this run's release as it wrote it is read by its id at once, and the job"
        " stops on what that read shows, or goes on when the listing was behind. A release the"
        " listing does not show yet, one a person makes while the job runs or the draft of an"
        " earlier attempt just before a re-run, is not seen, and the job can make or publish its"
        " own beside it: that is a stated limit, and the release it publishes and succeeds on"
        " is still its own, judged by its id."
    ) in comment
    assert "so it never makes a second release of its own in one attempt." in comment
    (limits,) = [" ".join(p.split()) for p in README.split("\n") if p.startswith("Three limits.")]
    assert limits.endswith(
        " The third limit is job `release`'s: no request compares and publishes at once, so a"
        " draft whose title, notes, files or flags a person changes after the job's last read of"
        " it and before its publication is published as changed; the job reads it and compares"
        " its files again after it publishes, stops on a difference it sees then and deletes"
        " nothing, so the changed release stays public, and a change made after its last read,"
        " before it ends, it does not see: do not edit this tag's release by hand while the"
        " `release` job runs."
    )
    assert "deleted if it is still the same draft" not in readme
    assert "either way `aliases` runs" not in readme
    assert "no write to a release that exists" not in readme


def cells(key: str) -> tuple[str, str]:
    assert key in stops()
    (row,) = [line for line in README.splitlines() if line.startswith(f"| `{key}` |")]
    _, means, next_step = (cell.strip() for cell in row.strip("|").split(" | "))
    return means, next_step


def test_the_draft_s_stop_has_its_row_and_the_row_says_what_the_tests_above_run():
    means, next_step = cells(
        "read by its id, the GitHub release for … is this run's draft and does not hold exactly"
        " the files this run attested: …"
    )
    assert means == (
        "Read by its id, a draft for the tag carries this run's mark and does not hold exactly"
        " the files this run attested (the line names a file, or the names it holds): an upload"
        " was cut, in this attempt or an earlier one, and it may hold none of them, some, or one"
        " cut off; or a person changed a file on it"
    )
    assert next_step == (
        "Delete it on the repository's releases page and re-run the job, which makes the release"
        " again (once the five days the run's files are kept have passed, re-run `package`"
        " instead, which runs only after a `verify` that passed and which `release` follows). Do"
        " not publish it by hand: the job then finds a public release it cannot finish."
    )
    # (Its file names are those a read by its id gives, not the listing's, which can be short
    # of one: test_ci_release_fresh_read_unit.)
    # `package` re-run alone is followed by `release`, which then finds no release and makes it;
    # it needs `verify` (test_ci_retry_limits_unit runs what a failed `verify` does to it).
    assert "release" in rerun({"package"}) and "verify" in JOBS["package"]["needs"]


def test_a_look_that_does_not_show_the_job_s_own_write_has_its_row(tmp_path):
    """(The listing that is behind, and that a second release is never made:
    test_generated_release_retry_unit, "a listing that does not show the step's own write".)"""
    means, next_step = cells(
        "GitHub's listing, at this job's last look, does not list the release for … as this job"
        " has just left it, and it can be seconds behind a write"
    )
    # (The wait before this stop, and the row's own words: test_ci_release_listing_lag_unit.)
    assert means.endswith("or a person deleted the release or made it a draft in that moment")
    assert next_step == (
        "Look at the repository's releases page first, then re-run the job, which starts from"
        " what is listed then: it makes the draft if there is"
        " no release, publishes this run's draft, or compares the published release. It never"
        " makes a second release in one attempt."
    )
    # A person deletes this run's whole draft just after the job published it: the job says so,
    # and the re-run, finding no release, makes it.
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), DELETED_ONCE_PUBLIC="1")
    assert done.returncode == 1 and len(writes(calls)) == 1 and held(tmp_path) == []
    assert the_error(done) == (
        "::error::GitHub's listing, at this job's last look, does not list the release for"
        " v1.2.3 as this job has just left it, and it can be seconds behind a write; look at the"
        " repository's releases page first, then re-run this job, which starts from what is"
        " listed then"
    )
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [line.split()[1:3] for line in writes(calls)] == [
        ["release", "create"],
        ["api", "--method"],
    ]
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]
