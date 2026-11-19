"""Every name infra/gcp/README.md tells an operator to type exists, and every line it quotes is
one the scripts print. Names are read from the guide by pattern, never by line number."""

import re

import pytest
from deploy_standins import (
    COMMIT,
    HOST_MAIN_MARKER,
    HOST_SCRIPT,
    IMAGE,
    MISSING_TOOLS,
    NEW,
    NEWER_COMMIT,
    OLD,
    README,
    REPO,
    SOURCE,
    Standins,
    manifest,
    status_line,
)

INFRA = REPO / "infra" / "gcp"
GUIDE = README.read_text(encoding="utf-8")
OUTPUTS = set(re.findall(r'^output "(\w+)"', (INFRA / "outputs.tf").read_text(), re.M))


def test_rehearsal_platform_limits_are_explicit():
    said = " ".join(
        GUIDE[GUIDE.index("**Rehearsed on a CI runner**") :].split("\n\n", 1)[0].split()
    )
    assert "Ubuntu runner (x64 or ARM64)" in said
    assert "build and run the `linux/amd64` cairn image" in said
    assert "QEMU when the runner is ARM64" in said
    assert "the guest kernel remains ARM64" in said
    assert "does not validate an AMD64 kernel or Container-Optimized OS" in said
    assert "only cairn's image is `linux/amd64`: Caddy and Litestream" in said
    assert "where nothing answers at that address, only the rules themselves are checked" in said
    assert "in CI the target is a stand-in, `scripts/ci_metadata_fixture.py`" in said
    assert "a run that fell back to the weaker check fails" in said


def test_every_tofu_output_the_guide_reads_exists():
    named = set(re.findall(r"tofu (?:-chdir=\S+ )?output -raw (\w+)", GUIDE))
    assert {"deploy_image", "instance_name", "rollback_command", "replica_url"} <= named
    assert named - OUTPUTS == set()


def host_commands() -> set[str]:
    _, marker, dispatch = HOST_SCRIPT.read_text(encoding="utf-8").partition(HOST_MAIN_MARKER)
    assert marker
    return set(re.findall(r"^  ([a-z-]+)\)", dispatch, re.M))


def test_every_host_sh_command_the_guide_names_is_one_host_sh_dispatches():
    named = set(re.findall(r"host\.sh ([a-z][a-z-]*)", GUIDE))
    assert {"hold", "pin", "release", "state", "litestream"} <= named
    assert named - host_commands() == set()


def test_release_takes_the_force_flag_the_guide_offers():
    assert "`release --force`" in GUIDE
    assert re.search(r'"\$\{1:-\}" != --force', HOST_SCRIPT.read_text(encoding="utf-8"))


def test_every_systemd_unit_the_guide_names_is_written_by_cloud_init():
    template = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
    written = set(re.findall(r"path: /etc/systemd/system/(\S+)$", template, re.M))
    named = set(re.findall(r"\b([a-z][a-z0-9-]*\.(?:service|timer))(?![\w])", GUIDE))
    assert {"cairn.service", "cairn-update.service", "litestream.service"} <= named
    assert named - written == set()


def test_the_rollback_commands_placeholder_is_the_one_the_guide_replaces():
    value = re.search(
        r'output "rollback_command" \{.*?value\s*=\s*"([^"]*)"',
        (INFRA / "outputs.tf").read_text(encoding="utf-8"),
        re.S,
    ).group(1)
    named = re.findall(r"placeholder is `(\w+)`|with its `(\w+)` replaced", GUIDE)
    placeholders = {a or b for a, b in named}
    assert placeholders == {"DIGEST"}, placeholders
    assert re.search(r"@DIGEST\b", value), value


# --- lines the guide quotes, against what the scripts print ------------------------------

# Lines the guide attributes to the deploy script without its "deploy: " prefix. A change
# to one of them in the guide must come with a change here.
DEPLOY_QUOTES_WITHOUT_PREFIX = (
    "copy to Artifact Registry failed (skopeo exit 1)",
    "cairn:production moved from sha256:... to sha256:...",
    "this run is for commit ..., but main is now at ...",
)


def as_pattern(quote: str) -> re.Pattern:
    """The guide's placeholders: sha256:... a digest, DOMAIN a host, ... anything."""
    pattern = re.escape(quote)
    pattern = pattern.replace(re.escape("sha256:..."), "sha256:[0-9a-f]{64}")
    pattern = pattern.replace(re.escape("..."), ".+?")
    return re.compile(pattern.replace("DOMAIN", r"[a-z0-9.-]+"))


@pytest.fixture
def deploy_output(tmp_path) -> str:
    """What the deploy script prints in the runs the guide describes."""
    if MISSING_TOOLS:
        pytest.skip(f"the deploy script needs {', '.join(MISSING_TOOLS)}")
    printed = []

    def run(name, *args, prepare, **env):
        s = Standins(tmp_path / name)
        prepare(s)
        printed.append(s.deploy(*args, **env).text)

    run(
        "copy-fails",
        "copy",
        NEW,
        prepare=lambda s: (
            s.put(f"{SOURCE.lower()}@{NEW}", manifest("new")),
            s.set_file("skopeo-copy-fails", "1"),
        ),
    )
    run(
        "promote",
        "promote",
        NEW,
        prepare=lambda s: (
            s.put(f"{IMAGE}@{NEW}", manifest("new")),
            s.put(f"{IMAGE}:production", manifest("old")),
        ),
    )
    run(
        "check-head",
        "check-head",
        COMMIT,
        prepare=lambda s: s.answers("github", (f'{{"commit": {{"sha": "{NEWER_COMMIT}"}}}}', 0)),
    )
    run(
        "wait",
        "wait",
        NEW,
        prepare=lambda s: s.statuses(status_line(2, running=NEW, healthy="yes", proven=NEW)),
        CAIRN_AFTER_SEQ="1",
    )
    run("check-url", "check-url", prepare=lambda s: s.answers("healthz", ("200", 0)))
    assert OLD in printed[1]
    return "".join(printed)


def test_every_deploy_line_the_guide_quotes_is_printed_by_the_deploy_script(deploy_output):
    prefixed = re.findall(r"`(deploy: [^`]+)`", GUIDE)
    assert len(prefixed) >= 2, prefixed
    for quote in DEPLOY_QUOTES_WITHOUT_PREFIX:
        assert f"`{quote}`" in GUIDE, quote
    for quote in (*prefixed, *DEPLOY_QUOTES_WITHOUT_PREFIX):
        assert as_pattern(quote).search(deploy_output), f"no run printed {quote!r}"


TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
VARIABLES = (INFRA / "variables.tf").read_text(encoding="utf-8")
MAIN = (INFRA / "main.tf").read_text(encoding="utf-8")
PLANS = "/etc/cairn/plans.yaml"


def flat(text: str) -> str:
    return " ".join(text.split())


def plans_entry() -> str:
    start = TEMPLATE.index(f"  - path: {PLANS}\n")
    return TEMPLATE[start : TEMPLATE.index("\n\n", start)]


def test_the_plans_paragraph_says_what_the_template_and_host_sh_do():
    """Changing the configuration, "Plans.": where the file lands, whose it is, how cairn
    gets it, what plan time refuses, and that a change is an apply and a restart."""
    said = flat(GUIDE[GUIDE.index("**Plans.**") :].split("\n\n", 1)[0])
    host = HOST_SCRIPT.read_text(encoding="utf-8")
    assert f"cloud-init writes it to `{PLANS}`, owned by root with mode 0644" in said
    assert "    owner: root\n" in plans_entry() and '    permissions: "0644"\n' in plans_entry()
    assert f"mounted read-only and `--plans {PLANS}`" in said
    assert f'plans_mount=(-v "$PLANS_FILE:{PLANS}:ro")' in host
    assert f"plans_option=(--plans {PLANS})" in host and f"PLANS_FILE={PLANS}\n" in host
    assert "relative to `infra/gcp` or absolute" in said
    refused = "a file that is missing, empty, larger than 16 KiB, or not printable ASCII text"
    assert refused in said and "ends in exactly one line break" in said
    assert "<= 16384" in VARIABLES and "printable ASCII" in VARIABLES
    assert "can(file(var.cairn_plans_file))" in VARIABLES  # missing
    assert 'file(var.cairn_plans_file) != ""' in VARIABLES  # empty
    assert "It also refuses `--plans` in `cairn_extra_args` while `cairn_plans_file` is set" in said
    assert "precondition {" in MAIN and "Remove --plans from cairn_extra_args" in MAIN
    assert "Without `cairn_plans_file` the file is empty, and cairn starts without plans" in said
    assert "${indent(6, chomp(plans_yaml))}" in plans_entry()
    assert 'plans_yaml       = var.cairn_plans_file == "" ? "" :' in MAIN
    # A change goes the way of every user-data change.
    assert "the extra arguments, the plans file, the Caddy" in GUIDE
    assert "After an edit of the file: `tofu apply`, then stop and start the VM" in said


def test_the_rehearsal_paragraph_names_the_plans_check_the_rehearsal_runs():
    rehearsal = (REPO / "scripts" / "rehearse_host.py").read_text(encoding="utf-8")
    assert "rehearsal.runs_with_the_plans_file_it_is_given," in rehearsal
    assert (
        "that without a plans file cairn runs with no `--plans`, that with one rendered into"
        f" the template cairn runs with it mounted read-only at `{PLANS}`" in flat(GUIDE)
    )


# --- docs/pre-release.md, against the infrastructure code ------------------------------------

PRE_RELEASE = (REPO / "docs" / "pre-release.md").read_text(encoding="utf-8")


def step(number: int) -> str:
    start = PRE_RELEASE.index(f"\n{number}. **")
    end = PRE_RELEASE.index(f"\n{number + 1}. **", start)
    return flat(PRE_RELEASE[start:end])


def test_the_pre_release_names_the_command_that_obtains_gmails_refresh_token():
    """Sources on the pre-release: Gmail needs one person's refresh token, which
    `google-authorize` obtains; the 7-day sentence stays as it was."""
    from cairn.web import cli, cli_google

    start = PRE_RELEASE.index("For a personal Google account")
    said = flat(PRE_RELEASE[start : PRE_RELEASE.index("\n\n", start)])
    assert "cairn has no command that obtains one" not in said
    assert (
        "Gmail needs one person's refresh token, which `google-authorize` obtains with a Desktop"
        " app client of your own, run on the computer whose browser that person uses (the"
        " sources guide gives the steps)." in said
    )
    assert (
        'While that client\'s consent screen is in "Testing", the token stops working after 7'
        " days." in said
    )
    assert cli_google.COMMAND == "google-authorize"
    assert cli_google.COMMAND in cli._parser()._subparsers._group_actions[0].choices


def test_the_pre_releases_settings_pass_plan_time():
    """The settings block gives the plans file by the variable, and its extra arguments hold
    no --plans (the precondition) and only characters cairn_extra_args takes."""
    block = re.search(r"```hcl\n(.*?)```", PRE_RELEASE, re.S).group(1)
    settings = dict(re.findall(r'^(\w+) = "([^"]*)"$', block, re.M))
    assert settings["cairn_plans_file"] == "../../deploy/plans.yaml"
    args = settings["cairn_extra_args"]
    assert not re.search(r"(^| )--plans( |=|$)", args)
    assert re.fullmatch(r"[A-Za-z0-9 ._:/=@,+-]*", args)
    assert '"^[A-Za-z0-9 ._:/=@,+-]*$"' in VARIABLES
    assert '"(^| )--plans( |=|$)"' in MAIN
    assert args == "--sign-in github --join personal"


def test_step_two_is_the_variable_with_no_shell_on_the_vm():
    two = step(2)
    assert "`cairn_plans_file`" in two
    assert "gcloud compute ssh" not in two and "10001" not in two and "sudo" not in two
    assert f"cloud-init installs it at `{PLANS}` and cairn starts with `--plans {PLANS}`" in two
    assert "larger than 16 KiB" in two and "<= 16384" in VARIABLES


def test_plan_time_does_not_read_the_file_as_plans_and_serve_still_refuses_a_bad_one(
    tmp_path, monkeypatch
):
    """Step 2: a file that passes the variable's checks but is no plans file stops `serve`."""
    import uvicorn

    from cairn.web import cli

    assert "one that `serve` cannot use still stops `serve` at start" in step(2)
    bad = tmp_path / "plans.yaml"
    bad.write_text("default: gold\nplans:\n  free:\n    label: Free\n", encoding="utf-8")
    pattern = re.search(r'regex\("([^"]*)", file\(var\.cairn_plans_file\)\)', VARIABLES).group(1)
    text = bad.read_text(encoding="utf-8")
    assert re.match(pattern.replace("\\t", "\t").replace("\\n", "\n")[:-1] + r"\Z", text)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["serve", "--db", str(tmp_path / "cairn.db"), "--plans", str(bad)]) == 2


def test_step_four_no_longer_lists_a_missing_plans_file():
    four = step(4)
    listed = four[four.index("not there:") : four.index("So put everything")]
    assert "--plans" not in listed
    assert "Set `cairn_plans_file` and `cairn_extra_args` in `terraform.tfvars`" in four


def test_the_pre_release_says_where_step_two_is_exercised():
    said = flat(PRE_RELEASE[PRE_RELEASE.index("## What is checked and what is not") :])
    assert "read from the host script" not in said
    assert "Step 2's way onto the VM is exercised by the host rehearsal in CI" in said
    rehearsal = (REPO / "scripts" / "rehearse_host.py").read_text(encoding="utf-8")
    assert "rehearsal.runs_with_the_plans_file_it_is_given," in rehearsal
    assert 'status == 402 and limit == "projects"' in rehearsal


def test_the_first_report_of_a_fresh_vm_holds_what_the_guide_says(tmp_path):
    """First deploy, step 3: before anything is deployed, the VM's line."""
    quote = re.search(r"prints a line with `([^`]+)`", GUIDE).group(1)
    host = Standins(tmp_path).host()
    host.running(None, healthy=False)
    assert quote in host.report()


SERVER_COMMAND = re.compile(r"sudo docker exec cairn python -m cairn\.server ([^`\n]+)")


def test_the_pre_releases_source_commands_name_the_workspace():
    """Sources on the pre-release: the settings block joins people with `--join personal`, so
    each has a workspace of their own, and `source-add` or `source-list` without --workspace
    acts on the default workspace and not on theirs. So both examples name a workspace, the
    text says where its id comes from, and every command the runbook gives for the VM is one
    the real parser takes."""
    import shlex

    from cairn.web import cli

    commands = [shlex.split(found) for found in SERVER_COMMAND.findall(PRE_RELEASE)]
    assert {words[0] for words in commands} >= {"source-add", "source-list", "workspace-list"}
    for words in commands:
        parsed = cli._parser().parse_args(words)  # exits when an example is not a command
        if words[0] in ("source-add", "source-list"):
            assert parsed.workspace == "ACCOUNT", words
    start = PRE_RELEASE.index("## Sources on the pre-release")
    said = flat(PRE_RELEASE[start : PRE_RELEASE.index("\n## ", start + 1)])
    assert "With `--join personal` each person has a workspace of their own" in said
    assert "`workspace-list` prints the ids" in said
    assert "goes to the default workspace, not to theirs" in said


# --- the brand, in infra/gcp/README.md and docs/pre-release.md -------------------------------

BRAND = "/etc/cairn/brand"
HOST = HOST_SCRIPT.read_text(encoding="utf-8")
TFVARS_EXAMPLE = (INFRA / "terraform.tfvars.example").read_text(encoding="utf-8")
TFTEST = (INFRA / "tests" / "brand.tftest.hcl").read_text(encoding="utf-8")
REHEARSAL = (REPO / "scripts" / "rehearse_host.py").read_text(encoding="utf-8")
# serve's rule for the name and the alt text, as variables.tf gives it to RE2; \p{Cc} is
# U+0000 to U+001F and U+007F to U+009F.
NAME_RULE = re.compile(r"[^\x00-\x1f\x7f-\x9f<>]{1,60}")


def brand_paragraph() -> str:
    return flat(GUIDE[GUIDE.index("**Brand.**") :].split("\n\n", 1)[0])


def variable_block(name: str) -> str:
    start = VARIABLES.index(f'variable "{name}" {{')
    return VARIABLES[start : VARIABLES.index("\n}\n", start)]


def test_the_brand_paragraph_says_where_each_value_lands_and_how_serve_gets_it():
    said = brand_paragraph()
    assert GUIDE.index("**Plans.**") < GUIDE.index("**Brand.**") < GUIDE.index("## Teardown")
    for name in ("cairn_brand_name", "cairn_brand_logo_file", "cairn_brand_logo_alt"):
        assert f"`{name}`" in said and f'variable "{name}"' in VARIABLES
    assert "an SVG or a PNG on your machine (relative to `infra/gcp` or absolute)" in said
    assert "relative to the directory tofu runs in or absolute" in variable_block(
        "cairn_brand_logo_file"
    )
    # base64, and only the values that are set
    assert "`tofu apply` puts each value that is set in the user-data, base64" in said
    assert "base64encode(var.cairn_brand_name)" in MAIN
    assert "filebase64(var.cairn_brand_logo_file)" in MAIN
    assert "base64encode(var.cairn_brand_logo_alt)" in MAIN
    assert 'if f.content != ""' in MAIN and "encoding: b64" in MAIN
    paths = f"`{BRAND}/name`, `{BRAND}/logo` and `{BRAND}/logo-alt`, owned by root with mode 0644"
    assert f"At boot cloud-init writes them to {paths}" in said
    assert re.findall(r'path = "(/etc/cairn/brand/[a-z-]+)"', MAIN) == [
        f"{BRAND}/name",
        f"{BRAND}/logo",
        f"{BRAND}/logo-alt",
    ]
    assert 'permissions: \\"0644\\"\\n    owner: root' in MAIN
    assert (
        "cairn starts with `--brand-name=NAME`, the logo mounted read-only with `--brand-logo"
        f" {BRAND}/logo`, and `--brand-logo-alt=TEXT`" in said
    )
    assert f"BRAND_DIR={BRAND}\n" in HOST
    assert 'brand_option+=("--brand-name=$(<"$BRAND_DIR/name")")' in HOST
    assert f'brand_mount=(-v "$BRAND_DIR/logo:{BRAND}/logo:ro")' in HOST
    assert f"brand_option+=(--brand-logo {BRAND}/logo)" in HOST
    assert 'brand_option+=("--brand-logo-alt=$(<"$BRAND_DIR/logo-alt")")' in HOST


def test_the_brand_paragraph_says_a_value_arrives_as_written():
    """Pinned by test_infra_tf_unit.py::test_run_cairn_gives_serve_each_name_as_one_argument_
    exactly, whose names hold each character the sentence names, and by the tofu test."""
    said = brand_paragraph()
    assert (
        "Each value reaches `serve` as one argument, as you wrote it: no shell expands it and"
        " nothing splits it, so a space, a quote, a backslash or a `$` arrives as it is." in said
    )
    tests = (REPO / "tests" / "unit" / "test_infra_tf_unit.py").read_text(encoding="utf-8")
    assert "def test_run_cairn_gives_serve_each_name_as_one_argument_exactly(" in tests
    for name in ("Knowledge Base", 'The \\"Base\\"', "Notes\\\\Base", "$HOME notes"):
        assert f'"{name}"' in TFTEST, name


def test_the_brand_paragraph_says_why_the_user_data_may_carry_it():
    said = brand_paragraph()
    assert (
        "The brand is not secret (the sign-in page shows it), so the user-data, which anyone"
        " who can read the instance's metadata can read, is an acceptable place for it." in said
    )
    variable = " ".join(variable_block("cairn_brand_name").replace("#", " ").split())
    assert "anyone who may read the instance's metadata can read" in variable


def test_the_brand_paragraph_lists_what_plan_time_refuses():
    said = brand_paragraph()
    assert (
        "`tofu plan` refuses what `serve` would refuse at start: a name that is not 1 to 60"
        " characters or an alt text that is not 1 to 200, one that is only spaces, or one that"
        " holds a control character, a character that is not seen (a zero-width space, a"
        " direction mark), `<` or `>`" in said
    )
    for variable, most in (("cairn_brand_name", 60), ("cairn_brand_logo_alt", 200)):
        rule = f'can(regex("^[^\\\\p{{Cc}}\\\\p{{Cf}}<>]{{1,{most}}}$", var.{variable})'
        assert VARIABLES.count(rule) == 1, variable
        assert f'trimspace(var.{variable}) != ""' in VARIABLES
    assert (
        "Of the logo file it refuses one that is missing, empty or larger than 60 KiB (61,440"
        " bytes), and it checks only how the file starts: the 16 bytes every PNG starts with, or"
        ' an SVG whose first element is `<svg` with `xmlns="http://www.w3.org/2000/svg"`, after'
        " at most an XML declaration, white space and comments. It refuses an SVG with a"
        " document type declaration (`<!DOCTYPE`), which `serve` refuses so that reading a logo"
        " fetches nothing and expands nothing. `serve` reads the whole file at start and may"
        " still refuse one the plan took, such as an SVG that is not well-formed XML; on the VM"
        " that is a container that does not come up." in said
    )
    assert "by its first bytes" not in said
    logo = variable_block("cairn_brand_logo_file")
    assert "can(filebase64(var.cairn_brand_logo_file))" in logo
    assert 'filebase64(var.cairn_brand_logo_file) != ""' in logo
    assert "length(filebase64(var.cairn_brand_logo_file)) <= 81920" in logo
    assert '"^iVBORw0KGgoAAAANSUhEU[g-v]"' in logo and "<svg" in logo
    assert "http://www\\\\.w3\\\\.org/2000/svg" in logo and "<!DOCTYPE" in logo
    assert (
        "a logo without a name, an alt text without a logo, and a name without `--sign-in` in"
        " `cairn_extra_args`" in said
    )
    assert 'var.cairn_brand_logo_file == "" || var.cairn_brand_name != ""' in MAIN
    assert 'var.cairn_brand_logo_alt == "" || var.cairn_brand_logo_file != ""' in MAIN
    assert 'var.cairn_brand_name == "" || can(regex("(^| )--sign-in( |=|$)"' in MAIN
    assert (
        "It also refuses `--brand-name`, `--brand-logo` and `--brand-logo-alt` in"
        " `cairn_extra_args`." in said
    )
    assert '!can(regex("(^| )--brand-(name|logo|logo-alt)( |=|$)", var.cairn_extra_args))' in MAIN


def test_the_brand_paragraph_gives_the_reason_for_the_smaller_logo():
    said = brand_paragraph()
    assert (
        "`serve` itself takes a logo of up to 256 KB; the VM takes at most 60 KiB because the"
        " logo travels in the user-data next to the plans file, and Compute Engine takes at most"
        " 256 KB in one metadata value, which `tofu plan` also checks for the whole user-data."
        in said
    )
    assert "precondition {\n      condition     = local.user_data_bytes <= 262144" in MAIN
    assert "serve itself takes up to 256 KB" in variable_block("cairn_brand_logo_file")


def test_the_brand_paragraph_leaves_the_wall_in_the_extra_arguments():
    said = brand_paragraph()
    assert (
        "`--wall` and `--session-days` stay in `cairn_extra_args`, and `tofu plan` refuses"
        " either without `--sign-in`." in said
    )
    assert (
        "It also refuses a `--session-days` that is not followed by a whole number from 1 to"
        " 365, written `--session-days 30` or `--session-days=30`, and a `--wall` with a value"
        " (`--wall=yes`): `--wall` takes none." in said
    )
    assert 'startswith(w, "--session-days=")' in MAIN
    assert re.fullmatch(r"[A-Za-z0-9 ._:/=@,+-]*", "--sign-in google --wall --session-days 30")
    assert '"^[A-Za-z0-9 ._:/=@,+-]*$"' in VARIABLES
    assert '!can(regex("(^| )--(wall|session-days)( |=|$)", var.cairn_extra_args))' in MAIN


def test_a_change_of_brand_goes_as_a_change_of_plans_does():
    said = brand_paragraph()
    assert (
        "A change of the brand goes as a change of the plans file does: `tofu apply`, then stop"
        " and start the VM as above." in said
    )
    assert "After an edit of the file: `tofu apply`, then stop and start the VM" in GUIDE
    # What differs: nothing is written for a value that is not set.
    assert (
        "Unlike the plans file, which is written empty when unset, a brand value that is not set"
        " is not written at all; `/etc` is on tmpfs, so a value you remove is gone after the"
        " restart and cairn starts without its option." in said
    )
    assert "COS keeps /etc on tmpfs" in TEMPLATE
    assert "${brand_files}" in TEMPLATE and f"  - path: {PLANS}\n" in TEMPLATE
    assert re.search(r'^\s*if \[ -s "\$BRAND_DIR/name" \]; then$', HOST, re.M)


def test_the_tfvars_example_shows_the_brand_commented_out():
    assert '# cairn_brand_name      = "Knowledge Base"\n' in TFVARS_EXAMPLE
    assert '# cairn_brand_logo_file = "../../deploy/brand-logo.svg"\n' in TFVARS_EXAMPLE
    assert '# cairn_brand_logo_alt  = "Juniper Field"\n' in TFVARS_EXAMPLE
    assert "# Leave --brand-name, --brand-logo and --brand-logo-alt out of cairn_extra_args" in (
        TFVARS_EXAMPLE
    )


def test_the_guide_says_ci_plans_against_a_mock_provider():
    assert (
        "CI formats, validates and plans it with OpenTofu (`tofu test`, against a mock provider"
        " and without a cloud project)" in flat(GUIDE)
    )
    assert (
        "CI runs `tofu fmt -check`, `tofu validate` and `tofu test`, which plans the brand's"
        " settings against a mock provider" in flat(GUIDE)
    )
    assert 'mock_provider "google"' in TFTEST
    assert "tofu -chdir=infra/gcp test" in (REPO / ".github/workflows/ci-cd.yml").read_text()


def test_the_rehearsal_paragraph_names_the_brand_check_the_rehearsal_runs():
    assert "rehearsal.runs_under_the_brand_it_is_given," in REHEARSAL
    assert (
        "that with a brand name holding a space, an alt text and a PNG logo rendered into the"
        " template cairn runs with each as one argument and reads the logo as its own user"
        in flat(GUIDE)
    )


# docs/pre-release.md, "Under your own name, behind a wall"


def wall_section() -> str:
    start = PRE_RELEASE.index("## Under your own name, behind a wall\n")
    return PRE_RELEASE[start : PRE_RELEASE.index("\n## ", start + 1)]


def test_the_wall_section_comes_right_before_who_can_get_in():
    start = PRE_RELEASE.index("## Under your own name, behind a wall\n")
    assert PRE_RELEASE.index("\n## ", start + 1) == PRE_RELEASE.index("\n## Who can get in\n")
    assert "session-end" not in wall_section()
    assert "sudo docker exec cairn python -m cairn.server" not in wall_section()


def test_the_wall_sections_settings_pass_plan_time():
    from cairn.web import cli

    [block] = re.findall(r"```hcl\n(.*?)```", wall_section(), re.S)
    settings = dict(re.findall(r'^(\w+) += "([^"]*)"$', block, re.M))
    assert set(settings) == {
        "cairn_extra_args",
        "cairn_brand_name",
        "cairn_brand_logo_file",
        "cairn_brand_logo_alt",
    }
    args = settings["cairn_extra_args"]
    assert args == "--sign-in google --join personal --wall --session-days 30"
    assert re.fullmatch(r"[A-Za-z0-9 ._:/=@,+-]*", args)
    assert re.search(r"(^| )--sign-in( |=|$)", args)
    assert not re.search(r"(^| )--brand-(name|logo|logo-alt)( |=|$)", args)
    for name in ("cairn_brand_name", "cairn_brand_logo_alt"):
        assert NAME_RULE.fullmatch(settings[name]), name
    assert (settings["cairn_brand_name"], settings["cairn_brand_logo_alt"]) == (
        "Knowledge Base",
        "Juniper Field",
    )
    assert settings["cairn_brand_logo_file"].endswith(".svg")
    # The two options this branch's server has, read by its own parser.
    parsed = cli._parser().parse_args(
        ["serve", "--db", "x.db", "--sign-in", "google", "--join", "personal"]
    )
    assert (parsed.sign_in, parsed.join) == (["google"], "personal")


def test_the_wall_section_says_where_googles_client_goes_and_points_to_the_runbook():
    from cairn.web import cli

    said = flat(wall_section())
    assert (
        "The env secret holds `CAIRN_GOOGLE_CLIENT_ID` and `CAIRN_GOOGLE_CLIENT_SECRET` from an"
        " OAuth client at Google whose callback is `https://DOMAIN/auth/google/callback`" in said
    )
    provider = cli._provider(
        "google", {"CAIRN_GOOGLE_CLIENT_ID": "id-0001", "CAIRN_GOOGLE_CLIENT_SECRET": "s-0001"}
    )
    assert provider.name == "google"
    signin = (REPO / "src" / "cairn" / "web" / "signin.py").read_text(encoding="utf-8")
    assert 'Route("/auth/{provider}/callback"' in signin
    assert "The logo is an SVG or a PNG of at most 60 KiB on your machine" in said
    assert "60 KiB (61,440 bytes)" in VARIABLES
    assert (
        '[infra/gcp/README.md, "Changing the configuration"](../infra/gcp/README.md'
        "#changing-the-configuration) says how the three reach the VM and what `tofu plan`"
        " refuses" in said
    )
    assert "\n## Changing the configuration\n" in GUIDE and "**Brand.**" in GUIDE
    assert "A change is a `tofu apply` and a restart of the VM, as for the plans file." in said


def test_the_wall_section_connects_agents_as_step_seven_does():
    command = "`claude mcp add --transport http cairn https://DOMAIN/mcp`"
    [seven] = re.findall(r"^7\. \*\*Connect an agent\*\* by URL: .*$", PRE_RELEASE, re.M)
    assert command in seven
    assert f"{command} works as in step 7" in flat(wall_section())


def test_the_wall_section_says_what_the_server_gives_a_signed_out_visitor():
    """What the server does behind the wall is the server's to test, not this branch's: these
    sentences are pinned here as written, and the one button's provider by the settings."""
    from cairn.identity import Google

    said = flat(wall_section())
    assert (
        "A visitor who is not signed in gets the sign-in page and nothing else: your logo, your"
        " name and one button, for Google. There is no landing page and no guides for them." in said
    )
    assert (Google.name, Google.label) == ("google", "Google")
    assert "--sign-in google " in said and said.count("--sign-in ") == 1
    assert "cairn's own name is on nothing a signed-out browser receives." in said
    assert "Agents still connect by URL, because their addresses are not behind the wall:" in said
    assert "The person signs in once, in the browser the agent opens." in said
    assert (
        "A sign-in is remembered for the days `--session-days` gives, here 30, and each use"
        " renews it." in said
    )
    assert "--session-days 30" in said
    assert (
        "To run the pre-release under your own name, with nothing public but the sign-in page,"
        " set these in `infra/gcp/terraform.tfvars`:" in said
    )
    assert (INFRA / "terraform.tfvars.example").exists()


def test_the_brand_paragraph_says_an_unset_plans_file_is_written_empty():
    assert "Without `cairn_plans_file` the file is empty" in GUIDE
    assert "(the sign-in page shows it)" in brand_paragraph()


def test_the_pre_release_says_where_the_brands_way_is_exercised():
    said = flat(PRE_RELEASE[PRE_RELEASE.index("## What is checked and what is not") :])
    assert (
        "The brand's way onto the VM is planned by `tofu test` against a mock provider and"
        " exercised by the host rehearsal in CI, on a runner and not on a real VM" in said
    )
    assert "rehearsal.runs_under_the_brand_it_is_given," in REHEARSAL
    assert "tofu -chdir=infra/gcp test" in (REPO / ".github/workflows/ci-cd.yml").read_text()


def test_the_readme_says_what_one_run_on_a_real_project_showed_and_what_it_did_not():
    """infra/gcp/README.md, "What was checked, and what was not": the run on a real project is
    reported as one run, with what it did not show, and what it says about the code is the
    code's: 28 resources besides the address reserved first, and the status line's fields. The
    replica alert came after that run and is the one resource more the code has now."""
    start = GUIDE.index("**Run once on a real project**")
    said = flat(GUIDE[start : GUIDE.index("Not checked, most likely to be wrong first:")])
    assert "What that one run showed, and nothing more:" in said
    blocks = sum(len(re.findall(r'^resource "', f.read_text(), re.M)) for f in INFRA.glob("*.tf"))
    assert "created the 28 resources besides the address reserved first" in said
    # 34 when it ran, the replica alert and the later alerts among them; then the audit
    # configuration and the log bucket's retention; then the host alerts (one block).
    assert blocks == 34 + 2 + 1
    assert (
        "The audit configuration, the log bucket's retention and the other settings of [Audit,"
        " keys and network](#audit-keys-and-network) came after that too, two resources more,"
        " and have not been applied." in said
    )
    assert (
        "The host alerts came after that too, one resource more (one block, four policies),"
        " and have not been applied." in said
    )
    assert "The replica alert was added after that run" in said
    assert "The replica check and the silence alert came after that and have not been" in said
    later = (INFRA / "monitoring.tf").read_text()
    assert 'resource "google_logging_metric" "replica_current"' in later
    assert 'resource "google_monitoring_alert_policy" "replica_silent"' in later
    assert "The compaction alert came after that and has not been applied" in said
    assert 'resource "google_logging_metric" "compact_not_finished"' in later
    assert 'resource "google_monitoring_alert_policy" "compact_not_finished"' in later
    monitoring = (INFRA / "monitoring.tf").read_text()
    assert 'resource "google_monitoring_alert_policy" "replica"' in monitoring
    assert "If the first apply stops there, run it again." in said
    assert "reported `running=none healthy=no proven=none` through guest attributes" in said
    host = (INFRA / "files" / "host.sh").read_text()
    assert "seq=N updated=EPOCH running=D healthy=yes|no proven=D" in host
    assert (
        "It did not show: the metadata block seen from inside a container, a rollback, a"
        " restore on the VM, a restart of Docker, the alert's email, or the deploy job from"
        " GitHub Actions" in said
    )
    assert (
        "The replica it had written, copied from the bucket and restored on another machine"
        " with the pinned Litestream image, passed `scripts/restore_drill.py --db-file`" in said
    )
    drill = (REPO / "scripts" / "restore_drill.py").read_text()
    assert "--db-file" in drill and "litestream/litestream:0.5.18" in drill
    assert "litestream/litestream:0.5.18" in (REPO / "deploy" / "compose.backup.yaml").read_text()
    listed = flat(GUIDE[GUIDE.index("Not checked, most likely to be wrong first:") :])
    assert "1. `tofu plan` and `tofu apply` beyond that one run." in listed
    assert "nothing has been planned against a project" not in GUIDE
    pre_release = flat(PRE_RELEASE[PRE_RELEASE.index("## What is checked and what is not") :])
    assert "It ran once on a real VM, on 2026-11-14" in pre_release


# --- the compaction alert and its runbook ---------------------------------------------------------

COMPACT_HEADING = "### When a compaction keeps not finishing"


def guide_part(heading: str) -> str:
    """From `heading` to the next heading of the same or a higher level."""
    level = len(heading.split(" ", 1)[0])
    start = GUIDE.index(f"\n{heading}\n") + 1
    body = start + len(heading)
    for match in re.finditer(r"^(#+) ", GUIDE[body:], re.M):
        if len(match.group(1)) <= level:
            return GUIDE[start : body + match.start()]
    return GUIDE[start:]


def test_the_alerts_section_says_when_the_compaction_alert_fires_and_when_it_does_not():
    said = flat(guide_part("## Alerts"))
    for sentence in (
        "**The compaction alert** fires when cairn logs `compact not finished` more than twice"
        " within an hour.",
        "One such line a day, or one after each restart of cairn, is what a server beside"
        " Litestream writes since decision 143, and does not fire it.",
        "It reads the lines that already reach Cloud Logging (log `cos_containers`, this VM's"
        " instance, container `cairn`); nothing on the VM changes for it.",
    ):
        assert sentence in said, sentence


def test_the_runbook_says_what_the_line_means_and_that_the_database_was_rewritten():
    said = flat(guide_part(COMPACT_HEADING))
    for sentence in (
        "A line that says `(rewritten; the write-ahead log was not emptied)` is a compaction"
        " that rewrote the whole database file under the store's lock, so every write waited"
        " for it, and Litestream then copied the rewritten file again.",
        "A line that says `(no rewrite: only the write-ahead log was due)` rewrote nothing:",
        "The step that did not finish is the last one, truncating the write-ahead log:"
        " Litestream reads the log for as long as it runs.",
        "Nothing is lost when it does not finish:",
        "If there is one line, do nothing:",
    ):
        assert sentence in said, sentence
    assert "docs/compaction-beside-a-replica.md" in said
    assert (REPO / "docs" / "compaction-beside-a-replica.md").exists()


def runbook_reader() -> str:
    """The Python the runbook runs in cairn's container to read the compaction record."""
    found = re.search(r"sudo docker exec cairn python -c '([^']+)'", guide_part(COMPACT_HEADING))
    assert found, "no `sudo docker exec cairn python -c '...'` in the runbook"
    return found.group(1)


def test_the_runbooks_reader_shows_a_deletion_not_yet_compacted(tmp_path):
    import subprocess
    import sys

    from cairn.store import Store
    from cairn.store.base import COMPACT_DUE, COMPACTED_AT

    db = tmp_path / "cairn.db"
    store = Store(db)
    store.delete_account(store.create_account("gone").id)
    store.close()
    code = runbook_reader()
    assert "/data/cairn.db" in code and "mode=ro" in code
    shown = subprocess.run(
        [sys.executable, "-c", code.replace("/data/cairn.db", str(db))],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert COMPACT_DUE in shown and COMPACTED_AT not in shown
    said = flat(guide_part(COMPACT_HEADING))
    assert f"`{COMPACT_DUE}` is there while a deletion waits for a compaction" in said


def test_the_runbook_names_the_commit_that_fixed_the_retry_and_how_to_check_the_image():
    said = flat(guide_part(COMPACT_HEADING))
    assert "commit `f14b49b`" in said
    assert "git merge-base --is-ancestor f14b49b COMMIT" in said
    assert "sha-COMMIT" in said
    workflow = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    assert "CAIRN_COPY_TAG: sha-${{ github.sha }}" in workflow
    compactor = (REPO / "src" / "cairn" / "web" / "compactor.py").read_text(encoding="utf-8")
    assert "self._began, self._ran = began, True  # the next try waits the interval" in compactor


# --- the runbook of host.sh compact -----------------------------------------------------------

AFTER_DELETION = "### After a workspace is deleted"


def compact_run(tmp_path, **kwargs):
    """host.sh compact against the stand-ins, as tests/unit/test_host_compact_unit.py runs it."""
    host = Standins(tmp_path).host()
    host.bound(900)
    host.files(db=2891776, wal=14045112)
    new_copy = "4102444800 4102444800 4102444801 4102444900\n"
    kwargs.setdefault("compact_does", ': >"$HOST_ROOT/data/cairn/cairn.db-wal"\n')
    return host.compact(["1000 1000 0 1100\n", new_copy], **kwargs)


def quoted_compact_lines(text: str) -> list[str]:
    return re.findall(r"`(compact: [^`]+)`", text)


def compact_pattern(quoted: str) -> re.Pattern[str]:
    """A quoted line with its placeholders (N, TIME, ...) as what host.sh puts there."""
    parts = re.split(r"(\bN\b|\bTIME\b|\.\.\.)", quoted)
    holes = {"N": r"\d+", "TIME": r"\S+", "...": r".*"}
    return re.compile("".join(holes.get(part, re.escape(part)) for part in parts))


def test_the_after_deletion_runbook_comes_after_the_compaction_alerts_and_names_the_command():
    said = flat(guide_part(AFTER_DELETION))
    assert GUIDE.index(f"\n{COMPACT_HEADING}\n") < GUIDE.index(f"\n{AFTER_DELETION}\n")
    assert "sudo bash /etc/cairn/host.sh compact" in said
    assert "compact not finished" in said
    assert "docs/compaction-beside-a-replica.md" in said


def test_every_line_the_runbook_quotes_is_one_host_sh_prints(tmp_path):
    """A run that passes, one whose compact does not finish, and one whose replica takes no new
    copy: every `compact: ...` line the runbook quotes is a line one of them printed."""
    printed = []
    for i, kwargs in enumerate(({}, {"compact_rc": 1}, {"start_fails": True})):
        done = compact_run(tmp_path / str(i), **kwargs)
        printed += [
            x.removeprefix("<3>").removeprefix("cairn-host: ") for x in done.text.splitlines()
        ]
        printed = [x.removeprefix("ERROR: ") for x in printed]
    # The nightly run's own lines (`compact: nightly: ...`) are checked against what
    # `compact-if-due` prints, in test_host_compact_nightly_unit.py.
    quoted = [
        x
        for x in quoted_compact_lines(guide_part(AFTER_DELETION))
        if not x.startswith("compact: nightly: ")
    ]
    assert len(quoted) >= 8, quoted
    for line in quoted:
        assert any(compact_pattern(line).fullmatch(x) for x in printed), line


def test_the_runbook_says_what_to_do_when_each_step_fails():
    said = flat(guide_part(AFTER_DELETION))
    for step in ("1/6", "2/6", "3/6", "4/6", "5/6", "6/6"):
        assert f"**Step {step}" in said, step
    assert "sudo systemctl start litestream" in said
    host = HOST_SCRIPT.read_text(encoding="utf-8")
    assert "COMPACT_CHECKS=24" in host and "sleep 5" in host
    assert "24 checks, 5 seconds apart" in said


def test_host_shs_record_reader_shows_a_deletion_not_yet_compacted(tmp_path):
    import subprocess
    import sys

    from cairn.store import Store

    db = tmp_path / "cairn.db"
    store = Store(db)
    store.delete_account(store.create_account("gone").id)
    store.close()
    host = HOST_SCRIPT.read_text(encoding="utf-8")
    found = re.search(r"docker exec cairn python -c '([^']+)'", host)
    assert found, "host.sh reads the compaction record with `docker exec cairn python -c '...'`"
    code = found.group(1)
    assert "mode=ro" in code
    shown = subprocess.run(
        [sys.executable, "-c", code.replace("/data/cairn.db", str(db))],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert shown and shown == Store(db).compaction_state().deleted_at


# --- numbers the guide states are numbers the definitions hold -------------------------------

VARIABLES_TF = (INFRA / "variables.tf").read_text(encoding="utf-8")


def default_of(name: str) -> str:
    start = VARIABLES_TF.index(f'variable "{name}"')
    block = VARIABLES_TF[start : VARIABLES_TF.index("\n}\n", start)]
    return re.search(r"^  default\s*=\s*(.+)$", block, re.M).group(1).strip('"')


def litestream_days() -> int:
    import yaml

    retention = yaml.safe_load((REPO / "deploy" / "litestream.yml").read_text())["snapshot"][
        "retention"
    ]
    assert retention.endswith("h") and int(retention[:-1]) % 24 == 0, retention
    return int(retention[:-1]) // 24


def snapshot_days() -> int:
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    return int(re.search(r"max_retention_days\s*=\s*(\d+)", main).group(1))


def test_every_soft_delete_period_the_guide_states_is_the_variables_default():
    days = int(default_of("replica_soft_delete_days"))
    assert days == 7
    said = flat(GUIDE)
    for sentence in (
        f"soft delete: whatever is deleted stays restorable for {days} days)",
        f"The bucket has soft delete set to {days} days (`replica_soft_delete_days`, the least"
        " Cloud Storage allows):",
        f"you have {days} days from the deletion to bring the replica back:",
        f"Past the {days} days the files are gone,",
        f"What you delete stays in soft delete, billed, for another {days} days.",
        f"everything Litestream deletes is billed for {days} more days (soft delete)",
        f"the bucket now keeps {days} days of soft-deleted files",
    ):
        assert sentence in said, sentence
    # No sentence about soft delete names another period.
    for sentence in re.split(r"(?<=[.:;|])\s", said):
        if re.search(r"soft[ -]delet", sentence):
            for number in re.findall(r"\b(\d+) (?:more )?days\b", sentence):
                assert int(number) in (days, snapshot_days()), sentence
    assert not re.search(r"\b38\b", said)


def test_the_table_of_how_long_a_deletion_stays_in_the_backups_adds_up():
    table = flat(guide_part("### How long a deletion stays in the backups"))
    lite, soft, snaps = (
        litestream_days(),
        int(default_of("replica_soft_delete_days")),
        snapshot_days(),
    )
    assert (lite, soft, snaps) == (8, 7, 14)
    for row in (
        f"| Litestream's files in the bucket | up to {lite} days (`retention: 192h` in"
        " `deploy/litestream.yml`) |",
        f"| Then soft delete in the bucket | {soft} more days (`replica_soft_delete_days`) |",
        f"| The replica bucket in all | up to {lite + soft} days |",
        f"| The daily disk snapshots | up to {snaps} days"
        " (`google_compute_resource_policy.data_daily`) |",
    ):
        assert row in table, row


def test_the_boot_disk_the_guide_prices_is_the_default_and_its_change_replaces_the_vm():
    size = int(default_of("boot_disk_size_gb"))
    said = flat(GUIDE)
    assert f"| Boot disk, {size} GB `pd-balanced` | fixed | ${size / 10:.2f} a month |" in said
    assert "Changing `boot_disk_size_gb` replaces the VM: the boot disk is made with it." in said
    assert "older than three days, except the running image and the one before it" in said


def test_the_registry_cleanup_the_guide_states_is_the_one_main_tf_makes():
    keep, days = (
        int(default_of("registry_keep_versions")),
        int(default_of("registry_delete_after_days")),
    )
    said = flat(GUIDE)
    assert (
        f"The registry keeps the newest {keep} versions of the image (`registry_keep_versions`)"
        " and whatever the deploy tag names, and deletes every other image once it is"
        f" {days} days old (`registry_delete_after_days`)." in said
    )
    assert f"| Artifact Registry storage | images pushed (older than {days} days deleted" in said
    assert "no cleanup policy is set" not in said


def test_bootstrap_says_to_remove_the_default_network_and_the_editor_role_with_the_commands():
    said = guide_part("## Bootstrap (once, by a person)")
    for command in (
        "gcloud compute firewall-rules list --filter=network=default --format='value(name)'",
        "| xargs -r -n 1 gcloud compute firewall-rules delete --quiet",
        "gcloud compute networks delete default --quiet",
        'NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")',
        "gcloud projects remove-iam-policy-binding PROJECT_ID \\\n"
        '  --member="serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com"'
        " --role=roles/editor",
    ):
        assert command in said, command


def test_the_guide_says_what_a_second_factor_asks_of_whoever_connects():
    assert default_of("os_login_2fa") == "false"
    said = flat(guide_part("## Bootstrap (once, by a person)"))
    assert (
        "**A second factor for SSH** is off by default. With `os_login_2fa = true`, OS Login"
        " asks everyone who connects for a second factor (a security key, an authenticator"
        " code or a phone prompt) at every connection, and their Google account must have"
        " 2-Step Verification on. A script or a scheduled job can then no longer connect by"
        " SSH. Whether to turn it on is the operator's decision." in said
    )


def test_the_guide_names_the_baselines_variables_and_their_defaults():
    said = flat(GUIDE)
    assert default_of("data_access_audit_logs") == "true"
    assert default_of("log_retention_days") == "30"
    assert default_of("firewall_logging") == "false" and default_of("vpc_flow_logs") == "false"
    for sentence in (
        "**Audit logs.** Data Access audit logs for Cloud Storage and Secret Manager are on"
        " (`data_access_audit_logs`):",
        "**Retention.** The project's `_Default` log bucket keeps logs 30 days"
        " (`log_retention_days`, Google's own default);",
        "Firewall rule logging (`firewall_logging`) and VPC flow logs (`vpc_flow_logs`) are"
        " off by default, because both are billed by volume.",
        "The subnet has Private Google Access on.",
    ):
        assert sentence in said, sentence


def test_the_guide_says_the_secret_is_checked_against_its_checksum():
    said = flat(guide_part("## Secret rotation"))
    assert (
        "The VM checks the data it reads against the checksum Secret Manager sends with it"
        " (`dataCrc32c`); when they differ it writes nothing, and `cairn-env.service` fails"
        " with one line in its journal, so cairn does not start." in said
    )


def test_the_customer_managed_keys_section_says_what_the_key_covers_and_its_iam():
    said = flat(guide_part("### Customer-managed keys"))
    for sentence in (
        "Set `kms_key_name` to a Cloud KMS key in the same region as everything else, and the"
        " data disk, the boot disk, the replica bucket and the environment secret use it.",
        "Unset, Google manages the keys, as before.",
        "`roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key for three of Google's service"
        " agents of this project:",
        "service-PROJECT_NUMBER@compute-system.iam.gserviceaccount.com",
        "service-PROJECT_NUMBER@gs-project-accounts.iam.gserviceaccount.com",
        "service-PROJECT_NUMBER@gcp-sa-secretmanager.iam.gserviceaccount.com",
        "Not covered: the disk snapshots, the registry, the logs and the state bucket.",
        "On an existing deployment, setting it replaces the data disk, the VM and the secret,"
        " as far as can be read without a project: read `tofu plan` before `apply`.",
    ):
        assert sentence in said, sentence


def test_what_a_regulated_customer_adds_names_what_these_definitions_do_not_do():
    said = flat(guide_part("## What a regulated customer adds"))
    for words in (
        "a signed agreement with Google",
        "organisation policies",
        "VPC Service Controls",
        "a locked log sink",
        "not ready as they stand for health or education records",
    ):
        assert words in said, words


def test_the_status_paragraph_and_the_dated_record_agree_that_it_was_applied():
    status = flat(GUIDE[: GUIDE.index("## What it builds")])
    record = re.search(r"\*\*Run once on a real project\*\* \((\d{4}-\d{2}-\d{2}): ", GUIDE)
    assert record, "no dated record of the run on a real project"
    day = record.group(1)
    assert f"**Status: applied once to a real project, on {day}," in status
    for contradiction in ("not yet applied", "no apply and no boot on Google Cloud has been run"):
        assert contradiction not in flat(GUIDE), contradiction
    assert "[What was checked](#what-was-checked-and-what-was-not)" in status
    assert "CI formats, validates and plans it with OpenTofu" in status
    assert "without a cloud project" in status


NUMBER_WORDS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
}


def test_the_count_of_systemd_units_is_the_number_cloud_init_writes():
    unit_entry = r"^(?:\$\{brand_files\})?  - path: /etc/systemd/system/"
    written = len(re.findall(unit_entry, TEMPLATE, re.M))
    assert written == len(re.findall(r"path: /etc/systemd/system/", TEMPLATE))
    [word] = re.findall(r"cloud-init writes (\w+) systemd units", GUIDE)
    assert NUMBER_WORDS[word] == written


def test_the_ground_paragraph_names_every_timer_cloud_init_writes():
    timers = re.findall(r"^  - path: /etc/systemd/system/([\w-]+)\.timer$", TEMPLATE, re.M)
    start = GUIDE.index("The others prepare the ground:")
    paragraph = GUIDE[start : GUIDE.index("\n\n", start)]
    for name in timers:
        assert f"`{name}` (a timer" in paragraph, name


def test_no_sentence_says_nothing_runs_the_compaction_by_itself():
    assert "Nothing runs it by itself" not in GUIDE
    start = GUIDE.index("### After a workspace is deleted")
    section = GUIDE[start : GUIDE.index("\n## ", start)]
    assert "cairn-compact" in section
    assert "03:00 UTC" in section


def test_the_silence_alert_paragraph_says_it_arms_itself_on_a_new_vm():
    start = GUIDE.index("**The silence alert**")
    paragraph = flat(GUIDE[start : GUIDE.index("\n\n", start)])
    assert "stays unarmed" not in paragraph
    assert "`replica: current, no database yet, nothing to copy`" in paragraph
    assert "within two minutes of boot" in paragraph
    timer_start = TEMPLATE.index("  - path: /etc/systemd/system/cairn-replica-check.timer\n")
    assert "OnBootSec=2min" in TEMPLATE[timer_start : TEMPLATE.index("\n  - path: ", timer_start)]


def test_the_host_alerts_price_is_four_log_based_policies_at_the_examples_price():
    count = len(re.findall(r'^    "(\w+)" = \{$', (INFRA / "monitoring.tf").read_text(), re.M))
    assert count == 4
    assert "$0.35 a month" in GUIDE
    assert f"${count * 0.35:.2f} a month together" in GUIDE


def test_the_logs_section_says_how_to_keep_a_copy_of_the_audit_log_nobody_can_change():
    """The audit log in the database is rewritten by a forget and pruned past --audit-keep;
    "## Logs" says where its unchangeable copy comes from, by the logger's real name and the
    real option, and that keeping it is the operator's step (no definition here makes it)."""
    from cairn.store.audit import AUDIT_LOGGER
    from cairn.web import cli

    logs = GUIDE[GUIDE.index("\n## Logs\n") : GUIDE.index("\n## Alerts\n")]
    said = " ".join(logs.split())
    assert (
        "The audit log has a copy the server cannot change: each stored event is one JSON line"
        f" from the logger `{AUDIT_LOGGER}` in the container's output. Route those lines with a"
        " Cloud Logging sink to a Cloud Storage bucket with a locked retention policy (Bucket"
        " Lock), so that nobody, the server included, can edit or delete them before the"
        " retention ends. The log in the database is rewritten when a note is forgotten or a"
        " project deleted, and pruned past `--audit-keep`." in said
    )
    parsed = cli._parser().parse_args(["serve", "--db", "x.db", "--audit-keep", "12"])
    assert parsed.audit_keep == 12
