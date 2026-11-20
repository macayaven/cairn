"""Describes and checks the image archive job `image_build` hands on:

    python3 scripts/oci_archive.py ARCHIVE [--root DIGEST] [--github-output FILE]

ARCHIVE is the OCI image layout, as a tar, that buildx's oci exporter writes for a build of
linux/amd64 and linux/arm64 with provenance and a bill of materials, or that `oras cp --to-oci-
layout` writes when it copies such an index from a registry. buildx's index.json names exactly
one image index, and without --root nothing else passes. oras's lists the index and every
manifest under it (oras-go v2.7.0, content/oci/oci.go: each manifest it stores is tagged by its
digest, and saveIndex writes one entry per tag); with --root the image index is the entry with
DIGEST, and any other entry must be a manifest that index holds. Every descriptor's media type
is checked, and its size against its blob: images are OCI image manifests of linux/amd64 (no
variant) or linux/arm64 (no variant, or v8), the index has no config or layers, an attestation
is listed as unknown/unknown and every one of its layers is an in-toto statement
(Statement/v0.1, as buildkit writes it, or /v1) whose subjects, if any, are its image and whose
predicate type is its layer's annotation. Every file under blobs/sha256/ is the bytes its name
says; the index holds exactly one image for each of the two platforms, whose own config says it
is built for that platform, and exactly one attestation manifest for each image (an entry
annotated `vnd.docker.reference.type: attestation-manifest` and `vnd.docker.reference.digest:
<the image's digest>`, as buildx writes them), and nothing else: no third platform, no second
attestation, no attestation of an image it does not hold; each attestation's layers include a
build provenance (in-toto predicate type `https://slsa.dev/provenance/...`) and a bill of
materials (`https://spdx.dev/Document`); and every blob those manifests name (each config and
each layer) is in the archive.

It prints the index's digest and, for each platform, its image's digest; with --github-output
it appends `digest=<the index's digest>`, `archive=<the archive's SHA-256>`, and `amd64=` and
`arm64=` with each platform's own manifest digest. The jobs that receive the archive check that
SHA-256 before anything else, and push the layout by the index's digest, so what they publish
is this index; a job that takes one platform names it by that platform's own digest, which
holds one image and so cannot stand for the host's platform. Otherwise it prints one line,
`::error::the build's archive is refused: ` and why, and exits 1.

Standard library only: the runner's python3 runs it before any environment is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
IMAGE_CONFIG = "application/vnd.oci.image.config.v1+json"
INTOTO = "application/vnd.in-toto+json"
PLATFORMS = ("linux/amd64", "linux/arm64")
# The variants a runtime of each platform selects: none for amd64, and for arm64 none or v8,
# which buildx may write beside it. v9 would not be chosen on a v8 machine.
VARIANTS = {"linux/amd64": (None,), "linux/arm64": (None, "v8")}
PREDICATES = {
    "build provenance": "https://slsa.dev/provenance/",
    "bill of materials": "https://spdx.dev/Document",
}
# buildkit v0.26.0 writes in-toto-golang's StatementInTotoV01; Docker's attestation-storage
# page shows Statement/v1.
STATEMENTS = ("https://in-toto.io/Statement/v0.1", "https://in-toto.io/Statement/v1")


class Refused(Exception):
    """The archive is not what the build must hand on; the message says why."""


def _blobs(archive: tarfile.TarFile) -> dict[str, bytes]:
    """Every blob of the layout by digest, each checked against its name."""
    blobs = {}
    for member in archive.getmembers():
        if not member.isfile() or not member.name.startswith("blobs/sha256/"):
            continue
        data = archive.extractfile(member).read()
        name = member.name.removeprefix("blobs/sha256/")
        if hashlib.sha256(data).hexdigest() != name:
            raise Refused(f"blob {name} is not the bytes its name says")
        blobs[f"sha256:{name}"] = data
    return blobs


def _blob(blobs: dict[str, bytes], descriptor: dict, what: str) -> bytes:
    """The bytes DESCRIPTOR names, present and of the size it says."""
    digest = descriptor.get("digest")
    if digest not in blobs:
        raise Refused(f"the layout has no blob for {what} ({digest})")
    if descriptor.get("size") != len(blobs[digest]):
        raise Refused(f"{what} ({digest}) is not the size its descriptor says")
    return blobs[digest]


def _json(data: bytes, what: str) -> dict:
    try:
        document = json.loads(data)
    except ValueError:
        raise Refused(f"{what} is not JSON") from None
    if not isinstance(document, dict):
        raise Refused(f"{what} is not a JSON object")
    return document


def _manifest(blobs: dict[str, bytes], descriptor: dict, what: str) -> dict:
    """The image manifest DESCRIPTOR names: the descriptor and the document both say it is one,
    and its config and every layer are present at their sizes."""
    kind = descriptor.get("mediaType")
    if kind != MANIFEST:
        raise Refused(f"{what} ({descriptor.get('digest')}) is a {kind}, not an image manifest")
    manifest = _json(_blob(blobs, descriptor, what), what)
    if manifest.get("mediaType") != MANIFEST or "manifests" in manifest:
        raise Refused(f"{what} ({descriptor.get('digest')}) is not an image manifest")
    _blob(blobs, manifest.get("config") or {}, f"the config of {what}")
    for layer in manifest.get("layers") or []:
        _blob(blobs, layer, f"a layer of {what}")
    return manifest


def _platform(entry: dict) -> str:
    found = entry.get("platform") or {}
    return f"{found.get('os')}/{found.get('architecture')}"


def _root(top: dict, root: str | None) -> dict:
    """The index.json entry of the image index: the one entry a build writes, or, in a layout
    oras wrote, the one with the digest the run was told to import."""
    entries = top.get("manifests") or []
    if root is None:
        if len(entries) != 1 or entries[0].get("mediaType") != INDEX:
            raise Refused(f"index.json names {len(entries)} manifests, not one image index")
        return entries[0]
    chosen = [entry for entry in entries if entry.get("digest") == root]
    if len(chosen) != 1 or chosen[0].get("mediaType") != INDEX:
        raise Refused(f"index.json does not list {root} once, as an image index")
    return chosen[0]


def _attested(blobs, proof: dict, image: str, platform: str) -> None:
    """PROOF is the attestation manifest of IMAGE: listed as no platform, and every layer an
    in-toto statement about IMAGE whose predicate type is its annotation's; among them a build
    provenance and a bill of materials."""
    what = f"the attestation of {platform}"
    if _platform(proof) != "unknown/unknown":
        raise Refused(f"{what} is an attestation listed as {_platform(proof)}, not unknown/unknown")
    manifest = _manifest(blobs, proof, what)
    subject = (manifest.get("subject") or {}).get("digest")
    if subject not in (None, image):
        raise Refused(f"{what} names {subject} as its subject, not the image for {platform}")
    kinds = []
    for layer in manifest.get("layers") or []:
        if layer.get("mediaType") != INTOTO:
            raise Refused(f"{what} has a layer of {layer.get('mediaType')}, not {INTOTO}")
        said = (layer.get("annotations") or {}).get("in-toto.io/predicate-type", "")
        statement = _json(blobs[layer["digest"]], f"a layer of {what}")
        if statement.get("_type") not in STATEMENTS:
            raise Refused(f"a layer of {what} is not an in-toto statement")
        if statement.get("predicateType") != said:
            raise Refused(
                f"a layer of {what} has a statement whose predicate type is not its annotation's"
            )
        hexes = [(s.get("digest") or {}).get("sha256") for s in statement.get("subject") or []]
        # buildkit names the image once per image name it was given, and not at all when it
        # was given none, as image_build's export is; the index's annotation binds it then.
        if any(f"sha256:{found}" != image for found in hexes):
            raise Refused(
                f"a layer of {what} has a statement whose subject is not the image for {platform}"
            )
        kinds.append(said)
    for name, prefix in PREDICATES.items():
        if not any(kind.startswith(prefix) for kind in kinds):
            raise Refused(f"{what} holds no {name}")


def describe(path: Path, root: str | None = None) -> tuple[str, dict[str, str]]:
    """The index's digest and {platform: image digest}; raises Refused."""
    try:
        with tarfile.open(path) as archive:
            blobs = _blobs(archive)
            member = archive.extractfile("index.json")
            top = json.loads(member.read())
    except (OSError, KeyError, ValueError, tarfile.TarError) as error:
        raise Refused(
            f"{path.name} is not an OCI layout archive ({type(error).__name__})"
        ) from None
    entry = _root(top, root)
    digest = entry["digest"]
    index = _json(_blob(blobs, entry, "the image index"), "the image index")
    if index.get("mediaType") != INDEX:
        raise Refused(f"the image index ({digest}) is a {index.get('mediaType')}, not an index")
    if "config" in index or "layers" in index:
        raise Refused("the image index has a config or layers, as only a manifest has")
    manifests = index.get("manifests") or []
    held = {m.get("digest") for m in manifests}
    for other in top.get("manifests") or []:
        if other.get("digest") not in held | {digest}:
            raise Refused(
                f"index.json lists {other.get('digest')}, which the image index does not hold"
            )
    images: dict[str, list[dict]] = {}
    attestations: dict[str, list[dict]] = {}
    for item in manifests:
        notes = item.get("annotations") or {}
        platform = _platform(item)
        if notes.get("vnd.docker.reference.type") == "attestation-manifest":
            attestations.setdefault(notes.get("vnd.docker.reference.digest"), []).append(item)
            continue
        if item.get("mediaType") != MANIFEST:
            raise Refused(
                f"the index lists {item.get('digest')}, which is a {item.get('mediaType')},"
                " not an image manifest"
            )
        if platform in PLATFORMS:
            variant = (item.get("platform") or {}).get("variant")
            if variant not in VARIANTS[platform]:
                raise Refused(f"the index lists {platform} with variant {variant}")
            images.setdefault(platform, []).append(item)
        elif platform != "unknown/unknown":
            raise Refused(f"the index holds an image for {platform}, which is not built here")
        else:
            raise Refused(
                f"the index holds an entry that is neither an image of {' or '.join(PLATFORMS)}"
                f" nor an attestation of one ({item.get('digest')})"
            )
    chosen = {}
    for platform in PLATFORMS:
        found = images.get(platform, [])
        if len(found) != 1:
            many = f"{len(found)} images" if found else "no image"
            raise Refused(f"the index holds {many} for {platform}")
        chosen[platform] = found[0]
    for platform, item in chosen.items():
        manifest = _manifest(blobs, item, f"the image for {platform}")
        config_entry = manifest["config"]
        if config_entry.get("mediaType") != IMAGE_CONFIG:
            raise Refused(f"the config of the image for {platform} is not an image config")
        # The image's own config says what it is built for.
        config = _json(blobs[config_entry["digest"]], f"the config of {platform}")
        built_for = f"{config.get('os')}/{config.get('architecture')}"
        if built_for != platform:
            raise Refused(f"the image listed for {platform} is built for {built_for}")
        proofs = attestations.pop(item["digest"], [])
        if not proofs:
            raise Refused(f"the image for {platform} has no attestation manifest")
        if len(proofs) != 1:
            raise Refused(f"the index holds {len(proofs)} attestation manifests for {platform}")
        _attested(blobs, proofs[0], item["digest"], platform)
    for subject in attestations:
        raise Refused(f"the index holds an attestation of {subject}, which is no image of it")
    return digest, {platform: item["digest"] for platform, item in chosen.items()}


def main(argv: list[str] | None = None, out=sys.stdout, err=sys.stderr) -> int:
    parser = argparse.ArgumentParser(prog="oci_archive.py", description=__doc__.splitlines()[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--root", help="in a layout oras wrote, the image index's digest; it may list others"
    )
    parser.add_argument("--github-output", type=Path, help="append digest= and archive= here")
    args = parser.parse_args(argv)
    try:
        digest, images = describe(args.archive, args.root)
    except Refused as refusal:
        print(f"::error::the build's archive is refused: {refusal}", file=err)
        return 1
    print(f"image index {digest}", file=out)
    for platform, image in images.items():
        print(f"{platform} {image}: provenance and bill of materials attested", file=out)
    if args.github_output is not None:
        archive = hashlib.sha256(args.archive.read_bytes()).hexdigest()
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"digest={digest}\narchive={archive}\n")
            for platform, image in images.items():
                output.write(f"{platform.split('/')[1]}={image}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
