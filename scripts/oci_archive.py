"""Describes and checks the image archive job `image_build` hands on:

    python3 scripts/oci_archive.py ARCHIVE [--github-output FILE]

ARCHIVE is the OCI image layout, as a tar, that buildx's oci exporter writes for a build of
linux/amd64 and linux/arm64 with provenance and a bill of materials. The archive passes only
when index.json names exactly one image index; every file under blobs/sha256/ is the bytes its
name says; the index holds exactly one image for each of the two platforms, whose own config
says it is built for that platform, and exactly one attestation manifest for each image (an
entry annotated `vnd.docker.reference.type: attestation-manifest` and
`vnd.docker.reference.digest: <the image's digest>`, as buildx writes them), and nothing else:
no third platform, no second attestation, no attestation of an image it does not hold; each
attestation's layers include a build provenance (in-toto predicate type
`https://slsa.dev/provenance/...`) and a bill of materials (`https://spdx.dev/Document`); and
every blob those manifests name (each config and each layer) is in the archive.

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
PLATFORMS = ("linux/amd64", "linux/arm64")
PREDICATES = {
    "build provenance": "https://slsa.dev/provenance/",
    "bill of materials": "https://spdx.dev/Document",
}


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


def _document(blobs: dict[str, bytes], digest: str, what: str) -> dict:
    if digest not in blobs:
        raise Refused(f"the layout has no blob for {what} ({digest})")
    try:
        return json.loads(blobs[digest])
    except ValueError:
        raise Refused(f"{what} ({digest}) is not JSON") from None


def _blob(blobs: dict[str, bytes], descriptor: dict, what: str) -> bytes:
    digest = descriptor.get("digest")
    if digest not in blobs:
        raise Refused(f"the layout has no blob for {what} ({digest})")
    return blobs[digest]


def _manifest(blobs: dict[str, bytes], descriptor: dict, what: str) -> dict:
    """The manifest DESCRIPTOR names, after checking its config and every layer are present."""
    manifest = _document(blobs, descriptor.get("digest"), what)
    _blob(blobs, manifest.get("config") or {}, f"the config of {what}")
    for layer in manifest.get("layers") or []:
        _blob(blobs, layer, f"a layer of {what}")
    return manifest


def describe(path: Path) -> tuple[str, dict[str, str]]:
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
    entries = top.get("manifests") or []
    if len(entries) != 1 or entries[0].get("mediaType") != INDEX:
        raise Refused(f"index.json names {len(entries)} manifests, not one image index")
    digest = entries[0]["digest"]
    index = _document(blobs, digest, "the image index")
    manifests = index.get("manifests") or []
    images: dict[str, list[dict]] = {}
    attestations: dict[str, list[dict]] = {}
    for entry in manifests:
        notes = entry.get("annotations") or {}
        found = entry.get("platform") or {}
        platform = f"{found.get('os')}/{found.get('architecture')}"
        if notes.get("vnd.docker.reference.type") == "attestation-manifest":
            attestations.setdefault(notes.get("vnd.docker.reference.digest"), []).append(entry)
        elif platform in PLATFORMS:  # a variant (arm64's v8) does not make another platform
            images.setdefault(platform, []).append(entry)
        elif platform != "unknown/unknown":
            raise Refused(f"the index holds an image for {platform}, which is not built here")
        else:
            raise Refused(
                f"the index holds an entry that is neither an image of {' or '.join(PLATFORMS)}"
                f" nor an attestation of one ({entry.get('digest')})"
            )
    chosen = {}
    for platform in PLATFORMS:
        found = images.get(platform, [])
        if len(found) != 1:
            many = f"{len(found)} images" if found else "no image"
            raise Refused(f"the index holds {many} for {platform}")
        chosen[platform] = found[0]["digest"]
    for platform, image in chosen.items():
        manifest = _manifest(blobs, {"digest": image}, f"the image for {platform}")
        # The image's own config says what it is built for.
        config = _document(blobs, manifest["config"]["digest"], f"the config of {platform}")
        built_for = f"{config.get('os')}/{config.get('architecture')}"
        if built_for != platform:
            raise Refused(f"the image listed for {platform} is built for {built_for}")
        proofs = attestations.pop(image, [])
        if not proofs:
            raise Refused(f"the image for {platform} has no attestation manifest")
        if len(proofs) != 1:
            raise Refused(f"the index holds {len(proofs)} attestation manifests for {platform}")
        what = f"the attestation of {platform}"
        layers = _manifest(blobs, proofs[0], what).get("layers") or []
        kinds = [
            (layer.get("annotations") or {}).get("in-toto.io/predicate-type", "")
            for layer in layers
        ]
        for name, prefix in PREDICATES.items():
            if not any(kind.startswith(prefix) for kind in kinds):
                raise Refused(f"the attestation of {platform} holds no {name}")
    for subject in attestations:
        raise Refused(f"the index holds an attestation of {subject}, which is no image of it")
    return digest, chosen


def main(argv: list[str] | None = None, out=sys.stdout, err=sys.stderr) -> int:
    parser = argparse.ArgumentParser(prog="oci_archive.py", description=__doc__.splitlines()[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--github-output", type=Path, help="append digest= and archive= here")
    args = parser.parse_args(argv)
    try:
        digest, images = describe(args.archive)
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
