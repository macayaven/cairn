"""Shared test helpers extracted from test_oci_archive_unit."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

from support.paths import REPO_ROOT

REPO = REPO_ROOT

_spec = importlib.util.spec_from_file_location("oci_archive", REPO / "scripts" / "oci_archive.py")

oci_archive = importlib.util.module_from_spec(_spec)

_spec.loader.exec_module(oci_archive)

INDEX = "application/vnd.oci.image.index.v1+json"

MANIFEST = "application/vnd.oci.image.manifest.v1+json"

PROVENANCE = "https://slsa.dev/provenance/v0.2"

SBOM = "https://spdx.dev/Document"

INTOTO = "application/vnd.in-toto+json"

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
