"""Original emblem logos for the fictional 'Saffron & Steam Coffee Co.' (SVG -> PNG via rsvg-convert).
true_svg(): the approved logo. wrong_svg(): a convincing look-alike (redrawn cup, other font, other proportions, shifted colour)."""
import math, os, subprocess
from PIL import ImageFont

FONTS = {"Fraunces700": "fraunces-latin-700-normal.ttf", "Inter600": "inter-latin-600-normal.ttf"}


def arc_text(text, cx, cy, r, size, spacing, family, weight, fill, top=True, fontfile=None):
    """Place each glyph along a circle (librsvg has no textPath support)."""
    f = ImageFont.truetype(os.path.expanduser("~/.fonts/" + fontfile), size)
    adv = [f.getlength(ch) + spacing for ch in text]
    total = sum(adv)
    ang = total / r  # radians spanned
    out = []
    a = (-math.pi / 2 - ang / 2) if top else (math.pi / 2 + ang / 2)
    for ch, w in zip(text, adv):
        mid = a + (w / 2 / r if top else -w / 2 / r)
        x, y = cx + r * math.cos(mid), cy + r * math.sin(mid)
        rot = math.degrees(mid) + (90 if top else -90)
        esc = ch.replace("&", "&amp;")
        out.append(f'<text transform="translate({x:.1f} {y:.1f}) rotate({rot:.1f})" text-anchor="middle" font-family="{family}" font-weight="{weight}" font-size="{size}" fill="{fill}">{esc}</text>')
        a += (w / r if top else -w / r)
    return "".join(out)


BROWN, SAFFRON, CREAM = "#3B2418", "#F28C28", "#FFF4E0"


def beans(cx, cy, r, n, rx, ry, fill, stroke):
    out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        deg = math.degrees(a)
        out.append(f'<g transform="translate({x:.1f} {y:.1f}) rotate({deg + 35:.1f})"><ellipse rx="{rx}" ry="{ry}" fill="{fill}"/>'
                   f'<path d="M{-rx} 0 Q0 {-ry*0.9} {rx} 0" fill="none" stroke="{stroke}" stroke-width="1.6"/></g>')
    return "".join(out)


def ticks(cx, cy, r1, r2, n, color, w):
    out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        out.append(f'<line x1="{cx + r1*math.cos(a):.1f}" y1="{cy + r1*math.sin(a):.1f}" x2="{cx + r2*math.cos(a):.1f}" y2="{cy + r2*math.sin(a):.1f}" stroke="{color}" stroke-width="{w}"/>')
    return "".join(out)


def true_svg() -> str:
    c = 400
    steam = ""
    for dx, ph in ((-46, 0), (0, 1), (46, 0)):
        x = c + dx
        d = f"M{x} 410 C{x-26} 380 {x+26} 350 {x} 320 C{x-26} 290 {x+26} 262 {x} 232"
        if ph:
            d = f"M{x} 410 C{x+28} 380 {x-28} 350 {x} 320 C{x+28} 290 {x-28} 262 {x} 214"
        steam += f'<path d="{d}" fill="none" stroke="{SAFFRON}" stroke-width="11" stroke-linecap="round"/>'
    # saffron threads crossing steam
    threads = "".join(f'<path d="M{c-70+i*35} {250+ (i%2)*18} q8 -24 20 -30" stroke="{SAFFRON}" stroke-width="3" fill="none" stroke-linecap="round"/>' for i in range(5))
    stripes = "".join(f'<line x1="{c-118+ (k*4)}" y1="{448+k*22}" x2="{c+118-(k*4)}" y2="{448+k*22}" stroke="{CREAM}" stroke-width="3" opacity=".75"/>' for k in range(1, 4))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
<defs>
 <path id="top" d="M {c-338} {c} A 338 338 0 0 1 {c+338} {c}"/>
 <path id="bot" d="M {c-352} {c} A 352 352 0 0 0 {c+352} {c}"/>
</defs>
<rect width="800" height="800" fill="{CREAM}"/>
<circle cx="{c}" cy="{c}" r="388" fill="{SAFFRON}"/>
<circle cx="{c}" cy="{c}" r="376" fill="none" stroke="{CREAM}" stroke-width="3" stroke-dasharray="2 9"/>
<circle cx="{c}" cy="{c}" r="300" fill="{BROWN}"/>
<circle cx="{c}" cy="{c}" r="292" fill="{CREAM}"/>
<circle cx="{c}" cy="{c}" r="284" fill="none" stroke="{BROWN}" stroke-width="2"/>
{ticks(c, c, 262, 280, 72, BROWN, 2)}
{beans(c, c, 246, 28, 11, 6.5, BROWN, CREAM)}
{arc_text("SAFFRON & STEAM COFFEE CO.", c, c, 346, 40, 5, "Fraunces", 700, BROWN, True, "fraunces-latin-700-normal.ttf")}
{arc_text("SMALL-BATCH ROASTERS · PUNE · EST. 2019", c, c, 346, 27, 5, "Inter", 600, BROWN, False, "inter-latin-600-normal.ttf")}
<circle cx="{c-318}" cy="{c+8}" r="9" fill="{CREAM}"/><circle cx="{c+318}" cy="{c+8}" r="9" fill="{CREAM}"/>
<g transform="translate(0 -40) scale(1 1)">{steam}{threads}
<!-- saucer -->
<ellipse cx="{c}" cy="560" rx="170" ry="20" fill="{SAFFRON}"/><ellipse cx="{c}" cy="554" rx="150" ry="13" fill="{BROWN}"/>
<!-- cup -->
<path d="M{c-124} 430 L{c+124} 430 C{c+124} 520 {c+84} 556 {c} 556 C{c-84} 556 {c-124} 520 {c-124} 430 Z" fill="{BROWN}"/>
<path d="M{c+120} 452 C{c+196} 440 {c+200} 528 {c+104} 520" fill="none" stroke="{BROWN}" stroke-width="20" stroke-linecap="round"/>
<ellipse cx="{c}" cy="430" rx="124" ry="22" fill="{BROWN}"/><ellipse cx="{c}" cy="430" rx="108" ry="15" fill="#6B3F26"/>
{stripes}
<path d="M{c-24} 496 q24 -22 48 0 q-24 22 -48 0z" fill="{SAFFRON}"/>
</g>
<text x="{c}" y="574" text-anchor="middle" font-family="Fraunces" font-weight="600" font-style="italic" font-size="42" fill="{BROWN}">Saffron &amp; Steam</text>
<line x1="{c-120}" y1="594" x2="{c-20}" y2="594" stroke="{SAFFRON}" stroke-width="3"/><line x1="{c+20}" y1="594" x2="{c+120}" y2="594" stroke="{SAFFRON}" stroke-width="3"/>
<rect x="{c-6}" y="588" width="12" height="12" transform="rotate(45 {c} 594)" fill="{SAFFRON}"/>
</svg>'''


def wrong_svg() -> str:
    c = 400
    rays = ticks(c, c, 120, 280, 36, "#9C6B3E", 4)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
<defs><path id="top" d="M {c-326} {c} A 326 326 0 0 1 {c+326} {c}"/><path id="bot" d="M {c-344} {c} A 344 344 0 0 0 {c+344} {c}"/></defs>
<rect width="800" height="800" fill="#FFF1DA"/>
<circle cx="{c}" cy="{c}" r="384" fill="#EE8A2E"/>
<circle cx="{c}" cy="{c}" r="318" fill="#FFF1DA"/>
<circle cx="{c}" cy="{c}" r="310" fill="none" stroke="#4A2C1A" stroke-width="6"/>
{rays}
<circle cx="{c}" cy="{c}" r="150" fill="#FFF1DA"/>
<text font-family="DejaVu Serif" font-weight="700" font-size="40" letter-spacing="3" fill="#4A2C1A"><textPath href="#top" startOffset="50%" text-anchor="middle">SAFFRON AND STEAM COFFEE COMPANY</textPath></text>
<text font-family="DejaVu Sans" font-weight="400" font-size="30" letter-spacing="3" fill="#4A2C1A" dy="26"><textPath href="#bot" startOffset="50%" text-anchor="middle">FRESHLY ROASTED · INDIA · SINCE 2018</textPath></text>
<!-- redrawn mug: straight sides, handle on the left, no saucer -->
<path d="M{c-70} 328 q-90 0 -90 62 q0 62 90 56" fill="none" stroke="#4A2C1A" stroke-width="22" stroke-linecap="round"/>
<rect x="{c-86}" y="296" width="172" height="172" rx="26" fill="#4A2C1A"/>
<rect x="{c-86}" y="296" width="172" height="34" rx="16" fill="#6B3F26"/>
<circle cx="{c}" cy="392" r="30" fill="#EE8A2E"/>
<path d="M{c-34} 262 q-22 -34 0 -60 q22 -26 0 -56" fill="none" stroke="#EE8A2E" stroke-width="16" stroke-linecap="round"/>
<path d="M{c+34} 262 q22 -34 0 -60 q-22 -26 0 -56" fill="none" stroke="#EE8A2E" stroke-width="16" stroke-linecap="round"/>
<text x="{c}" y="548" text-anchor="middle" font-family="DejaVu Serif" font-weight="700" font-size="50" fill="#4A2C1A">Saffron+Steam</text>
<text x="{c}" y="590" text-anchor="middle" font-family="DejaVu Sans" font-size="26" letter-spacing="9" fill="#4A2C1A">COFFEE</text>
</svg>'''


def render(svg: str, out: str, size: int = 800):
    p = subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", out], input=svg.encode(), capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode())


if __name__ == "__main__":
    import sys
    d = sys.argv[1]
    render(true_svg(), f"{d}/logo.png")
    render(wrong_svg(), f"{d}/logo_alt_wrong.png")
    open(f"{d}/build/logo.svg", "w").write(true_svg())
