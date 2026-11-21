"""scripts/oci_archive.py: what job `image_build` hands on, described and checked.

The build writes one OCI image layout as a tar (buildx's oci exporter). The script refuses it
unless index.json names exactly one image index, every blob is the bytes its name says, the
index holds one image for linux/amd64 and one for linux/arm64, and each of them has an
attestation manifest (buildx's `vnd.docker.reference.*` annotations) holding a build
provenance and a bill of materials. It writes the archive's SHA-256 and the index's digest for
the jobs after it, which check both before they push, scan or run what they received.
Archives here are made up: a few bytes stand for each layer."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("oci_archive", REPO / "scripts" / "oci_archive.py")
oci_archive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oci_archive)

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
PROVENANCE = "https://slsa.dev/provenance/v0.2"
SBOM = "https://spdx.dev/Document"
INTOTO = "application/vnd.in-toto+json"
# buildkit v0.26.0 writes in-toto-golang's StatementInTotoV01; Docker's attestation-storage
# page shows Statement/v1.
STATEMENT = "https://in-toto.io/Statement/v0.1"


class Layout:
    """An OCI layout held in memory: blobs by digest, and index.json."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.index_json: dict = {}

    def blob(self, data: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.blobs[digest] = data
        return digest

    def doc(self, document: dict, media_type: str, **extra) -> dict:
        data = json.dumps(document).encode()
        return {"mediaType": media_type, "digest": self.blob(data), "size": len(data), **extra}

    def tar(self, path: Path, tamper: str | None = None, drop: str | None = None) -> Path:
        with tarfile.open(path, "w") as out:
            files = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}'}
            files["index.json"] = json.dumps(self.index_json).encode()
            for digest, data in self.blobs.items():
                if digest == drop:
                    continue
                files[f"blobs/sha256/{digest.split(':')[1]}"] = (
                    b"other bytes" if digest == tamper else data
                )
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                out.addfile(info, io.BytesIO(data))
        return path


def image(
    layout: Layout,
    arch: str,
    built_for: str | None = None,
    variant: str | None = None,
    off: int = 0,
    layer_type: str = "application/vnd.oci.image.layer.v1.tar+gzip",
) -> dict:
    config = layout.doc(
        {"architecture": built_for or arch, "os": "linux"},
        "application/vnd.oci.image.config.v1+json",
    )
    data = f"a layer of {arch}".encode()
    layer = {
        "mediaType": layer_type,
        "digest": layout.blob(data),
        "size": len(data) + off,
    }
    manifest = {"schemaVersion": 2, "mediaType": MANIFEST, "config": config, "layers": [layer]}
    platform = {"architecture": arch, "os": "linux"}
    if variant is not None:
        platform["variant"] = variant
    return layout.doc(manifest, MANIFEST, platform=platform)


def statement(of: dict, predicate: str, kind: str = STATEMENT) -> dict:
    """An in-toto statement as buildx writes one in an attestation layer: the image as its
    subject, by digest."""
    return {
        "_type": kind,
        "predicateType": predicate,
        "subject": [
            {
                "name": "pkg:docker/cairn@latest?platform=linux%2Famd64",
                "digest": {"sha256": of["digest"].split(":")[1]},
            }
        ],
        "predicate": {"made": "up"},
    }


def attestation(
    layout: Layout, of: dict, predicates=(PROVENANCE, SBOM), body=statement, layer_type=INTOTO,
    platform=("unknown", "unknown"), config_type="application/vnd.oci.image.config.v1+json",
) -> dict:  # fmt: skip
    layers = []
    for predicate in predicates:
        data = json.dumps(body(of, predicate)).encode()
        layers.append({
            "mediaType": layer_type,
            "digest": layout.blob(data),
            "size": len(data),
            "annotations": {"in-toto.io/predicate-type": predicate},
        })  # fmt: skip
    config = layout.doc({}, config_type)
    manifest = {"schemaVersion": 2, "mediaType": MANIFEST, "config": config, "layers": layers}
    return layout.doc(
        manifest,
        MANIFEST,
        platform={"architecture": platform[0], "os": platform[1]},
        annotations={
            "vnd.docker.reference.digest": of["digest"],
            "vnd.docker.reference.type": "attestation-manifest",
        },
    )


def build(tmp_path, arches=("amd64", "arm64"), predicates=(PROVENANCE, SBOM), attest=True,
          entries=None, tamper=None, built_for=None, drop=None, twice=False,
          stray=None, variants=None, proof=None, index_extra=None, oras=None,
          resize=None, layer_off=0, layer_type=None) -> tuple[Path, str]:  # fmt: skip
    layout = Layout()
    images = [
        image(
            layout,
            arch,
            (built_for or {}).get(arch),
            (variants or {}).get(arch),
            layer_off if arch == "amd64" else 0,
            **({} if layer_type is None else {"layer_type": layer_type}),
        )
        for arch in arches
    ]
    manifests = list(images)
    attestations = (
        [attestation(layout, one, predicates, **(proof or {})) for one in images] if attest else []
    )
    manifests += attestations
    if twice:  # a second attestation entry for the first image
        manifests.append(attestation(layout, images[0], (PROVENANCE, SBOM, "https://made.up/x")))
    if stray is not None:
        manifests.append(stray(layout, images))
    if resize is not None:  # a descriptor whose size is not its blob's, its digest unchanged
        which_one = {"image": manifests[-len(attestations) - 1], "attestation": manifests[-1]}
        which_one[resize]["size"] += 1
    document = {
        "schemaVersion": 2,
        "mediaType": INDEX,
        "manifests": manifests,
        **(index_extra or {}),
    }
    index = layout.doc(document, INDEX)
    index["annotations"] = {"org.opencontainers.image.ref.name": "latest"}
    if oras is not None:
        entries = oras(index, manifests)
    layout.index_json = {"schemaVersion": 2, "manifests": [index] if entries is None else entries}
    which = {"index": index["digest"], "layer": images[0]["digest"]}.get(tamper, tamper)
    gone = None
    if drop is not None:
        first = json.loads(layout.blobs[images[-1]["digest"]])
        proof = json.loads(layout.blobs[attestations[-1]["digest"]]) if attestations else {}
        gone = {
            "image": images[-1]["digest"],
            "config": first["config"]["digest"],
            "layer": first["layers"][0]["digest"],
            "attestation": attestations[-1]["digest"] if attestations else None,
            "attestation config": proof.get("config", {}).get("digest"),
            "attestation layer": proof.get("layers", [{}])[-1].get("digest"),
        }[drop]
    return layout.tar(tmp_path / "cairn.oci.tar", tamper=which, drop=gone), index["digest"]


def as_oras_writes(index: dict, manifests: list[dict], tag: str = "main") -> list[dict]:
    """index.json as `oras cp --to-oci-layout REF@DIGEST DIR:TAG` leaves it (oras 1.3.6, which
    pins oras-go v2.7.0): content/oci/oci.go's Push tags every manifest it stores by its digest
    (IsManifest counts image manifests and indexes), Tag adds TAG to the root, and saveIndex
    writes one entry per tagged descriptor, first those tagged by name, with the annotation
    org.opencontainers.image.ref.name set to the name, then the rest without it. The rest are
    the descriptors as the index lists them (platform and annotations kept), and the order of
    each part is a Go map's, so not fixed: the root is put last here."""
    root = {k: v for k, v in index.items() if k != "annotations"}
    root["annotations"] = {"org.opencontainers.image.ref.name": tag}
    return [*reversed(manifests), root]


def run(path: Path, tmp_path, root: str | None = None) -> tuple[int, str, str, str]:
    output = tmp_path / "github_output"
    out, err = io.StringIO(), io.StringIO()
    extra = [] if root is None else ["--root", root]
    code = oci_archive.main([str(path), *extra, "--github-output", str(output)], out=out, err=err)
    return code, out.getvalue(), err.getvalue(), output.read_text() if output.exists() else ""


def test_a_build_of_both_platforms_with_their_attestations_is_described(tmp_path):
    path, digest = build(tmp_path)
    code, out, err, output = run(path, tmp_path)
    assert (code, err) == (0, "")
    archive = hashlib.sha256(path.read_bytes()).hexdigest()
    lines = out.splitlines()
    amd64, arm64 = lines[1].split()[1].rstrip(":"), lines[2].split()[1].rstrip(":")
    # Each platform's own manifest digest, so a job that takes one platform names it by its own
    # digest and never by the index's and a platform flag.
    assert output == f"digest={digest}\narchive={archive}\namd64={amd64}\narm64={arm64}\n"
    assert amd64 != arm64 and digest not in (amd64, arm64)
    assert lines[0] == f"image index {digest}"
    assert lines[1].startswith("linux/amd64 sha256:") and lines[2].startswith("linux/arm64 sha256:")
    assert all("provenance and bill of materials attested" in line for line in lines[1:3])


@pytest.mark.parametrize(
    ("case", "said"),
    [
        ({"arches": ("amd64",)}, "the index holds no image for linux/arm64"),
        ({"arches": ("amd64", "arm64", "arm64")}, "the index holds 2 images for linux/arm64"),
        ({"attest": False}, "the image for linux/amd64 has no attestation manifest"),
        (
            {"predicates": (PROVENANCE,)},
            "the attestation of linux/amd64 holds no bill of materials",
        ),
        ({"predicates": (SBOM,)}, "the attestation of linux/amd64 holds no build provenance"),
        ({"tamper": "index"}, "is not the bytes its name says"),
        ({"tamper": "layer"}, "is not the bytes its name says"),
        ({"entries": []}, "index.json names 0 manifests, not one image index"),
        (
            {"built_for": {"arm64": "amd64"}},
            "the image listed for linux/arm64 is built for linux/amd64",
        ),
    ],
)
def test_anything_else_is_refused_in_one_line(tmp_path, case, said):
    path, _ = build(tmp_path, **case)
    code, _, err, output = run(path, tmp_path)
    assert code == 1 and output == ""
    assert err.count("\n") == 1 and err.startswith("::error::the build's archive is refused: ")
    assert said in err


def unlisted(layout: Layout, images: list[dict]) -> dict:
    """An entry that is neither an image of the two platforms nor an attestation of one."""
    return layout.doc({"schemaVersion": 2, "mediaType": MANIFEST, "config": images[0]["digest"]},
                      MANIFEST, platform={"architecture": "unknown", "os": "unknown"})  # fmt: skip


def foreign_attestation(layout: Layout, images: list[dict]) -> dict:
    """An attestation entry for an image the index does not hold."""
    return attestation(layout, {"digest": "sha256:" + "f" * 64})


@pytest.mark.parametrize(
    ("case", "said"),
    [
        ({"arches": ("amd64", "arm64", "386")}, "the index holds an image for linux/386"),
        ({"arches": ("amd64", "arm64", "s390x")}, "the index holds an image for linux/s390x"),
        ({"twice": True}, "the index holds 2 attestation manifests for linux/amd64"),
        ({"stray": unlisted}, "the index holds an entry that is neither"),
        ({"stray": foreign_attestation}, "an attestation of sha256:ffff"),
        ({"drop": "image"}, "the layout has no blob for the image for linux/arm64"),
        ({"drop": "config"}, "the layout has no blob for the config of the image for linux/arm64"),
        ({"drop": "layer"}, "the layout has no blob for a layer of the image for linux/arm64"),
        ({"drop": "attestation"}, "the layout has no blob for the attestation of linux/arm64"),
        (
            {"drop": "attestation config"},
            # Both attestations' configs are the same empty document, one blob.
            "the layout has no blob for the config of the attestation of linux/amd64",
        ),
        (
            {"drop": "attestation layer"},
            "the layout has no blob for a layer of the attestation of linux/arm64",
        ),
    ],
)
def test_exactly_the_build_s_descriptors_and_every_blob_they_name(tmp_path, case, said):
    """Two images, one attestation each, nothing else, and every blob the index, the images and
    the attestations name is in the archive: an extra platform would otherwise be promoted with
    the index without its own scan."""
    path, _ = build(tmp_path, **case)
    code, _, err, output = run(path, tmp_path)
    assert code == 1 and output == ""
    assert err.count("\n") == 1 and err.startswith("::error::the build's archive is refused: ")
    assert said in err


def test_an_arm64_image_with_its_variant_is_the_arm64_image(tmp_path):
    """buildx may write `variant: v8` beside linux/arm64; that is still the arm64 image."""
    path, digest = build(tmp_path, variants={"arm64": "v8"})
    code, out, err, _ = run(path, tmp_path)
    assert (code, err) == (0, ""), err
    assert out.splitlines()[0] == f"image index {digest}"


def test_a_missing_archive_is_refused(tmp_path):
    code, _, err, output = run(tmp_path / "absent.tar", tmp_path)
    assert code == 1 and output == "" and "::error::" in err


def test_the_script_needs_nothing_but_the_standard_library():
    source = (REPO / "scripts" / "oci_archive.py").read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {"__future__", "argparse", "hashlib", "json", "sys", "tarfile", "pathlib"}


# --- a layout oras wrote: the root chosen by the digest the run was told to import ------------


def test_a_layout_oras_wrote_is_checked_from_the_root_it_was_told(tmp_path):
    """A tag run copies main's index into a fresh layout with oras, whose index.json lists the
    index and every manifest under it; the script takes the root by the digest it was given
    and checks the same as for a build's archive."""
    (tmp_path / "built").mkdir()
    (tmp_path / "copied").mkdir()
    built, digest = build(tmp_path / "built")
    expected = run(built, tmp_path / "built")
    path, same = build(tmp_path / "copied", oras=as_oras_writes)
    assert same == digest
    code, out, err, output = run(path, tmp_path / "copied", root=digest)
    assert (code, err) == (0, ""), err
    assert out == expected[1]
    assert output.split("archive=")[0] == expected[3].split("archive=")[0]
    assert output.splitlines()[2:] == expected[3].splitlines()[2:]


def stranger(index: dict, manifests: list[dict]) -> list[dict]:
    """oras's entries and one more manifest the root does not hold."""
    other = {"mediaType": MANIFEST, "digest": STRANGER, "size": 2}
    return [*as_oras_writes(index, manifests), other]


ELSE = "sha256:" + "a" * 64
STRANGER = "sha256:" + "e" * 64


@pytest.mark.parametrize(
    ("case", "root", "said"),
    [
        ({"oras": as_oras_writes}, None, "index.json names 5 manifests, not one image index"),
        ({"oras": as_oras_writes}, ELSE, f"index.json does not list {ELSE}"),
        (
            {"oras": stranger},
            "ROOT",
            f"index.json lists {STRANGER}, which the image index does not",
        ),
        ({}, ELSE, f"index.json does not list {ELSE}"),
    ],
)
def test_a_layout_is_refused_unless_it_holds_the_root_and_only_what_it_names(
    tmp_path, case, root, said
):
    path, digest = build(tmp_path, **case)
    code, _, err, output = run(path, tmp_path, root=digest if root == "ROOT" else root)
    assert code == 1 and output == ""
    assert err.count("\n") == 1 and err.startswith("::error::the build's archive is refused: ")
    assert said in err


def child_resized(index: dict, manifests: list[dict]) -> list[dict]:
    """oras's entries, one child's with a size that is not its blob's."""
    entries = as_oras_writes(index, manifests)
    return [{**entries[0], "size": entries[0]["size"] + 1}, *entries[1:]]


def child_retyped(index: dict, manifests: list[dict]) -> list[dict]:
    entries = as_oras_writes(index, manifests)
    return [{**entries[0], "mediaType": INDEX}, *entries[1:]]


def child_named(index: dict, manifests: list[dict]) -> list[dict]:
    """The tag given to a child: a lookup by the name would find the child, not the index."""
    entries = as_oras_writes(index, manifests)
    named = {**entries[0], "annotations": {"org.opencontainers.image.ref.name": "main"}}
    return [named, *entries[1:]]


def two_names_for_the_root(index: dict, manifests: list[dict]) -> list[dict]:
    """`oras cp ... DIR:main,latest`: saveIndex writes one entry for each name of the root."""
    entries = as_oras_writes(index, manifests, tag="main")
    latest = {**entries[-1], "annotations": {"org.opencontainers.image.ref.name": "latest"}}
    return [*entries, latest]


def root_alias_resized(index: dict, manifests: list[dict]) -> list[dict]:
    entries = two_names_for_the_root(index, manifests)
    return [*entries[:-1], {**entries[-1], "size": entries[-1]["size"] + 1}]


def test_a_root_listed_once_for_each_of_its_names_passes(tmp_path):
    path, digest = build(tmp_path, oras=two_names_for_the_root)
    code, out, err, _ = run(path, tmp_path, root=digest)
    assert (code, err) == (0, ""), err
    assert out.splitlines()[0] == f"image index {digest}"


@pytest.mark.parametrize(
    ("oras", "said"),
    [
        (child_resized, "index.json lists sha256:"),
        (child_retyped, "index.json lists sha256:"),
        (child_named, "index.json gives the name main to sha256:"),
        (root_alias_resized, "index.json lists the image index"),
    ],
)
def test_index_json_s_own_descriptors_are_checked_like_the_index_s(tmp_path, oras, said):
    """Each entry of index.json is checked against the descriptor the image index holds for the
    same digest and against its blob: a false size or media type, or a name on a child, is
    refused even though the run imports by the root's digest."""
    path, digest = build(tmp_path, oras=oras)
    code, _, err, output = run(path, tmp_path, root=digest)
    assert code == 1 and output == ""
    assert said in err, err


# --- every descriptor is what a runtime and a verifier take it for -----------------------------


def nested_index(layout: Layout, images: list[dict]) -> dict:
    inner = {"schemaVersion": 2, "mediaType": INDEX, "manifests": [images[0]]}
    return layout.doc(inner, INDEX, platform={"architecture": "amd64", "os": "linux"})


def docker_list(layout: Layout, images: list[dict]) -> dict:
    kind = "application/vnd.docker.distribution.manifest.list.v2+json"
    return layout.doc({"schemaVersion": 2, "mediaType": kind, "manifests": [images[0]]}, kind,
                      platform={"architecture": "amd64", "os": "linux"})  # fmt: skip


def other_subject(of: dict, predicate: str) -> dict:
    return statement({"digest": "sha256:" + "d" * 64}, predicate)


def other_predicate(of: dict, predicate: str) -> dict:
    return statement(of, "https://made.up/predicate")


def other_statement(of: dict, predicate: str) -> dict:
    return statement(of, predicate, kind="https://made.up/Statement")


NOT_AN_IMAGE = "not an image manifest"
HYBRID = "the image index has a config or layers"
SIZE = "is not the size its descriptor says"


@pytest.mark.parametrize(
    ("case", "said"),
    [
        ({"variants": {"arm64": "v9"}}, "the index lists linux/arm64 with variant v9"),
        ({"variants": {"amd64": "v3"}}, "the index lists linux/amd64 with variant v3"),
        ({"stray": nested_index}, f"is a {INDEX}, {NOT_AN_IMAGE}"),
        ({"stray": docker_list}, f"manifest.list.v2+json, {NOT_AN_IMAGE}"),
        ({"index_extra": {"config": {"digest": "sha256:" + "c" * 64}}}, HYBRID),
        ({"index_extra": {"layers": []}}, HYBRID),
        ({"resize": "image"}, SIZE),
        ({"resize": "attestation"}, SIZE),
        ({"layer_off": 1}, SIZE),
        ({"proof": {"platform": ("arm64", "linux")}}, "listed as linux/arm64, not unknown/unknown"),
        ({"proof": {"body": other_subject}}, "whose subject is not the image for linux/amd64"),
        ({"proof": {"body": other_predicate}}, "predicate type is not its annotation's"),
        ({"proof": {"body": other_statement}}, "is not an in-toto statement"),
        ({"proof": {"layer_type": "application/octet-stream"}}, f"not {INTOTO}"),
    ],
)
def test_each_descriptor_is_checked_for_its_kind_size_platform_and_statement(tmp_path, case, said):
    path, _ = build(tmp_path, **case)
    code, _, err, output = run(path, tmp_path)
    assert code == 1 and output == ""
    assert err.count("\n") == 1 and err.startswith("::error::the build's archive is refused: ")
    assert said in err, err


@pytest.mark.parametrize("kind", [STATEMENT, "https://in-toto.io/Statement/v1"])
def test_both_in_toto_statement_versions_are_statements(tmp_path, kind):
    def written(of: dict, predicate: str) -> dict:
        return statement(of, predicate, kind=kind)

    path, _ = build(tmp_path, proof={"body": written})
    code, _, err, _ = run(path, tmp_path)
    assert (code, err) == (0, ""), err


def no_subject(of: dict, predicate: str) -> dict:
    return {**statement(of, predicate), "subject": []}


def two_names(of: dict, predicate: str) -> dict:
    once = statement(of, predicate)
    return {**once, "subject": once["subject"] * 2}


def one_of_two_elsewhere(of: dict, predicate: str) -> dict:
    once = statement(of, predicate)
    return {**once, "subject": once["subject"] + other_subject(of, predicate)["subject"]}


def no_predicate(of: dict, predicate: str) -> dict:
    return {k: v for k, v in statement(of, predicate).items() if k != "predicate"}


def empty_predicate(of: dict, predicate: str) -> dict:
    return {**statement(of, predicate), "predicate": {}}


def listed_predicate(of: dict, predicate: str) -> dict:
    return {**statement(of, predicate), "predicate": ["made", "up"]}


@pytest.mark.parametrize(
    ("body", "passes"),
    [
        (statement, True),
        (two_names, True),
        (no_subject, False),
        (one_of_two_elsewhere, False),
        (no_predicate, False),
        (empty_predicate, False),
        (listed_predicate, False),
    ],
)
def test_a_statement_names_its_image_and_holds_a_predicate(tmp_path, body, passes):
    """buildkit v0.26.0 gives a statement one subject per image name the export is given
    (exporter/containerimage/writer.go), each with that platform's image digest, and none when
    it is given no name; the provenance it makes and the bill of materials the syft scanner
    (v1.12.1, internal/scanner.go) writes carry no subjects of their own, so both take those
    (exporter/attestation/make.go). image_build names its export, so a statement with no
    subject, or one of another digest, is refused, as is one with no predicate."""
    path, _ = build(tmp_path, proof={"body": body})
    code, _, err, _ = run(path, tmp_path)
    assert (code == 0) is passes, err


def test_which_media_types_are_checked_and_which_are_not(tmp_path):
    """The third report's remark on 8c, as the script's own words now say it: the media type
    is checked of the index, of each manifest it lists, of each image's config and of each
    attestation's layers. An image's layer and an attestation's config are checked for being
    there, as the bytes their digests name, at the size their descriptors say (the cases
    above), and not for their media type: a layer or an attestation's config of another type
    passes."""
    said = " ".join(oci_archive.__doc__.split())
    assert (
        "The media type is checked of the index, of every manifest it lists, of each image's"
        " config and of each layer of an attestation, and every descriptor's size against its"
        " blob; the media type of an image's layers and of an attestation's config is not"
        " checked:" in said
    )
    assert "Every descriptor's media type is checked" not in said
    path, _ = build(tmp_path, layer_type="application/x-made-up")
    assert run(path, tmp_path)[0] == 0
    path, _ = build(tmp_path, proof={"config_type": "application/x-made-up"})
    assert run(path, tmp_path)[0] == 0
