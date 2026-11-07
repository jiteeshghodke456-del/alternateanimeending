"""Small hand-made images for the provenance tests (and frontend/e2e/provenance.py).

Every file here is synthetic: a flat-colour image from Pillow plus metadata written byte by byte, following
the C2PA 2.x (JUMBF + CBOR), XMP, EXIF and PNG chunk layouts. They are NOT real Content Credentials (no
signature) and are labelled as demo assets. Run `python tests/provenance_fixtures.py` to write them to
tests/fixtures/provenance/.
"""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from typing import Any

from PIL import Image

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"


# ------------------------------------------------------------------ tiny CBOR encoder (test-side only)
def _head(mt: int, n: int) -> bytes:
    if n < 24:
        return bytes([mt << 5 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([mt << 5 | ai]) + n.to_bytes(size, "big")
    raise ValueError(n)


def cbor(v: Any) -> bytes:
    if v is None:
        return b"\xf6"
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, bytes):
        return _head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, list):
        return _head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(type(v))


# ------------------------------------------------------------------ JUMBF
def box(t: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + t + payload


def jumb(label: str, *children: bytes, uuid: bytes = b"c2pa\x00\x11\x00\x10\x80\x00\x00\xaa\x00\x38\x9b\x71") -> bytes:
    jumd = box(b"jumd", uuid + b"\x03" + label.encode() + b"\x00")
    return box(b"jumb", jumd + b"".join(children))


def manifest_store(generator: str = "Adobe_Firefly c2pa-rs/0.32.0", source_type: str | None = "trainedAlgorithmicMedia",
                   agent: str | None = "Adobe Firefly", claim_v2: bool = False) -> bytes:
    action: dict[str, Any] = {"action": "c2pa.created"}
    if source_type:
        action["digitalSourceType"] = IPTC + source_type
    if agent:
        action["softwareAgent"] = {"name": agent} if claim_v2 else agent
    claim: dict[str, Any] = ({"claim_generator_info": [{"name": generator, "version": "1.0"}]} if claim_v2
                             else {"claim_generator": generator})
    claim["signature"] = "self#jumbf=c2pa.signature"
    return jumb("c2pa", jumb("urn:uuid:6f1c0e0e-0000-4000-8000-000000000001",
                             jumb("c2pa.assertions", jumb("c2pa.actions.v2" if claim_v2 else "c2pa.actions",
                                                          box(b"cbor", cbor({"actions": [action]})))),
                             jumb("c2pa.claim.v2" if claim_v2 else "c2pa.claim", box(b"cbor", cbor(claim))),
                             jumb("c2pa.signature", box(b"cbor", cbor({"note": "not a real signature"})))))


# ------------------------------------------------------------------ containers
def base_png(colour: str = "#F28C28", size: tuple[int, int] = (64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def base_jpeg(colour: str = "#3B6EF2", size: tuple[int, int] = (64, 48), **kw: Any) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "JPEG", quality=85, **kw)
    return out.getvalue()


def png_chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def png_insert(png: bytes, *chunks: bytes) -> bytes:
    """Insert chunks right after IHDR."""
    ihdr_end = 8 + 12 + int.from_bytes(png[8:12], "big")
    return png[:ihdr_end] + b"".join(chunks) + png[ihdr_end:]


def jpeg_insert(jpeg: bytes, *segments: bytes) -> bytes:
    """Insert APPn segments right after SOI (and the JFIF APP0, if any)."""
    i = 2
    if jpeg[2:4] == b"\xff\xe0":
        i = 4 + int.from_bytes(jpeg[4:6], "big")
    return jpeg[:i] + b"".join(segments) + jpeg[i:]


def segment(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def app11_segments(store: bytes, max_payload: int = 65_000) -> list[bytes]:
    """JPEG XT APP11 segments for a JUMBF store; later segments repeat the 8-byte box header."""
    head, rest = store[:8], store[8:]
    first = max_payload - 8
    parts = [store[:8 + first]]
    rest = rest[first:]
    while rest:
        parts.append(head + rest[:first])
        rest = rest[first:]
    return [segment(0xEB, b"JP" + struct.pack(">HI", 1, z) + p) for z, p in enumerate(parts, start=1)]


def xmp_packet(source_type: str = "trainedAlgorithmicMedia", creator_tool: str | None = None) -> str:
    tool = f'\n   xmp:CreatorTool="{creator_tool}"' if creator_tool else ""
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"\n'
        f'   xmlns:xmp="http://ns.adobe.com/xap/1.0/"{tool}\n'
        f'   Iptc4xmpExt:DigitalSourceType="{IPTC}{source_type}"/>\n'
        '</rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>'
    )


# ------------------------------------------------------------------ the fixtures
def jpeg_xmp_ai() -> bytes:
    """JPEG with an XMP packet: Iptc4xmpExt:DigitalSourceType = trainedAlgorithmicMedia."""
    return jpeg_insert(base_jpeg("#7A3BF2"), segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp_packet().encode()))


def png_sd_parameters() -> bytes:
    """PNG with the Stable Diffusion WebUI `parameters` tEXt chunk."""
    params = ("a sunrise over Mumbai, product banner\nNegative prompt: text, watermark\n"
              "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1234, Size: 64x48, Model: sd_xl_base_1.0")
    return png_insert(base_png("#2BB673"), png_chunk(b"tEXt", b"parameters\x00" + params.encode("latin-1")))


def png_c2pa_ai() -> bytes:
    """PNG with a minimal caBX JUMBF manifest store: c2pa.actions with trainedAlgorithmicMedia, Adobe Firefly."""
    return png_insert(base_png("#F2C12B"), png_chunk(b"caBX", manifest_store()))


def jpeg_c2pa_ai(split: bool = False) -> bytes:
    """JPEG with the same manifest store in APP11 (optionally split over several segments)."""
    segs = app11_segments(manifest_store(generator="ChatGPT", agent="DALL-E", claim_v2=True),
                          max_payload=120 if split else 65_000)
    return jpeg_insert(base_jpeg("#F25C2B"), *segs)


def jpeg_camera() -> bytes:
    """A plain camera-like JPEG with EXIF Make/Model/Software."""
    ex = Image.Exif()
    ex[0x010F] = "Canon"
    ex[0x0110] = "Canon EOS R6"
    ex[0x0131] = "Adobe Lightroom Classic 13.0"
    return base_jpeg("#888888", exif=ex.tobytes())


def png_plain() -> bytes:
    """No metadata at all."""
    return base_png("#CCCCCC")


def demo_banner() -> bytes:
    """800x450 demo banner (labelled as a demo asset in the image) with a caBX manifest saying Adobe Firefly
    + trainedAlgorithmicMedia. Used by frontend/e2e/provenance.py."""
    from PIL import ImageDraw

    im = Image.new("RGB", (800, 450), "#1E2A44")
    d = ImageDraw.Draw(im)
    for y in range(450):
        d.line([(0, y), (800, y)], fill=(30 + y // 6, 42 + y // 9, 68 + y // 5))
    d.ellipse((520, 60, 760, 300), fill="#F2A541")
    d.rectangle((40, 330, 470, 400), fill="#FFFFFF")
    d.text((56, 350), "DEMO ASSET - synthetic test image with a C2PA manifest", fill="#1E2A44")
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return png_insert(out.getvalue(), png_chunk(b"caBX", manifest_store()))


FIXTURES = {
    "ai_c2pa_demo_banner.png": demo_banner,
    "ai_xmp_trained.jpg": jpeg_xmp_ai,
    "ai_sd_parameters.png": png_sd_parameters,
    "ai_c2pa_firefly.png": png_c2pa_ai,
    "ai_c2pa_dalle.jpg": jpeg_c2pa_ai,
    "camera_exif.jpg": jpeg_camera,
    "no_metadata.png": png_plain,
}

if __name__ == "__main__":
    d = Path(__file__).parent / "fixtures" / "provenance"
    d.mkdir(parents=True, exist_ok=True)
    for name, fn in FIXTURES.items():
        (d / name).write_bytes(fn())
        print("wrote", d / name)
