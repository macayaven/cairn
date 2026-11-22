"""The parts of scripts/rehearse_host.py that decide a pass or a fail, without Docker or root.

The rehearsal itself runs in CI on a throwaway runner. What it renders, which files it
installs, and what it concludes from a command's output is plain text handling, tested here.
"""

import base64
import importlib.util
import subprocess

import pytest
import yaml
from starlette.testclient import TestClient

from cairn.plans import load as load_plans
from cairn.plans import plan_cap
from cairn.server import create_app
from cairn.store import Store
from support.paths import REPO_ROOT

REPO = REPO_ROOT
SCRIPT = REPO / "scripts" / "rehearse_host.py"
spec = importlib.util.spec_from_file_location("rehearse_host", SCRIPT)
rehearse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearse)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


# --- rendering the template ---------------------------------------------------------------


def test_names_are_replaced_and_values_are_not_read_again():
    text = 'A="${first}" B="${ second }"\n'
    values = {"first": "one ${second} %{ if }", "second": "two"}
    assert rehearse.render_template(text, values) == 'A="one ${second} %{ if }" B="two"\n'


def test_indent_of_chomp_drops_trailing_newlines_and_indents_every_later_line():
    text = "content: |\n  ${indent(2, chomp(body))}\nnext: 1\n"
    values = {"body": "line one\n\nline three\n\n\n"}
    assert rehearse.render_template(text, values) == (
        "content: |\n  line one\n  \n  line three\nnext: 1\n"
    )


def test_chomp_removes_crlf_but_not_a_lone_carriage_return():
    text = "${indent(0, chomp(a))}|${indent(0, chomp(b))}"
    assert rehearse.render_template(text, {"a": "x\r\n\n", "b": "y\r"}) == "x|y\r"


@pytest.mark.parametrize(
    "text",
    [
        "${upper(name)}",
        "${indent(2, name)}",
        "%{ if name }x%{ endif }",
        "$${name}",
        "%%{name}",
        "${name~}",
        "${~name}",
        "${name",
        '${"literal"}',
    ],
)
def test_any_other_template_form_fails_loudly(text):
    with pytest.raises(rehearse.Failed):
        rehearse.render_template(text, {"name": "value"})


def test_a_name_without_a_value_fails_and_says_which():
    with pytest.raises(rehearse.Failed, match="missing"):
        rehearse.render_template("${here} ${missing}", {"here": "1"})


def test_the_rehearsal_gives_the_template_exactly_the_names_opentofu_gives_it():
    template = (REPO / "infra/gcp/cloud-init.yaml.tftpl").read_text()
    main_tf = (REPO / "infra/gcp/main.tf").read_text()
    values = rehearse.rehearsal_values(REPO)
    assert rehearse.template_names(template) <= set(values)
    assert set(values) == rehearse.templatefile_keys(main_tf)


def test_image_pins_are_read_from_the_variables_defaults():
    tf = (
        'variable "caddy_image" {\n  type    = string\n  default = "caddy:1@sha256:abc"\n'
        "  validation {\n    condition = true\n  }\n}\n\n"
        'variable "other" {\n  default = "no"\n}\n'
    )
    assert rehearse.variable_defaults(tf, ["caddy_image"]) == {"caddy_image": "caddy:1@sha256:abc"}
    with pytest.raises(rehearse.Failed):
        rehearse.variable_defaults(tf, ["litestream_image"])


# --- reading write_files and runcmd ---------------------------------------------------------


def _shipped():
    template = (REPO / "infra/gcp/cloud-init.yaml.tftpl").read_text()
    return rehearse.render_template(template, rehearse.rehearsal_values(REPO))


def test_the_shipped_template_installs_its_files_byte_for_byte():
    files = {f.path: f for f in rehearse.write_files(_shipped())}
    assert files["/etc/cairn/host.sh"].content == (REPO / "infra/gcp/files/host.sh").read_bytes()
    assert files["/etc/cairn/host.sh"].permissions == 0o755
    assert files["/etc/cairn/Caddyfile"].content == (REPO / "deploy/Caddyfile").read_text()
    assert (
        files["/etc/cairn/litestream.yml"].content == (REPO / "deploy/litestream.yml").read_text()
    )
    config = files["/etc/cairn/config.env"].content
    assert f'DATA_DEVICE="{rehearse.DEVICE_LINK}"\n' in config
    assert f'CAIRN_IMAGE="{rehearse.CAIRN_IMAGE}"\n' in config
    units = rehearse.unit_names(list(files.values()))
    assert "cairn.service" in units and "cairn-update.timer" in units
    assert all(files[f"/etc/systemd/system/{unit}"].permissions == 0o644 for unit in units)


def test_write_files_refuses_what_it_cannot_install_faithfully():
    encoded = (
        "#cloud-config\nwrite_files:\n  - path: /etc/x\n    encoding: gzip\n    content: eA==\n"
    )
    with pytest.raises(rehearse.Failed, match="encoding"):
        rehearse.write_files(encoded)
    unknown = "#cloud-config\nwrite_files:\n  - path: /etc/x\n    defer: true\n    content: x\n"
    with pytest.raises(rehearse.Failed, match="defer"):
        rehearse.write_files(unknown)
    broken = (
        '#cloud-config\nwrite_files:\n  - path: /etc/x\n    encoding: b64\n    content: "e A"\n'
    )
    with pytest.raises(rehearse.Failed, match="base64"):
        rehearse.write_files(broken)
    other = "#cloud-config\nwrite_files:\n  - path: /etc/x\n    owner: cairn\n    content: x\n"
    with pytest.raises(rehearse.Failed, match="owner"):
        rehearse.write_files(other)
    defaults = "#cloud-config\nwrite_files:\n  - path: /etc/x\n    content: x\n"
    [only] = rehearse.write_files(defaults)
    assert (only.path, only.permissions, only.content) == ("/etc/x", 0o644, "x")


def test_a_base64_entry_is_its_decoded_bytes_as_cloud_init_writes_them():
    """encoding: b64 is what main.tf gives the brand files: cloud-init decodes the content and
    writes the bytes, binary included; quoted, so that a value of digits stays a string."""
    png = b"\x89PNG\r\n\x1a\n\x00\xff"
    document = (
        "#cloud-config\nwrite_files:\n"
        f'  - path: /etc/x\n    encoding: b64\n    content: "{base64.b64encode(png).decode()}"\n'
        '  - path: /etc/y\n    encoding: b64\n    content: "12345678"\n'
    )
    x, y = rehearse.write_files(document)
    assert (x.path, x.permissions, x.content) == ("/etc/x", 0o644, png)
    assert y.content == base64.b64decode("12345678")


def test_by_default_the_template_installs_an_empty_plans_file_cairns_user_cannot_change():
    """cairn_plans_file unset: OpenTofu passes "", and cloud-init writes an empty file, root's
    and mode 0644 (uid 10001 reads it, cannot write it). host.sh then mounts nothing."""
    assert rehearse.rehearsal_values(REPO)["plans_yaml"] == ""
    files = {f.path: f for f in rehearse.write_files(_shipped())}
    plans = files[rehearse.PLANS_PATH]
    assert (plans.content, plans.permissions) == ("", 0o644)
    [entry] = [
        e for e in yaml.safe_load(_shipped())["write_files"] if e["path"] == rehearse.PLANS_PATH
    ]
    assert entry["owner"] == "root" and set(entry) == {"path", "permissions", "owner", "content"}


def test_the_template_takes_the_plans_file_only_through_the_byte_for_byte_form():
    template = (REPO / "infra/gcp/cloud-init.yaml.tftpl").read_text()
    uses = [i for i in rehearse._interpolations(template) if i.name == "plans_yaml"]
    assert [i.indent for i in uses] == [6]
    entry = template[template.index(f"  - path: {rehearse.PLANS_PATH}\n") :]
    assert entry.startswith(
        f"  - path: {rehearse.PLANS_PATH}\n"
        '    permissions: "0644"\n'
        "    owner: root\n"
        "    content: |\n"
        "      ${indent(6, chomp(plans_yaml))}\n"
    )


def test_a_plans_file_changes_its_own_entry_and_nothing_else():
    template = (REPO / "infra/gcp/cloud-init.yaml.tftpl").read_text()
    values = {**rehearse.rehearsal_values(REPO), "plans_yaml": rehearse.PLANS_TEXT}
    given = {f.path: f for f in rehearse.write_files(rehearse.render_template(template, values))}
    shipped = {f.path: f for f in rehearse.write_files(_shipped())}
    assert set(given) == set(shipped)
    assert [p for p in given if given[p] != shipped[p]] == [rehearse.PLANS_PATH]
    assert given[rehearse.PLANS_PATH] == (rehearse.PLANS_PATH, 0o644, rehearse.PLANS_TEXT)


def test_the_rehearsals_plans_file_caps_projects_at_one_and_serve_refuses_a_second(tmp_path):
    """What the rehearsal's plans check watches through Caddy, here in-process: with its plans
    file, a workspace that has a project gets 402 (limit "projects") for another; without, 201."""
    path = tmp_path / "plans.yaml"
    path.write_text(rehearse.PLANS_TEXT, encoding="utf-8")
    plans = load_plans(path)
    assert plan_cap(plans.resolve(None)[1], "projects") == 1
    for given, status in ((plans, 402), (None, 201)):
        store = Store(tmp_path / f"cairn-{status}.db")
        key = store.create_key("rehearsal")
        store.create_project("rehearsal")
        app = create_app(store, public_url="https://localhost", plans=given)
        with TestClient(app, base_url="https://localhost") as client:
            answer = client.post(
                "/api/projects",
                headers={"x-api-key": key},
                json={"name": rehearse.OVER_CAP_PROJECT},
            )
        store.close()
        assert answer.status_code == status, answer.text
        if status == 402:
            assert answer.json()["limit"] == "projects"


def test_the_plans_option_is_read_from_a_command_line_in_either_spelling():
    assert rehearse.plans_argument(["serve", "--plans", "/etc/p.yaml", "--x"]) == "/etc/p.yaml"
    assert rehearse.plans_argument(["serve", "--plans=/etc/p.yaml"]) == "/etc/p.yaml"
    assert rehearse.plans_argument(["serve", "--plans"]) == ""
    assert rehearse.plans_argument(["serve", "--plansx", "y"]) is None


def test_the_brand_check_changes_config_env_and_the_three_brand_files_and_nothing_else():
    """What the rehearsal's brand check expects of the rendering, here without Docker: sign-in
    in the extra arguments (serve needs it for a brand) and the three brand entries."""
    template = (REPO / "infra/gcp/cloud-init.yaml.tftpl").read_text()
    logo = rehearse.brand_logo()
    values = {
        **rehearse.rehearsal_values(REPO),
        "extra_args": rehearse.BRAND_EXTRA_ARGS,
        "brand_files": rehearse.brand_files(
            rehearse.BRAND_NAME_TEXT, logo, rehearse.BRAND_ALT_TEXT
        ),
    }
    given = {f.path: f for f in rehearse.write_files(rehearse.render_template(template, values))}
    shipped = {f.path: f for f in rehearse.write_files(_shipped())}
    changed = sorted(p for p in set(given) | set(shipped) if given.get(p) != shipped.get(p))
    brand = [rehearse.BRAND_NAME, rehearse.BRAND_LOGO, rehearse.BRAND_ALT]
    assert changed == sorted([rehearse.CONFIG_ENV, *brand])
    assert f'CAIRN_EXTRA_ARGS="{rehearse.BRAND_EXTRA_ARGS}"\n' in given[rehearse.CONFIG_ENV].content
    assert given[rehearse.BRAND_NAME].content == b"Knowledge Base"
    assert " " in rehearse.BRAND_NAME_TEXT  # the check is about a name that holds a space
    assert given[rehearse.BRAND_LOGO].content == logo
    assert given[rehearse.BRAND_ALT].content == b"Juniper Field"
    assert rehearse.ENV_FILE == rehearse.ENV_DIR / "cairn.env"
    assert "CAIRN_GITHUB_CLIENT_ID=" in rehearse.BRAND_ENV
    assert "CAIRN_GITHUB_CLIENT_SECRET=" in rehearse.BRAND_ENV


def test_the_rehearsals_logo_is_a_png_serve_can_tell_by_its_content():
    logo = rehearse.brand_logo()
    assert logo.startswith(b"\x89PNG\r\n\x1a\n")
    assert logo[12:16] == b"IHDR" and logo[16:24] == (2).to_bytes(4, "big") * 2
    assert logo.endswith(b"IEND\xaeB`\x82")


def test_an_options_values_are_read_in_either_spelling():
    args = ["serve", "--brand-name=Knowledge Base", "--brand-logo", "/etc/l", "--brand-logo-alt=J"]
    assert rehearse.option_values(args, "--brand-name") == ["Knowledge Base"]
    assert rehearse.option_values(args, "--brand-logo") == ["/etc/l"]
    assert rehearse.option_values(args, "--brand-logo-alt") == ["J"]
    assert rehearse.option_values(["--brand-name", "Knowledge", "Base"], "--brand-name") == [
        "Knowledge"
    ]
    assert rehearse.option_values(["serve"], "--brand-name") == []


def test_the_brand_check_runs_after_the_plans_check_and_before_the_disk_is_replaced():
    text = SCRIPT.read_text()
    order = [
        "rehearsal.runs_with_the_plans_file_it_is_given,",
        "rehearsal.runs_under_the_brand_it_is_given,",
        "rehearsal.a_first_deployment_that_fails_leaves_nothing_proven,",
    ]
    assert [text.index(line) for line in order] == sorted(text.index(line) for line in order)
    assert rehearse.seen_script(rehearse.PLANS_PATH) == rehearse.PLANS_SEEN


def test_mounts_are_read_by_where_they_land_in_the_container():
    mounts = [
        {"Type": "bind", "Source": "/mnt/disks/data/cairn", "Destination": "/data", "RW": True},
        {"Type": "bind", "Source": "/etc/cairn/plans.yaml", "Destination": "/etc/cairn/plans.yaml",
         "Mode": "ro", "RW": False},
    ]  # fmt: skip
    assert rehearse.mounts_at(mounts, rehearse.PLANS_PATH) == [("/etc/cairn/plans.yaml", False)]
    assert rehearse.mounts_at(mounts[:1], rehearse.PLANS_PATH) == []
    assert rehearse.mounts_at(None, "/data") == []


def test_runcmd_reloads_systemd_and_starts_the_services():
    commands = rehearse.runcmd(_shipped())
    assert "systemctl daemon-reload" in commands
    assert rehearse.started_units(commands) == [
        "caddy.service",
        "cairn.service",
        "litestream.service",
        "cairn-update.timer",
        "cairn-replica-check.timer",
        "cairn-compact.timer",
        "cairn-host-check.timer",
    ]


def test_the_replica_checks_lines_are_read_with_their_age():
    log = (
        "Starting cairn-replica-check.service...\n"
        "replica: current, newest 12 s old\n"
        "cairn-host: something else\n"
        "Error: replica: newest 905 s old, over the bound\n"
        "replica: current, newest x s old\n"
        "cairn-replica-check.service: Deactivated successfully.\n"
    )
    assert rehearse.replica_lines(log) == [("current", 12), ("error", 905)]
    # Docker's log of the litestream container holds Litestream's own lines too.
    litestreams_own = 'time=2026 level=INFO msg="replica: current, newest 1 s old"'
    assert rehearse.replica_lines(litestreams_own) == []


def test_the_replica_check_runs_once_the_database_is_restored_and_before_the_updater():
    source = (REPO / "scripts/rehearse_host.py").read_text()
    order = [
        "rehearsal.restores_the_database_before_cairn_starts,",
        "rehearsal.tells_a_current_replica_from_one_that_stopped,",
        "rehearsal.follows_the_deploy_tag,",
    ]
    assert source.index(order[0]) < source.index(order[1]) < source.index(order[2])
    assert rehearse.REPLICA_BOUND < 900  # past the bound within the run, not 15 minutes
    assert rehearse.rehearsal_values(REPO)["replica_bound_seconds"] == str(rehearse.REPLICA_BOUND)
    host = (REPO / "infra/gcp/files/host.sh").read_text()
    assert f"\nREPLICA_GRACE={rehearse.REPLICA_GRACE}\n" in host
    assert "\nREPLICA_UNCONFIRMED=$DATA_MOUNT/replica-unconfirmed\n" in host


def test_the_rehearsal_starts_neither_timer_with_the_boot_units():
    """Every run of the updater and of the replica check is one the rehearsal starts itself."""
    assert rehearse.TIMERS == (
        "cairn-update.timer",
        "cairn-replica-check.timer",
        "cairn-compact.timer",
        "cairn-host-check.timer",
    )
    units = rehearse.started_units(rehearse.runcmd(_shipped()))
    assert [u for u in units if u not in rehearse.TIMERS] == [
        "caddy.service",
        "cairn.service",
        "litestream.service",
    ]
    assert "cairn-replica-check.timer" in rehearse.STOP_ORDER
    assert "cairn-replica-check.service" in rehearse.STOP_ORDER
    assert "cairn-compact.timer" in rehearse.STOP_ORDER
    assert "cairn-compact.service" in rehearse.STOP_ORDER
    assert "cairn-host-check.timer" in rehearse.STOP_ORDER
    assert "cairn-host-check.service" in rehearse.STOP_ORDER
    assert "cairn-replica-check" in rehearse.CONTAINERS


# --- what the host says -------------------------------------------------------------------


def test_the_state_command_is_read_by_its_first_words():
    said = (
        f"tag production: {DIGEST_A}\n"
        f"proven (cairn starts it): {DIGEST_B}\n"
        "running: none\n"
        "pending: none\n"
        "failed: other\n"
        "interrupted: \n"
        "hold: held since 2026-11-12T10:00:00Z\n"
    )
    state = rehearse.parse_state(said)
    assert state["tag"] == DIGEST_A
    assert state["proven"] == DIGEST_B
    assert state["running"] == "none"
    assert state["interrupted"] == ""
    assert state["hold"] == "held since 2026-11-12T10:00:00Z"
    assert rehearse.parse_state("cairn-host: a log line without a colon\n") == {}


def test_a_pending_record_is_a_candidate_then_a_boot_id():
    assert rehearse.pending_record(f"img@{DIGEST_A}\nboot-1\n") == (f"img@{DIGEST_A}", "boot-1")
    assert rehearse.pending_record("") == ("", "")
    assert rehearse.pending_record("only\n") == ("only", "")


def test_the_mount_of_the_data_disk_is_read_from_proc_mounts():
    mounts = (
        "/dev/root / ext4 rw,relatime 0 0\n"
        "/dev/loop7 /mnt/disks/data ext4 rw,nosuid,nodev,noexec,relatime,discard 0 0\n"
        "/dev/sdb1 /mnt ext4 rw 0 0\n"
    )
    device, fstype, options = rehearse.mount_entry(mounts, "/mnt/disks/data")
    assert (device, fstype) == ("/dev/loop7", "ext4")
    assert {"nosuid", "nodev", "noexec", "discard"} <= options
    assert rehearse.mount_entry(mounts, "/mnt/disks") is None


def test_only_complete_metadata_rules_count():
    rule = "-A DOCKER-USER -d 169.254.169.254/32 ! -i br-cairn-bkp"
    listing = "\n".join(
        [
            "-N DOCKER-USER",
            f"{rule} -p udp -m udp ! --dport 53 -j DROP",
            f"{rule} -p tcp -m tcp ! --dport 53 -j DROP",
            # missing the exception for Litestream's bridge
            "-A DOCKER-USER -d 169.254.169.254/32 -p tcp -m tcp ! --dport 53 -j DROP",
            # DNS not excepted
            "-A DOCKER-USER -d 169.254.169.254/32 ! -i br-cairn-bkp -p udp -j DROP",
            # another address
            "-A DOCKER-USER -d 10.0.0.1/32 ! -i br-cairn-bkp -p tcp -m tcp ! --dport 53 -j DROP",
            "-A DOCKER-USER -j RETURN",
        ]
    )
    assert rehearse.metadata_drop_rules(listing) == ["udp", "tcp"]


def test_a_probe_says_reached_only_when_it_connected():
    assert rehearse.probe_reached("reached\n") is True
    assert rehearse.probe_reached("no answer: TimeoutError\n") is False
    assert rehearse.probe_reached("Unable to find image\nreached-ish\n") is False
    assert rehearse.probe_reached("") is False


def test_the_journal_cursor_is_read_from_its_own_line():
    said = "-- No entries --\n-- cursor: s=5966;i=4866;b=3f29;m=635f;t=6601;x=9e85\n"
    assert rehearse.journal_cursor(said) == "s=5966;i=4866;b=3f29;m=635f;t=6601;x=9e85"
    with pytest.raises(rehearse.Failed):
        rehearse.journal_cursor("-- No entries --\n")


def test_a_units_log_is_complete_once_systemd_says_the_run_ended():
    started = "Starting cairn-update.service - cairn: follow the deploy tag...\n"
    working = started + "cairn-host: deploying localhost:5000/cairn@sha256:aa (running: none)\n"
    assert not rehearse.run_ended("")
    assert not rehearse.run_ended(working)
    assert rehearse.run_ended(working + "cairn-update.service: Deactivated successfully.\n")
    assert rehearse.run_ended(working + "cairn-update.service: Failed with result 'exit-code'.\n")
    assert rehearse.run_ended(working + "Stopped cairn-update.service - cairn: follow ...\n")
    assert rehearse.run_ended("Finished cairn-restore.service - cairn: restore ...\n")
    # host.sh's own words never count as the end of a run.
    assert not rehearse.run_ended("cairn-host: Stopped nothing; Finished nothing\n")


def test_the_first_difference_names_a_line():
    assert rehearse.first_difference("a\nb\n", "a\nb\n") == ""
    assert "line 2" in rehearse.first_difference("a\nb\n", "a\nc\n")
    assert "line 3" in rehearse.first_difference("a\nb\n", "a\nb\nextra\n")


def test_a_secret_is_cut_out_of_diagnostics():
    assert rehearse.redact("key=abc123 and abc123", ["abc123", ""]) == (
        "key=[redacted] and [redacted]"
    )


# --- the guide's runbooks, as written -------------------------------------------------------


GUIDE = (REPO / "infra/gcp/README.md").read_text()

SYNTHETIC_GUIDE = """\
## Restore

### Lost

Intro.

```bash
gcloud compute ssh cairn --zone ZONE --tunnel-through-iap
sudo -i
bash /etc/cairn/host.sh hold      # prints the state
# - "pending" names an image:
#     bash /etc/cairn/host.sh pin sha256:DIGEST      # the digest on the "proven" line
systemctl stop cairn.service
cd /tmp && mv a b \\
  c 2>/dev/null
bash /etc/cairn/host.sh release   # only after /healthz answers
```

Or run `bash /etc/cairn/host.sh release --force`.

#### A deeper heading stays inside

```bash
echo second block
```

### Next
"""


def test_a_guide_part_ends_at_the_next_heading_outside_code_blocks():
    part = rehearse.guide_part(SYNTHETIC_GUIDE, "### Lost")
    assert part.startswith("### Lost\n")
    assert '# - "pending" names an image:' in part  # a comment line in a block is no heading
    assert "#### A deeper heading stays inside" in part
    assert "### Next" not in part
    assert rehearse.bash_blocks(SYNTHETIC_GUIDE, "### Lost")[1] == "echo second block\n"
    real = rehearse.guide_part(GUIDE, rehearse.LOST_DATABASE)
    assert "# Read what it printed before going on:" in real
    assert "`release` refuses" in real
    assert rehearse.POINT_IN_TIME not in real


def test_the_runbook_leaves_out_only_the_ssh_and_sudo_lines():
    runbook = rehearse.lost_database_runbook(SYNTHETIC_GUIDE, "### Lost")
    assert runbook.hold == "bash /etc/cairn/host.sh hold      # prints the state"
    assert runbook.pin == "bash /etc/cairn/host.sh pin sha256:DIGEST"
    assert runbook.middle == ["systemctl stop cairn.service", "cd /tmp && mv a b c 2>/dev/null"]
    assert runbook.release == "bash /etc/cairn/host.sh release   # only after /healthz answers"
    assert runbook.forced_release == "bash /etc/cairn/host.sh release --force"


def test_the_lost_database_block_is_read_from_the_guide():
    runbook = rehearse.lost_database_runbook(GUIDE)
    every = [runbook.hold, runbook.pin, *runbook.middle, runbook.release]
    assert not any(c.startswith("gcloud") or c == "sudo -i" for c in every)
    assert runbook.hold.startswith("bash /etc/cairn/host.sh hold ")
    assert runbook.pin == "bash /etc/cairn/host.sh pin sha256:DIGEST"
    assert runbook.middle[0] == "systemctl stop cairn.service litestream.service"
    assert any(" mv cairn.db " in c for c in runbook.middle)
    assert runbook.middle[-1] == "systemctl start cairn.service litestream.service"
    assert runbook.release.startswith("bash /etc/cairn/host.sh release ")
    assert runbook.forced_release == "bash /etc/cairn/host.sh release --force"


def test_a_runbook_without_hold_first_or_release_last_is_refused():
    no_hold = SYNTHETIC_GUIDE.replace("bash /etc/cairn/host.sh hold", "echo hold")
    with pytest.raises(rehearse.Failed, match="hold"):
        rehearse.lost_database_runbook(no_hold, "### Lost")
    no_pin = SYNTHETIC_GUIDE.replace("host.sh pin", "host.sh nip")
    with pytest.raises(rehearse.Failed, match="pin"):
        rehearse.lost_database_runbook(no_pin, "### Lost")


def test_the_pin_takes_the_digest_on_the_proven_line():
    template = "bash /etc/cairn/host.sh pin sha256:DIGEST"
    assert rehearse.pin_command(template, DIGEST_A) == f"bash /etc/cairn/host.sh pin {DIGEST_A}"
    with pytest.raises(rehearse.Failed):
        rehearse.pin_command(template, "none")
    with pytest.raises(rehearse.Failed):
        rehearse.pin_command("bash /etc/cairn/host.sh pin DIGEST", DIGEST_A)


def test_the_first_lines_end_with_the_move():
    middle = ["systemctl stop x", "aside=/a", "cd /d && mv a b 2>/dev/null", "systemctl start x"]
    assert rehearse.through_the_move(middle) == middle[:3]
    with pytest.raises(rehearse.Failed):
        rehearse.through_the_move(["systemctl stop x", "systemctl start x"])
    real = rehearse.through_the_move(rehearse.lost_database_runbook(GUIDE).middle)
    assert real[-1].startswith("cd /mnt/disks/data/cairn && mv ")


def test_the_point_in_time_commands_are_read_from_the_guide():
    look, restore = rehearse.point_in_time(GUIDE)
    assert look == [
        "sudo bash /etc/cairn/host.sh litestream ltx -level all /data/cairn.db | tail -n 40",
        "sudo bash /etc/cairn/host.sh litestream restore -dry-run -timestamp 2026-11-10T08:00:00Z"
        " -o /data/dry-run.db /data/cairn.db",
    ]
    assert restore == (
        "bash /etc/cairn/host.sh litestream restore -timestamp 2026-11-10T08:00:00Z /data/cairn.db"
    )


def test_the_timestamp_is_replaced_once():
    command = "x restore -timestamp 2026-11-10T08:00:00Z /data/cairn.db"
    assert rehearse.with_timestamp(command, "2026-11-12T10:11:12Z") == (
        "x restore -timestamp 2026-11-12T10:11:12Z /data/cairn.db"
    )
    with pytest.raises(rehearse.Failed):
        rehearse.with_timestamp("x restore /data/cairn.db", "2026-11-12T10:11:12Z")


def test_a_marked_script_runs_in_one_shell_and_reports_each_status(tmp_path):
    commands = ["cd / && pwd", "where=here", "echo $where; false", "printf 'no newline'"]
    script = tmp_path / "runbook.sh"
    script.write_text(rehearse.marked_script(commands))
    ran = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=True)
    assert rehearse.marked_results(ran.stdout) == [
        ("/", 0),
        ("", 0),
        ("here", 1),
        ("no newline", 0),
    ]


def test_aside_directories_are_the_ones_named_by_the_second():
    names = ["cairn", "aside-20261112T101112Z", "deploy", "aside-x", "aside-20261112T101113Z"]
    assert rehearse.aside_names(names) == ["aside-20261112T101112Z", "aside-20261112T101113Z"]


# --- refusing to run ----------------------------------------------------------------------


def test_it_refuses_without_the_flag_root_or_a_clean_machine():
    flag = rehearse.FLAG
    ok = {"is_root": True, "already_there": []}
    assert rehearse.refusal(["cairn:ci", flag], **ok) is None
    assert rehearse.refusal([flag, "cairn:ci"], **ok) is None
    assert "usage" in rehearse.refusal(["cairn:ci"], **ok)
    assert "usage" in rehearse.refusal([flag], **ok)
    assert "usage" in rehearse.refusal(["a", "b", flag], **ok)
    assert "usage" in rehearse.refusal(["cairn:ci", flag, "--other"], **ok)
    assert "root" in rehearse.refusal(["cairn:ci", flag], is_root=False, already_there=[])
    said = rehearse.refusal(["cairn:ci", flag], is_root=True, already_there=["/etc/cairn"])
    assert "/etc/cairn" in said
    assert rehearse.image_argument(["--on-this-throwaway-machine", "cairn:ci"]) == "cairn:ci"


# --- host.sh compact --------------------------------------------------------------------------


def test_the_compact_runbooks_command_is_read_from_the_guide():
    guide = rehearse.GUIDE.read_text(encoding="utf-8")
    [block] = rehearse.bash_blocks(guide, rehearse.COMPACT_RUNBOOK)
    assert rehearse.stack._commands(block) == ["sudo bash /etc/cairn/host.sh compact"]


def test_the_compact_check_runs_after_the_replica_check_and_before_the_updater():
    source = SCRIPT.read_text(encoding="utf-8")
    order = [
        source.index("rehearsal.tells_a_current_replica_from_one_that_stopped,"),
        source.index("rehearsal.compacts_with_the_replica_paused,"),
        source.index("rehearsal.follows_the_deploy_tag,"),
    ]
    assert order == sorted(order)


def test_the_deleted_word_is_counted_in_both_files_and_a_missing_log_counts_zero(tmp_path):
    word = rehearse.stack.DELETED_WORD
    (tmp_path / "cairn.db").write_bytes(f"{word} x {word}".encode())
    assert rehearse.word_counts(tmp_path) == {"cairn.db": 2, "cairn.db-wal": 0}
    (tmp_path / "cairn.db-wal").write_bytes(word.encode())
    assert rehearse.word_counts(tmp_path) == {"cairn.db": 2, "cairn.db-wal": 1}


def test_the_workspace_both_rehearsals_delete_holds_the_word_and_goes(tmp_path):
    """DELETE_A_WORKSPACE, run against a database of its own: the word goes into the deleted
    workspace's project and notes, and the other workspace keeps its own."""
    import sys

    db = tmp_path / "cairn.db"
    keeper = Store(db)
    project = keeper.create_project("rehearsal")
    keeper.add_note(project.id, "# Kept\n\nThe quartzlantern stays.\n")
    keeper.close()
    code = rehearse.stack.DELETE_A_WORKSPACE.replace("/data/cairn.db", str(db))
    said = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()
    assert said[-1:] == ["True"]
    store = Store(db)
    assert [p.name for p in store.list_projects()] == ["rehearsal"]
    assert [a.name for a in store.list_accounts()] == ["default"]
    store.close()


def test_the_stand_in_registry_runs_on_the_engines_own_platform():
    """The registry stands in for Artifact Registry; it is not what is rehearsed. Under
    emulation it died at the first push, so it never takes the platform of the images."""
    command = rehearse.registry_command("linux/arm64")
    assert command[:3] == ["docker", "run", "-d"]
    assert command[command.index("--platform") + 1] == "linux/arm64"
    assert command[-1] == rehearse.REGISTRY_IMAGE
    assert command[command.index("-p") + 1] == "127.0.0.1:5000:5000"
    assert command[command.index("--name") + 1] == rehearse.REGISTRY_CONTAINER
    # The engine says its own platform; the default platform of the environment is not asked.
    assert rehearse.engine_platform("linux arm64\n") == "linux/arm64"
    assert rehearse.engine_platform("linux amd64") == "linux/amd64"
    with pytest.raises(rehearse.Failed):
        rehearse.engine_platform("")
    source = (REPO / "scripts/rehearse_host.py").read_text()
    assert "sh(*registry_command(engine_platform(out(*ENGINE_PLATFORM))))" in source
    assert rehearse.ENGINE_PLATFORM == (
        "docker",
        "version",
        "--format",
        "{{.Server.Os}} {{.Server.Arch}}",
    )
