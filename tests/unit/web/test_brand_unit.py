"""Unit tests for the brand (cairn.web.brand): the name's rule, the logo judged by its content,
and what its reading refuses. They pin what tests/acceptance/test_b1_wall.py leaves open."""

import dataclasses
import struct
import zlib

import pytest

from cairn.web import brand
from cairn.web.brand import Brand, Logo, make_brand, read_logo

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32">'
    b'<text x="4" y="22" fill="#1b2a24">Juniper Field</text></svg>\n'
)


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\x1b\x2a\x24"))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("name", ["K", "K" * 60, "Knowledge Base", "Field & Sons", "Ünïcode Co"])
def test_a_name_of_1_to_60_plain_characters_is_taken(name):
    assert make_brand(name).name == name


@pytest.mark.parametrize(
    "name",
    ["", "   ", "K" * 61, "A\x07B", "A\nB", "A\tB", "A‮B", "A​B", "<b>", "a > b", "a < b"],
)
def test_a_name_that_cannot_be_shown_as_it_is_is_refused_naming_the_option(name):
    with pytest.raises(ValueError, match="--brand-name"):
        make_brand(name)


def test_no_name_is_no_brand_and_a_logo_or_its_text_without_a_name_is_refused(tmp_path):
    assert make_brand(None) is None
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    with pytest.raises(ValueError, match="--brand-logo needs --brand-name"):
        make_brand(None, logo)
    with pytest.raises(ValueError, match="--brand-logo-alt needs --brand-logo"):
        make_brand("Knowledge Base", None, "Juniper Field")


def test_the_logo_is_told_by_its_content_never_by_its_name(tmp_path):
    named_png = tmp_path / "logo.png"
    named_png.write_bytes(SVG)
    named_svg = tmp_path / "logo.svg"
    named_svg.write_bytes(png())
    assert read_logo(named_png).media_type == "image/svg+xml"
    assert read_logo(named_svg).media_type == "image/png"
    assert read_logo(named_png).data == SVG  # the bytes as the file holds them


def test_the_logo_text_is_the_name_unless_given(tmp_path):
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    assert make_brand("Knowledge Base", logo).logo_alt == "Knowledge Base"
    assert make_brand("Knowledge Base", logo, "Juniper Field").logo_alt == "Juniper Field"
    for bad in ("", "  ", "a\nb", "x" * 201):
        with pytest.raises(ValueError, match="--brand-logo-alt"):
            make_brand("Knowledge Base", logo, bad)


@pytest.mark.parametrize(
    "value",
    ["Knowledge \udcff Base", "\ud800", "Base \udfff"],
    ids=["stray-byte-from-argv", "lone-high-surrogate", "lone-low-surrogate"],
)
def test_a_name_or_text_that_no_page_can_be_written_in_is_refused(tmp_path, value):
    """A lone surrogate (what Python makes of a byte that is not UTF-8 in an argument) cannot
    be written in a page: every branded page would then fail. It is refused at start, naming
    the option, as any value that cannot be used."""
    with pytest.raises(ValueError, match="--brand-name"):
        make_brand(value)
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    with pytest.raises(ValueError, match="--brand-logo-alt"):
        make_brand("Knowledge Base", logo, value)


def test_serve_stops_at_start_on_a_name_with_a_stray_byte(tmp_path, monkeypatch, capsys):
    import os

    from cairn import server
    from cairn.web import cli

    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **options: None)  # never a server
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "made-up")
    db = tmp_path / "kb.db"
    name = os.fsdecode(b"Knowledge \xff Base")  # how an argument carries a byte that is not UTF-8
    argv = ["serve", "--db", str(db), "--public-url", "https://kb.example.test",
            "--sign-in", "github", "--brand-name", name]  # fmt: skip
    try:
        code = server.main(argv)
    except SystemExit as stopped:
        code = stopped.code
    assert code == 2 and not db.exists()
    assert "--brand-name" in capsys.readouterr().err.strip().splitlines()[-1]


@pytest.mark.parametrize(
    "content",
    [
        b"A logo, they said. This is a line of text.\n",
        b"",
        b"<html><body>not an image</body></html>",
        b'<?xml version="1.0"?><svg viewBox="0 0 1 1"></svg>',  # no SVG namespace
        b'<svg xmlns="http://www.w3.org/2000/svg">',  # not a whole document
        b'<svg xmlns="http://www.w3.org/2000/svg"></svg><svg/>',
        b'<g xmlns="http://www.w3.org/2000/svg"></g>',
        b"\x89PNG\r\n\x1a\n",  # a signature and nothing after it
        b"GIF89a\x01\x00\x01\x00",
    ],
    ids=["text", "empty", "html", "svg-without-namespace", "unclosed", "two-roots", "root-g",
         "png-signature-only", "gif"],
)  # fmt: skip
def test_a_file_that_is_neither_an_svg_nor_a_png_is_refused(tmp_path, content):
    path = tmp_path / "logo.svg"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="--brand-logo"):
        read_logo(path)


def test_a_logo_over_256_kb_missing_or_a_folder_is_refused_and_its_path_is_not_said(tmp_path):
    large = tmp_path / "secret-folder" / "large.svg"
    large.parent.mkdir()
    large.write_bytes(SVG.replace(b"<text", b"<!--" + b"x" * (256 * 1024) + b"--><text"))
    exactly = tmp_path / "exactly.svg"
    filler = b"x" * (256 * 1024 - len(SVG) - 7)
    exactly.write_bytes(SVG.replace(b"<text", b"<!--" + filler + b"--><text"))
    assert len(exactly.read_bytes()) == 256 * 1024
    assert read_logo(exactly).media_type == "image/svg+xml"
    for path in (large, tmp_path / "missing.svg", tmp_path):
        with pytest.raises(ValueError, match="--brand-logo") as refused:
            read_logo(path)
        assert "secret-folder" not in str(refused.value)


def test_an_svg_with_an_external_dtd_is_refused_and_nothing_is_fetched(tmp_path, monkeypatch):
    """The reader fetches nothing: a document type is refused before anything it names is
    read, so neither a file nor an address is opened."""
    target = tmp_path / "dtd.txt"
    target.write_text("<!ENTITY x 'read'>")
    path = tmp_path / "logo.svg"
    path.write_bytes(
        f'<?xml version="1.0"?><!DOCTYPE svg SYSTEM "file://{target}">'.encode()
        + b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'
    )
    opened = []
    real_open = open

    def watching(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", watching)
    with pytest.raises(ValueError, match="--brand-logo"):
        read_logo(path)
    assert str(target) not in opened


def test_an_svg_with_nested_entities_is_refused_at_once(tmp_path, monkeypatch):
    entities = "".join(
        f'<!ENTITY e{n} "{("&e" + str(n - 1) + ";") * 10 if n else "lol"}">' for n in range(10)
    )
    path = tmp_path / "logo.svg"
    path.write_bytes(
        f'<?xml version="1.0"?><!DOCTYPE svg [{entities}]>'.encode()
        + b'<svg xmlns="http://www.w3.org/2000/svg"><text>&e9;</text></svg>'
    )
    refused: list[tuple[object, ...]] = []
    real_refuse = brand._refuse

    def refuse(*args: object) -> None:
        refused.append(args)
        real_refuse(*args)

    monkeypatch.setattr(brand, "_refuse", refuse)
    with pytest.raises(ValueError, match="--brand-logo"):
        read_logo(path)
    # At once: refused at the document type, before an entity is declared or expanded (left
    # to itself, expat expands 3.4 million characters before its own limit stops it).
    assert [args[0] for args in refused] == ["svg"]


def test_the_logo_address_carries_its_digest_and_never_its_path(tmp_path):
    one, other = tmp_path / "private-name.svg", tmp_path / "b.svg"
    one.write_bytes(SVG)
    other.write_bytes(SVG.replace(b"Juniper", b"Juniperus"))
    first, second, again = read_logo(one), read_logo(other), read_logo(one)
    assert first.path.startswith("/static/") and first.path.endswith(".svg")
    assert first.path == again.path != second.path
    assert "private-name" not in first.path
    assert read_logo(_written(tmp_path, png())).path.endswith(".png")


def _written(folder, content: bytes):
    path = folder / "any"
    path.write_bytes(content)
    return path


def test_a_brand_cannot_be_changed_once_made(tmp_path):
    made = make_brand("Knowledge Base", _written(tmp_path, SVG), "Juniper Field")
    assert isinstance(made, Brand) and isinstance(made.logo, Logo)
    with pytest.raises(dataclasses.FrozenInstanceError):
        made.name = "Other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        made.logo.data = b""  # type: ignore[misc]


def test_a_logo_already_read_is_taken_as_it_is(tmp_path):
    logo = read_logo(_written(tmp_path, SVG))
    assert make_brand("Knowledge Base", logo).logo is logo
    assert brand.LOGO_MAX == 256 * 1024 and brand.NAME_MAX == 60
