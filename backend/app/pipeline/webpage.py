"""Web page -> checkable units + basic accessibility audit. Pure functions (no network, no DB).

`parse_page(html, base_url)` reads the HTML once with the stdlib parser (no lxml, no browser) and returns:
- the page's main text as blocks: headings and paragraphs, each with its heading context, after dropping
  scripts, styles, navigation, footers, asides, forms, cookie/consent banners and hidden elements
  (readability-style: inside <main>/<article> when the page has one);
- image candidates (largest content images first; icons, sprites, tracking pixels and SVGs skipped);
- accessibility issues found in code: images without useful alt text, links and buttons with no name,
  missing or skipped heading levels, missing <html lang>, vague link text ("click here"), very long paragraphs.

Everything here treats the page as untrusted data: nothing in it is executed or followed, and the text only
ever reaches a model inside the usual <content> wrapper (pipeline/explain.py, questions.text_state).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

MAX_BLOCKS = 40  # text units per page (one Jev call each); the rest is dropped with a note
LONG_PARAGRAPH_WORDS = 120
MIN_PARAGRAPH_WORDS = 5
MAX_IMAGE_CANDIDATES = 8  # fetched to find the 4 largest usable ones

_SKIP_CONTENT = {"script", "style", "noscript", "template", "iframe", "object", "embed", "canvas", "head",
                 "select", "textarea", "math"}
_BOILERPLATE = {"nav", "footer", "aside", "form", "dialog", "button", "menu"}
_BOILER_ROLES = {"navigation", "contentinfo", "search", "complementary", "dialog", "alertdialog", "menu", "menubar",
                 "banner", "button"}
_BOILER_CLASS = re.compile(
    r"cookie|consent|gdpr|onetrust|cc-window|cc-banner|truste|didomi|newsletter-?pop|popup|modal|"
    r"skip-?link|skip-?to|sr-only|visually-?hidden|screen-?reader|breadcrumb|share|social|"
    r"comment|related-?posts|advert|sponsor", re.IGNORECASE)
_BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "li", "ul", "ol", "dl", "dt", "dd",
          "table", "tr", "td", "th", "tbody", "thead", "caption", "blockquote", "figure", "figcaption", "pre",
          "address", "details", "summary", "h1", "h2", "h3", "h4", "h5", "h6", "form", "fieldset", "legend", "hr",
          "body", "html", "center", "picture", "video", "audio", "label", "dialog"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_AUTO_CLOSE_P = _BLOCK - {"label", "picture", "video", "audio"}

_IMG_SKIP = re.compile(r"(^|[/_.-])(icon|icons|sprite|sprites|pixel|tracking|tracker|spacer|blank|1x1|favicon|"
                       r"analytics|beacon|badge|emoji|avatar|gravatar|loader|spinner|placeholder)([/_.-]|$)|"
                       r"doubleclick|facebook\.com/tr|google-analytics|googletagmanager|/pixel\?|bat\.bing",
                       re.IGNORECASE)
_IMG_EXT_SKIP = re.compile(r"\.(svg|gif|ico|bmp|tiff?|avif|heic)(\?|#|$)", re.IGNORECASE)
_FILENAME_ALT = re.compile(r"^[\w\- ]+\.(jpe?g|png|gif|webp|svg|avif|bmp)$|^(img|image|dsc|dscn|pxl|screenshot)[_\- ]?\d+",
                           re.IGNORECASE)
GENERIC_ALT = {"image", "img", "photo", "picture", "pic", "graphic", "banner", "untitled", "placeholder", "alt",
               "thumbnail", "icon"}
VAGUE_LINK_TEXT = {"click here", "here", "click", "read more", "more", "learn more", "link", "this link",
                   "click this", "more info", "find out more", "go", "continue", "details", "this", "tap here"}
_WS = re.compile(r"\s+")


@dataclass
class Block:
    tag: str  # h1..h6 | p | li | ... (the innermost block element)
    text: str
    heading: str = ""  # nearest heading above this block (context), "" for none
    in_main: bool = False
    link_chars: int = 0

    @property
    def is_heading(self) -> bool:
        return self.tag in _HEADINGS


@dataclass
class ImageRef:
    url: str
    alt: Optional[str]  # None = no alt attribute at all
    width: Optional[int] = None  # declared in HTML (may be absent)
    height: Optional[int] = None
    in_main: bool = False
    boilerplate: bool = False  # inside nav/footer/aside
    decorative: bool = False  # role=presentation/none or aria-hidden
    og: bool = False  # og:image / twitter:image

    @property
    def declared_area(self) -> int:
        return (self.width or 0) * (self.height or 0)


@dataclass
class LinkRef:
    href: str
    text: str
    named: bool  # has an accessible name (text, aria-label, labelled-by, title, img alt, svg title)
    block_idx: Optional[int] = None  # raw block the link text sits in


@dataclass
class ParsedPage:
    title: str = ""
    lang: str = ""
    blocks: list[Block] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    links: list[LinkRef] = field(default_factory=list)
    unnamed_buttons: list[str] = field(default_factory=list)  # short descriptions ("<button class=…>")
    headings: list[tuple[int, str]] = field(default_factory=list)  # every heading in the document, in order
    has_main: bool = False


def clean(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def _int(v: Optional[str]) -> Optional[int]:
    m = re.match(r"\s*(\d{1,5})", v or "")
    return int(m.group(1)) if m else None


def best_srcset(srcset: str) -> Optional[str]:
    """Largest candidate of a srcset up to 1600w (bigger costs memory for nothing: we store <= 2048 px)."""
    best: tuple[float, str] | None = None
    for part in (srcset or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url, size = bits[0], (bits[1] if len(bits) > 1 else "1x")
        try:
            w = float(size[:-1]) * (1 if size.endswith("w") else 800)
        except ValueError:
            continue
        if w > 1600:
            w = 1600 / w  # still usable, but any candidate under the cap wins
        if best is None or w > best[0]:
            best = (w, url)
    return best[1] if best else None


class _Parser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.page = ParsedPage()
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, skips content, boilerplate)
        self.skip_depth = 0  # inside script/style/... : text ignored
        self.boiler_depth = 0  # inside nav/footer/cookie banner: not page content (still audited)
        self.main_depth = 0
        self.buf: list[str] = []
        self.buf_tag = "p"
        self.buf_link_chars = 0
        self.raw_blocks: list[Block] = []
        self.heading_ctx = ""
        self.in_title = False
        self.title_parts: list[str] = []
        self.links: list[dict] = []  # open <a> elements
        self.buttons: list[dict] = []  # open <button> elements
        self.svg_depth = 0
        self.svg_title = False

    # ------------------------------------------------------------ helpers
    def _block_tag(self) -> str:
        for tag, _, _ in reversed(self.stack):
            if tag in _BLOCK:
                return tag
        return "p"

    def flush(self) -> None:
        text = clean("".join(self.buf))
        if text and self.skip_depth == 0:
            self.raw_blocks.append(Block(tag=self.buf_tag, text=text, heading=self.heading_ctx,
                                         in_main=self.main_depth > 0, link_chars=self.buf_link_chars))
            if self.buf_tag in _HEADINGS:
                self.heading_ctx = text
        self.buf, self.buf_link_chars = [], 0

    def _text(self, data: str) -> None:
        for el in self.links + self.buttons:
            el["text"].append(data)
        if self.skip_depth or self.boiler_depth:
            return
        if not self.buf or not "".join(self.buf).strip():
            self.buf_tag = self._block_tag()
        self.buf.append(data)
        if self.links:
            self.buf_link_chars += len(data.strip())

    # ------------------------------------------------------------ parser callbacks
    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, Optional[str]]]) -> None:  # noqa: C901
        a = {k.lower(): (v if v is not None else "") for k, v in attrs_list}
        if tag == "html":
            self.page.lang = clean(a.get("lang", "") or a.get("xml:lang", ""))
            return
        if tag == "title" and self.svg_depth == 0:
            self.in_title = True
            return
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
            return
        if tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image") and a.get("content"):
                self._add_image(a["content"], None, a, og=True)
            return
        if self.svg_depth:
            if tag == "svg":
                self.svg_depth += 1
            elif tag == "title":
                self.svg_title = True
            return
        if tag == "svg":
            self.svg_depth = 1
            named = bool(clean(a.get("aria-label", "")) or a.get("aria-labelledby"))
            for el in self.links + self.buttons:
                el["svg_named"] = el.get("svg_named") or named
            return
        if tag in _BLOCK or tag == "br":
            if tag == "br":
                self.buf.append(" ")
            else:
                self.flush()
        if tag in _VOID:
            self._void(tag, a)
            return
        if tag == "p" or tag in _AUTO_CLOSE_P:
            # an open <p> is closed by any block element (HTML parsing rules), and <li> closes an open <li>
            if self.stack and self.stack[-1][0] == "p" and tag in _AUTO_CLOSE_P:
                self._pop_to("p")
            if tag == "li" and self.stack and self.stack[-1][0] == "li":
                self._pop_to("li")
        skips = tag in _SKIP_CONTENT
        role = (a.get("role") or "").lower()
        hidden = ("hidden" in a or (a.get("aria-hidden") or "").lower() == "true"
                  or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", a.get("style", "") or "", re.I) is not None)
        cls = f"{a.get('class', '')} {a.get('id', '')}"
        boiler = (tag in _BOILERPLATE or role in _BOILER_ROLES or hidden or bool(_BOILER_CLASS.search(cls))) and not skips
        if tag in ("main", "article") or role in ("main", "article"):
            self.page.has_main = True
            self.main_depth += 1
            self.stack.append((tag, skips, boiler))
            self.stack[-1] = (tag + "#main", skips, boiler)  # remember to decrement main_depth
        else:
            self.stack.append((tag, skips, boiler))
        if skips:
            self.skip_depth += 1
        if boiler:
            self.boiler_depth += 1
        if tag == "a" and "href" in a:
            self.links.append({"href": a.get("href", ""), "text": [], "named_attr": self._named_attr(a),
                               "block": len(self.raw_blocks), "svg_named": False, "img_named": False})
        elif tag == "button":
            self.buttons.append({"desc": self._desc(tag, a), "text": [], "named_attr": self._named_attr(a),
                                 "svg_named": False, "img_named": False})
        if tag in _HEADINGS:
            self.buf_tag = tag

    @staticmethod
    def _named_attr(a: dict) -> bool:
        return bool(clean(a.get("aria-label", "")) or clean(a.get("aria-labelledby", "")) or clean(a.get("title", "")))

    @staticmethod
    def _desc(tag: str, a: dict) -> str:
        bits = [tag]
        if a.get("id"):
            bits.append(f"id “{a['id'][:40]}”")
        elif a.get("class"):
            bits.append(f"class “{clean(a['class'])[:40]}”")
        return " ".join(bits)

    def _void(self, tag: str, a: dict) -> None:
        if tag == "img" or (tag == "input" and (a.get("type") or "").lower() == "image"):
            src = a.get("src", "")
            for key in ("data-src", "data-lazy-src", "data-original"):
                if a.get(key):
                    src = a[key]
            srcset = a.get("srcset") or a.get("data-srcset") or ""
            if srcset:
                src = best_srcset(srcset) or src
            alt = a.get("alt") if "alt" in a else None
            if alt is not None and alt.strip():
                for el in self.links + self.buttons:
                    el["img_named"] = True
            if tag == "img":
                self._add_image(src, alt, a)
        elif tag == "input":
            t = (a.get("type") or "text").lower()
            if t == "button" and not (clean(a.get("value", "")) or self._named_attr(a)):
                self.page.unnamed_buttons.append(self._desc("input type=button", a))
        elif tag == "source" and self.stack and self.stack[-1][0] == "picture" and a.get("srcset"):
            pass  # the <img> inside <picture> carries the alt; its own src/srcset is used

    def _add_image(self, src: str, alt: Optional[str], a: dict, og: bool = False) -> None:
        src = (src or "").strip()
        if not src or src.startswith("data:"):
            if not og and src.startswith("data:") and alt is None:
                # still counts for the alt audit
                self.page.images.append(ImageRef(url="", alt=None, in_main=self.main_depth > 0,
                                                 boilerplate=self.boiler_depth > 0,
                                                 decorative=self._decorative(a)))
            return
        url = urljoin(self.base, src)
        if urlsplit(url).scheme not in ("http", "https"):
            return
        self.page.images.append(ImageRef(url=url, alt=alt, width=_int(a.get("width")), height=_int(a.get("height")),
                                         in_main=self.main_depth > 0, boilerplate=self.boiler_depth > 0,
                                         decorative=self._decorative(a), og=og))

    @staticmethod
    def _decorative(a: dict) -> bool:
        return (a.get("role") or "").lower() in ("presentation", "none") or (a.get("aria-hidden") or "").lower() == "true"

    def _pop_to(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            t = self.stack[i][0]
            if t == tag or t == tag + "#main":
                for t2, skips, boiler in reversed(self.stack[i:]):
                    self._close(t2, skips, boiler)
                del self.stack[i:]
                return

    def _close(self, tag: str, skips: bool, boiler: bool) -> None:
        if skips:
            self.skip_depth -= 1
        if boiler:
            self.boiler_depth -= 1
        if tag.endswith("#main"):
            self.main_depth -= 1
            tag = tag[:-5]
        if tag in _BLOCK:
            self.flush()
        if tag in _HEADINGS:
            pass
        if tag == "a" and self.links:
            el = self.links.pop()
            text = clean("".join(el["text"]))
            named = bool(text) or el["named_attr"] or el["img_named"] or el["svg_named"]
            self.page.links.append(LinkRef(href=el["href"], text=text, named=named, block_idx=el["block"]))
        elif tag == "button" and self.buttons:
            el = self.buttons.pop()
            if not (clean("".join(el["text"])) or el["named_attr"] or el["img_named"] or el["svg_named"]):
                self.page.unnamed_buttons.append(el["desc"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.in_title:
            self.in_title = False
            self.page.title = clean("".join(self.title_parts))[:200]
            return
        if self.svg_depth:
            if tag == "svg":
                self.svg_depth -= 1
            elif tag == "title":
                self.svg_title = False
            return
        if tag in _VOID:
            return
        if any(t == tag or t == tag + "#main" for t, _, _ in self.stack):
            self._pop_to(tag)

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.svg_depth:
            if self.svg_title and data.strip():
                for el in self.links + self.buttons:
                    el["svg_named"] = True
            return
        self._text(data)

    def close(self) -> None:
        super().close()
        for t, skips, boiler in reversed(self.stack):
            self._close(t, skips, boiler)
        self.stack = []
        self.flush()


def _headings_from(blocks: list[Block]) -> list[tuple[int, str]]:
    return [(_HEADINGS[b.tag], b.text) for b in blocks if b.is_heading]


def parse_page(html: str, base_url: str) -> ParsedPage:
    p = _Parser(base_url)
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001  (html.parser is lenient; keep whatever was read)
        p.flush()
    page = p.page
    page.headings = _headings_from(p.raw_blocks)
    page.blocks = _content_blocks(p.raw_blocks, page.has_main)
    return page


def _content_blocks(raw: list[Block], has_main: bool) -> list[Block]:
    """Keep the main text: inside <main>/<article> when the page has one (plus the page's h1), headings,
    and paragraphs of at least MIN_PARAGRAPH_WORDS words that are not mostly links. Exact repeats dropped."""
    out: list[Block] = []
    seen: set[str] = set()
    for b in raw:
        if has_main and not b.in_main and b.tag != "h1":
            continue
        words = len(b.text.split())
        if b.is_heading:
            if len(b.text) > 200:
                continue
        elif words < MIN_PARAGRAPH_WORDS:
            continue
        elif b.link_chars > 0.7 * len(b.text) and words < 25:
            continue  # a list of links (menu, tags, "related") is not copy
        key = b.text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    # drop headings that head nothing (e.g. a lone "Menu" before the next heading), except the first h1
    kept: list[Block] = []
    for i, b in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        if b.is_heading and b.tag != "h1" and (nxt is None or (nxt.is_heading and _HEADINGS[nxt.tag] <= _HEADINGS[b.tag])):
            continue
        kept.append(b)
    return kept


# ------------------------------------------------------------------ images
def image_candidates(page: ParsedPage, limit: int = MAX_IMAGE_CANDIDATES) -> list[ImageRef]:
    """Images worth fetching: og:image first, then content images by declared size. Skips icons, sprites,
    tracking pixels, SVG/GIF, decorative and tiny (declared < 100 px) images, and nav/footer images."""
    out: list[ImageRef] = []
    seen: set[str] = set()

    def ok(im: ImageRef) -> bool:
        if not im.url or im.url in seen or _IMG_SKIP.search(urlsplit(im.url).path + "?" + urlsplit(im.url).query):
            return False
        if _IMG_EXT_SKIP.search(urlsplit(im.url).path):
            return False
        if (im.width is not None and im.width < 100) or (im.height is not None and im.height < 100):
            return False
        return not (im.decorative and not im.og)

    pool = [im for im in page.images if not im.og and not im.boilerplate]
    if page.has_main and any(im.in_main for im in pool):
        pool = [im for im in pool if im.in_main] + [im for im in pool if not im.in_main]
    order = sorted(range(len(pool)), key=lambda i: (-(pool[i].declared_area if pool[i].declared_area else 120_000), i))
    for im in [im for im in page.images if im.og] + [pool[i] for i in order]:
        if ok(im):
            seen.add(im.url)
            out.append(im)
        if len(out) >= limit:
            break
    return out


def alt_problem(alt: Optional[str]) -> Optional[str]:
    """Why this alt text doesn't help a screen-reader user, or None if it is fine. alt="" (decorative) is fine."""
    if alt is None:
        return "has no alt attribute"
    a = alt.strip()
    if not a:
        return None
    if _FILENAME_ALT.search(a):
        return f"has a file name as its alt text (“{a[:60]}”)"
    if a.lower().strip(" .") in GENERIC_ALT:
        return f"has generic alt text (“{a}”)"
    return None


# ------------------------------------------------------------------ units
@dataclass
class PageUnits:
    text: str  # Version.text: blocks joined by "\n\n"
    blocks: list[Block]
    offsets: list[tuple[int, int]]
    truncated: int = 0  # blocks dropped beyond MAX_BLOCKS / the character limit


def to_units(page: ParsedPage, max_chars: int, max_blocks: int = MAX_BLOCKS) -> PageUnits:
    parts: list[str] = []
    kept: list[Block] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    for b in page.blocks:
        extra = (2 if parts else 0) + len(b.text)
        if len(kept) >= max_blocks or pos + extra > max_chars:
            break
        if parts:
            pos += 2
        offsets.append((pos, pos + len(b.text)))
        parts.append(b.text)
        kept.append(b)
        pos += len(b.text)
    return PageUnits(text="\n\n".join(parts), blocks=kept, offsets=offsets, truncated=len(page.blocks) - len(kept))


# ------------------------------------------------------------------ accessibility audit
def _examples(items: list[str], n: int = 4) -> str:
    shown = [f"“{x}”" for x in items[:n]]
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def _short_url(u: str) -> str:
    path = urlsplit(u).path.rsplit("/", 1)[-1] or urlsplit(u).netloc
    return path[:60]


def audit(page: ParsedPage, units: PageUnits, chosen_urls: list[str]) -> list[dict[str, Any]]:
    """Accessibility issues as plain dicts (stored with the version; engine.py turns them into findings).

    Keys: type (alt|empty_link|empty_button|headings|lang|link_text|long_paragraph), detail (plain words),
    quote, count, unit_idx (text block index, or "img:<n>" for the n-th chosen image), local_start/local_end.
    """
    issues: list[dict[str, Any]] = []

    # 1. images without useful alt text (chosen images get their own finding on their image unit)
    loose: list[str] = []
    for im in page.images:
        if im.og or im.decorative or (im.width is not None and im.width <= 2) or (im.height is not None and im.height <= 2):
            continue  # og:image isn't shown; 1x1 tracking pixels are invisible
        why = alt_problem(im.alt)
        if why is None:
            continue
        name = _short_url(im.url) if im.url else "an inline image"
        if im.url and im.url in chosen_urls:
            issues.append({"type": "alt", "unit_ref": f"img:{chosen_urls.index(im.url)}", "count": 1, "quote": name,
                           "detail": f"This image ({name}) {why}. Screen-reader users hear nothing useful, or the file name."})
        else:
            short = "no alt" if im.alt is None else ("file name as alt" if _FILENAME_ALT.search(im.alt.strip()) else "generic alt")
            loose.append(f"{name}: {short}")
    if loose:
        issues.append({"type": "alt", "unit_ref": None, "count": len(loose), "quote": "",
                       "detail": f"{len(loose)} other image{'s' if len(loose) != 1 else ''} on the page "
                                 f"{'have' if len(loose) != 1 else 'has'} no useful alt text: {_examples(loose)}."})

    # 2. links and buttons with no accessible name
    empty_links = [ln.href for ln in page.links if not ln.named]
    if empty_links:
        issues.append({"type": "empty_link", "unit_ref": None, "count": len(empty_links), "quote": "",
                       "detail": f"{len(empty_links)} link{'s have' if len(empty_links) != 1 else ' has'} no text and no "
                                 f"label, so a screen reader announces only “link”: {_examples([h[:60] for h in empty_links])}."})
    if page.unnamed_buttons:
        n = len(page.unnamed_buttons)
        issues.append({"type": "empty_button", "unit_ref": None, "count": n, "quote": "",
                       "detail": f"{n} button{'s have' if n != 1 else ' has'} no text and no label, so a screen reader "
                                 f"announces only “button”: {_examples(page.unnamed_buttons)}."})

    # 3. heading structure
    levels = page.headings
    if not levels:
        issues.append({"type": "headings", "unit_ref": None, "count": 1, "quote": "",
                       "detail": "The page has no headings, so screen-reader users can't skim or jump between sections."})
    else:
        if not any(lv == 1 for lv, _ in levels):
            issues.append({"type": "headings", "unit_ref": None, "count": 1, "quote": "",
                           "detail": "The page has no main heading (h1), so its topic isn't announced first."})
        skips: list[str] = []
        prev = 0
        for lv, text in levels:
            if prev and lv > prev + 1:
                skips.append(f"h{prev} → h{lv} at “{text[:50]}”")
            prev = lv
        if skips:
            issues.append({"type": "headings", "unit_ref": None, "count": len(skips), "quote": "",
                           "detail": f"Heading levels are skipped ({'; '.join(skips[:3])}{' …' if len(skips) > 3 else ''}). "
                                     "Skipped levels make the outline look like content is missing."})

    # 4. page language
    if not page.lang:
        issues.append({"type": "lang", "unit_ref": None, "count": 1, "quote": "",
                       "detail": "The page doesn't declare its language (<html lang>), so screen readers may read it "
                                 "with the wrong voice and pronunciation."})

    # 5. vague link text: highlighted inside the paragraph when that paragraph is one of the units
    vague_loose: list[str] = []
    for ln in page.links:
        t = ln.text.lower().strip(" .:!»›→>")
        if t not in VAGUE_LINK_TEXT:
            continue
        hit = _find_in_units(units, ln.text)
        if hit is not None:
            ui, s, e = hit
            issues.append({"type": "link_text", "unit_ref": ui, "count": 1, "quote": ln.text, "local_start": s, "local_end": e,
                           "detail": f"The link text “{ln.text}” doesn't say where it goes. Out of context (screen readers "
                                     "list links on their own) it is meaningless."})
        else:
            vague_loose.append(ln.text)
    if vague_loose:
        issues.append({"type": "link_text", "unit_ref": None, "count": len(vague_loose), "quote": "",
                       "detail": f"{len(vague_loose)} link{'s use' if len(vague_loose) != 1 else ' uses'} text that doesn't say "
                                 f"where it goes: {_examples(vague_loose)}."})

    # 6. very long paragraphs
    for i, b in enumerate(units.blocks):
        n = len(b.text.split())
        if not b.is_heading and n > LONG_PARAGRAPH_WORDS:
            issues.append({"type": "long_paragraph", "unit_ref": i, "count": 1, "quote": "", "local_start": 0,
                           "local_end": len(b.text),
                           "detail": f"This paragraph is {n} words long (over {LONG_PARAGRAPH_WORDS}). Long blocks of text "
                                     "are hard to read, especially for people with dyslexia or on small screens."})
    return issues


def _find_in_units(units: PageUnits, text: str) -> Optional[tuple[int, int, int]]:
    needle = text.strip()
    if not needle:
        return None
    for i, b in enumerate(units.blocks):
        if b.is_heading:
            continue
        m = re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", b.text, re.IGNORECASE)
        if m:
            return i, m.start(), m.end()
    return None


# ------------------------------------------------------------------ issues -> findings (built-in rules)
# Built-in accessibility rules for web pages, like R-LOGO-AUTO for logos: used when the brand has no rule
# that covers the same check. Each cites the WCAG 2.2 success criterion it comes from.
WEB_RULES: dict[str, tuple[str, str, str, str]] = {
    # type: (code, severity, band, statement)
    "alt": ("R-WEB-ALT", "major", "flag",
            "Every meaningful image needs alt text that describes it (WCAG 2.2, 1.1.1 Non-text Content)."),
    "empty_link": ("R-WEB-LINK-NAME", "major", "flag",
                   "Every link needs text or a label that says where it goes (WCAG 2.2, 2.4.4 Link Purpose and 4.1.2 Name, Role, Value)."),
    "empty_button": ("R-WEB-BUTTON-NAME", "major", "flag",
                     "Every button needs text or a label that says what it does (WCAG 2.2, 4.1.2 Name, Role, Value)."),
    "headings": ("R-WEB-HEADINGS", "minor", "flag",
                 "Pages need one main heading and heading levels in order, without skipping (WCAG 2.2, 1.3.1 Info and Relationships)."),
    "lang": ("R-WEB-LANG", "minor", "flag",
             "The page must declare its language in <html lang> (WCAG 2.2, 3.1.1 Language of Page)."),
    "link_text": ("R-WEB-LINK-TEXT", "minor", "flag",
                  "Link text must make sense on its own; avoid “click here” and “read more” (WCAG 2.2, 2.4.4 Link Purpose)."),
    "long_paragraph": ("R-WEB-LONG-PARA", "minor", "human",
                       f"Keep web paragraphs short (at most {LONG_PARAGRAPH_WORDS} words) so they are easy to read on any screen."),
}


def finding_specs(issues: list[dict[str, Any]], brand_rules: list[Any]) -> list[dict[str, Any]]:
    """Turn stored issues into finding specs. Alt-text issues use the brand's own alt-text rule (a rule with
    params.requires_alt_text, e.g. R-ACC-01) when it has one; everything else uses the built-in R-WEB-* codes."""
    alt_rule = next((r for r in brand_rules if (r.params or {}).get("requires_alt_text")), None)
    out: list[dict[str, Any]] = []
    for it in issues:
        spec = WEB_RULES.get(str(it.get("type")))
        if spec is None:
            continue
        code, severity, band, statement = spec
        brand_rule = alt_rule if it["type"] == "alt" else None
        detail = str(it.get("detail") or "")
        out.append({
            "rule_code": brand_rule.code if brand_rule else code,
            "brand_rule": brand_rule,
            "severity": brand_rule.severity if brand_rule else severity,
            "band": band,
            "unit_idx": it.get("unit_idx"),
            "local_start": it.get("local_start"),
            "local_end": it.get("local_end"),
            "quote": str(it.get("quote") or ""),
            "detail": detail,
            "explanation": f"{detail} Built-in accessibility check {code}: {statement}" if brand_rule is None else "",
        })
    return out


def load_record(path: Optional[str]) -> dict[str, Any]:
    """The fetch record stored with a web version (Version.file_path, JSON). {} when missing or unreadable."""
    import json
    from pathlib import Path

    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}
