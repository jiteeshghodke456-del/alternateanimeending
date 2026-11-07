"""Generate the JSON/CSV seed files, content items, promo.pdf and the eval dataset. Usage: python build_seed.py SEED_DIR EVAL_DIR"""
import csv, json, os, sys
from playwright.sync_api import sync_playwright
from rules_data import RULES
from eval_items import E


def w(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False); f.write("\n")


FACTS = [
    ("F-01", "Beans come from 14 partner estates in Karnataka and Kerala", "14 estates", "Sourcing report FY26", "2027-03-31"),
    ("F-02", "We roast in small batches of 30 kg", "30 kg", "Roastery log 2026", "2027-12-31"),
    ("F-03", "We roast fresh in Pune every Tuesday and Friday", "Tuesday and Friday", "Roastery schedule 2026", "2027-06-30"),
    ("F-04", "We run 12 cafes across Pune, Mumbai and Bengaluru", "12 cafes", "Operations summary Oct 2026", "2027-03-31"),
    ("F-05", "Saffron & Steam was founded in Pune in 2019", "2019", "Company records", "2027-12-31"),
    ("F-06", "Rated 4.6 on Google from 3,200 reviews", "4.6 / 3,200 reviews", "Google Business profile export, Oct 2026", "2027-01-31"),
    ("F-07", "Saffron Signature Roast won Silver at the Western Ghats Roasters Showcase 2026", "Silver, 2026", "Showcase results letter", "2027-12-31"),
    ("F-08", "Saffron Signature Roast 250g costs Rs 495 including taxes", "Rs 495", "Price list Oct 2026", "2027-03-31"),
    ("F-09", "Monsoon Malabar 250g costs Rs 545 including taxes", "Rs 545", "Price list Oct 2026", "2027-03-31"),
    ("F-10", "Chikmagalur Estate Reserve 250g costs Rs 695 including taxes", "Rs 695", "Price list Oct 2026", "2027-03-31"),
    ("F-11", "Diwali offer: flat 15% off gift boxes with code DIWALI15", "15% off, DIWALI15", "Campaign brief Diwali 2026", "2026-11-15"),
    ("F-12", "Monsoon offer: flat 20% off online orders with code MONSOON20", "20% off, MONSOON20", "Campaign brief Monsoon 2026", "2026-09-30"),
    ("F-13", "Customers rate us 4.8 stars on our ordering app", "4.8 stars", "App store export, Q2 2026", "2026-06-30"),
    ("F-14", "Free shipping on online orders above Rs 499", "Rs 499 threshold", "Shipping policy v2", "2026-08-31"),
    ("F-15", "Our coffee pouches are mono-material and recyclable", "recyclable pouch", "Packaging spec sheet", "2027-06-30"),
    ("F-16", "We pay partner estates 18% above the local auction average", "18% premium", "Sourcing report FY26", "2027-03-31"),
    ("F-17", "Free shipping on online orders above Rs 799", "Rs 799 threshold", "Shipping policy v3", "2027-03-31"),
    ("F-18", "Our cafes are open from 8 am to 10 pm every day", "8 am to 10 pm", "Operations summary Oct 2026", "2027-03-31"),
    ("F-19", "Every barista completes 40 hours of training before serving guests", "40 hours", "Training handbook 2026", "2027-12-31"),
    ("F-20", "Steam Club members earn 1 point for every Rs 10 spent", "1 point per Rs 10", "Loyalty programme terms", "2027-03-31"),
    ("F-21", "Cold Steam Brew is steeped for 18 hours", "18 hours", "Recipe card CSB-2", "2027-12-31"),
    ("F-22", "Chikmagalur Estate Reserve comes from Kaveri Hills Estate", "Kaveri Hills Estate", "Sourcing report FY26", "2027-03-31"),
    ("F-23", "The roast date is printed on every bag", "roast date on bag", "Packaging spec sheet", "2027-12-31"),
    ("F-24", "We have over 50,000 followers on Instagram", "50,000+", "Instagram insights export, Oct 2026", "2027-01-31"),
    ("F-25", "Our coffee is not certified organic or Fairtrade", "no certification", "Compliance register 2026", "2027-12-31"),
    ("F-26", "The Diwali gift box costs Rs 1,299 including taxes", "Rs 1,299", "Campaign brief Diwali 2026", "2026-11-30"),
]


def main(seed, evald):
    w(f"{seed}/brand.json", {"name": "Saffron & Steam Coffee Co. (fictional demo brand)",
        "tone_labels": ["warm", "confident", "playful", "formal", "pushy", "sarcastic"],
        "channel_targets": {"instagram": {"max_grade": 8}, "email": {"max_grade": 10}, "website": {"max_grade": 10}, "linkedin": {"max_grade": 12}}})
    w(f"{seed}/users.json", [{"id": 1, "name": "Asha", "role": "admin"}, {"id": 2, "name": "Rohan", "role": "creator"},
                              {"id": 3, "name": "Meera", "role": "reviewer"}, {"id": 4, "name": "Kabir", "role": "approver"}])
    w(f"{seed}/rules.json", RULES)
    T = lambda k, v, r=None: {"kind": k, "value": v, "replacement": r}
    w(f"{seed}/terms.json", [T("banned", x) for x in ["cheap", "guaranteed", "miracle", "unbeatable", "bargain", "detox", "world's best", "No. 1"]]
      + [T("preferred", "Steam Classic", "Saffron Signature Roast"), T("preferred", "S&S", "Saffron & Steam"), T("preferred", "staff", "baristas"), T("preferred", "outlet", "cafe")]
      + [T("capitalise", x) for x in ["Saffron & Steam Coffee Co.", "Saffron Signature Roast", "Monsoon Malabar", "Chikmagalur Estate Reserve", "Cold Steam Brew"]])
    w(f"{seed}/palette.json", [{"name": "Saffron Orange", "hex": "#F28C28"}, {"name": "Deep Coffee Brown", "hex": "#3B2418"}, {"name": "Cream", "hex": "#FFF4E0"},
                               {"name": "Steam Grey", "hex": "#B9B4AD"}, {"name": "Cardamom Green", "hex": "#4F6B3A"}, {"name": "Roast Terracotta", "hex": "#B5482A"}])
    w(f"{seed}/fonts.json", ["Fraunces", "Inter"])
    with open(f"{seed}/facts.csv", "w", newline="", encoding="utf-8") as f:
        cw = csv.writer(f); cw.writerow(["code", "statement", "value", "source", "valid_until"]); cw.writerows(FACTS)

    # promo.pdf (one page, a couple of deliberate violations: old name, no end date, health claim, off-brand fonts)
    import base64
    logo = base64.b64encode(open(f"{seed}/logo.png", "rb").read()).decode()
    promo_text = ["MONSOON MANIA SALE", "Flat 20% off our Steam Classic, the healthy coffee that is good for your heart.",
                  "Pick up a bag at any Saffron & Steam cafe. Fictional demo promo."]
    html = f"""<html><body style="margin:0;width:210mm;height:297mm;background:#FFF4E0;font-family:'DejaVu Sans';color:#3B2418;padding:25mm;box-sizing:border-box">
<img src="data:image/png;base64,{logo}" style="width:60mm"/><h1 style="font-family:'DejaVu Serif';font-size:44pt;margin:14mm 0 6mm">{promo_text[0]}</h1>
<p style="font-size:20pt;line-height:1.4">{promo_text[1]}</p><p style="font-size:14pt">{promo_text[2]}</p></body></html>"""
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); pg.set_content(html)
        pg.pdf(path=f"{seed}/content/promo.pdf", width="210mm", height="297mm", print_background=True); b.close()

    flawed = ("Steam Classic is India's #1 coffee, hands down!!!\n\nRated 4.8 stars by thousands of happy customers, and the best filter blend you will ever taste. "
              "Cheap? No way, it is a guaranteed miracle in a cup.\n\nFlat 20% off with code MONSOON20. Only today, hurry, last chance to grab it before stock runs out!!!\n\n"
              "Order now. You would be silly to skip this.")
    items = [
        {"title": "Monsoon caption (demo_flawed_post)", "key": "demo_flawed_post", "channel": "instagram", "creator": "Rohan", "state": "draft", "confidential": False, "text": flawed, "file": None},
        {"title": "Diwali gift box email", "channel": "email", "creator": "Rohan", "state": "in_review", "confidential": False,
         "text": "Subject: Your Diwali gift box\n\nHello,\n\nOur Diwali gift box is here, Rs 1,299 incl. taxes. Flat 15% off with code DIWALI15, valid till 15 November 2026. Order online from our website.", "file": None},
        {"title": "How we source: LinkedIn post", "channel": "linkedin", "creator": "Rohan", "state": "changes_requested", "confidential": False,
         "text": "We are proud to be a sustainable roaster. Our beans come from 14 partner estates in Karnataka and Kerala, and we pay them 18% above the local auction average. Great coffee starts with fair partnerships.", "file": None},
        {"title": "Cold Steam Brew caption", "channel": "instagram", "creator": "Kabir", "state": "in_review", "confidential": False,
         "text": "Cold Steam Brew is steeped for 18 hours, then served cold. Visit your nearest cafe to try a glass.", "file": None},
        {"title": "Slow mornings ad (good)", "channel": "instagram", "creator": "Rohan", "state": "approved", "confidential": False,
         "text": "Slow mornings, steady brews. Freshly roasted in Pune. Find your nearest cafe.", "file": "ad_good.png"},
        {"title": "Fresh roast ad (neon)", "channel": "instagram", "creator": "Rohan", "state": "draft", "confidential": False,
         "text": "Fresh roast is here. Order today at our nearest cafe.", "file": "ad_offpalette.png"},
        {"title": "Slow mornings ad (logo check)", "channel": "instagram", "creator": "Rohan", "state": "in_review", "confidential": False,
         "text": "Slow mornings, steady brews. Freshly roasted in Pune. Find your nearest cafe.", "file": "ad_wronglogo.png"},
        {"title": "Monsoon promo flyer (PDF)", "channel": "website", "creator": "Rohan", "state": "changes_requested", "confidential": False,
         "text": " ".join(promo_text), "file": "promo.pdf"},
    ]
    w(f"{seed}/content/items.json", items)

    with open(f"{evald}/dataset.jsonl", "w", encoding="utf-8") as f:
        for i, (sp, ch, t, labels) in enumerate(E, 1):
            f.write(json.dumps({"id": f"E-{i:03d}", "split": sp, "channel": ch, "text": t,
                                "labels": [{"code": c, "span": s} for c, s in labels], "labelled_by": "draft-ai"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
