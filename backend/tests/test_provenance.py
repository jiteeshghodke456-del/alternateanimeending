"""AI-image provenance (pipeline/provenance.py): readers per format, the R-AI-LABEL truth table, disclosure
detection, metadata stripping of the stored copy, and the pipeline/API wiring (incl. confidential mode)."""
from __future__ import annotations

import asyncio
import io
import struct
from pathlib import Path

import pytest
from PIL import Image

import provenance_fixtures as F
from app.pipeline import engine as E
from app.pipeline import provenance as P
from app.pipeline import units as U
from app.pipeline.context import CheckContext, UnitIn
from app.pipeline.jev import CallLog

FIX = Path(__file__).parent / "fixtures" / "provenance"


# ------------------------------------------------------------------ readers, one per format/case
def test_jpeg_xmp_trained_algorithmic_media_is_ai_generated() -> None:
    p = P.read_provenance(F.jpeg_xmp_ai(), "x.jpg")
    assert p.status == "ai_generated" and not p.c2pa
    assert p.source_types == ["trainedAlgorithmicMedia"]
    assert any(e["source"] == "XMP" and "DigitalSourceType" in e["field"] for e in p.evidence)


def test_jpeg_xmp_composite_is_ai_edited_and_creator_tool_named() -> None:
    xmp = F.xmp_packet("compositeWithTrainedAlgorithmicMedia", creator_tool="Adobe Photoshop 25.9")
    data = F.jpeg_insert(F.base_jpeg(), F.segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode()))
    p = P.read_provenance(data)
    assert p.status == "ai_edited" and p.generator == "Adobe Photoshop 25.9"


def test_png_sd_parameters_chunk_is_ai_generated_without_storing_the_prompt() -> None:
    p = P.read_provenance(F.png_sd_parameters(), "x.png")
    assert p.status == "ai_generated" and p.generator == "Stable Diffusion WebUI"
    assert "Mumbai" not in str(p.to_dict())  # the prompt itself is never kept


def test_png_comfyui_and_itxt_xmp_and_ztxt() -> None:
    comfy = F.png_chunk(b"tEXt", b'prompt\x00{"3": {"class_type": "KSampler", "inputs": {}}}')
    assert P.read_provenance(F.png_insert(F.base_png(), comfy)).generator == "ComfyUI"
    itxt = F.png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + F.xmp_packet().encode())
    assert P.read_provenance(F.png_insert(F.base_png(), itxt)).status == "ai_generated"
    import zlib
    ztxt = F.png_chunk(b"zTXt", b"Software\x00\x00" + zlib.compress(b"Midjourney"))
    p = P.read_provenance(F.png_insert(F.base_png(), ztxt))
    assert p.status == "ai_generated" and p.generator == "Midjourney"


def test_png_c2pa_cabx_manifest_is_decoded_signature_not_verified() -> None:
    p = P.read_provenance(F.png_c2pa_ai(), "x.png")
    assert p.status == "ai_generated" and p.c2pa and p.c2pa_method == "cbor"
    assert p.generator == "Adobe Firefly" and p.signature == "not_verified"
    assert "signature not verified" in p.note


@pytest.mark.parametrize("split", [False, True])
def test_jpeg_app11_manifest_v2_claim_generator_info_and_split_segments(split: bool) -> None:
    p = P.read_provenance(F.jpeg_c2pa_ai(split=split))
    assert p.status == "ai_generated" and p.c2pa_method == "cbor" and p.generator == "DALL-E"
    assert any(e["field"] == "claim generator" and e["value"] == "ChatGPT" for e in p.evidence)


def test_c2pa_without_ai_source_type_is_camera_or_unknown() -> None:
    store = F.manifest_store(generator="Leica_M11P c2pa-rs/0.30", source_type="digitalCapture", agent=None)
    p = P.read_provenance(F.png_insert(F.base_png(), F.png_chunk(b"caBX", store)))
    assert p.status == "camera_or_unknown" and p.c2pa and p.generator == "Leica M11P"


def test_undecodable_manifest_falls_back_to_byte_scan() -> None:
    junk = b"\xff\xff garbage " + (P.IPTC_PREFIX + "trainedAlgorithmicMedia").encode() + b" Adobe Firefly"
    store = F.jumb("c2pa", F.jumb("urn:uuid:x", F.jumb("c2pa.claim", F.box(b"cbor", junk))))
    p = P.read_provenance(F.png_insert(F.base_png(), F.png_chunk(b"caBX", store)))
    assert p.c2pa and p.c2pa_method == "byte-scan" and p.status == "ai_generated"
    assert "scanned" in p.note


def test_webp_chunks() -> None:
    xmp = F.xmp_packet().encode()
    out = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(out, "WEBP")
    w = out.getvalue()
    chunk = b"XMP " + struct.pack("<I", len(xmp)) + xmp + (b"\x00" if len(xmp) & 1 else b"")
    body = w[12:] + chunk
    data = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body
    assert P.read_provenance(data).status == "ai_generated"


def test_camera_exif_is_camera_or_unknown() -> None:
    p = P.read_provenance(F.jpeg_camera(), "x.jpg")
    assert p.status == "camera_or_unknown"
    assert {e["field"] for e in p.evidence} >= {"Make", "Model", "Software"}


def test_exif_software_naming_a_generator_and_sd_usercomment() -> None:
    ex = Image.Exif()
    ex[0x0131] = "Adobe Firefly"
    assert P.read_provenance(F.base_jpeg(exif=ex.tobytes())).generator == "Adobe Firefly"


def test_no_metadata() -> None:
    p = P.read_provenance(F.png_plain())
    assert p.status == "no_metadata" and p.evidence == [] and "stripped" in p.note


def test_real_c2pa_sample_from_c2pa_rs() -> None:
    p = P.read_provenance((FIX / "c2pa-rs_C.jpg").read_bytes())
    assert p.c2pa and p.c2pa_method == "cbor" and "algorithmicMedia" in p.source_types
    assert p.status == "ai_generated"


@pytest.mark.parametrize("data", [b"", b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, b"RIFF\x00\x00\x00\x00WEBPxx",
                                  F.png_c2pa_ai()[:200], F.jpeg_c2pa_ai()[:300], b"%PDF-1.4"])
def test_malformed_input_never_raises(data: bytes) -> None:
    assert P.read_provenance(data).status in ("ai_generated", "ai_edited", "camera_or_unknown", "no_metadata")


def test_cbor_reader_limits() -> None:
    assert P.cbor_loads(F.cbor({"a": [1, -2, "x", b"\x00", True, None]})) == {"a": [1, -2, "x", b"\x00", True, None]}
    with pytest.raises(P._CborError):
        P.cbor_loads(b"\x81" * 100 + b"\x00")  # nesting deeper than the limit
    with pytest.raises(P._CborError):
        P.cbor_loads(b"\x7a\xff\xff\xff\xff")  # string longer than the buffer


def test_files_on_disk_read_like_the_builders() -> None:  # bytes may differ across Pillow/zlib versions
    for name, fn in F.FIXTURES.items():
        a, b = P.read_provenance((FIX / name).read_bytes()), P.read_provenance(fn())
        assert (a.status, a.generator, a.source_types) == (b.status, b.generator, b.source_types), name


# ------------------------------------------------------------------ disclosure + truth table
@pytest.mark.parametrize("text", ["Image: AI-generated.", "Made with AI ✨", "#AIgenerated #coffee", "AI generated visual",
                                  "This visual was created using generative AI.", "Synthetic image.",
                                  "यह चित्र एआई से बना है", "Digitally generated artwork"])
def test_disclosure_found(text: str) -> None:
    assert P.disclosure_found(text)


@pytest.mark.parametrize("text", ["Fresh roast, every morning.", "This photo is not AI-generated.", "Said the chairman",
                                  "Visit our AIIMS partner", "", None])
def test_disclosure_not_found(text: str | None) -> None:
    assert not P.disclosure_found(text)


@pytest.mark.parametrize("status,disclosed,needs", [
    ("ai_generated", False, True), ("ai_generated", True, False),
    ("ai_edited", False, True), ("ai_edited", True, False),
    ("camera_or_unknown", False, False), ("no_metadata", False, False), ("no_metadata", True, False),
])
def test_truth_table(status: str, disclosed: bool, needs: bool) -> None:
    prov = P.Provenance(status=status, generator="Adobe Firefly", c2pa=True,  # type: ignore[arg-type]
                        evidence=[{"source": "C2PA manifest", "field": "f", "value": "v"}])
    d = P.decide(prov, ["Our new blend. AI-generated image." if disclosed else "Our new blend."])
    assert d.needs_label is needs and d.disclosed is disclosed
    if needs:
        assert "Content Credentials" in d.message and "Adobe Firefly" in d.message and "Label it as AI-generated" in d.message
    assert P.decide(None, ["x"]).needs_label is False


def test_label_fix() -> None:
    assert P.label_fix("Fresh roast.") == "Fresh roast. Image: AI-generated."
    assert P.label_fix("Fresh roast") == "Fresh roast. Image: AI-generated."
    assert P.disclosure_found(P.label_fix("x"))


# ------------------------------------------------------------------ stored copy has no metadata
@pytest.mark.parametrize("name", list(F.FIXTURES))
def test_normalise_strips_metadata_but_keeps_pixels(name: str) -> None:
    data = F.FIXTURES[name]()
    out, _, _ = U.normalise_image(data)
    assert P.read_provenance(out).status == "no_metadata"
    a, b = Image.open(io.BytesIO(data)).convert("RGB"), Image.open(io.BytesIO(out)).convert("RGB")
    assert a.size == b.size and a.getpixel((3, 3)) == b.getpixel((3, 3))


def test_reencode_path_strips_metadata_too() -> None:
    big = F.png_insert(F.base_png(size=(2100, 40)), F.png_chunk(b"caBX", F.manifest_store()))
    assert P.read_provenance(big).status == "ai_generated"
    out, _, _ = U.normalise_image(big)
    assert P.read_provenance(out).status == "no_metadata"


def test_strip_keeps_icc_profile() -> None:
    icc = F.segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + b"\x00" * 64)
    data = F.jpeg_insert(F.jpeg_xmp_ai(), icc)
    out = P.strip_metadata(data)
    assert out is not None and b"ICC_PROFILE" in out and b"DigitalSourceType" not in out


# ------------------------------------------------------------------ pipeline (engine) incl. confidential
def _units(prov: dict, caption: str | None, alt: str | None = None) -> list[UnitIn]:
    us = [UnitIn(id=1, kind="image", idx=0, image_path=None, meta={"provenance": prov})]
    if caption:
        us.append(UnitIn(id=2, kind="caption", idx=1, text=caption, char_start=0))
    if alt:
        us.append(UnitIn(id=3, kind="alt_text", idx=len(us), text=alt))
    return us


def _run(units: list[UnitIn], confidential: bool = True, caption: str = "") -> tuple[E.EngineResult, CallLog]:
    ctx = CheckContext(channel="instagram", rules=[], version_kind="image", post_text=caption, confidential=confidential)
    calls = CallLog()
    res = asyncio.run(E.analyze(units, ctx, calls=calls))
    return res, calls


def test_engine_flags_undisclosed_ai_image_in_confidential_mode_with_zero_calls() -> None:
    prov = P.read_provenance(F.png_c2pa_ai()).to_dict()
    cap = "Meet our new cold brew. Order today."
    res, calls = _run(_units(prov, cap), caption=cap)
    f = [d for d in res.findings if d.rule_code == P.RULE_CODE]
    assert len(f) == 1 and f[0].band == "flag" and f[0].severity == "major" and f[0].category == "legal"
    assert f[0].tier == "provenance" and "Adobe Firefly" in f[0].explanation
    assert f[0].quote == "Order today." and f[0].fix == "Order today. Image: AI-generated." and f[0].fix_verified
    assert (f[0].span_start, f[0].span_end) == (cap.index("Order"), len(cap))
    assert calls.records == [] and res.provenance[0]["needs_label"] is True


def test_engine_no_caption_finding_on_image_without_fix() -> None:
    res, _ = _run(_units(P.read_provenance(F.png_sd_parameters()).to_dict(), None))
    f = next(d for d in res.findings if d.rule_code == P.RULE_CODE)
    assert f.fix is None and f.span_start is None and "Add a caption or alt text" in f.explanation


@pytest.mark.parametrize("caption,alt", [("New blend. #AIgenerated", None), ("New blend.", "AI-generated image of a cup")])
def test_engine_disclosed_records_pass_note(caption: str, alt: str | None) -> None:
    res, _ = _run(_units(P.read_provenance(F.jpeg_xmp_ai()).to_dict(), caption, alt), caption=caption)
    assert not [d for d in res.findings if d.rule_code == P.RULE_CODE]
    assert res.provenance[0]["disclosed"] is True and res.provenance[0]["needs_label"] is False


@pytest.mark.parametrize("fn", [F.png_plain, F.jpeg_camera])
def test_engine_no_finding_without_ai_metadata(fn) -> None:  # noqa: ANN001
    res, _ = _run(_units(P.read_provenance(fn()).to_dict(), "Hello."), caption="Hello.")
    assert not [d for d in res.findings if d.rule_code == P.RULE_CODE]


def test_engine_respects_a_disabled_brand_rule() -> None:
    from app.pipeline.context import RuleSpec

    ctx = CheckContext(channel="instagram", version_kind="image", confidential=True,
                       rules=[RuleSpec(code=P.RULE_CODE, category="legal", severity="major", statement="x",
                                       check_method="tier0", enabled=False)])
    res = asyncio.run(E.analyze(_units(P.read_provenance(F.png_c2pa_ai()).to_dict(), None), ctx, calls=CallLog()))
    assert not [d for d in res.findings if d.rule_code == P.RULE_CODE]


# ------------------------------------------------------------------ API: upload -> unit provenance -> finding -> fix -> resubmit
def test_api_upload_flag_accept_resubmit(client) -> None:  # noqa: ANN001
    from conftest import login
    from test_run import findings, wait_done

    login(client, "creator")
    data = (FIX / "ai_c2pa_demo_banner.png").read_bytes()
    r = client.post("/api/items", data={"title": "AI banner", "channel": "instagram", "confidential": "true",
                                        "caption": "Meet our new cold brew.", "alt_text": "A cup of cold brew at sunrise"},
                    files={"file": ("banner.png", data, "image/png")})
    assert r.status_code == 201, r.text
    ids = r.json()
    v = wait_done(client, ids["version_id"])
    img = next(u for u in v["units"] if u["kind"] == "image")
    assert img["provenance"]["status"] == "ai_generated" and img["provenance"]["generator"] == "Adobe Firefly"
    assert img["provenance"]["signature"] == "not_verified"
    assert v["stats"]["provenance"][0]["needs_label"] is True and v["stats"]["external_calls"] == 0
    stored = client.get(v["file_url"]).content
    assert P.read_provenance(stored).status == "no_metadata"  # the stored copy is stripped
    f = next(x for x in findings(client, ids["version_id"]) if x["rule_code"] == P.RULE_CODE)
    assert f["tier"] == "provenance" and f["fix"] == "Meet our new cold brew. Image: AI-generated."
    assert client.post(f"/api/findings/{f['id']}/accept").status_code == 200
    r2 = client.post(f"/api/items/{ids['item_id']}/resubmit")
    assert r2.status_code == 200, r2.text
    v2 = wait_done(client, r2.json()["version_id"])
    assert v2["text"] == "Meet our new cold brew. Image: AI-generated."
    img2 = next(u for u in v2["units"] if u["kind"] == "image")
    assert img2["provenance"]["status"] == "ai_generated"  # carried over from the original upload
    assert v2["stats"]["provenance"][0]["disclosed"] is True
    assert not [x for x in findings(client, r2.json()["version_id"]) if x["rule_code"] == P.RULE_CODE]
