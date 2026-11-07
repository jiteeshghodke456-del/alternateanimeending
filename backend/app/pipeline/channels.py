"""Channel limits (built-in rules R-CH-*) and the PDF font check. Code only, local, no AI calls.

The platform numbers live in `seed/channels.json`, each with the official page it was read from and the
date. A brand can override any of them, and its per-channel score target, in Brand Kit -> Channels; the
overrides are stored in `Brand.channel_targets[channel]` (per workspace, like every brand setting):

    {"instagram": {"max_grade": 8, "target_score": 85, "limits": {"caption_max_chars": 2000}}}

Severities (decided here, never by a model):
- a hard platform limit (caption/post length, hashtag or @ count, image aspect ratio) is MAJOR, not
  blocking: the platform cuts or refuses the post, which is real, but it is a formatting problem the
  writer fixes in seconds, not a brand or legal risk; blocking stays reserved for rules a brand marks
  blocking (claims, health, logo).
- soft guidance (subject-line truncation, image upscaling) is MINOR.
"""
from __future__ import annotations

import gc
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from app.pipeline.context import RuleSpec

CHANNELS_FILE = Path(__file__).resolve().parent.parent / "seed" / "channels.json"
FONT_RULE_CODE = "R-FONT-AUTO"


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))


def default_target_score() -> int:
    return int(catalog().get("default_target_score", 80))


def checks_for(channel: str) -> dict[str, dict[str, Any]]:
    return {code: spec for code, spec in catalog()["checks"].items() if spec["channel"] == channel}


def defaults_for(channel: str) -> dict[str, Any]:
    return {spec["limit_key"]: spec["default"] for spec in checks_for(channel).values()}


def citation(code: str) -> Optional[dict[str, Any]]:
    """CitationOut-shaped dict for a built-in channel rule (the finding card links the official page)."""
    spec = catalog()["checks"].get(code)
    if spec is None:
        return None
    src = catalog()["sources"][spec["source"]]
    return {"source_id": spec["source"], "document": src["document"], "short": src["short"], "clause": spec["clause"],
            "quote": spec["quote"], "url": src["url"], "retrieved": src["retrieved"],
            "status": "platform guidance" if spec["kind"] == "soft" else "platform limit"}


def _valid_limit(key: str, value: Any) -> bool:
    if key == "aspect_ratio":
        return (isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(x, (int, float)) for x in value)
                and 0 < float(value[0]) <= float(value[1]) <= 10)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 100_000


def effective_limits(channel: str, channel_targets: dict[str, Any]) -> dict[str, Any]:
    """Platform defaults overlaid with the brand's overrides (invalid overrides are ignored)."""
    out = defaults_for(channel)
    over = ((channel_targets or {}).get(channel) or {}).get("limits") or {}
    for k, v in over.items():
        if k in out and _valid_limit(k, v):
            out[k] = list(v) if k == "aspect_ratio" else v
    return out


def target_score(channel: str, channel_targets: dict[str, Any]) -> int:
    t = ((channel_targets or {}).get(channel) or {}).get("target_score")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= 100:
        return int(t)
    return default_target_score()


@dataclass
class ChannelIssue:
    rule_code: str
    severity: str
    category: str
    detail: str  # plain words: the measured value, the limit and where it comes from
    quote: str = ""
    unit_idx: Optional[int] = None  # None = the whole post / file
    local_start: Optional[int] = None
    local_end: Optional[int] = None
    band: str = "flag"
    statement: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _fmt(n: float) -> str:
    return f"{int(n):,}" if float(n).is_integer() else f"{n:g}"


def _source_note(code: str, limit: Any, default: Any) -> str:
    spec = catalog()["checks"][code]
    src = catalog()["sources"][spec["source"]]["short"]
    if limit != default:
        shown = "–".join(_fmt(x) for x in default) if isinstance(default, list) else _fmt(default)
        return f"Limit set by your brand (platform default {shown}, {src})."
    return f"Source: {src}, read {catalog()['sources'][spec['source']]['retrieved']}."


def _issue(code: str, detail: str, limit: Any, channel: str, **kw: Any) -> ChannelIssue:
    spec = catalog()["checks"][code]
    default = spec["default"]
    return ChannelIssue(rule_code=code, severity=spec["severity"], category="channel",
                        detail=f"{detail} {_source_note(code, limit, default)}", statement=spec["statement"], **kw)


HASHTAG = re.compile(r"(?<![\w#&])#[^\s#.,!?;:()\[\]{}\"'<>]+", re.UNICODE)
MENTION = re.compile(r"(?<![\w@.+-])@[A-Za-z0-9._]{1,30}")  # not the @ of an email address
_SUBJECT = re.compile(r"^\s*subject\s*:\s*(.*)$", re.IGNORECASE)


def count_hashtags(text: str) -> int:
    return sum(1 for m in HASHTAG.finditer(text) if not m.group(0)[1:].isdigit())


def count_mentions(text: str) -> int:
    return sum(1 for _ in MENTION.finditer(text))


def _cut(text: str, limit: int) -> str:
    """The first words past the limit (what the platform would cut), marked as an excerpt."""
    return "…" + text[limit:limit + 60].strip() + ("…" if len(text) > limit + 60 else "")


def text_length(text: str) -> int:
    """Characters as people count them: code points, with Windows line breaks counted once."""
    return len((text or "").replace("\r\n", "\n"))


def email_subject(text: str) -> Optional[tuple[int, int, str]]:
    """The subject line of an email body that starts with "Subject: ..." -> (start, end, subject)."""
    first = (text or "").split("\n", 1)[0]
    m = _SUBJECT.match(first)
    if not m:
        return None
    subj = m.group(1).strip()
    start = first.find(subj) if subj else len(first)
    return start, start + len(subj), subj


def check_text(channel: str, text: str, limits: dict[str, Any]) -> list[ChannelIssue]:
    """Post-level text limits. `text` is the whole post (Version.text: the caption for image posts)."""
    out: list[ChannelIssue] = []
    text = text or ""
    n = text_length(text)
    if channel == "instagram":
        lim = limits.get("caption_max_chars")
        if lim and n > lim:
            out.append(_issue("R-CH-IG-CAPTION", f"This caption is {_fmt(n)} characters; Instagram allows {_fmt(lim)}, "
                              f"so {_fmt(n - lim)} characters would be cut.", lim, channel,
                              quote=_cut(text, int(lim)), extra={"length": n, "limit": lim}))
        lim = limits.get("hashtags_max")
        tags = count_hashtags(text)
        if lim and tags > lim:
            out.append(_issue("R-CH-IG-HASHTAGS", f"This caption has {tags} hashtags; the limit is {_fmt(lim)}.", lim,
                              channel, extra={"count": tags, "limit": lim}))
        lim = limits.get("mentions_max")
        ats = count_mentions(text)
        if lim and ats > lim:
            out.append(_issue("R-CH-IG-MENTIONS", f"This caption tags {ats} accounts; the limit is {_fmt(lim)}.", lim,
                              channel, extra={"count": ats, "limit": lim}))
    elif channel == "linkedin":
        lim = limits.get("post_max_chars")
        if lim and n > lim:
            out.append(_issue("R-CH-LI-LENGTH", f"This post is {_fmt(n)} characters; LinkedIn allows {_fmt(lim)}, "
                              f"so {_fmt(n - lim)} characters would be cut.", lim, channel,
                              quote=_cut(text, int(lim)), extra={"length": n, "limit": lim}))
    elif channel == "email":
        lim = limits.get("subject_max_chars")
        sub = email_subject(text)
        if lim and sub and text_length(sub[2]) > lim:
            out.append(_issue("R-CH-EMAIL-SUBJECT", f"The subject line is {text_length(sub[2])} characters; inboxes "
                              f"usually show about {_fmt(lim)}, so the end may be hidden.", lim, channel,
                              quote=sub[2], extra={"length": text_length(sub[2]), "limit": lim, "span": [sub[0], sub[1]]}))
    return out


def image_size(path: str) -> Optional[tuple[int, int]]:
    """Width and height from the header (no decode)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001
        return None


def _ratio_label(r: float) -> str:
    for label, val in (("4:5", 0.8), ("1:1", 1.0), ("1.91:1", 1.91), ("9:16", 9 / 16), ("16:9", 16 / 9), ("3:4", 0.75), ("2:3", 2 / 3)):
        if abs(r - val) < 0.005:
            return label
    return f"{r:.2f}:1"


def check_image(channel: str, size: tuple[int, int], limits: dict[str, Any], unit_idx: Optional[int] = None) -> list[ChannelIssue]:
    out: list[ChannelIssue] = []
    w, h = size
    if w <= 0 or h <= 0 or channel != "instagram":
        return out
    rng = limits.get("aspect_ratio")
    if rng:
        r = w / h
        lo, hi = float(rng[0]), float(rng[1])
        # 3 decimal places: a 1080x1350 (4:5) or 1080x566 (1.908:1) image is inside the range
        if round(r, 3) < round(lo, 3) or round(r, 3) > round(hi, 3):
            shape = "too tall" if r < lo else "too wide"
            out.append(_issue("R-CH-IG-RATIO", f"This image is {w}×{h} px (aspect ratio {_ratio_label(r)}), {shape} for the "
                              f"Instagram feed, which accepts {_ratio_label(lo)} to {_ratio_label(hi)}; it would be cropped "
                              f"or refused.", rng, channel, unit_idx=unit_idx, extra={"width": w, "height": h}))
    lim = limits.get("image_min_width")
    if lim and w < lim:
        out.append(_issue("R-CH-IG-WIDTH", f"This image is {w} px wide; Instagram scales images narrower than {_fmt(lim)} px "
                          f"up, so it will look soft.", lim, channel, unit_idx=unit_idx, extra={"width": w, "height": h}))
    return out


# ------------------------------------------------------------------ PDF fonts
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_STYLE = re.compile(
    r"(?:[-_,\s]?(?:Regular|Roman|Book|Medium|SemiBold|Semibold|DemiBold|Demi|Bold|ExtraBold|Extrabold|Black|Heavy|Light|"
    r"ExtraLight|Thin|Hairline|Italic|Oblique|It|Condensed|Narrow|Expanded|MT|PS|PSMT|Variable|VF|Std|Pro|Display))+$",
    re.IGNORECASE)


def normalise_font(name: str) -> str:
    """'ABCDEF+Inter-SemiBold' -> 'Inter'; 'ArialMT' -> 'Arial'; 'OpenSans-BoldItalic' -> 'OpenSans'."""
    n = _SUBSET.sub("", (name or "").strip())
    n = n.split(",")[0]
    if "-" in n:
        n = n.split("-")[0]
    prev = None
    while prev != n:
        prev = n
        n = _STYLE.sub("", n)
    return n.strip() or (name or "").strip()


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def font_allowed(family: str, brand_fonts: Iterable[str]) -> bool:
    k = _key(family)
    for b in brand_fonts:
        bk = _key(normalise_font(b)) or _key(b)
        if bk and (k == bk or k.startswith(bk)):
            return True
    return False


def pdf_fonts(path: str, max_pages: int = 20) -> dict[str, list[int]]:
    """{normalised family: [pages]} for every font used for visible text. Memory: one page at a time; only
    the fontname of each char is read and the page's objects are dropped before the next page."""
    import pdfplumber

    found: dict[str, list[int]] = {}
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages[:max_pages], start=1):
            try:
                names = {c.get("fontname", "") for c in page.chars if str(c.get("text", "")).strip()}
            finally:
                page.close()
            for raw in names:
                fam = normalise_font(raw)
                if fam:
                    pages = found.setdefault(fam, [])
                    if pno not in pages:
                        pages.append(pno)
    gc.collect()
    return found


def _pages(ps: list[int]) -> str:
    return ("page " if len(ps) == 1 else "pages ") + ", ".join(str(p) for p in ps)


def check_pdf_fonts(path: str, brand_fonts: list[str], rule: Optional[RuleSpec]) -> list[ChannelIssue]:
    """One finding listing every off-brand font with its pages (nothing when the brand lists no fonts)."""
    if not brand_fonts or not path:
        return []
    try:
        used = pdf_fonts(path)
    except Exception:  # noqa: BLE001  (an unreadable PDF was already rejected at upload; never fail a check here)
        return []
    off = {fam: ps for fam, ps in used.items() if not font_allowed(fam, brand_fonts)}
    if not off:
        return []
    listed = "; ".join(f"{fam} ({_pages(ps)})" for fam, ps in sorted(off.items()))
    detail = (f"This PDF uses fonts that are not in the brand kit: {listed}. Brand fonts: {', '.join(brand_fonts)}. "
              f"Read from the PDF's embedded font names.")
    code = rule.code if rule else FONT_RULE_CODE
    severity = rule.severity if rule else "minor"
    return [ChannelIssue(rule_code=code, severity=severity, category="visual", detail=detail,
                         quote=", ".join(sorted(off)), statement="" if rule else
                         f"Use only the brand's fonts ({', '.join(brand_fonts)}) in PDFs.",
                         extra={"fonts": off})]


def font_rule(rules: list[RuleSpec]) -> Optional[RuleSpec]:
    return next((r for r in rules if "fonts_file" in (r.params or {})), None)
