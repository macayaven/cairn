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
PROVENANCE = "https://slsa.dev/provenance/v1"
SBOM = "https://spdx.dev/Document"


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

    def tar(self, path: Path, tamper: str | None = None) -> Path:
        with tarfile.open(path, "w") as out:
            files = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}'}
            files["index.json"] = json.dumps(self.index_json).encode()
            for digest, data in self.blobs.items():
                files[f"blobs/sha256/{digest.split(':')[1]}"] = (
                    b"other bytes" if digest == tamper else data
                )
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                out.addfile(info, io.BytesIO(data))
        return path


def image(layout: Layout, arch: str, built_for: str | None = None) -> dict:
    config = layout.doc(
        {"architecture": built_for or arch, "os": "linux"},
        "application/vnd.oci.image.config.v1+json",
    )
    layer = layout.blob(f"a layer of {arch}".encode())
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST,
        "config": config,
        "layers": [
            {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": layer, "size": 1}
        ],
    }
    return layout.doc(manifest, MANIFEST, platform={"architecture": arch, "os": "linux"})


def attestation(layout: Layout, of: dict, predicates=(PROVENANCE, SBOM)) -> dict:
    layers = [
        {
            "mediaType": "application/vnd.in-toto+json",
            "digest": layout.blob(f"{predicate} of {of['digest']}".encode()),
            "size": 1,
            "annotations": {"in-toto.io/predicate-type": predicate},
        }
        for predicate in predicates
    ]
    config = layout.doc({}, "application/vnd.oci.image.config.v1+json")
    manifest = {"schemaVersion": 2, "mediaType": MANIFEST, "config": config, "layers": layers}
    return layout.doc(
        manifest,
        MANIFEST,
        platform={"architecture": "unknown", "os": "unknown"},
        annotations={
            "vnd.docker.reference.digest": of["digest"],
            "vnd.docker.reference.type": "attestation-manifest",
        },
    )


def build(tmp_path, arches=("amd64", "arm64"), predicates=(PROVENANCE, SBOM), attest=True,
          entries=None, tamper=None, built_for=None) -> tuple[Path, str]:  # fmt: skip
    layout = Layout()
    images = [image(layout, arch, (built_for or {}).get(arch)) for arch in arches]
    manifests = list(images)
    if attest:
        manifests += [attestation(layout, one, predicates) for one in images]
    index = layout.doc({"schemaVersion": 2, "mediaType": INDEX, "manifests": manifests}, INDEX)
    index["annotations"] = {"org.opencontainers.image.ref.name": "latest"}
    layout.index_json = {"schemaVersion": 2, "manifests": [index] if entries is None else entries}
    which = {"index": index["digest"], "layer": images[0]["digest"]}.get(tamper, tamper)
    return layout.tar(tmp_path / "cairn.oci.tar", tamper=which), index["digest"]


def run(path: Path, tmp_path) -> tuple[int, str, str, str]:
    output = tmp_path / "github_output"
    out, err = io.StringIO(), io.StringIO()
    code = oci_archive.main([str(path), "--github-output", str(output)], out=out, err=err)
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
