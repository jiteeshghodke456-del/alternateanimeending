"""AI-image provenance from file metadata. Pure Python, local, no pixel decoding. OWNER: pipeline-agent.

`read_provenance(original_bytes, filename)` must be called on the ORIGINAL upload, before
`units.normalise_image` stores a metadata-free copy. It reads:

- **C2PA / Content Credentials** manifest stores: JPEG APP11 JUMBF segments, the PNG `caBX` chunk and the
  WebP `C2PA` chunk. The JUMBF box tree is walked and the CBOR claim and `c2pa.actions` assertions are
  decoded with a small CBOR reader below, for `claim_generator` / `claim_generator_info` and each action's
  `digitalSourceType` and `softwareAgent`. If that decoding fails, the manifest bytes are scanned for the
  IPTC digital-source-type URIs and known generator names instead (`c2pa_method = "byte-scan"`).
  **Signatures are NOT verified**: we report "manifest found; signature not verified". A forged or
  stripped manifest is not detected.
- **XMP** packets (JPEG APP1, PNG iTXt `XML:com.adobe.xmp`, WebP `XMP `): `DigitalSourceType`,
  `CreatorTool`, IPTC 2025 `AISystemUsed`, and `photoshop:Credit` / `photoshop:Source`. Read with regular
  expressions, never an XML parser (no entity expansion).
- **EXIF** (JPEG APP1, PNG `eXIf`, WebP `EXIF`): Make, Model, Software, and UserComment (where the
  Stable Diffusion WebUI writes its settings in JPEGs).
- **PNG text chunks** written by generators: `parameters` (Stable Diffusion WebUI / Forge), `prompt` and
  `workflow` (ComfyUI), `invokeai_metadata` / `sd-metadata` / `Dream` (InvokeAI), and `Software`.

What it can't do: detect an AI image whose metadata was stripped (most social apps strip it). Absence
of provenance data proves nothing, so `no_metadata` never produces a finding, only a note.

`decide(prov, texts)` is the truth table for the built-in R-AI-LABEL rule (code decides, no model).
`strip_metadata(data)` removes metadata from a PNG/JPEG without re-encoding pixels (privacy of the stored copy).
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Optional

Status = Literal["ai_generated", "ai_edited", "camera_or_unknown", "no_metadata"]

RULE_CODE = "R-AI-LABEL"
RULE_STATEMENT = ("Label AI-generated or AI-edited images as AI-generated in the caption or alt text "
                  "(India IT Rules 2026: synthetically generated information must be labelled).")
LABEL_SUFFIX = "Image: AI-generated."
SIGNATURE_NOTE = "Content Credentials manifest found; signature not verified."
NO_DATA_NOTE = ("No provenance data (metadata may have been stripped). "
                "This does not show the image is or isn't AI-made.")

IPTC_PREFIX = "http://cv.iptc.org/newscodes/digitalsourcetype/"
AI_GENERATED_TYPES = {"trainedAlgorithmicMedia", "algorithmicMedia"}
AI_EDITED_TYPES = {"compositeWithTrainedAlgorithmicMedia", "compositeSynthetic"}
SOURCE_TYPE_WORDS = {
    "trainedAlgorithmicMedia": "made by a trained AI model",
    "algorithmicMedia": "made by an algorithm",
    "compositeWithTrainedAlgorithmicMedia": "a composite that includes AI-generated parts",
    "compositeSynthetic": "a composite that includes synthetic parts",
    "digitalCapture": "a digital camera capture",
    "computationalCapture": "a computational camera capture",
    "negativeFilm": "scanned from negative film",
    "positiveFilm": "scanned from positive film",
    "print": "scanned from a print",
    "minorHumanEdits": "a capture with minor human edits",
    "humanEdits": "edited by a person",
    "compositeCapture": "a composite of captures",
    "digitalArt": "digital art made by a person",
    "virtualRecording": "a virtual recording",
    "screenCapture": "a screen capture",
    "dataDrivenMedia": "data-driven media",
    "composite": "a composite",
}
_DST_RE = re.compile(rb"digitalsourcetype/([A-Za-z]{4,60})", re.I)

# Tool names that only belong to AI image generators (matched case-insensitively). Name-only evidence.
_AI_NAMES: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.I), n) for p, n in [
    (r"midjourney", "Midjourney"),
    (r"dall[\s\-·.]?e", "DALL·E"),
    (r"chatgpt", "ChatGPT"),
    (r"\bopenai\b", "OpenAI"),
    (r"firefly", "Adobe Firefly"),
    (r"stable[\s_\-]?diffusion", "Stable Diffusion"),
    (r"comfyui", "ComfyUI"),
    (r"automatic1111", "Stable Diffusion WebUI"),
    (r"novelai", "NovelAI"),
    (r"invokeai", "InvokeAI"),
    (r"\bimagen\b", "Google Imagen"),
    (r"\bgemini\b", "Google Gemini"),
    (r"made with google ai", "Google AI"),
    (r"microsoft[\s_]responsible[\s_]ai", "Microsoft Designer / Bing Image Creator"),
    (r"bing image creator", "Bing Image Creator"),
    (r"microsoft designer", "Microsoft Designer"),
    (r"leonardo\.ai", "Leonardo.Ai"),
    (r"ideogram", "Ideogram"),
]]


def ai_name(text: str) -> Optional[str]:
    """The AI generator a tool/software string names, if any."""
    for pat, name in _AI_NAMES:
        if pat.search(text or ""):
            return name
    return None


@dataclass
class Provenance:
    status: Status = "no_metadata"
    generator: Optional[str] = None
    evidence: list[dict[str, str]] = field(default_factory=list)  # {source, field, value}
    c2pa: bool = False  # a Content Credentials manifest store is present
    c2pa_method: Optional[str] = None  # "cbor" (decoded) | "byte-scan" (fallback) | None
    signature: str = "none"  # "not_verified" when a manifest is present; we never verify
    source_types: list[str] = field(default_factory=list)  # IPTC digitalSourceType terms found
    note: str = NO_DATA_NOTE
    checked_locally: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> Optional["Provenance"]:
        if not isinstance(d, dict) or "status" not in d:
            return None
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})


# ------------------------------------------------------------------ small CBOR reader (RFC 8949 subset)
class _CborError(ValueError):
    pass


_BREAK = object()


def _cbor(buf: bytes, i: int, depth: int = 0, budget: list[int] | None = None) -> tuple[Any, int]:
    """Decode one CBOR item at buf[i]. Bounded: depth <= 24, <= 20k items, lengths checked."""
    if budget is None:
        budget = [20_000]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0 or i >= len(buf):
        raise _CborError("cbor limits")
    ib = buf[i]
    mt, ai = ib >> 5, ib & 31
    i += 1
    if mt == 7:
        if ai == 31:
            return _BREAK, i
        if ai < 24:
            return {20: False, 21: True, 22: None, 23: None}.get(ai), i
        size = {24: 1, 25: 2, 26: 4, 27: 8}.get(ai)
        if size is None or i + size > len(buf):
            raise _CborError("bad simple")
        raw = buf[i:i + size]
        if ai == 25:
            return struct.unpack(">e", raw)[0], i + size
        if ai == 26:
            return struct.unpack(">f", raw)[0], i + size
        if ai == 27:
            return struct.unpack(">d", raw)[0], i + size
        return raw[0], i + size
    if ai < 24:
        arg: Optional[int] = ai
    elif ai in (24, 25, 26, 27):
        size = 1 << (ai - 24)
        if i + size > len(buf):
            raise _CborError("short arg")
        arg = int.from_bytes(buf[i:i + size], "big")
        i += size
    elif ai == 31 and mt in (2, 3, 4, 5):
        arg = None  # indefinite length
    else:
        raise _CborError("bad arg")
    if mt == 0:
        return arg, i
    if mt == 1:
        return -1 - (arg or 0), i
    if mt in (2, 3):
        if arg is None:
            parts: list[Any] = []
            while True:
                v, i = _cbor(buf, i, depth + 1, budget)
                if v is _BREAK:
                    break
                parts.append(v)
            return (b"".join(parts) if mt == 2 else "".join(parts)), i
        if i + arg > len(buf):
            raise _CborError("short string")
        raw = buf[i:i + arg]
        return (bytes(raw) if mt == 2 else bytes(raw).decode("utf-8", "replace")), i + arg
    if mt == 4:
        out: list[Any] = []
        n = 0
        while arg is None or n < arg:
            v, i = _cbor(buf, i, depth + 1, budget)
            if v is _BREAK:
                if arg is None:
                    break
                raise _CborError("stray break")
            out.append(v)
            n += 1
        return out, i
    if mt == 5:
        m: dict[Any, Any] = {}
        n = 0
        while arg is None or n < arg:
            k, i = _cbor(buf, i, depth + 1, budget)
            if k is _BREAK:
                if arg is None:
                    break
                raise _CborError("stray break")
            v, i = _cbor(buf, i, depth + 1, budget)
            m[k if isinstance(k, (str, int, float, bool, bytes)) or k is None else repr(k)] = v
            n += 1
        return m, i
    # mt == 6: tag; return the tagged value
    return _cbor(buf, i, depth + 1, budget)


def cbor_loads(data: bytes) -> Any:
    v, _ = _cbor(data, 0)
    if v is _BREAK:
        raise _CborError("stray break")
    return v


# ------------------------------------------------------------------ JUMBF (ISO 19566-5) box tree
def _boxes(buf: bytes, start: int, end: int):  # noqa: ANN202
    """Yield (type, payload_start, payload_end) for consecutive ISO BMFF-style boxes."""
    i = start
    while i + 8 <= end:
        lbox = int.from_bytes(buf[i:i + 4], "big")
        tbox = bytes(buf[i + 4:i + 8])
        hdr = 8
        if lbox == 1:
            if i + 16 > end:
                return
            lbox = int.from_bytes(buf[i + 8:i + 16], "big")
            hdr = 16
        elif lbox == 0:
            lbox = end - i
        if lbox < hdr or i + lbox > end:
            return
        yield tbox, i + hdr, i + lbox
        i += lbox


def _jumbf_content(buf: bytes, start: int, end: int, path: tuple[str, ...] = (), depth: int = 0,
                   out: Optional[list] = None) -> list[tuple[tuple[str, ...], bytes, int, int]]:
    """Flatten a JUMBF tree into (label path, content box type, start, end)."""
    if out is None:
        out = []
    if depth > 16:
        return out
    for t, ps, pe in _boxes(buf, start, end):
        if t != b"jumb":
            out.append((path, t, ps, pe))
            continue
        label = ""
        kids = list(_boxes(buf, ps, pe))
        if kids and kids[0][0] == b"jumd":
            _, ds, de = kids[0]
            if de - ds >= 17 and buf[ds + 16] & 0x02:  # toggles: label present
                stop = buf.find(b"\x00", ds + 17, de)
                label = bytes(buf[ds + 17:stop if stop >= 0 else de]).decode("utf-8", "replace")
            kids = kids[1:]
        for kt, ks, ke in kids:
            if kt == b"jumb":
                _jumbf_content(buf, ks - 8, ke, path + (label,), depth + 1, out)
            else:
                out.append((path + (label,), kt, ks, ke))
    return out


def _agent_name(agent: Any) -> Optional[str]:
    if isinstance(agent, str):
        return agent
    if isinstance(agent, dict) and isinstance(agent.get("name"), str):
        return agent["name"]
    return None


def pretty_generator(raw: str) -> str:
    """"Adobe_Firefly c2pa-rs/0.32.0" -> "Adobe Firefly"; "Microsoft_Responsible_AI/1.0" -> "Microsoft Responsible AI"."""
    toks = [t for t in raw.split() if not t.lower().startswith(("c2pa-", "c2pa_", "c2patool"))]
    toks = [t.split("/")[0].replace("_", " ") for t in toks] or [raw]
    return " ".join(toks).strip()[:80] or raw[:80]


@dataclass
class _C2paInfo:
    generators: list[str] = field(default_factory=list)  # claim generators, active manifest last
    actions: list[tuple[str, Optional[str], Optional[str]]] = field(default_factory=list)  # (action, dst, agent)
    method: str = "cbor"


def _parse_manifest_store(store: bytes) -> _C2paInfo:
    info = _C2paInfo()
    try:
        content = _jumbf_content(store, 0, len(store))
        if not content:
            raise _CborError("no jumbf boxes")
        for path, t, s, e in content:
            label = path[-1] if path else ""
            if t != b"cbor":
                continue
            if label.startswith("c2pa.claim"):
                claim = cbor_loads(store[s:e])
                if not isinstance(claim, dict):
                    raise _CborError("claim is not a map")
                g = claim.get("claim_generator")
                gi = claim.get("claim_generator_info")
                names = [g] if isinstance(g, str) else []
                if isinstance(gi, dict):
                    gi = [gi]
                if isinstance(gi, list):
                    names += [n for n in (_agent_name(x) for x in gi) if n]
                if names:
                    info.generators.append(names[0])
            elif label.startswith("c2pa.actions"):
                body = cbor_loads(store[s:e])
                acts = body.get("actions") if isinstance(body, dict) else None
                for a in acts if isinstance(acts, list) else []:
                    if not isinstance(a, dict):
                        continue
                    dst = a.get("digitalSourceType")
                    info.actions.append((str(a.get("action", "")), dst if isinstance(dst, str) else None,
                                         _agent_name(a.get("softwareAgent"))))
        return info
    except (_CborError, UnicodeDecodeError, struct.error, IndexError, ValueError):
        pass
    # Fallback: scan the raw manifest bytes (documented as "byte-scan").
    info = _C2paInfo(method="byte-scan")
    for m in _DST_RE.finditer(store):
        info.actions.append(("", IPTC_PREFIX + m.group(1).decode("ascii"), None))
    k = store.find(b"claim_generator")
    if k >= 0:
        try:
            v, _ = _cbor(store, k + len(b"claim_generator"))
            if isinstance(v, str):
                info.generators.append(v)
        except _CborError:
            pass
    if not info.generators:
        name = ai_name(store[:200_000].decode("latin-1"))
        if name:
            info.generators.append(name)
    return info


# ------------------------------------------------------------------ EXIF (TIFF) and XMP
_EXIF_TAGS = {0x010F: "Make", 0x0110: "Model", 0x0131: "Software", 0x013B: "Artist", 0x9286: "UserComment"}


def _read_exif(tiff: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return out
    end = "<" if tiff[:2] == b"II" else ">"

    def u16(o: int) -> int:
        return struct.unpack(end + "H", tiff[o:o + 2])[0]

    def u32(o: int) -> int:
        return struct.unpack(end + "I", tiff[o:o + 4])[0]

    def ifd(off: int, depth: int) -> None:
        if depth > 2 or off <= 0 or off + 2 > len(tiff):
            return
        n = min(u16(off), 512)
        for k in range(n):
            e = off + 2 + 12 * k
            if e + 12 > len(tiff):
                return
            tag, typ, cnt = u16(e), u16(e + 2), u32(e + 4)
            if tag == 0x8769:
                ifd(u32(e + 8), depth + 1)
                continue
            name = _EXIF_TAGS.get(tag)
            if not name or typ not in (2, 7) or cnt == 0 or cnt > 65_536:
                continue
            vo = e + 8 if cnt <= 4 else u32(e + 8)
            raw = tiff[vo:vo + cnt]
            if tag == 0x9286 and len(raw) >= 8:  # 8-byte charset prefix
                enc = raw[:8]
                text = raw[8:].decode("utf-16-be" if end == ">" else "utf-16-le", "replace") \
                    if enc.startswith(b"UNICODE") else raw[8:].decode("latin-1")
            else:
                text = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            text = text.strip("\x00 ").strip()
            if text:
                out[name] = text
    try:
        ifd(u32(4), 0)
    except struct.error:
        pass
    return out


def _xmp_values(xmp: str, local: str) -> list[str]:
    """Attribute (`ns:Local="v"`) and element (`<ns:Local>v</ns:Local>`, incl. rdf:li) values."""
    vals = re.findall(rf'(?:\w+:)?{local}\s*=\s*["\']([^"\']*)["\']', xmp)
    for block in re.findall(rf"<(?:\w+:)?{local}\b[^>]*>(.*?)</(?:\w+:)?{local}>", xmp, re.S):
        inner = re.findall(r"<rdf:li[^>]*>([^<]*)</rdf:li>", block) or [block]
        vals += [re.sub(r"<[^>]+>", "", v) for v in inner]
    vals += re.findall(rf'<(?:\w+:)?{local}\b[^>]*rdf:resource\s*=\s*["\']([^"\']*)["\']', xmp)
    return [v.strip() for v in vals if v and v.strip()]


# ------------------------------------------------------------------ container walkers
@dataclass
class _Raw:
    c2pa: list[bytes] = field(default_factory=list)
    xmp: list[str] = field(default_factory=list)
    exif: list[bytes] = field(default_factory=list)
    png_text: list[tuple[str, str]] = field(default_factory=list)


def _jpeg(data: bytes, raw: _Raw) -> None:
    i = 2
    app11: dict[int, list[tuple[int, bytes]]] = {}  # En -> [(Z, chunk)]
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xDA, 0xD9):  # start of scan / end: metadata segments come before
            break
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        body = data[i + 4:i + 2 + seg_len]
        if marker == 0xE1:
            if body.startswith(b"Exif\x00\x00"):
                raw.exif.append(body[6:])
            elif body.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
                raw.xmp.append(body[29:].decode("utf-8", "replace"))
            elif body.startswith(b"http://ns.adobe.com/xmp/extension/\x00"):
                raw.xmp.append(body[35 + 32 + 8:].decode("utf-8", "replace"))
        elif marker == 0xEB and body[:2] == b"JP" and len(body) >= 16:
            en = int.from_bytes(body[2:4], "big")
            z = int.from_bytes(body[4:8], "big")
            app11.setdefault(en, []).append((z, bytes(body[8:])))
        i += 2 + seg_len
    for chunks in app11.values():
        chunks.sort(key=lambda c: c[0])
        # The first chunk carries the box header (LBox+TBox); later ones repeat it, so it is skipped.
        store = chunks[0][1] + b"".join(c[8:] for _, c in chunks[1:])
        if store[4:8] == b"jumb":
            raw.c2pa.append(store)


def _png_text(ctype: bytes, body: bytes) -> Optional[tuple[str, str]]:
    try:
        key, _, rest = body.partition(b"\x00")
        k = key.decode("latin-1")
        if ctype == b"tEXt":
            return k, rest.decode("latin-1")
        if ctype == b"zTXt":
            return k, _inflate(rest[1:]).decode("latin-1")
        # iTXt: compression flag, method, language\0, translated keyword\0, text
        flag = rest[0]
        rest = rest[2:]
        _, _, rest = rest.partition(b"\x00")
        _, _, text = rest.partition(b"\x00")
        return k, (_inflate(text) if flag else text).decode("utf-8", "replace")
    except (IndexError, zlib.error):
        return None


def _inflate(b: bytes, limit: int = 2_000_000) -> bytes:
    d = zlib.decompressobj()
    out = d.decompress(b, limit)  # bounded: a zip bomb in a text chunk stops at `limit`
    return out


def _png(data: bytes, raw: _Raw) -> None:
    i = 8
    while i + 12 <= len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        ctype = bytes(data[i + 4:i + 8])
        if i + 12 + n > len(data):
            break
        if ctype in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"caBX"):
            body = data[i + 8:i + 8 + n]
            if ctype == b"caBX":
                raw.c2pa.append(bytes(body))
            elif ctype == b"eXIf":
                raw.exif.append(bytes(body))
            else:
                kv = _png_text(ctype, body)
                if kv:
                    if kv[0] == "XML:com.adobe.xmp":
                        raw.xmp.append(kv[1])
                    else:
                        raw.png_text.append(kv)
        if ctype == b"IEND":
            break
        i += 12 + n


def _webp(data: bytes, raw: _Raw) -> None:
    i = 12
    end = min(len(data), 8 + int.from_bytes(data[4:8], "little"))
    while i + 8 <= end:
        ctype = bytes(data[i:i + 4])
        n = int.from_bytes(data[i + 4:i + 8], "little")
        body = data[i + 8:i + 8 + n]
        if ctype == b"C2PA":
            raw.c2pa.append(bytes(body))
        elif ctype == b"EXIF":
            b = bytes(body)
            raw.exif.append(b[6:] if b.startswith(b"Exif\x00\x00") else b)
        elif ctype == b"XMP ":
            raw.xmp.append(bytes(body).decode("utf-8", "replace"))
        i += 8 + n + (n & 1)


# ------------------------------------------------------------------ public API
def _short(v: str, n: int = 120) -> str:
    v = " ".join(str(v).split())
    return v if len(v) <= n else v[: n - 1] + "…"


def read_provenance(original_bytes: bytes, filename: str = "") -> Provenance:
    """Provenance of an image from its ORIGINAL bytes (before any re-encode). Never raises; never decodes pixels."""
    raw = _Raw()
    data = memoryview(original_bytes).tobytes() if not isinstance(original_bytes, bytes) else original_bytes
    try:
        if data.startswith(b"\xff\xd8\xff"):
            _jpeg(data, raw)
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            _png(data, raw)
        elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            _webp(data, raw)
    except Exception:  # noqa: BLE001  (a malformed container still yields whatever was read)
        pass
    return _classify(raw)


def _classify(raw: _Raw) -> Provenance:
    p = Provenance()
    ev = p.evidence
    gen_ai: list[str] = []  # AI generator names (strongest first)
    gen_any: list[str] = []
    strength = 0  # 0 none, 1 camera/unknown, 2 ai_edited, 3 ai_generated

    def note_dst(term: str, where: str, field_name: str) -> None:
        nonlocal strength
        if term not in p.source_types:
            p.source_types.append(term)
        ev.append({"source": where, "field": field_name, "value": f"{term} ({SOURCE_TYPE_WORDS.get(term, 'IPTC term')})"})
        strength = max(strength, 3 if term in AI_GENERATED_TYPES else 2 if term in AI_EDITED_TYPES else 1)

    # C2PA
    for store in raw.c2pa:
        p.c2pa = True
        p.signature = "not_verified"
        info = _parse_manifest_store(store)
        p.c2pa_method = info.method if p.c2pa_method in (None, "cbor") else p.c2pa_method
        if info.generators:
            g = info.generators[-1]  # the active manifest is the last one in the store
            ev.append({"source": "C2PA manifest", "field": "claim generator", "value": _short(g)})
            gen_any.append(pretty_generator(g))
        for action, dst, agent in info.actions:
            if dst:
                term = dst.rsplit("/", 1)[-1]
                note_dst(term, "C2PA manifest", f"{action or 'action'} digitalSourceType")
                if term in AI_GENERATED_TYPES | AI_EDITED_TYPES:
                    if agent:
                        gen_ai.append(pretty_generator(agent))
                    gen_ai += [pretty_generator(g) for g in info.generators[-1:]]
            elif agent and ai_name(agent):
                ev.append({"source": "C2PA manifest", "field": f"{action or 'action'} softwareAgent", "value": _short(agent)})
        for g in info.generators:
            n = ai_name(g)
            if n:
                gen_ai.append(pretty_generator(g))
                strength = max(strength, 3)
        strength = max(strength, 1)
    # XMP
    for xmp in raw.xmp:
        dsts = [m.decode("ascii") for m in _DST_RE.findall(xmp.encode("utf-8", "replace"))]
        for d in dict.fromkeys(dsts):
            note_dst(d, "XMP", "Iptc4xmpExt:DigitalSourceType")
        for local, label in (("CreatorTool", "xmp:CreatorTool"), ("AISystemUsed", "Iptc4xmpExt:AISystemUsed"),
                             ("Credit", "photoshop:Credit"), ("Source", "photoshop:Source")):
            for v in _xmp_values(xmp, local)[:3]:
                ev.append({"source": "XMP", "field": label, "value": _short(v)})
                gen_any.append(_short(v, 60))
                n = ai_name(v)
                if n or local == "AISystemUsed":
                    gen_ai.append(n or _short(v, 60))
                    strength = max(strength, 3)
        strength = max(strength, 1)
    # EXIF
    for tiff in raw.exif:
        tags = _read_exif(tiff)
        for name in ("Make", "Model", "Software", "Artist"):
            if name in tags:
                ev.append({"source": "EXIF", "field": name, "value": _short(tags[name])})
                strength = max(strength, 1)
                n = ai_name(tags[name])
                if n:
                    gen_ai.append(n)
                    strength = max(strength, 3)
        uc = tags.get("UserComment", "")
        if re.search(r"Steps:\s*\d+.*Sampler:", uc, re.S):
            ev.append({"source": "EXIF", "field": "UserComment",
                       "value": f"Stable Diffusion generation settings ({len(uc)} characters)"})
            gen_ai.append("Stable Diffusion WebUI")
            strength = 3
        if "Software" in tags:
            gen_any.append(_short(tags["Software"], 60))
    # PNG text chunks
    for key, val in raw.png_text:
        k = key.lower()
        if k == "parameters" and re.search(r"Steps:\s*\d+|Sampler:|CFG scale|Negative prompt", val):
            ev.append({"source": "PNG text", "field": "parameters",
                       "value": f"Stable Diffusion generation settings ({len(val)} characters)"})
            gen_ai.append("Stable Diffusion WebUI")
            strength = 3
        elif k in ("prompt", "workflow") and val.lstrip().startswith("{") and ("class_type" in val or "nodes" in val):
            ev.append({"source": "PNG text", "field": key, "value": f"ComfyUI workflow ({len(val)} characters)"})
            gen_ai.append("ComfyUI")
            strength = 3
        elif k in ("invokeai_metadata", "sd-metadata", "dream"):
            ev.append({"source": "PNG text", "field": key, "value": f"InvokeAI generation metadata ({len(val)} characters)"})
            gen_ai.append("InvokeAI")
            strength = 3
        elif k in ("software", "source", "author", "creator", "generator"):
            ev.append({"source": "PNG text", "field": key, "value": _short(val)})
            gen_any.append(_short(val, 60))
            strength = max(strength, 1)
            n = ai_name(val)
            if n:
                gen_ai.append(n)
                strength = 3

    p.status = {3: "ai_generated", 2: "ai_edited", 1: "camera_or_unknown", 0: "no_metadata"}[strength]  # type: ignore[assignment]
    if strength >= 2:
        p.generator = gen_ai[0] if gen_ai else (gen_any[0] if gen_any else None)
    else:
        p.generator = gen_any[0] if gen_any else None
    if p.status == "no_metadata":
        p.note = NO_DATA_NOTE
    else:
        parts = [SIGNATURE_NOTE] if p.c2pa else []
        if p.c2pa and p.c2pa_method == "byte-scan":
            parts.append("The manifest could not be fully decoded, so its bytes were scanned for IPTC source types.")
        if p.status == "camera_or_unknown":
            parts.append("Metadata found, but nothing in it says the image was made or edited with AI.")
        p.note = " ".join(parts)
    return p


# ------------------------------------------------------------------ disclosure + the R-AI-LABEL truth table
_DISCLOSURE = re.compile(
    r"(?<![\w#])(?:"
    r"ai[\s\-]?(?:generated|created|made|edited|assisted|enhanced|image|art|artwork|visual)"
    r"|(?:generated|created|made|edited|produced)\s+(?:with|by|using)\s+(?:an?\s+)?(?:ai|a\.i\.|generative ai|artificial intelligence)"
    r"|synthetic(?:ally generated)?(?:\s+(?:image|media|content|visual))?"
    r"|digitally\s+generated"
    r"|#\s?ai(?:generated|art|image|created)"
    r")(?!\w)"
    r"|एआई|ए\.आई\.|कृत्रिम\s+बुद्धिमत्ता|एआई[\s\-]?(?:जनित|निर्मित)",
    re.I,
)
_NEGATION = re.compile(r"\b(?:not|no|never|isn't|is not|without)\s+(?:an?\s+)?$", re.I)


def disclosure_found(*texts: Optional[str]) -> bool:
    """True when the text labels the image as AI-made ("AI-generated", "made with AI", "#AIgenerated",
    "synthetic", Hindi "एआई" ...). "not AI-generated" doesn't count."""
    for t in texts:
        for m in _DISCLOSURE.finditer(t or ""):
            if not _NEGATION.search(t[max(0, m.start() - 16):m.start()]):
                return True
    return False


@dataclass
class Decision:
    needs_label: bool  # True -> R-AI-LABEL finding
    disclosed: bool
    message: str


def decide(prov: Optional[Provenance], texts: Iterable[Optional[str]]) -> Decision:
    """Code decides: ai_generated / ai_edited without a disclosure -> finding; otherwise no finding."""
    texts = list(texts)
    disclosed = disclosure_found(*texts)
    if prov is None or prov.status not in ("ai_generated", "ai_edited"):
        return Decision(False, disclosed, "")
    who = prov.generator or "an AI tool"
    where = "Content Credentials" if prov.c2pa and any(e["source"] == "C2PA manifest" for e in prov.evidence) \
        else "metadata"
    made = "made with" if prov.status == "ai_generated" else "edited with"
    if disclosed:
        return Decision(False, True, f"This image's {where} say it was {made} {who}, and the caption labels it as AI-generated.")
    return Decision(True, False, f"This image's {where} say it was {made} {who}. Label it as AI-generated.")


def label_fix(text: str) -> str:
    """Append the AI label to a caption sentence (deterministic fix)."""
    t = text.rstrip()
    sep = "" if not t else (" " if t[-1] in ".!?…।" else ". ")
    return f"{t}{sep}{LABEL_SUFFIX}"


# ------------------------------------------------------------------ metadata stripping (stored copy)
_JPEG_KEEP = {0xE0, 0xEE}  # APP0 JFIF, APP14 Adobe (colour transform); APP2 kept only for ICC_PROFILE
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"caBX", b"tIME"}


def strip_metadata(data: bytes) -> Optional[bytes]:
    """PNG/JPEG without EXIF, XMP, IPTC, comments, text chunks or C2PA, pixels untouched (no re-encode).
    Returns `data` itself when there was nothing to strip, or None when the file can't be walked safely
    (the caller then re-encodes)."""
    try:
        if data.startswith(b"\xff\xd8\xff"):
            out = bytearray(data[:2])
            i, dropped = 2, False
            while i + 4 <= len(data):
                if data[i] != 0xFF:
                    return None
                marker = data[i + 1]
                if marker == 0xDA:
                    out += data[i:]
                    return data if not dropped else bytes(out)
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                seg = data[i:i + 2 + seg_len]
                keep = (not (0xE0 <= marker <= 0xEF) and marker != 0xFE) or marker in _JPEG_KEEP \
                    or (marker == 0xE2 and seg[4:16] == b"ICC_PROFILE\x00")
                if keep:
                    out += seg
                else:
                    dropped = True
                i += 2 + seg_len
            return None
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            out = bytearray(data[:8])
            i, dropped = 8, False
            while i + 12 <= len(data):
                n = int.from_bytes(data[i:i + 4], "big")
                ctype = data[i + 4:i + 8]
                if i + 12 + n > len(data):
                    return None
                if ctype in _PNG_DROP:
                    dropped = True
                else:
                    out += data[i:i + 12 + n]
                i += 12 + n
                if ctype == b"IEND":
                    return data if not dropped else bytes(out)
            return None
    except Exception:  # noqa: BLE001
        return None
    return None
