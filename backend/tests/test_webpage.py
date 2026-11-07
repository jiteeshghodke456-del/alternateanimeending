"""Web page source: extraction, image selection, accessibility audit, SSRF guard, limits, robots.txt, API.

Network tests use a local HTTP server on 127.0.0.1. Loopback is blocked by design, so those tests allow
exactly 127.0.0.1 (and the server's port); every SSRF test runs against the real, unpatched checks.
"""
from __future__ import annotations

import asyncio
import io
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.pipeline import webfetch as F
from app.pipeline import webpage as P
from conftest import login

PAGE = """<!doctype html><html><head><title>Saffron Cafe – Fresh coffee</title>
<meta property="og:image" content="/img/og.png"></head>
<body><header><nav><a href="/">Home</a><a href="/menu">Menu</a><a href="/cart"><svg><path d="M0"/></svg></a></nav>
<h1>Fresh coffee, roasted in Pune</h1></header>
<div class="cookie-banner"><p>We use cookies to improve your experience. Accept all cookies to continue.</p></div>
<main><h2>Our beans</h2>
<p>We roast our beans every Tuesday and Friday so every bag you buy is fresh. <a href="/more">Click here</a> to see when.</p>
<img src="/img/hero.png" width="800" height="600">
<img src="/img/icon-cart.png" width="24" height="24" alt="">
<img src="/img/tiny.png" alt="IMG_1234.jpg">
<img src="https://tracker.example/pixel?id=1" width="1" height="1">
<h4>Skipped level</h4>
<p>The best coffee in India, guaranteed to make you happy every single morning of the year.</p>
<button><svg></svg></button><button aria-label="Close">x</button>
<ul><li>Single origin beans from the Coorg estates</li><li>Short</li></ul>
<script>var leak = "this script text must never become a unit";</script>
<p style="display:none">Hidden paragraph that should not be checked at all today.</p>
</main><footer><p>Copyright 2026 Saffron Cafe. All rights reserved worldwide forever.</p></footer></body></html>"""


def png(w: int, h: int, colour: tuple[int, int, int] = (200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ extraction (pure)
def test_extracts_main_text_with_heading_context() -> None:
    p = P.parse_page(PAGE, "https://cafe.example/page")
    assert p.title == "Saffron Cafe – Fresh coffee"
    texts = [b.text for b in p.blocks]
    assert texts[0] == "Fresh coffee, roasted in Pune"  # the page's h1 is kept even outside <main>
    assert any(t.startswith("We roast our beans") for t in texts)
    assert "Single origin beans from the Coorg estates" in texts
    joined = " ".join(texts)
    for gone in ("cookies", "leak", "Hidden paragraph", "Copyright", "Home", "Short"):
        assert gone not in joined, gone
    roast = next(b for b in p.blocks if b.text.startswith("We roast"))
    assert roast.heading == "Our beans" and roast.tag == "p"


def test_units_offsets_and_limits() -> None:
    p = P.parse_page(PAGE, "https://cafe.example/")
    u = P.to_units(p, max_chars=20_000)
    for b, (s, e) in zip(u.blocks, u.offsets):
        assert u.text[s:e] == b.text
    small = P.to_units(p, max_chars=35)
    assert len(small.blocks) == 1 and small.truncated == len(p.blocks) - 1
    many = "<html lang=en><body><main>" + "".join(f"<p>Paragraph number {i} has enough words in it.</p>" for i in range(60)) + "</main></body></html>"
    mu = P.to_units(P.parse_page(many, "https://x.example/"), 20_000)
    assert len(mu.blocks) == P.MAX_BLOCKS and mu.truncated == 60 - P.MAX_BLOCKS


def test_image_candidates_skip_icons_pixels_and_prefer_og_and_large() -> None:
    p = P.parse_page(PAGE, "https://cafe.example/page")
    urls = [c.url for c in P.image_candidates(p)]
    assert urls[0] == "https://cafe.example/img/og.png"
    assert "https://cafe.example/img/hero.png" in urls
    assert not any("icon-cart" in u or "pixel" in u for u in urls)
    html = '<main><img src="a.jpg" width="300" height="200"><img src="b.jpg" width="1600" height="900"><img src="x.svg"></main>'
    assert [c.url.rsplit("/", 1)[1] for c in P.image_candidates(P.parse_page(html, "https://x.example/"))] == ["b.jpg", "a.jpg"]
    assert P.best_srcset("s.jpg 400w, m.jpg 1200w, l.jpg 2400w") == "m.jpg"


def test_accessibility_audit() -> None:
    p = P.parse_page(PAGE, "https://cafe.example/page")
    u = P.to_units(p, 20_000)
    issues = P.audit(p, u, ["https://cafe.example/img/hero.png"])
    by = {}
    for it in issues:
        by.setdefault(it["type"], []).append(it)
    assert by["alt"][0]["unit_ref"] == "img:0" and "no alt attribute" in by["alt"][0]["detail"]
    assert "tiny.png: file name as alt" in by["alt"][1]["detail"] and by["alt"][1]["unit_ref"] is None
    assert by["alt"][1]["count"] == 1  # the 1x1 tracking pixel is invisible: not audited
    assert by["empty_link"][0]["count"] == 1 and "/cart" in by["empty_link"][0]["detail"]
    assert by["empty_button"][0]["count"] == 1  # the svg-only button; aria-label="Close" is fine
    assert any("h2 → h4" in it["detail"] for it in by["headings"])
    assert by["lang"]
    lt = by["link_text"][0]
    block = u.blocks[lt["unit_ref"]].text
    assert block[lt["local_start"]:lt["local_end"]] == "Click here"
    assert "long_paragraph" not in by
    assert P.alt_problem("") is None and P.alt_problem("photo") and P.alt_problem("DSC_0042")
    assert P.alt_problem("Barista pouring coffee") is None


def test_audit_clean_page_and_long_paragraph() -> None:
    long_p = " ".join(["word"] * 130)
    html = (f'<html lang="en"><body><main><h1>Title here</h1><p>{long_p}</p>'
            '<img src="/a.jpg" alt="A barista pouring coffee"><a href="/x">Our opening hours</a></main></body></html>')
    p = P.parse_page(html, "https://x.example/")
    issues = P.audit(p, P.to_units(p, 20_000), [])
    assert [i["type"] for i in issues] == ["long_paragraph"]
    specs = P.finding_specs(issues, [])
    assert specs[0]["rule_code"] == "R-WEB-LONG-PARA" and specs[0]["band"] == "human"


def test_finding_specs_use_brand_alt_rule() -> None:
    from app.pipeline.context import RuleSpec

    r = RuleSpec(code="R-ACC-01", category="accessibility", severity="major", statement="Alt text.", check_method="tier0",
                 params={"requires_alt_text": True})
    specs = P.finding_specs([{"type": "alt", "unit_idx": 3, "detail": "d", "quote": "q"},
                             {"type": "lang", "unit_idx": None, "detail": "no lang"}], [r])
    assert specs[0]["rule_code"] == "R-ACC-01" and specs[0]["brand_rule"] is r
    assert specs[1]["rule_code"] == "R-WEB-LANG" and "WCAG" in specs[1]["explanation"]


# ------------------------------------------------------------------ SSRF: static and address checks
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                                "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255", "::1", "fe80::1", "fc00::5",
                                "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe", "2002:7f00:1::", "::"])
def test_private_and_special_addresses_are_blocked(ip: str) -> None:
    assert F.ip_allowed(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.215.14", "2606:4700::6810:84e5"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert F.ip_allowed(ip) is True


@pytest.mark.parametrize("url,msg", [
    ("ftp://example.com/a", "Only http"), ("file:///etc/passwd", "Only http"), ("javascript:alert(1)", "Only http"),
    ("https://user:pw@example.com/", "user name or password"), ("http://example.com:22/", "Port 22"),
    ("http://example.com:6379/", "Port 6379"), ("", "Paste the address"), ("https:///nohost", "no website name"),
])
def test_url_static_checks(url: str, msg: str) -> None:
    with pytest.raises(F.FetchError) as e:
        F.check_url(url)
    assert msg in e.value.message


def test_url_normalisation() -> None:
    assert F.check_url("example.com/a?b=1#frag").url == "https://example.com/a?b=1"
    assert F.check_url("http://example.com:8080").port == 8080


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.8/", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::1]/", "http://localhost/", "http://2130706433/", "http://0x7f.0.0.1/",
                                 "http://[::ffff:169.254.169.254]/"])
def test_fetch_blocks_internal_targets(url: str) -> None:
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(url))
    assert e.value.message == F.BLOCKED and e.value.status_code == 400


def fake_dns(monkeypatch: pytest.MonkeyPatch, answers: Callable[[str, int], list[str]]) -> None:
    import asyncio.base_events as be

    real = be.BaseEventLoop.getaddrinfo

    async def gai(self: Any, host: str, port: Any, *a: Any, **kw: Any) -> list:
        ips = answers(host, port)
        if ips is None:
            return await real(self, host, port, *a, **kw)
        return [(10 if ":" in ip else 2, 1, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(be.BaseEventLoop, "getaddrinfo", gai)


def test_dns_answer_with_any_private_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns(monkeypatch, lambda h, p: ["93.184.215.14", "10.0.0.5"] if h == "mixed.example" else None)
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.resolve_public("mixed.example", 80))
    assert e.value.message == F.BLOCKED


# ------------------------------------------------------------------ local server
Route = Callable[[BaseHTTPRequestHandler], None]


class _Server:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.seen: list[dict] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                srv.seen.append({"path": self.path, "headers": dict(self.headers)})
                fn = srv.routes.get(self.path.split("?")[0])
                if fn is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                fn(self)

            def log_message(self, *a: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def send(h: BaseHTTPRequestHandler, body: bytes, ctype: str = "text/html; charset=utf-8", status: int = 200,
         headers: dict | None = None) -> None:
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    try:
        h.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    s = _Server()
    real = F.ip_allowed
    monkeypatch.setattr(F, "ip_allowed", lambda ip: ip == "127.0.0.1" or real(ip))  # only the test server
    monkeypatch.setattr(F, "ALLOWED_PORTS", F.ALLOWED_PORTS | {s.port})
    s.routes["/page"] = lambda h: send(h, PAGE.encode())
    s.routes["/img/og.png"] = lambda h: send(h, png(1200, 630, (60, 30, 10)), "image/png")
    s.routes["/img/hero.png"] = lambda h: send(h, png(800, 600), "image/png")
    s.routes["/img/tiny.png"] = lambda h: send(h, png(60, 60), "image/png")
    yield s
    s.httpd.shutdown()


def test_fetch_page_and_images(server: _Server) -> None:
    page = asyncio.run(F.fetch_page(server.base + "/page"))
    assert page.status == 200 and "Saffron" in page.html and page.robots == "no robots.txt"
    req = next(r for r in server.seen if r["path"] == "/page")
    assert req["headers"]["User-Agent"].startswith("BrandGateBot/1.0 (+")
    assert "Cookie" not in req["headers"] and "Authorization" not in req["headers"]
    parsed = P.parse_page(page.html, page.final_url)
    imgs, skipped = asyncio.run(F.fetch_images([c.url for c in P.image_candidates(parsed)]))
    assert [i.url.rsplit("/", 1)[1] for i in imgs] == ["og.png", "hero.png"]
    assert skipped == 1  # tiny.png is under the 120 px minimum


def test_redirect_to_private_address_is_blocked(server: _Server) -> None:
    server.routes["/go"] = lambda h: send(h, b"", status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(server.base + "/go"))
    assert e.value.message == F.BLOCKED


def test_redirect_dns_rebinding_is_checked_at_connect(server: _Server, monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: the name first resolves to the (allowed) server, then to a private address. Every
    connection resolves and checks the address it then connects to, so the private answer is refused."""
    calls = {"n": 0}

    def answers(host: str, port: Any) -> Any:
        if host != "rebind.example":
            return None
        calls["n"] += 1
        return ["127.0.0.1"] if calls["n"] <= 2 else ["10.0.0.7"]  # robots.txt, /go, then the rebound answer

    fake_dns(monkeypatch, answers)
    server.routes["/go"] = lambda h: send(h, b"", status=302, headers={"Location": "/page"})
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(f"http://rebind.example:{server.port}/go"))
    assert e.value.message == F.BLOCKED and calls["n"] == 3
    assert [r["path"] for r in server.seen] == ["/robots.txt", "/go"]  # /page was never requested


def test_redirect_limit_and_cookies_never_sent(server: _Server) -> None:
    for i in range(5):
        server.routes[f"/r{i}"] = (lambda n: lambda h: send(h, b"", status=302, headers={
            "Location": f"/r{n + 1}", "Set-Cookie": "sid=secret; Path=/"}))(i)
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(server.base + "/r0"))
    assert "more than 3 times" in e.value.message
    assert all("Cookie" not in r["headers"] for r in server.seen)
    server.routes["/r3"] = lambda h: send(h, PAGE.encode())
    page = asyncio.run(F.fetch_page(server.base + "/r1"))  # r1 -> r2 -> r3: 2 redirects is fine
    assert page.final_url.endswith("/r3")


def test_size_limit(server: _Server) -> None:
    big = b"<html><body>" + b"a" * (F.MAX_HTML_BYTES + 10) + b"</body></html>"
    server.routes["/big"] = lambda h: send(h, big)

    def chunked(h: BaseHTTPRequestHandler) -> None:  # no Content-Length: the streamed cap must stop it
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.end_headers()
        try:
            for _ in range(40):
                h.wfile.write(b"x" * 65536)
        except (BrokenPipeError, ConnectionResetError):
            pass

    server.routes["/stream"] = chunked
    for path in ("/big", "/stream"):
        with pytest.raises(F.FetchError) as e:
            asyncio.run(F.fetch_page(server.base + path))
        assert e.value.status_code == 413 and "2 MB" in e.value.message


def test_timeout(server: _Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "PAGE_DEADLINE_S", 0.5)

    def slow(h: BaseHTTPRequestHandler) -> None:
        time.sleep(1.5)
        send(h, PAGE.encode())

    server.routes["/slow"] = slow
    t0 = time.perf_counter()
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(server.base + "/slow"))
    assert e.value.status_code == 504 and "10 seconds" in e.value.message
    assert time.perf_counter() - t0 < 1.4


def test_not_html_and_http_errors(server: _Server) -> None:
    server.routes["/json"] = lambda h: send(h, b'{"a": 1}', "application/json")
    server.routes["/gone"] = lambda h: send(h, b"nope", status=404)
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(server.base + "/json"))
    assert e.value.status_code == 415 and "isn't a web page" in e.value.message
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(server.base + "/gone"))
    assert "404" in e.value.message


def test_robots_disallow_and_unreachable_robots(server: _Server) -> None:
    server.routes["/robots.txt"] = lambda h: send(h, b"User-agent: BrandGateBot\nDisallow: /page\n", "text/plain")
    with pytest.raises(F.FetchError) as e:
        asyncio.run(F.fetch_page(server.base + "/page"))
    assert e.value.status_code == 403 and "robots.txt" in e.value.message
    server.routes["/robots.txt"] = lambda h: send(h, b"User-agent: *\nDisallow: /admin\n", "text/plain")
    assert asyncio.run(F.fetch_page(server.base + "/page")).robots == "allowed"
    server.routes["/robots.txt"] = lambda h: send(h, b"boom", status=500)
    assert asyncio.run(F.fetch_page(server.base + "/page")).status == 200  # robots.txt failed: proceed


# ------------------------------------------------------------------ API + pipeline
def _wait(client: TestClient, vid: int, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        v = client.get(f"/api/versions/{vid}").json()
        if v["check_status"] in ("done", "error"):
            return v
        time.sleep(0.2)
    raise AssertionError("check did not finish")


def test_api_from_url_creates_checked_item(client: TestClient, server: _Server) -> None:
    login(client, "creator")
    r = client.post("/api/items/from-url", json={"url": server.base + "/page", "confidential": True})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["page"]["title"] == "Saffron Cafe – Fresh coffee" and out["page"]["images"] == 2
    assert out["page"]["paragraphs"] >= 3 and out["page"]["a11y_issues"] >= 5
    v = _wait(client, out["version_id"])
    assert v["check_status"] == "done" and v["kind"] == "web"
    assert v["source"]["final_url"].endswith("/page") and v["source"]["fetched_at"]
    kinds = [u["kind"] for u in v["units"]]
    assert kinds.count("image") == 2 and kinds[-1] == "image"
    for u in v["units"]:
        if u["kind"] == "text":
            assert v["text"][u["char_start"]:u["char_end"]] == u["text"]
    item = client.get(f"/api/items/{out['item_id']}").json()["item"]
    assert item["channel"] == "website" and item["confidential"] is True
    fs = client.get(f"/api/versions/{out['version_id']}/findings").json()
    codes = {f["rule_code"] for f in fs}
    assert {"R-WEB-LINK-NAME", "R-WEB-BUTTON-NAME", "R-WEB-HEADINGS", "R-WEB-LANG", "R-WEB-LINK-TEXT"} <= codes
    alt = [f for f in fs if f["category"] == "accessibility" and f["rule_code"] in ("R-ACC-01", "R-WEB-ALT")]
    assert alt and any(f["unit_id"] for f in alt)  # hero.png has no alt: on its image unit
    lt = next(f for f in fs if f["rule_code"] == "R-WEB-LINK-TEXT")
    assert v["text"][lt["span_start"]:lt["span_end"]] == "Click here"
    assert all("couldn't" not in f["explanation"] for f in fs)
    usage = client.get(f"/api/items/{out['item_id']}/ai-usage").json()
    assert usage["no_external_services"] is True and usage["fetches"][0].startswith("Fetched http://127.0.0.1")
    assert "(no third-party service)" in usage["fetches"][0]


def test_api_from_url_errors(client: TestClient, server: _Server) -> None:
    login(client, "creator")
    for url, status in (("http://169.254.169.254/latest/meta-data/", 400), ("ftp://x.example/", 400),
                        (server.base + "/missing", 502)):
        r = client.post("/api/items/from-url", json={"url": url})
        assert r.status_code == status, (url, r.text)
        assert r.json()["detail"] and "Traceback" not in r.text
    server.routes["/empty"] = lambda h: send(h, b"<html><body><div id=root></div><script>x()</script></body></html>")
    r = client.post("/api/items/from-url", json={"url": server.base + "/empty"})
    assert r.status_code == 422 and "JavaScript" in r.json()["detail"]
    login(client, "reviewer")
    assert client.post("/api/items/from-url", json={"url": server.base + "/page"}).status_code == 403


def test_adjacent_inline_elements_get_a_space_but_words_stay_whole() -> None:
    html = ('<main><p><span>Website Redesign Project</span><span>Project Assets</span> are <b>B</b>old and '
            '<a href="/x">linked</a>. Done here today.</p></main>')
    assert P.parse_page(html, "https://x.example/").blocks[0].text == (
        "Website Redesign Project Project Assets are Bold and linked. Done here today.")
