"""Things the infrastructure code must keep saying, read as text (CI runs `tofu validate`), and
what host.sh's run-cairn starts, run from a copy against stand-ins (deploy_standins)."""

import argparse
import base64
import importlib.util
import re
from pathlib import Path

import deploy_standins
import pytest
import yaml
from deploy_standins import EXTRA_ARGS, IMAGE, NEW, PUBLIC_URL, SUBNET, Standins

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra" / "gcp"
_spec = importlib.util.spec_from_file_location(
    "rehearse_host_for_tf", REPO / "scripts/rehearse_host.py"
)
rehearse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearse)


def block(text: str, header: str) -> str:
    """The body of the resource block that starts with `header`, braces balanced."""
    start = text.index(header)
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i : j + 1]
    raise AssertionError(f"unbalanced block after {header!r}")


def test_the_deploy_jobs_status_binding_follows_a_replaced_vm():
    """An instance's IAM policy goes with the instance, and a replaced VM keeps its name: the
    binding that lets the deploy job read the VM's status has to be made again with it."""
    iam = (INFRA / "iam.tf").read_text(encoding="utf-8")
    binding = block(iam, 'resource "google_compute_instance_iam_member" "deployer_status"')
    assert re.search(
        r"lifecycle\s*{\s*replace_triggered_by\s*=\s*\[\s*google_compute_instance\.cairn\.instance_id\s*\]",
        binding,
    )
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    assert 'resource "google_compute_instance" "cairn"' in main


# --- the plans file (cairn_plans_file) ------------------------------------------------------

VARIABLES = (INFRA / "variables.tf").read_text(encoding="utf-8")
MAIN = (INFRA / "main.tf").read_text(encoding="utf-8")
PLANS = "/etc/cairn/plans.yaml"
EXAMPLE = (REPO / "deploy" / "plans.example.yaml").read_text(encoding="utf-8")


def plans_variable() -> str:
    return block(VARIABLES, 'variable "cairn_plans_file"')


def hcl_string(literal: str) -> str:
    """The value of an HCL quoted string's body (the escapes these files use)."""
    escapes = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
    return re.sub(r"\\(.)", lambda m: escapes[m.group(1)], literal)


def validations(variable: str) -> list[tuple[str, str]]:
    """(condition, error message) of each validation block, in order."""
    return re.findall(
        r'validation \{\s*condition\s*=\s*(.+?)\n\s*error_message\s*=\s*"((?:[^"\\]|\\.)*)"',
        variable,
    )


def plain_text_pattern() -> re.Pattern:
    """The regex the variable's plain-text check gives OpenTofu, compiled for Python. RE2's
    `$` without the m flag is the end of the text; Python's also matches before a last line
    break, so it is written as \\Z here."""
    [literal] = re.findall(
        r'regex\("((?:[^"\\]|\\.)*)", file\(var\.cairn_plans_file\)\)', plans_variable()
    )
    pattern = hcl_string(literal)
    assert pattern.endswith("$") and pattern.count("$") == 1, pattern
    return re.compile(pattern[:-1] + r"\Z")


def test_the_plans_file_variable_is_an_optional_path_that_defaults_to_none():
    assert re.search(r"^\s*type\s*=\s*string$", plans_variable(), re.M)
    assert re.search(r'^\s*default\s*=\s*""$', plans_variable(), re.M)
    # Every check lets the default through, and reads the file only inside can() or try(),
    # so a missing file is said once, by the first check, and never as an error of another.
    checks = validations(plans_variable())
    assert len(checks) == 4, checks
    for condition, _ in checks:
        assert re.match(r'var\.cairn_plans_file == "" (\|\||\?)', condition), condition
        guarded = re.sub(r"^.*?\b(can|try)\(", "", condition)
        assert "file(" in guarded and "file(" not in condition[: -len(guarded)], condition


def test_each_plans_file_check_says_what_to_do():
    """In order: it exists, it is not empty, it is at most 16 KiB, it is plain text."""
    exists, empty, small, plain = validations(plans_variable())
    assert "can(file(var.cairn_plans_file))" in exists[0]
    assert "relative to the directory tofu runs in" in exists[1]
    assert 'file(var.cairn_plans_file) != ""' in empty[0]
    assert "Copy deploy/plans.example.yaml" in empty[1]
    assert "length(file(var.cairn_plans_file)) <= 16384" in small[0]
    assert "16 KiB" in small[1] and "metadata" in small[1]
    assert "regex(" in plain[0]
    message = hcl_string(plain[1])
    for words in ("printable ASCII", "Unix line breaks", "other than a space", "exactly one"):
        assert words in message, words
    assert "\\u escapes inside double quotes" in message


def test_the_plain_text_check_takes_what_the_template_installs_byte_for_byte():
    """Every text the regex takes, rendered by the template's own form and read back from
    write_files, is the same text; the texts it refuses include each way that form loses or
    changes bytes, so each part of the rule is needed."""
    pattern = plain_text_pattern()
    taken = [
        EXAMPLE,
        rehearse.PLANS_TEXT,
        "x\n",
        "default: free\n\n\nplans:\n  free:\n    label: Free\n",
        "a:\n\tb  \n   \nc\n",
        "#c\n---\n...\n${not} %{ a template }\n",
    ]
    template = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
    for text in taken:
        assert pattern.match(text), repr(text)
        values = {**rehearse.rehearsal_values(REPO), "plans_yaml": text}
        files = {
            f.path: f for f in rehearse.write_files(rehearse.render_template(template, values))
        }
        assert files[PLANS].content == text, repr(text)
    lossy = ["x", "x\n\n", " x\n  y\n", "a\r\nb\n", "x\u2028y\n", "x\x85y\n"]
    others = ["", "\n", "\nx\n", "caf\u00e9\n", "x\x00\n", "\x1b[0m\n"]
    for text in lossy + others:
        assert not pattern.match(text), repr(text)
    for text in lossy:
        values = {**rehearse.rehearsal_values(REPO), "plans_yaml": text}
        try:
            files = rehearse.write_files(rehearse.render_template(template, values))
        except (rehearse.Failed, yaml.YAMLError):
            continue  # cloud-init would not read the document at all
        assert {f.path: f.content for f in files}[PLANS] != text, repr(text)


def test_the_example_plans_file_fits_well_under_the_cap():
    assert len(EXAMPLE.encode("utf-8")) <= 16384 // 2


def test_main_tf_gives_the_template_the_plans_files_content_or_nothing():
    rendering = re.search(r"templatefile\(.*?\n  \}\)", MAIN, re.S).group(0)
    assert re.search(
        r'^\s*plans_yaml\s*=\s*var\.cairn_plans_file == "" \? "" : file\(var\.cairn_plans_file\)$',
        rendering,
        re.M,
    )


def preconditions() -> list[tuple[str, str]]:
    instance = block(MAIN, 'resource "google_compute_instance" "cairn"')
    lifecycle = block(instance, "lifecycle")
    return re.findall(
        r'precondition \{\s*condition\s*=\s*(.+?)\n\s*error_message\s*=\s*"((?:[^"\\]|\\.)*)"',
        lifecycle,
    )


def precondition(about: str = "--plans") -> tuple[str, str]:
    """The one precondition whose condition names ABOUT."""
    [(condition, message)] = [found for found in preconditions() if about in found[0]]
    return condition, message


def test_plans_in_the_extra_args_beside_the_plans_file_is_refused_at_plan_time():
    condition, message = precondition()
    [literal] = re.findall(r'regex\("((?:[^"\\]|\\.)*)", var\.cairn_extra_args\)', condition)
    assert condition.startswith('var.cairn_plans_file == "" || !can(regex(')
    pattern = re.compile(hcl_string(literal))  # no line break can be in the extra args
    for args in (
        "--plans /data/plans.yaml",
        "--sign-in github --plans=/x",
        "--join personal --plans",
    ):
        assert pattern.search(args), args
    for args in ("", EXTRA_ARGS, "--plansx", "--no-plans", "--allowed-host plans.example.net"):
        assert not pattern.search(args), args
    assert "Remove --plans from cairn_extra_args" in message and "cairn_plans_file" in message


def test_the_tfvars_example_shows_the_plans_file_commented_out():
    example = (INFRA / "terraform.tfvars.example").read_text(encoding="utf-8")
    assert '# cairn_plans_file = "../../deploy/plans.yaml"\n' in example


# --- host.sh run-cairn, with and without a plans file ---------------------------------------


# config.env as cloud-init writes it carries cairn's limits (container_limits); run-cairn does
# not start without them. Made-up values, appended to every stand-in host made here.
CAIRN_LIMITS = 'CAIRN_MEMORY_MB="900"\nCAIRN_PIDS_LIMIT="400"\n'


@pytest.fixture(autouse=True)
def _cairn_limits(monkeypatch):
    made = deploy_standins.HostReport.__init__

    def with_limits(self, standins):
        made(self, standins)
        config = self.root / "config.env"
        config.write_text(config.read_text(encoding="utf-8") + CAIRN_LIMITS, encoding="utf-8")

    monkeypatch.setattr(deploy_standins.HostReport, "__init__", with_limits)


def docker_run(host, mount: tuple[str, ...] = (), option: tuple[str, ...] = ()) -> list[str]:
    """What cmd_run_cairn gives `docker run`, as it did before plans files existed, with the
    plans file's mount and option where they go, and the flags and limits of
    tests/unit/test_host_hardening_unit.py."""
    return [
        "run", "--rm", "--name", "cairn", "--network", "cairn",
        "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=5",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--memory", "900m", "--memory-swap", "900m", "--pids-limit", "400",
        "--env-file", "/run/cairn/cairn.env",
        "-v", f"{host.data}/cairn:/data", *mount,
        f"{IMAGE}@{NEW}", "serve", "--db", "/data/cairn.db", "--host", "0.0.0.0",
        "--public-url", PUBLIC_URL, "--trusted-proxy", SUBNET, *option, *EXTRA_ARGS.split(),
    ]  # fmt: skip


@pytest.mark.parametrize("plans", [None, ""], ids=["no file", "empty file"])
def test_without_a_plans_file_cairn_runs_exactly_as_before(tmp_path, plans):
    """cloud-init writes an empty file when cairn_plans_file is not set; that, or no file at
    all, gives cairn neither a mount nor --plans."""
    host = Standins(tmp_path).host()
    assert host.run_cairn(plans) == docker_run(host)


def test_a_plans_file_is_mounted_read_only_and_named_to_serve(tmp_path):
    host = Standins(tmp_path).host()
    argv = host.run_cairn(rehearse.PLANS_TEXT)
    assert argv == docker_run(
        host, mount=("-v", f"{host.plans_file}:{PLANS}:ro"), option=("--plans", PLANS)
    )


def test_run_cairn_adds_the_plans_file_without_new_word_splitting():
    """The two additions are arrays expanded quoted (the +-form keeps an empty one from
    tripping `set -u` on bash before 4.4, as /bin/bash on macOS is); nothing is eval'd."""
    text = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    body = re.search(r"^cmd_run_cairn\(\) \{\n(.*?)^\}\n", text, re.S | re.M).group(1)
    assert "eval" not in body
    assert f"PLANS_FILE={PLANS}\n" in text
    assert re.search(r'^\s*if \[ -s "\$PLANS_FILE" \]; then$', body, re.M)
    assert f'plans_mount=(-v "$PLANS_FILE:{PLANS}:ro")' in body
    assert f"plans_option=(--plans {PLANS})" in body
    assert '${plans_mount[@]+"${plans_mount[@]}"}' in body
    assert '${plans_option[@]+"${plans_option[@]}"}' in body


# --- the brand (cairn_brand_name, cairn_brand_logo_file, cairn_brand_logo_alt) ---------------
#
# What OpenTofu itself does with the three variables (validations, preconditions, base64 of
# the values, the size of the whole user-data) is run by `tofu test` against a mock provider:
# infra/gcp/tests/brand.tftest.hcl. Here: the template and what cloud-init writes from it.

BRAND = "/etc/cairn/brand"
BRAND_PATHS = [f"{BRAND}/name", f"{BRAND}/logo", f"{BRAND}/logo-alt"]
FIXTURES = INFRA / "tests" / "fixtures"
TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
TFTEST = (INFRA / "tests" / "brand.tftest.hcl").read_text(encoding="utf-8")
NAMES = [
    "Knowledge Base",
    "R&D notes",
    "O'Brien's",
    "Caf\u00e9 notes",
    "$HOME notes",
    "`id` notes",
    'The "Base"',
    "Notes\\Base",
]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
METADATA_VALUE_LIMIT = 256 * 1024  # Compute Engine's limit for one metadata value


def brand_rendering(values: dict[str, str] | None = None, **brand) -> str:
    values = {**rehearse.rehearsal_values(REPO), **(values or {})}
    values["brand_files"] = rehearse.brand_files(**brand)
    return rehearse.render_template(TEMPLATE, values)


def brand_variable(name: str) -> str:
    return block(VARIABLES, f'variable "{name}"')


def hcl_quoted(text: str) -> str:
    """TEXT as the tofu test writes it: an HCL string."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def test_ci_runs_the_tofu_test_after_init():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci-cd.yml").read_text())
    runs = [step.get("run", "") for step in workflow["jobs"]["infra"]["steps"]]
    init = next(i for i, run in enumerate(runs) if "tofu -chdir=infra/gcp init" in run)
    test = next(i for i, run in enumerate(runs) if "tofu -chdir=infra/gcp test" in run)
    assert init < test
    assert 'mock_provider "google"' in TFTEST


def test_without_a_brand_the_user_data_has_not_one_byte_more():
    """The template's one new interpolation stands at the start of a line and renders to
    nothing by default, so the user-data is the template's rendering as it was before it."""
    assert rehearse.rehearsal_values(REPO)["brand_files"] == ""
    assert rehearse.brand_files() == ""
    assert TEMPLATE.count("${brand_files}") == 1
    assert "\n\n${brand_files}  - path: /etc/systemd/system/cairn-disk.service\n" in TEMPLATE
    before = TEMPLATE.replace("${brand_files}", "")
    values = {k: v for k, v in rehearse.rehearsal_values(REPO).items() if k != "brand_files"}
    assert brand_rendering() == rehearse.render_template(before, values)
    paths = [f.path for f in rehearse.write_files(brand_rendering())]
    assert not [p for p in paths if p.startswith(f"{BRAND}/")]


def test_main_tf_builds_the_brand_entries_as_the_rehearsal_does():
    """main.tf's local.brand_files and rehearse_host.brand_files are the same text; the tofu
    test checks the same literal against OpenTofu's own rendering."""
    rendering = re.search(r"templatefile\(.*?\n  \}\)", MAIN, re.S).group(0)
    assert re.search(r"^\s*brand_files\s*=\s*local\.brand_files$", rendering, re.M)
    entry = (
        '  - path: /etc/cairn/brand/name\n    permissions: "0644"\n    owner: root\n'
        '    encoding: b64\n    content: "S25vd2xlZGdlIEJhc2U="\n\n'
    )
    assert rehearse.brand_files(name="Knowledge Base") == entry
    as_hcl = entry.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    assert as_hcl + "  - path: /etc/systemd/system/cairn-disk.service\\n" in TFTEST
    assert (
        '"  - path: ${f.path}\\n    permissions: \\"0644\\"\\n    owner: root\\n    encoding:'
        ' b64\\n    content: \\"${f.content}\\"\\n\\n"' in MAIN
    )
    assert re.findall(r'path = "(/etc/cairn/brand/[a-z-]+)"', MAIN) == BRAND_PATHS


@pytest.mark.parametrize("name", NAMES)
def test_each_name_is_written_byte_for_byte(name):
    """Base64 in the document, so no YAML quoting and no shell ever reads the name; the tofu
    test plans with the same name."""
    files = {f.path: f for f in rehearse.write_files(brand_rendering(name=name))}
    assert files[f"{BRAND}/name"] == (f"{BRAND}/name", 0o644, name.encode("utf-8"))
    assert f"cairn_brand_name = {hcl_quoted(name)}" in re.sub(r" += ", " = ", TFTEST)


@pytest.mark.parametrize("logo", ["logo.png", "logo.svg", "logo-declared.svg"])
def test_a_logo_and_its_alt_text_are_written_byte_for_byte_and_nothing_else_changes(logo):
    data = (FIXTURES / logo).read_bytes()
    given = brand_rendering(name="Knowledge Base", logo=data, alt="Juniper Field")
    files = {f.path: f for f in rehearse.write_files(given)}
    shipped = {f.path: f for f in rehearse.write_files(brand_rendering())}
    assert [p for p in files if p.startswith(f"{BRAND}/")] == BRAND_PATHS
    assert files[f"{BRAND}/logo"] == (f"{BRAND}/logo", 0o644, data)
    assert files[f"{BRAND}/logo-alt"].content == b"Juniper Field"
    assert set(files) - set(shipped) == set(BRAND_PATHS)
    assert all(files[p] == shipped[p] for p in shipped)
    entries = [e for e in yaml.safe_load(given)["write_files"] if e["path"] in BRAND_PATHS]
    for entry in entries:
        assert entry["owner"] == "root" and entry["encoding"] == "b64"
        assert set(entry) == {"path", "permissions", "owner", "encoding", "content"}


def test_each_brand_precondition_says_what_to_change():
    """The tofu test proves each refusal; here, that each message names what to change."""
    said = {condition: message for condition, message in preconditions()}
    assert len(said) == 9
    _, brand_args = precondition("--brand-")
    pattern = re.compile(
        re.search(r'regex\("([^"]*)", var\.cairn_extra_args\)', precondition("--brand-")[0])[1]
    )
    for args in ("--brand-name=x", "--sign-in google --brand-logo /x", "--brand-logo-alt y"):
        assert pattern.search(args), args
    for args in ("--brand-names", "--branding", "--sign-in google --wall", ""):
        assert not pattern.search(args), args
    assert "Remove it from cairn_extra_args and set cairn_brand_name" in brand_args
    _, logo = precondition('var.cairn_brand_logo_file == "" || var.cairn_brand_name')
    assert "Set cairn_brand_name too" in logo
    _, alt = precondition("var.cairn_brand_logo_alt ==")
    assert "Set cairn_brand_logo_file too" in alt
    _, name = precondition('var.cairn_brand_name == "" || can(regex("(^| )--sign-in')
    assert "Add --sign-in PROVIDER (github, google or oidc) to cairn_extra_args" in name
    _, wall = precondition("--(wall|session-days)")
    assert "Add --sign-in PROVIDER" in wall and "or remove --wall and --session-days" in wall
    _, size = precondition("local.user_data_bytes <= 262144")
    assert "Make cairn_brand_logo_file or cairn_plans_file smaller" in size


def test_only_the_values_given_are_written():
    files = [f.path for f in rehearse.write_files(brand_rendering(name="Knowledge Base"))]
    assert [p for p in files if p.startswith(f"{BRAND}/")] == [f"{BRAND}/name"]


def test_the_brand_variables_default_to_nothing_and_say_why_the_user_data_may_carry_them():
    for name in ("cairn_brand_name", "cairn_brand_logo_file", "cairn_brand_logo_alt"):
        variable = brand_variable(name)
        assert re.search(r"^\s*type\s*=\s*string$", variable, re.M), name
        assert re.search(r'^\s*default\s*=\s*""$', variable, re.M), name
    said = " ".join(brand_variable("cairn_brand_name").replace("#", " ").split())
    assert "anyone who may read the instance's metadata can read" in said
    assert "the sign-in page shows" in said


def test_the_logo_cap_is_60_kib_and_the_check_is_exact_on_the_base64():
    """filebase64() is the only way OpenTofu reads a PNG (file() wants UTF-8). 61,440 bytes is
    a multiple of 3, so a file is at most that long exactly when its base64 is at most 81,920
    characters."""
    cap = 60 * 1024
    assert cap % 3 == 0 and len(base64.b64encode(b"\x00" * cap)) == 81920
    assert len(base64.b64encode(b"\x00" * (cap + 1))) > 81920
    variable = brand_variable("cairn_brand_logo_file")
    assert "length(filebase64(var.cairn_brand_logo_file)) <= 81920" in variable
    [(_, message)] = [c for c in validations(variable) if "81920" in c[0]]
    assert "60 KiB (61,440 bytes)" in message and "256 KB" in message
    assert "60 KiB (61,440 bytes)" in variable.split("validation")[0]
    assert (FIXTURES / "logo-at-cap.png").stat().st_size == cap
    assert (FIXTURES / "logo-over-cap.png").stat().st_size == cap + 1


PNG_START = PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR"  # the first 16 bytes of every PNG
SVG_NAMESPACE = 'xmlns="http://www.w3.org/2000/svg"'


def logo_checks() -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    """The patterns of the logo's format and document type checks, compiled for Python: the
    PNG one reads the file's base64, the others its UTF-8 text. They are written with
    [\\t\\r\\n ] for white space (XML's), so RE2 and Python read them alike; RE2's \\x{FEFF}
    is written \\ufeff here."""
    checks = validations(brand_variable("cairn_brand_logo_file"))
    [(form, _)] = [c for c in checks if "<svg" in c[0]]
    [(doctype, _)] = [c for c in checks if "<!DOCTYPE" in c[0]]
    text = r'regex\("((?:[^"\\]|\\.)*)", file\(var\.cairn_brand_logo_file\)\)'
    [png] = re.findall(
        r'regexall\("((?:[^"\\]|\\.)*)", filebase64\(var\.cairn_brand_logo_file\)\)', form
    )
    [svg] = re.findall(text, form)
    [declared] = re.findall(text, doctype)
    assert "\\s" not in svg + declared  # RE2's \s takes a form feed, XML's white space does not

    def compiled(literal: str) -> re.Pattern:
        return re.compile(hcl_string(literal).replace(r"\x{FEFF}", r"﻿"))

    return compiled(png), compiled(svg), compiled(declared)


def plan_takes(data: bytes) -> bool:
    """What the logo's format and document type checks take (the size checks aside)."""
    png, svg, declared = logo_checks()
    if png.search(base64.b64encode(data).decode()):
        return True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False  # file() fails on what is not UTF-8, and can() makes that false
    return svg.search(text) is not None and declared.search(text) is None


def serve_takes(data: bytes, tmp_path: Path) -> bool:
    from cairn.web.brand import read_logo

    path = tmp_path / "logo"
    path.write_bytes(data)
    try:
        read_logo(path)
    except ValueError:
        return False
    return True


SVG_TAKEN = [
    f"<svg {SVG_NAMESPACE}/>",
    "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    f'<?xml version="1.0"?>\n<!-- a -->\n<svg\tviewBox="0 0 1 1"\n {SVG_NAMESPACE} >x</svg>',
    "﻿<svg xmlns = 'http://www.w3.org/2000/svg' data-a='>'/>",
]
SVG_REFUSED = {
    "a document type": f"<!DOCTYPE svg>\n<svg {SVG_NAMESPACE}/>",
    "a document type between comments": f"<!-- a --><!DOCTYPE svg><!-- b --><svg {SVG_NAMESPACE}/>",
    "no namespace": '<svg viewBox="0 0 1 1"/>',
    "another namespace": '<svg xmlns="urn:example:juniper"/>',
    "the namespace on a child": f"<svg><g {SVG_NAMESPACE}/></svg>",
    "the namespace inside a value": "<svg a=\"xmlns='http://www.w3.org/2000/svg'\"/>",
    "another element": f"<svgx {SVG_NAMESPACE}/>",
    "a space before the declaration": f' <?xml version="1.0"?><svg {SVG_NAMESPACE}/>',
    "a comment holding --": f"<!-- a -- b --><svg {SVG_NAMESPACE}/>",
    "html": f"<html><svg {SVG_NAMESPACE}/></html>",
}
# What a pattern on the start of a file cannot see: the description and the README say so.
SVG_NOT_WELL_FORMED = [
    f"<svg {SVG_NAMESPACE}><g></svg>",
    f'<svg xmlns="urn:example:juniper" {SVG_NAMESPACE}/>',
]


def test_the_logo_check_takes_what_serve_takes_and_refuses_what_it_can_see_serve_refuse(
    tmp_path,
):
    for text in SVG_TAKEN:
        data = text.encode("utf-8")
        assert serve_takes(data, tmp_path) and plan_takes(data), text
    for why, text in SVG_REFUSED.items():
        data = text.encode("utf-8")
        assert not serve_takes(data, tmp_path), why
        assert not plan_takes(data), why
    for text in SVG_NOT_WELL_FORMED:
        data = text.encode("utf-8")
        assert not serve_takes(data, tmp_path) and plan_takes(data), text


def test_the_png_check_is_the_first_16_bytes_of_every_png(tmp_path):
    """A PNG is its 8-byte signature and then the IHDR chunk, whose length is 13: 16 bytes
    every PNG starts with, which is what serve looks for. In base64 the first 21 characters
    hold 126 of those bits, and the 22nd the last 2 of them (10) with 4 of the next byte's."""
    for following in range(256):
        data = PNG_START + bytes([following]) + b"\x00" * 16
        assert plan_takes(data) and serve_takes(data, tmp_path), following
    assert plan_takes(PNG_START) and serve_takes(PNG_START, tmp_path)
    # serve does not look at the IHDR chunk's length; the plan does, as every PNG has 13 there.
    assert not plan_takes(PNG_SIGNATURE + b"\x00\x00\x00\x0eIHDR" + b"\x00" * 16)
    for refused in (
        PNG_SIGNATURE + b"\x00" * 40,
        PNG_START[:15],
        *(PNG_START[:15] + bytes([last]) + b"\x00" * 16 for last in b"PQSr"),
        PNG_SIGNATURE + b"\x00\x00\x00\x0dIDAT" + b"\x00" * 16,
        (FIXTURES / "not-a-logo.gif").read_bytes(),
    ):
        assert not serve_takes(refused, tmp_path), refused[:16]
        assert not plan_takes(refused), refused[:16]


def tftest_logos() -> tuple[set[str], set[str]]:
    """The logo files the tofu test plans with: those of runs it expects to be taken, and
    those of runs it expects to be refused."""
    runs = re.findall(r'^run "[^"]+" \{\n(.*?)^\}', TFTEST, re.S | re.M)
    taken, refused = set(), set()
    for run in runs:
        named = re.search(r'cairn_brand_logo_file\s*=\s*"tests/fixtures/([^"]+)"', run)
        if named:
            (refused if "expect_failures" in run else taken).add(named[1])
    return taken, refused


def test_each_logo_the_tofu_test_plans_as_taken_is_one_serve_takes(tmp_path):
    """A fixture the plan takes is one serve would take, so the tofu test proves nothing
    a container on the VM would undo; and each format refusal is one serve refuses too."""
    taken, refused = tftest_logos()
    assert {"logo.svg", "logo.png", "logo-at-cap.png", "logo-declared-no-doctype.svg"} <= taken
    for name in taken:
        data = (FIXTURES / name).read_bytes()
        assert serve_takes(data, tmp_path), name
        assert plan_takes(data), name
    for name in (
        "logo-declared.svg",
        "logo-doctype-between-comments.svg",
        "logo-no-namespace.svg",
        "logo-other-namespace.svg",
        "logo-namespace-on-a-child.svg",
        "logo-space-before-declaration.svg",
        "logo-no-ihdr.png",
    ):
        assert name in refused and name not in taken, name
        data = (FIXTURES / name).read_bytes()
        assert not serve_takes(data, tmp_path) and not plan_takes(data), name


def test_a_document_type_is_refused_with_why_serve_refuses_it():
    """Under `tofu test` OpenTofu showed only the first check of a variable that failed, and
    the format check refuses a document type too, so this check comes before it."""
    checks = validations(brand_variable("cairn_brand_logo_file"))
    [(condition, message)] = [c for c in checks if "<!DOCTYPE" in c[0]]
    [(form, _)] = [c for c in checks if "<svg" in c[0]]
    assert checks.index((condition, message)) < [c[0] for c in checks].index(form)
    assert condition.startswith('var.cairn_brand_logo_file == "" ? true : try(!can(regex(')
    message = hcl_string(message)
    assert "document type declaration (<!DOCTYPE" in message
    assert "serve refuses" in message and "fetches nothing and expands nothing" in message
    assert "Remove the declaration" in message


def test_the_logo_variable_says_what_the_plan_checks_and_that_serve_may_still_refuse():
    variable = brand_variable("cairn_brand_logo_file")
    said = hcl_string(re.search(r'description = "((?:[^"\\]|\\.)*)"', variable)[1])
    assert "tofu plan checks only how the file starts" in said
    assert "the 16 bytes every PNG starts with" in said
    assert SVG_NAMESPACE in said and "no document type" in said
    assert "serve reads the whole file at start" in said
    assert "may still refuse" in said
    start, end = VARIABLES.index("# The brand:"), VARIABLES.index('variable "cairn_brand_name"')
    comment = VARIABLES[start:end]
    assert "The checks below are serve's own rules" not in comment
    assert "the logo's checks see only how the file starts" in " ".join(
        comment.replace("#", " ").split()
    )


# --- --session-days in cairn_extra_args --------------------------------------------------------


def test_session_days_is_held_at_plan_time_to_what_serve_takes():
    """serve's --session-days takes a whole number from 1 to 365 (cairn.web.cli._days) and
    refuses anything else at start. The plan reads each --session-days, as `--session-days N`
    or `--session-days=N`, and refuses one that is not followed by such a number; it may be
    stricter than int() (`+30`, `3_0`), never looser."""
    from cairn.web.cli import _days

    condition, message = precondition('"--session-days="')
    assert 'w == "--session-days"' in condition
    assert 'startswith(w, "--session-days=")' in condition
    assert "alltrue([for i, w in local.extra_words" in condition
    assert re.search(
        r'^\s*extra_words\s*=\s*\[for w in split\(" ", var\.cairn_extra_args\) : w if w != ""\]$',
        MAIN,
        re.M,
    )
    [literal] = re.findall(r'regex\("((?:[^"\\]|\\.)*)", ', condition)
    number = re.compile(hcl_string(literal))

    def serve_takes_days(word: str) -> bool:
        try:
            _days(word)
        except argparse.ArgumentTypeError:
            return False
        return True

    words = [str(n) for n in range(-5, 400)] + [
        *("", "1.5", "30.0", "thirty", "+30", "3_0", "030", "0030", "0x1e", "1e2", "--wall"),
    ]
    for word in words:
        if number.search(word):
            assert serve_takes_days(word), word
    for n in range(1, 366):
        assert number.search(str(n)), n
    for word in ("", "0", "00", "366", "1000", "1.5", "thirty", "--wall", "-30"):
        assert not number.search(word), word
    message = hcl_string(message)
    assert "--session-days 30" in message and "--session-days=30" in message
    assert "a whole number of days from 1 to 365" in message


def test_a_wall_with_a_value_is_refused_at_plan_time_as_serve_refuses_it():
    """--wall takes no value: serve's parser exits at start on --wall=VALUE, and the older
    precondition counts --wall= as a --wall, so the plan refuses the value itself."""
    from cairn.web.cli import _parser

    with pytest.raises(SystemExit):
        _parser().parse_args(["serve", "--sign-in", "google", "--wall=yes"])
    condition, message = precondition('"--wall="')
    assert condition == '!anytrue([for w in local.extra_words : startswith(w, "--wall=")])'
    assert "--wall takes no value" in message and "Write --wall alone" in message


def test_the_largest_plans_file_and_the_largest_logo_leave_room_under_the_limit():
    """The arithmetic behind the logo's cap, with this repository's host.sh, Caddyfile and
    litestream.yml: a 16 KiB plans file in the shape that grows most (a line break nearly
    every byte, each next line indented 6 spaces), a 60 KiB logo, and a name and an alt text
    of 60 four-byte characters. 4 KiB stay free for real values longer than the rehearsal's:
    the domain (twice, at most 253 bytes each), the registry path, the image references,
    cairn_extra_args, and an alt text of up to 200 characters (here 60). It was 8 KiB until
    host.sh gained `compact` (about 4 KiB with the template's indentation). Some of those
    values have no length bound of their own, so main.tf's precondition on the whole
    user-data, checked at plan time, is what holds the line on the VM."""
    plans = (FIXTURES / "plans-largest.yaml").read_text(encoding="utf-8")
    assert len(plans) == 16384 and plain_text_pattern().match(plans)
    assert plans.count("\n") == 16384 - 2
    wide = "\U0001d50e" * 60
    given = brand_rendering(
        {"plans_yaml": plans},
        name=wide,
        logo=(FIXTURES / "logo-at-cap.png").read_bytes(),
        alt=wide,
    )
    size = len(given.encode("utf-8"))
    assert size <= METADATA_VALUE_LIMIT - 4 * 1024, size
    assert given.isascii()


# --- host.sh run-cairn, with a brand ----------------------------------------------------------


def as_serve_reads_them(argv: list[str]) -> argparse.Namespace:
    """The brand options of a `docker run` command line, read by argparse as serve's parser
    reads options (allow_abbrev=False); the server's own parser has them only once the server
    side is merged."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    for option in ("--brand-name", "--brand-logo", "--brand-logo-alt", "--plans"):
        parser.add_argument(option)
    known, _ = parser.parse_known_args(argv[argv.index("serve") + 1 :])
    return known


@pytest.mark.parametrize("name", [*NAMES, "-Knowledge Base", "Knowledge=Base"])
def test_run_cairn_gives_serve_each_name_as_one_argument_exactly(tmp_path, name):
    """Read whole from its file and given as one word: no shell evaluates $HOME, a backtick, a
    quote or a backslash in it, nothing splits it at a space, and a leading dash or an equals
    sign is still part of the value."""
    host = Standins(tmp_path).host()
    argv = host.run_cairn(None, brand={"name": name.encode("utf-8")})
    assert argv == docker_run(host, option=(f"--brand-name={name}",))
    assert as_serve_reads_them(argv).brand_name == name


def test_a_logo_is_mounted_read_only_and_named_to_serve_with_its_alt_text(tmp_path):
    host = Standins(tmp_path).host()
    brand = {
        "name": b"Knowledge Base",
        "logo": (FIXTURES / "logo.png").read_bytes(),
        "logo-alt": b"Juniper Field",
    }
    argv = host.run_cairn(None, brand=brand)
    assert argv == docker_run(
        host,
        mount=("-v", f"{host.brand_dir}/logo:{BRAND}/logo:ro"),
        option=(
            "--brand-name=Knowledge Base",
            "--brand-logo",
            f"{BRAND}/logo",
            "--brand-logo-alt=Juniper Field",
        ),
    )
    read = as_serve_reads_them(argv)
    assert (read.brand_name, read.brand_logo, read.brand_logo_alt) == (
        "Knowledge Base",
        f"{BRAND}/logo",
        "Juniper Field",
    )


def test_the_brand_comes_after_the_plans_file_and_before_the_extra_arguments(tmp_path):
    host = Standins(tmp_path).host()
    brand = {"name": b"Knowledge Base", "logo": (FIXTURES / "logo.svg").read_bytes()}
    argv = host.run_cairn(rehearse.PLANS_TEXT, brand=brand)
    assert argv == docker_run(
        host,
        mount=(
            "-v",
            f"{host.plans_file}:{PLANS}:ro",
            "-v",
            f"{host.brand_dir}/logo:{BRAND}/logo:ro",
        ),
        option=("--plans", PLANS, "--brand-name=Knowledge Base", "--brand-logo", f"{BRAND}/logo"),
    )


@pytest.mark.parametrize(
    "brand",
    [{}, {"name": b"", "logo": b"", "logo-alt": b""}],
    ids=["no files", "empty files"],
)
def test_without_a_brand_cairn_runs_exactly_as_before(tmp_path, brand):
    host = Standins(tmp_path).host()
    assert host.run_cairn(None, brand=brand) == docker_run(host)


def test_run_cairn_adds_the_brand_without_eval_or_word_splitting():
    text = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    body = re.search(r"^cmd_run_cairn\(\) \{\n(.*?)^\}\n", text, re.S | re.M).group(1)
    assert "eval" not in body
    assert f"BRAND_DIR={BRAND}\n" in text
    assert 'brand_option+=("--brand-name=$(<"$BRAND_DIR/name")")' in body
    assert 'brand_option+=("--brand-logo-alt=$(<"$BRAND_DIR/logo-alt")")' in body
    assert f'brand_mount=(-v "$BRAND_DIR/logo:{BRAND}/logo:ro")' in body
    assert f"brand_option+=(--brand-logo {BRAND}/logo)" in body
    assert '${brand_mount[@]+"${brand_mount[@]}"}' in body
    assert '${brand_option[@]+"${brand_option[@]}"}' in body
    # The paths host.sh reads are the ones main.tf has cloud-init write.
    assert [f"{BRAND}/{n}" for n in ("name", "logo", "logo-alt")] == BRAND_PATHS


def test_plan_time_holds_a_name_and_an_alt_to_serves_own_rules():
    """A value serve refuses at start is refused by `tofu plan` first, or the VM gets a
    container that never comes up: the same length, no control or format character, no < or
    >, not blank (cairn.web.brand._plain, NAME_MAX and ALT_MAX)."""
    from cairn.web import brand

    for variable, most in (
        ("cairn_brand_name", brand.NAME_MAX),
        ("cairn_brand_logo_alt", brand.ALT_MAX),
    ):
        said = brand_variable(variable)
        rule = f'regex("^[^\\\\p{{Cc}}\\\\p{{Cf}}<>]{{1,{most}}}$", var.{variable})'
        assert rule in said, (variable, rule)
        assert f'trimspace(var.{variable}) != ""' in said, variable
        assert said.count(f"1 to {most} characters") >= 2, variable  # described and refused
    for refused in ("Knowledge\u200bBase", "   ", "K" * 61, "<b>"):
        with pytest.raises(ValueError):
            brand._plain(refused, "--brand-name", brand.NAME_MAX)


# --- the replica alert (monitoring.tf) ---------------------------------------------------------

MONITORING = (INFRA / "monitoring.tf").read_text(encoding="utf-8")


def replica_policy() -> str:
    return block(MONITORING, 'resource "google_monitoring_alert_policy" "replica"')


def test_the_replica_alert_matches_litestreams_error_lines_from_the_vms_container():
    """One log-match condition: the log the VM's containers write to, this instance, the
    container host.sh names litestream, and a line at Litestream's level for a failure (or the
    line it prints when it exits at start)."""
    matched = block(replica_policy(), "condition_matched_log")
    found = re.search(r"filter\s*=\s*<<-EOT\n(.*?)\n\s*EOT\n", matched, re.S)
    assert found, matched
    lines = [line.strip() for line in found.group(1).splitlines()]
    assert lines == [
        'logName = "projects/${var.project_id}/logs/cos_containers"',
        'AND resource.type = "gce_instance"',
        'AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"',
        'AND jsonPayload."cos.googleapis.com/container_name" = "litestream"',
        'AND jsonPayload.message =~ "^(time=[^ ]+ level=ERROR|Error:) "',
    ]
    assert replica_policy().count("conditions {") == 1  # a log-match condition stands alone
    host = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    assert '$runner docker run --rm --name "$name"' in host
    assert "litestream exec litestream replicate" in host  # the container's name: litestream


def test_the_replica_alert_emails_the_same_address_at_most_once_an_hour():
    assert re.search(
        r"notification_channels\s*=\s*\[google_monitoring_notification_channel\.email\.name\]",
        replica_policy(),
    )
    strategy = block(replica_policy(), "alert_strategy")
    assert re.search(r'notification_rate_limit\s*{\s*period\s*=\s*"3600s"\s*}', strategy)
    assert re.search(r'auto_close\s*=\s*"1800s"', strategy)  # Google's minimum


def test_the_replica_alerts_documentation_names_its_runbook_section():
    documentation = block(replica_policy(), "documentation")
    assert 'mime_type = "text/markdown"' in documentation
    heading = "When the replica alert fires"
    assert f'\\"{heading}\\" in infra/gcp/README.md' in documentation
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    assert f"\n### {heading}\n" in readme


def test_the_first_alert_its_check_and_its_email_are_unchanged():
    """Adding the replica alert moves nothing that was there: each block, byte for byte."""
    import hashlib

    for header, digest in (
        ('resource "google_monitoring_notification_channel" "email"', "f57a25ce1363dc3a"),
        ('resource "google_monitoring_uptime_check_config" "healthz"', "766da81c313d824f"),
        ('resource "google_monitoring_alert_policy" "healthz"', "a9e9518e230e2483"),
    ):
        text = block(MONITORING, header)
        assert hashlib.sha256(text.encode()).hexdigest()[:16] == digest, header


# --- the replica check and the alert on its silence ---------------------------------------------

TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
MAIN_TF = (INFRA / "main.tf").read_text(encoding="utf-8")


def silence_policy() -> str:
    return block(MONITORING, 'resource "google_monitoring_alert_policy" "replica_silent"')


def heredoc_lines(text: str) -> list[str]:
    found = re.search(r"filter\s*=\s*<<-EOT\n(.*?)\n\s*EOT\n", text, re.S)
    assert found, text
    return [line.strip() for line in found.group(1).splitlines()]


def test_the_first_replica_alert_is_unchanged_by_the_second():
    import hashlib

    text = block(MONITORING, 'resource "google_monitoring_alert_policy" "replica"')
    assert hashlib.sha256(text.encode()).hexdigest()[:16] == "c344cd8da182dac6"


def test_a_log_based_metric_counts_the_current_lines_from_litestreams_container():
    metric = block(MONITORING, 'resource "google_logging_metric" "replica_current"')
    assert heredoc_lines(metric) == [
        'logName = "projects/${var.project_id}/logs/cos_containers"',
        'AND resource.type = "gce_instance"',
        'AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"',
        'AND jsonPayload."cos.googleapis.com/container_name" = "litestream"',
        'AND jsonPayload.message =~ "^replica: current, "',
    ]
    descriptor = block(metric, "metric_descriptor")
    assert re.search(r'metric_kind\s*=\s*"DELTA"', descriptor)
    assert re.search(r'value_type\s*=\s*"INT64"', descriptor)
    assert re.search(r'name\s*=\s*"\$\{var\.name\}-replica-current"', metric)


def test_the_silence_alert_fires_after_20_minutes_without_a_current_line():
    policy = silence_policy()
    assert policy.count("conditions {") == 1
    absent = block(policy, "condition_absent")
    assert re.search(r'duration\s*=\s*"1200s"', absent)
    assert (
        'metric.type=\\"logging.googleapis.com/user/'
        '${google_logging_metric.replica_current.name}\\"' in absent
    )
    assert re.search(r"trigger\s*{\s*count\s*=\s*1\s*}", absent)
    assert re.search(r'cross_series_reducer\s*=\s*"REDUCE_SUM"', absent)  # one series


def test_the_silence_alert_emails_the_same_address_when_it_opens_only():
    """A metric condition takes no notification_rate_limit (Google applies it to log-match
    conditions only): an incident emails once when it opens, and not again until it has closed
    (a current line is counted) and a new one opens after 20 more minutes of silence."""
    policy = silence_policy()
    assert re.search(
        r"notification_channels\s*=\s*\[google_monitoring_notification_channel\.email\.name\]",
        policy,
    )
    strategy = block(policy, "alert_strategy")
    assert re.search(r'notification_prompts\s*=\s*\["OPENED"\]', strategy)
    # Google closes an incident that has had no data for auto_close; for an absence that would
    # close it in the middle of the silence, and a new one would email again.
    assert "auto_close" not in strategy
    assert "notification_rate_limit" not in strategy


def test_the_silence_alerts_documentation_names_its_runbook_section():
    documentation = block(silence_policy(), "documentation")
    heading = "When the replica goes quiet"
    assert f'\\"{heading}\\" in infra/gcp/README.md' in documentation
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    assert f"\n### {heading}\n" in readme


def unit_text(name: str) -> str:
    start = TEMPLATE.index(f"/etc/systemd/system/{name}")
    rest = TEMPLATE[start:]
    end = min(i for i in (rest.find("\n  - path:"), rest.find("\n# ")) if i != -1)
    return rest[:end]


def test_a_timer_runs_the_replica_check_every_five_minutes():
    service = unit_text("cairn-replica-check.service")
    assert "Type=oneshot\n" in service
    assert "ExecStart=/bin/bash /etc/cairn/host.sh replica-check\n" in service
    assert "Requires=cairn-disk.service cairn-network.service\n" in service
    timer = unit_text("cairn-replica-check.timer")
    assert "OnCalendar=*:0/5\n" in timer
    runcmd = TEMPLATE[TEMPLATE.index("runcmd:") :]
    assert "cairn-replica-check.timer" in runcmd


def test_the_bound_is_15_minutes_from_main_tf_and_reaches_host_sh_through_config_env():
    assert 'REPLICA_BOUND_SECONDS="${replica_bound_seconds}"' in TEMPLATE
    assert re.search(r"^\s*replica_bound_seconds\s*=\s*900\n", MAIN_TF, re.M)
    host = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    assert '"${REPLICA_BOUND_SECONDS:-}"' in host
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    assert "replica_bound" not in variables  # nothing new for the operator to set


# --- the alert on a compaction that keeps not finishing -----------------------------------------

_stack_spec = importlib.util.spec_from_file_location(
    "rehearse_stack_for_compaction", REPO / "scripts" / "rehearse_stack.py"
)
stack = importlib.util.module_from_spec(_stack_spec)
_stack_spec.loader.exec_module(stack)

COMPACT_METRIC = 'resource "google_logging_metric" "compact_not_finished"'
COMPACT_POLICY = 'resource "google_monitoring_alert_policy" "compact_not_finished"'


def compact_policy() -> str:
    return block(MONITORING, COMPACT_POLICY)


def compact_filter() -> list:
    return stack.parse_filter(
        "\n".join(heredoc_lines(block(MONITORING, COMPACT_METRIC))), stack.STAND_INS
    )


class _Held:
    """A store whose compaction meets another connection, or another failure, or none;
    `only_log`: a rewrite already done, so only emptying the log is due."""

    def __init__(self, error: Exception | None, only_log: bool = False) -> None:
        self.error = error
        self.only_log = only_log

    def compaction_state(self):
        from cairn.store import CompactionState

        return CompactionState(deleted_at="2026-11-19T09:00:00+00:00", rewritten=self.only_log)

    def compacting(self):
        """The lock that keeps one compaction at a time: no other one runs here."""
        import contextlib

        return contextlib.nullcontext()

    def compact(self) -> None:
        if self.error is not None:
            if getattr(self.error, "rewrote", None):  # as a store's record: rewritten
                self.only_log = True
            raise self.error

    empty_log = compact


def serve_line(error: Exception | None, capsys, only_log: bool = False) -> str:
    """The line `serve` writes to stderr for one compaction, by the compactor and the handler
    serve itself installs."""
    from cairn.web.cli import _access_log_on_stderr
    from cairn.web.compactor import Compactor

    capsys.readouterr()
    with _access_log_on_stderr():
        Compactor(_Held(error, only_log), 24).run_once()
    (line,) = capsys.readouterr().err.splitlines()
    return line


def test_the_earlier_metric_and_silence_alert_are_unchanged_by_the_compaction_alert():
    import hashlib

    for header, digest in (
        ('resource "google_logging_metric" "replica_current"', "e64b16b25a5f25fe"),
        ('resource "google_monitoring_alert_policy" "replica_silent"', "12ea635ba15232dc"),
    ):
        text = block(MONITORING, header)
        assert hashlib.sha256(text.encode()).hexdigest()[:16] == digest, header


def test_a_log_based_metric_counts_the_unfinished_compaction_lines_from_cairns_container():
    metric = block(MONITORING, COMPACT_METRIC)
    assert heredoc_lines(metric) == [
        'logName = "projects/${var.project_id}/logs/cos_containers"',
        'AND resource.type = "gce_instance"',
        'AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"',
        'AND jsonPayload."cos.googleapis.com/container_name" = "cairn"',
        'AND jsonPayload.message =~ "^cairn[.]access: compact not finished: "',
    ]
    descriptor = block(metric, "metric_descriptor")
    assert re.search(r'metric_kind\s*=\s*"DELTA"', descriptor)
    assert re.search(r'value_type\s*=\s*"INT64"', descriptor)
    assert re.search(r'name\s*=\s*"\$\{var\.name\}-compact-not-finished"', metric)


def test_the_line_serve_writes_for_an_unfinished_compaction_is_counted(capsys):
    from cairn.store import CompactIncomplete

    line = serve_line(CompactIncomplete("held"), capsys)
    assert line.startswith("cairn.access: compact not finished: another connection held the file")
    assert stack.matches(compact_filter(), stack.cos_entry(line, container="cairn"))


def test_a_rewrite_whose_log_was_held_and_a_held_log_alone_are_counted(capsys):
    """Each still says another connection held the file; what follows says whether the file
    was rewritten, so the runbook can tell the two apart."""
    from cairn.store import CompactIncomplete

    rewrote = CompactIncomplete("held")
    rewrote.rewrote = True
    line = serve_line(rewrote, capsys)
    assert line.startswith(
        "cairn.access: compact not finished: another connection held the file"
        " (rewritten; the write-ahead log was not emptied) in "
    )
    assert stack.matches(compact_filter(), stack.cos_entry(line, container="cairn"))
    line = serve_line(CompactIncomplete("held"), capsys, only_log=True)
    assert line.startswith(
        "cairn.access: compact not finished: another connection held the file"
        " (no rewrite: only the write-ahead log was due) in "
    )
    assert stack.matches(compact_filter(), stack.cos_entry(line, container="cairn"))


def test_a_compaction_that_did_not_start_for_want_of_room_is_not_counted(capsys):
    from cairn.store import NoRoom

    line = serve_line(NoRoom(10, 20), capsys)
    assert line.startswith("cairn.access: compact not started: ")
    assert not stack.matches(compact_filter(), stack.cos_entry(line, container="cairn"))
    line = serve_line(None, capsys, only_log=True)
    assert line.startswith("cairn.access: compact done (no rewrite")
    assert not stack.matches(compact_filter(), stack.cos_entry(line, container="cairn"))


def test_a_finished_or_otherwise_failed_compaction_and_other_containers_are_not_counted(capsys):
    from cairn.store import CompactIncomplete

    for error in (None, OSError("disk")):
        line = serve_line(error, capsys)
        assert line.startswith("cairn.access: compact ")
        assert not stack.matches(compact_filter(), stack.cos_entry(line, container="cairn"))
    line = serve_line(CompactIncomplete("held"), capsys)
    for container in ("litestream", "caddy", "cairn-replica-check"):
        assert not stack.matches(compact_filter(), stack.cos_entry(line, container=container))
    assert not stack.matches(
        compact_filter(), stack.cos_entry(line, container="cairn", instance_id="1")
    )


def test_the_compaction_alert_fires_on_a_third_line_within_an_hour():
    policy = compact_policy()
    assert policy.count("conditions {") == 1
    threshold = block(policy, "condition_threshold")
    assert (
        'metric.type=\\"logging.googleapis.com/user/'
        '${google_logging_metric.compact_not_finished.name}\\"' in threshold
    )
    assert re.search(r'comparison\s*=\s*"COMPARISON_GT"', threshold)
    assert re.search(r"threshold_value\s*=\s*2\n", threshold)
    aggregations = block(threshold, "aggregations")
    assert re.search(r'alignment_period\s*=\s*"3600s"', aggregations)
    assert re.search(r'per_series_aligner\s*=\s*"ALIGN_SUM"', aggregations)
    assert re.search(r'cross_series_reducer\s*=\s*"REDUCE_SUM"', aggregations)
    # Google ignores evaluation_missing_data when duration is zero: one minute keeps it read.
    assert re.search(r'duration\s*=\s*"60s"', threshold)
    assert re.search(r'evaluation_missing_data\s*=\s*"EVALUATION_MISSING_DATA_INACTIVE"', threshold)
    assert re.search(r"trigger\s*{\s*count\s*=\s*1\s*}", threshold)


def test_the_compaction_alert_emails_the_same_address_once_per_incident():
    policy = compact_policy()
    assert re.search(
        r"notification_channels\s*=\s*\[google_monitoring_notification_channel\.email\.name\]",
        policy,
    )
    strategy = block(policy, "alert_strategy")
    assert re.search(r'notification_prompts\s*=\s*\["OPENED"\]', strategy)
    assert "notification_rate_limit" not in strategy


def test_the_compaction_alerts_documentation_names_its_runbook_section():
    documentation = block(compact_policy(), "documentation")
    assert 'mime_type = "text/markdown"' in documentation
    heading = "When a compaction keeps not finishing"
    assert f'\\"{heading}\\" in infra/gcp/README.md' in documentation
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    assert f"\n### {heading}\n" in readme
