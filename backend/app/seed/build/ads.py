"""Compose three 1080x1080 image ads with Pillow. Usage: python -I ads.py SEED_DIR"""
import os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

F = os.path.expanduser("~/.fonts/")
def font(n, s): return ImageFont.truetype(F + n, s)
SERIF, SERIFB, SANS, SANSB = "fraunces-latin-600-normal.ttf", "fraunces-latin-700-normal.ttf", "inter-latin-400-normal.ttf", "inter-latin-600-normal.ttf"


def ad(seed, logo_file, out, bg, fg, accent, panel, headline, sub, cta, logo_px=380, shift=(0, 0)):
    im = Image.new("RGB", (1080, 1080), bg)
    d = ImageDraw.Draw(im)
    # decorative saffron arc + bean pattern (some texture, other than the logo)
    d.ellipse((620, -260, 1340, 460), fill=accent)
    for i in range(7):
        d.line((60, 60 + i * 14, 300, 60 + i * 14), fill=fg, width=3)
    d.text((60, 190), headline[0], font=font(SERIFB, 92), fill=fg)
    d.text((60, 296), headline[1], font=font(SERIFB, 92), fill=accent if bg != accent else fg)
    d.text((60, 430), sub, font=font(SANS, 34), fill=fg)
    btn = (60, 520, 520, 600)
    d.rounded_rectangle(btn, 40, fill=fg)
    d.text((90, 541), cta, font=font(SANSB, 32), fill=bg)
    # product: cup silhouette
    d.rounded_rectangle((740, 700, 940, 880), 34, fill=fg)
    d.arc((900, 730, 990, 830), -80, 80, fill=fg, width=16)
    d.ellipse((730, 880, 950, 905), fill=accent)
    logo = Image.open(os.path.join(seed, logo_file)).convert("RGB").resize((logo_px, logo_px), Image.LANCZOS)
    im.paste(logo, (60 + shift[0], 640 + shift[1] - 0))
    d.text((60, 1030), "Fictional demo brand. Illustrative ad for Brand Gate.", font=font(SANS, 20), fill=fg)
    im.save(os.path.join(seed, "content", out))


def main(seed):
    P = dict(good=("#FFF4E0", "#3B2418", "#F28C28", "#B9B4AD"), neon=("#39FF14", "#FF00E6", "#00E5FF", "#FFEE00"))
    h = ("Slow mornings,", "steady brews.")
    for out, lf, pal in (("ad_good.png", "logo.png", "good"), ("ad_offpalette.png", "logo.png", "neon"), ("ad_wronglogo.png", "logo_alt_wrong.png", "good")):
        bg, fg, ac, pn = P[pal]
        s = "Freshly roasted in Pune. Brewed for you." if pal == "good" else "FRESH ROAST!! GRAB IT NOW!!"
        c = "Find your nearest cafe" if pal == "good" else "ORDER TODAY"
        ad(seed, lf, out, bg, fg, ac, pn, h, s, c)


if __name__ == "__main__":
    main(sys.argv[1])
