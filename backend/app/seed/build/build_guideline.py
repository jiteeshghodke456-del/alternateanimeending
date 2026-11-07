"""Render guideline.pdf (10 pages) from HTML with Playwright. Usage: python build_guideline.py SEED_DIR"""
import base64, json, os, sys
from playwright.sync_api import sync_playwright
from rules_data import RULES

QUOTE = {r["code"]: r["source_quote"] for r in RULES}
PAL = [("Saffron Orange", "#F28C28", "Accents, buttons, highlights"), ("Deep Coffee Brown", "#3B2418", "Text and logo"),
       ("Cream", "#FFF4E0", "Backgrounds"), ("Steam Grey", "#B9B4AD", "Dividers, secondary shapes"),
       ("Cardamom Green", "#4F6B3A", "Labels, sustainability-neutral accents"), ("Roast Terracotta", "#B5482A", "Warm highlights, sale tags")]


def esc(s): return s.replace("&", "&amp;")
def rule(code, why): return f'<div class="rule"><div class="rq">{esc(QUOTE[code])}</div><div class="rw">{why}</div></div>'


def page(n, title, kicker, body, cls=""):
    return f'''<section class="page {cls}"><header><span>Saffron &amp; Steam Coffee Co. Brand Guide</span><span>Fictional demo brand</span></header>
<div class="kick">{kicker}</div><h2>{title}</h2>{body}
<footer><span>Fictional demo brand created for the Brand Gate hackathon project</span><span>Page {n}</span></footer></section>'''


def build(seed):
    logo = base64.b64encode(open(os.path.join(seed, "logo.png"), "rb").read()).decode()
    pages = []
    pages.append(f'''<section class="page cover"><img src="data:image/png;base64,{logo}" class="coverlogo"/>
<div class="ck">Brand Guide, edition 2026</div><h1>Saffron &amp; Steam<br/>Coffee Co.</h1>
<p class="cs">How we sound, what we say, and how we look.</p><div class="badge">Fictional demo brand</div>
<p class="cf">This brand, its products, people and numbers are invented for the Brand Gate hackathon demo. Any resemblance to a real company is coincidental.</p></section>''')

    pages.append(page(2, "Who we are", "01 Introduction", f'''
<p class="lead">Saffron &amp; Steam Coffee Co. is an Indian specialty coffee brand. We began in Pune in 2019 with one small roaster and a simple idea: serve coffee that tastes of where it grew.</p>
<p>Today we work with partner estates in Karnataka and Kerala, roast in small batches, and serve guests in our cafes. Everything we publish should feel like a conversation at the counter: warm, unhurried and honest.</p>
<h3>Our three beliefs</h3>
<ul><li><b>Craft first.</b> Talk about the bean, the roast and the brew before the price.</li>
<li><b>Be honest.</b> Say only what we can prove, and prove it with an approved fact.</li>
<li><b>Be welcoming.</b> Everyone who walks in, or scrolls past, is a guest.</li></ul>
<h3>How to use this guide</h3>
<p>Every writer, designer and agency partner follows this guide. Before anything is published, it is checked against these pages and against our list of approved facts. Each rule below has a short statement you can quote back to a colleague.</p>
{rule("R-MSG-03", "This is the umbrella rule. When in doubt, ask what a warm, confident Saffron &amp; Steam barista would say or show.")}
<h3>Contents</h3>
<table class="toc"><tr><td>03</td><td>Voice and tone</td></tr><tr><td>04</td><td>Words we use and avoid</td></tr><tr><td>05</td><td>Claims and proof</td></tr><tr><td>06</td><td>Promotions and legal</td></tr><tr><td>07</td><td>Colour, type and imagery</td></tr><tr><td>08</td><td>Using the logo</td></tr><tr><td>09</td><td>Accessibility</td></tr><tr><td>10</td><td>Channel guidance</td></tr></table>'''))

    pages.append(page(3, "Voice and tone", "02 How we sound", f'''
<p class="lead">Our two tone words are <b>warm</b> and <b>confident</b>. We can be playful in small doses and formal in contracts, but we are never pushy and never sarcastic.</p>
{rule("R-TONE-01", "Warm means friendly and welcoming. Confident means we state what we know plainly, without hedging or boasting.")}
{rule("R-TONE-02", "No pressure tactics, no guilt, no shaming people who prefer another cup.")}
{rule("R-TONE-03", "Sarcasm reads as unfriendly, and it does not translate well across languages and cultures.")}
{rule("R-TONE-04", "Enthusiasm should show in word choice, not in punctuation. A few brand terms such as MRP, UPI and T&amp;C are fine in capitals.")}
{rule("R-TONE-05", "Humour should include everyone. Keep jokes gentle and about coffee, not about people.")}
{rule("R-MSG-04", "Premium means calm, careful and well made. Think quiet confidence, not loud sales.")}
<h3>Tone at a glance</h3>
<table class="grid"><tr><th>We are</th><th>We are not</th></tr>
<tr><td>Warm, welcoming, patient</td><td>Pushy, bossy, guilt-tripping</td></tr>
<tr><td>Confident, clear, specific</td><td>Boastful, vague, hedging</td></tr>
<tr><td>Gently playful</td><td>Sarcastic, mocking, edgy</td></tr></table>'''))

    pages.append(page(4, "Words we use and avoid", "03 Terminology", f'''
<p class="lead">Consistent words make us recognisable and keep us out of trouble. Use the product names exactly as written here.</p>
{rule("R-TERM-01", "Steam Classic was the name until early 2026. Any copy still using it is out of date.")}
{rule("R-TERM-02", "These words promise things we cannot back up, or they make good coffee sound disposable. Say fairly priced or well made instead.")}
{rule("R-TERM-03", "The ampersand is part of our name. Use it in full, with capital letters on both words.")}
<h3>Product names</h3>
<table class="grid"><tr><th>Product</th><th>What it is</th></tr>
<tr><td>Saffron Signature Roast</td><td>Our medium-roast house blend (formerly Steam Classic)</td></tr>
<tr><td>Monsoon Malabar</td><td>Monsooned arabica with a low-acid, heavy-bodied cup</td></tr>
<tr><td>Chikmagalur Estate Reserve</td><td>Single-estate lot, roasted lightly</td></tr>
<tr><td>Cold Steam Brew</td><td>Cold brew, steeped for 18 hours</td></tr></table>
<h3>Preferred phrasing</h3>
<ul><li>Say <b>baristas</b> or <b>our team</b>, not staff or workers.</li><li>Say <b>cafe</b>, not outlet or shop.</li><li>Say <b>roasted this week</b> only when the roast date backs it up.</li></ul>'''))

    pages.append(page(5, "Claims and proof", "04 Saying only what we can prove", f'''
<p class="lead">People trust us because we are specific and honest. Every claim in a post must be backed by something our team has approved and can show.</p>
{rule("R-CLAIM-01", "Words like best, number one and finest are claims. Without a source they are opinions, and we do not publish them.")}
{rule("R-CLAIM-02", "Approved facts live in the facts list. Each fact has a source and a date until which it stays valid. Once a fact expires, stop using it.")}
{rule("R-CLAIM-05", "We source carefully and pay fairly, but we do not hold third-party certifications. Describe what we actually do instead.")}
<h3>What counts as a claim</h3>
<ul><li>Numbers: cafe counts, ratings, review totals, farm prices, years.</li><li>Awards, press mentions and rankings.</li><li>Sourcing, roasting and sustainability statements.</li><li>Prices, discounts, offers and shipping terms.</li></ul>
<h3>If you cannot find a fact</h3>
<p>Ask the brand admin to add it, with a source. Do not guess a number, round it up or reuse last quarter's figure.</p>'''))

    pages.append(page(6, "Promotions and legal", "05 Offers, health and competitions", f'''
<p class="lead">Offers and competitions are covered by consumer law. These rules protect our guests and us.</p>
{rule("R-CLAIM-03", "Real deadlines are welcome. Pressure that has no real deadline behind it is not.")}
{rule("R-CLAIM-04", "Give the date in full, for example valid till 15 November 2026, so guests know exactly when it ends.")}
{rule("R-LEGAL-01", "Do not say that coffee cures, treats, detoxes or prevents anything, and do not describe it as healthy. This is a legal rule, not only a style rule.")}
{rule("R-LEGAL-02", "Place the line T&amp;C apply at the end of the post, and keep the full terms on our website.")}
{rule("R-LEGAL-03", "Write Rs 495 incl. taxes. If the price on a post differs from the approved list, the post must wait.")}
<h3>Before you publish a promotion</h3>
<ul><li>Is there a code, a price or a percentage? Check it against the approved facts.</li><li>Is the end date written out? Check the calendar.</li><li>Does it make a health claim, even indirectly? Remove it.</li></ul>'''))

    sw = "".join(f'<div class="sw"><div class="chip" style="background:{h}"></div><div class="sn">{n}</div><div class="sh">{h}</div><div class="su">{u}</div></div>' for n, h, u in PAL)
    pages.append(page(7, "Colour, type and imagery", "06 Visual identity", f'''
<p class="lead">Our look is warm paper, dark coffee and a flash of saffron. The palette is small on purpose.</p>
{rule("R-VIS-02", "Check every colour in an ad or PDF against the swatches below. Close matches are fine; neon and gradients are not.")}
<div class="swatches">{sw}</div>
{rule("R-VIS-04", "Fraunces gives us warmth in titles. Inter keeps small text easy to read.")}
<div class="spec"><div class="sf">Fraunces SemiBold: Slow mornings, steady brews.</div><div class="si">Inter Regular: Freshly roasted in Pune and brewed for you.</div></div>
{rule("R-VIS-05", "Use real people, real cafes and real beans wherever possible, and avoid heavy filters.")}'''))

    pages.append(page(8, "Using the logo", "07 Our emblem", f'''
<div class="logorow"><img src="data:image/png;base64,{logo}"/><div>
<p class="lead">The emblem shows a steaming cup inside a saffron ring. It is the same on every surface.</p>
<p>Master file: <b>logo.png</b>. It is supplied by the brand admin and checked by Brand Gate against every image.</p></div></div>
{rule("R-VIS-01", "Look-alikes are easy to spot: wrong cup shape, wrong typeface, wrong orange. If it was not exported from the master file, do not use it.")}
{rule("R-VIS-03", "Leave a margin of at least ten percent of the logo width on every side, on a calm cream or flat saffron area.")}
<h3>Minimum size</h3>
<p>Never print the logo smaller than 18 mm wide, or show it smaller than 96 pixels wide on screen. Below that, use the wordmark Saffron &amp; Steam in Fraunces SemiBold Italic.</p>'''))

    pages.append(page(9, "Accessibility", "08 Everyone can read us", f'''
<p class="lead">Many of our guests use screen readers, large text or low-vision settings. Accessible content is good manners and good business.</p>
{rule("R-ACC-01", "Describe the scene in one plain sentence, for example: Barista pouring hot water over ground coffee. Do not start with image of.")}
{rule("R-ACC-03", "Deep coffee brown on cream passes comfortably. Saffron on white, and light grey on cream, do not.")}
<h3>More habits</h3>
<ul><li>Write hashtags in CamelCase, such as #SlowMorningsSteadyBrews.</li><li>Do not put essential information only in an image.</li><li>Add captions or transcripts to video.</li><li>Keep sentences short and words simple.</li></ul>'''))

    pages.append(page(10, "Channel guidance", "09 Where we publish", f'''
<p class="lead">Each channel has its own rhythm and its own reading level. Shorter, plainer writing travels furthest.</p>
{rule("R-ACC-02", "Reading level is measured with the Flesch-Kincaid grade. Plain language lets more people enjoy what we write.")}
<table class="grid"><tr><th>Channel</th><th>Max grade</th><th>Notes</th></tr>
<tr><td>Instagram</td><td>8</td><td>Up to 120 words. Warm, visual, one idea.</td></tr>
<tr><td>Email</td><td>10</td><td>Clear subject line. One main offer.</td></tr>
<tr><td>Website</td><td>10</td><td>Product facts first. Tell the story second.</td></tr>
<tr><td>LinkedIn</td><td>12</td><td>Behind the scenes, sourcing, team and growth.</td></tr></table>
{rule("R-MSG-02", "Tell readers exactly one next step, and make it easy to take.")}
{rule("R-MSG-05", "Lead with the coffee and the people. A reminder of an offer is fine; shouting about it is not.")}
<h3>Approval</h3>
<p>Creators draft, reviewers check against this guide and the facts, and an approver signs off. Nobody approves their own work.</p>'''))

    css = """
@page{size:A4;margin:0}*{box-sizing:border-box;font-variant-ligatures:none}
body{margin:0;font-family:'Inter';color:#3B2418;font-size:11.5pt;line-height:1.5}
.page{width:210mm;height:297mm;padding:22mm 20mm 20mm;background:#FFF4E0;position:relative;overflow:hidden;page-break-after:always}
header,footer{position:absolute;left:20mm;right:20mm;display:flex;justify-content:space-between;font-size:8.5pt;letter-spacing:.04em;color:#6B5545}
header{top:10mm;border-bottom:1px solid #B9B4AD;padding-bottom:3mm;text-transform:uppercase}footer{bottom:9mm;border-top:1px solid #B9B4AD;padding-top:3mm}
.kick{color:#8A4A06;font-weight:600;font-size:10pt;letter-spacing:.1em;text-transform:uppercase;margin-top:4mm}
h2{font-family:'Fraunces';font-weight:700;font-size:30pt;margin:2mm 0 5mm;line-height:1.1}h3{font-family:'Fraunces';font-weight:600;font-size:15pt;margin:6mm 0 2mm}
.lead{font-size:13pt;line-height:1.45}
.rule{border-left:5px solid #F28C28;background:#fff;padding:3mm 5mm;margin:4mm 0;border-radius:0 3mm 3mm 0}
.rq{font-family:'Fraunces';font-weight:600;font-size:13pt;line-height:1.3}.rw{font-size:10pt;color:#5A4333;margin-top:1.5mm}
ul{padding-left:5mm;margin:2mm 0}li{margin:1mm 0}
table{border-collapse:collapse;width:100%;font-size:10.5pt}.grid th,.grid td{border-bottom:1px solid #B9B4AD;padding:2mm 3mm;text-align:left;vertical-align:top}.grid th{background:#3B2418;color:#FFF4E0}
.toc td{padding:1mm 3mm;border-bottom:1px dotted #B9B4AD}.toc td:first-child{font-family:'Fraunces';font-weight:700;color:#8A4A06;width:14mm}
.swatches{display:grid;grid-template-columns:repeat(3,1fr);gap:4mm;margin:4mm 0}.chip{height:20mm;border-radius:3mm;border:1px solid #B9B4AD}.sn{font-weight:600;margin-top:1.5mm;font-size:10pt}.sh{font-size:9pt;color:#6B5545}.su{font-size:8.5pt;color:#6B5545}
.spec{background:#fff;padding:4mm;border-radius:3mm}.sf{font-family:'Fraunces';font-weight:600;font-size:18pt}.si{font-size:12pt;margin-top:2mm}
.logorow{display:flex;gap:8mm;align-items:center;margin-bottom:2mm}.logorow img{width:62mm;height:62mm}
.cover{background:#3B2418;color:#FFF4E0;display:flex;flex-direction:column;justify-content:center;padding:30mm 25mm}.coverlogo{width:100mm;height:100mm;border-radius:50%;margin-bottom:12mm}
.ck{letter-spacing:.2em;text-transform:uppercase;font-size:10pt;color:#F28C28}.cover h1{font-family:'Fraunces';font-weight:700;font-size:46pt;line-height:1.05;margin:3mm 0}
.cs{font-size:14pt}.badge{display:inline-block;align-self:flex-start;background:#F28C28;color:#3B2418;font-weight:600;padding:2mm 5mm;border-radius:10mm;margin:6mm 0}.cf{font-size:9.5pt;max-width:120mm;color:#E6D6BE}
"""
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>Saffron &amp; Steam Brand Guide (fictional)</title><style>{css}</style></head><body>{''.join(pages)}</body></html>"
    open(os.path.join(seed, "build", "guideline.html"), "w").write(html)
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); pg.set_content(html); pg.wait_for_timeout(500)
        pg.pdf(path=os.path.join(seed, "guideline.pdf"), prefer_css_page_size=True, print_background=True); b.close()


if __name__ == "__main__":
    build(sys.argv[1])
