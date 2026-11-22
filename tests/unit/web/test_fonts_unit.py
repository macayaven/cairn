"""cairn's pages ship their two typefaces, Atkinson Hyperlegible Next and Atkinson
Hyperlegible Mono, as files on cairn's own origin.

- The two fonts and their SIL Open Font License texts are in src/cairn/static/fonts/, the
  fonts byte for byte as approved (their sha256 is pinned here).
- The stylesheet declares both families with @font-face, from those files by an address
  relative to itself, with `font-display: swap` and the weight range each file really has
  (read from its `fvar` table). The family names are the first names of the font stacks.
- The pages' Content-Security-Policy allows fonts from cairn itself and nowhere else.
- GET /static/fonts/<file> answers without a session: the fonts as font/ttf, the licences
  as text, whatever media types the host's own tables hold.
- scripts/smoke_container.py checks that the image serves them, with the sizes pinned here.
"""

import hashlib
import importlib.util
import re
import struct

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from support.paths import REPO_ROOT

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
PACKAGE = REPO_ROOT / "src" / "cairn"
CSS = PACKAGE / "static" / "cairn.css"
FONTS = PACKAGE / "static" / "fonts"
SHA256 = {
    "atkinson-hyperlegible-next.ttf": (
        "5a455d1cfa099b601ab70751bb9673e8fe1854dc4500c80e1a220d0d75e31745"
    ),
    "atkinson-hyperlegible-mono.ttf": (
        "5ce8b1698d1ded7dff2178c1a3ad159470085a58ea239e8b2cb88f4fb4a6f646"
    ),
}
LICENCES = ("atkinson-hyperlegible-next.OFL.txt", "atkinson-hyperlegible-mono.OFL.txt")
FAMILIES = {
    "Atkinson Hyperlegible Next": "atkinson-hyperlegible-next.ttf",
    "Atkinson Hyperlegible Mono": "atkinson-hyperlegible-mono.ttf",
}
# The policy before the fonts, which must not change in any other directive.
POLICY = {
    "default-src": ["'none'"],
    "style-src": ["'self'"],
    "img-src": ["'self'"],
    "form-action": ["'self'"],
    "manifest-src": ["'self'"],
    "base-uri": ["'none'"],
    "frame-ancestors": ["'none'"],
}


# The files


def test_the_two_fonts_and_their_licences_are_shipped_as_approved():
    assert sorted(p.name for p in FONTS.iterdir()) == sorted([*SHA256, *LICENCES])
    for name, digest in SHA256.items():
        assert hashlib.sha256((FONTS / name).read_bytes()).hexdigest() == digest, name
    for name in LICENCES:
        text = (FONTS / name).read_text(encoding="utf-8")
        assert "Atkinson Hyperlegible" in text.splitlines()[0], name
        assert "SIL OPEN FONT LICENSE Version 1.1" in text, name


def weight_range(font: bytes) -> tuple[int, int]:
    """The `wght` axis of a variable TrueType font, as (minimum, maximum), from its `fvar`
    table: the table directory names where `fvar` is; its axis records hold 16.16 numbers."""
    (count,) = struct.unpack(">H", font[4:6])
    for i in range(count):
        tag, _, offset, _ = struct.unpack(">4sIII", font[12 + 16 * i : 28 + 16 * i])
        if tag == b"fvar":
            break
    else:
        raise AssertionError("no fvar table: not a variable font")
    _, _, axes, _, axis_count, axis_size = struct.unpack(">6H", font[offset : offset + 12])
    for n in range(axis_count):
        at = offset + axes + n * axis_size
        axis, low, _, high = struct.unpack(">4siii", font[at : at + 16])
        if axis == b"wght":
            return low // 65536, high // 65536
    raise AssertionError("no wght axis")


def test_the_weight_reader_reads_a_made_up_fvar_table():
    """A font of one table, `fvar`, with an `ital` axis then a `wght` axis of 300 to 700."""
    fvar = struct.pack(">8H", 1, 0, 16, 2, 2, 20, 0, 0)
    fvar += struct.pack(">4siiiHH", b"ital", 0, 0, 1 << 16, 0, 256)
    fvar += struct.pack(">4siiiHH", b"wght", 300 << 16, 400 << 16, 700 << 16, 0, 257)
    head = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
    font = head + struct.pack(">4sIII", b"fvar", 0, 28, len(fvar)) + fvar
    assert weight_range(font) == (300, 700)
    with pytest.raises(AssertionError, match="no fvar"):
        weight_range(struct.pack(">IHHHH", 0x00010000, 0, 16, 0, 0))  # no table at all


# The stylesheet


def font_faces(css: str) -> dict[str, dict[str, str]]:
    """Each @font-face rule's declarations, by its font-family (quotes removed)."""
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    faces = {}
    for body in re.findall(r"@font-face\s*\{([^{}]*)\}", flat):
        declared = {}
        for line in body.split(";"):
            if ":" in line:
                name, value = line.split(":", 1)
                declared[name.strip()] = " ".join(value.split())
        faces[declared["font-family"].strip("\"'")] = declared
    return faces


def first_family(css: str, token: str) -> str:
    stack = re.search(rf"{token}:\s*([^;]+);", css)
    assert stack, token
    return stack[1].split(",")[0].strip().strip("\"'")


def test_the_stylesheet_declares_both_families_from_cairns_own_files():
    css = CSS.read_text(encoding="utf-8")
    faces = font_faces(css)
    assert set(faces) == set(FAMILIES)
    assert {first_family(css, "--font-sans"), first_family(css, "--font-mono")} == set(FAMILIES)
    for family, file in FAMILIES.items():
        face = faces[family]
        # Relative to the stylesheet (/static/cairn.css), so /static/fonts/<file>: cairn's own.
        assert face["src"] == f'url("fonts/{file}") format("truetype")', family
        assert face["font-display"] == "swap", family
        assert face["font-style"] == "normal", family
        low, high = weight_range((FONTS / file).read_bytes())
        assert face["font-weight"] == f"{low} {high}", family


# The policy and the files over HTTP


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    store.create_project("alpha")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.key = key
        yield client
    store.close()


def directives(policy: str) -> dict[str, list[str]]:
    return {part.split()[0]: part.split()[1:] for part in policy.split(";") if part.strip()}


def test_the_pages_allow_fonts_from_cairn_itself_and_nowhere_else(client):
    signed_out = [client.get("/login"), client.get("/no/such/page")]
    client.post("/login", data={"key": client.key}, headers=SAME_ORIGIN)
    for r in (*signed_out, client.get("/"), client.get("/b/alpha"), client.get("/search?q=x")):
        policy = directives(r.headers["content-security-policy"])
        assert policy == {**POLICY, "font-src": ["'self'"]}, r.url


@pytest.fixture
def no_host_types(monkeypatch):
    """A host whose media-type tables know no font (as a slim container image may be)."""
    import starlette.responses

    monkeypatch.setattr(starlette.responses, "guess_type", lambda *_a, **_k: (None, None))


def test_the_fonts_and_licences_are_served_without_a_session(client, no_host_types):
    for name in SHA256:
        r = client.get(f"/static/fonts/{name}")
        assert r.status_code == 200, name
        assert r.content == (FONTS / name).read_bytes(), name
        assert r.headers["content-type"] == "font/ttf", name
    for name in LICENCES:
        r = client.get(f"/static/fonts/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].split(";")[0] == "text/plain", name
        assert r.content == (FONTS / name).read_bytes(), name
    assert client.get("/static/fonts/nothing.ttf").status_code == 404


# The container image


def smoke_script():
    path = PACKAGE.parents[1] / "scripts" / "smoke_container.py"
    spec = importlib.util.spec_from_file_location("smoke_container", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_container_smoke_test_checks_the_files_by_their_real_sizes(monkeypatch):
    smoke = smoke_script()
    shipped = {
        name: ((FONTS / name).stat().st_size, kind)
        for name, kind in (
            ("atkinson-hyperlegible-next.ttf", "font/ttf"),
            ("atkinson-hyperlegible-mono.ttf", "font/ttf"),
            ("atkinson-hyperlegible-next.OFL.txt", "text/plain"),
        )
    }
    assert shipped == smoke.FONT_FILES
    assert "the two typefaces and a licence" in smoke.__doc__
    check = smoke.Smoke.serves_the_typefaces
    assert check.__name__ in smoke.main.__code__.co_names

    served = {name: (FONTS / name).read_bytes() for name in smoke.FONT_FILES}
    types = {name: kind for name, (_, kind) in smoke.FONT_FILES.items()}

    def http(method, url, **_):
        name = url.rsplit("/", 1)[1]
        return 200, {"Content-Type": types[name]}, served[name]

    stand = type("Stand", (), {"base": "http://127.0.0.1:1"})()
    monkeypatch.setattr(smoke, "http", http)
    assert "atkinson-hyperlegible-next.ttf" in check(stand)
    # It sees a font served as something else, and a short file.
    types["atkinson-hyperlegible-mono.ttf"] = "application/octet-stream"
    with pytest.raises(smoke.Failed, match="mono"):
        check(stand)
    types["atkinson-hyperlegible-mono.ttf"] = "font/ttf"
    served["atkinson-hyperlegible-next.ttf"] = served["atkinson-hyperlegible-next.ttf"][:-1]
    with pytest.raises(smoke.Failed, match="next"):
        check(stand)
