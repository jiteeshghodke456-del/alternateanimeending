"""Fetch a public web page safely (SSRF-guarded). OWNER: backend.

Rules (each one has a test in tests/test_webpage.py):
- http/https only, no user:password@ in the URL, ports 80, 443, 8080 and 8443 only.
- Every hostname is resolved and EVERY address must be public: loopback, private (10/8, 172.16/12, 192.168/16,
  fc00::/7), link-local (169.254/16 incl. cloud metadata, fe80::/10), CGNAT, multicast, reserved and unspecified
  addresses are refused. The check runs inside the connection itself (a guarded httpcore network backend that
  connects to the address it just checked), so a DNS answer can't change between the check and the connect
  (DNS rebinding), and it runs again for every redirect hop (max 3, each hop re-validated).
- One deadline for the page (robots.txt + redirects + HTML): 10 s. HTML is capped at 2 MB, read as a stream.
- Images: up to 8 candidates fetched (2 at a time) to keep the 4 largest usable ones, each capped at 10 MB,
  with their own 8 s deadline; a failed image is skipped, never fatal.
- robots.txt is honoured for our user agent; if it can't be loaded, the fetch goes ahead.
- No cookies are ever sent (the jar is cleared before every request), no credentials, no proxy from the
  environment (trust_env=False). User agent: RulemintBot/1.0 (+<contact url>).

`WEBFETCH_PROXY` (dev only, off by default) sends requests through an HTTP proxy, for sandboxes whose outbound
traffic must go through one. The DNS check then runs before each hop instead of at connect time.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx

from app.config import get_settings

log = logging.getLogger("brandgate.webfetch")

ALLOWED_PORTS = {80, 443, 8080, 8443}
MAX_REDIRECTS = 3
PAGE_DEADLINE_S = 10.0
IMAGE_DEADLINE_S = 8.0
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ROBOTS_BYTES = 256 * 1024
MAX_IMAGES = 4
BOT_NAME = "RulemintBot"
_HTML_TYPES = ("text/html", "application/xhtml+xml")


class FetchError(Exception):
    """A human message for the person who pasted the URL, plus an HTTP status for the API."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


BLOCKED = ("That address points to a private or internal network, so Rulemint won't fetch it. "
           "Paste the address of a public web page.")


def user_agent() -> str:
    return f"{BOT_NAME}/1.0 (+{get_settings().webfetch_contact_url})"


# ------------------------------------------------------------------ address checks
def ip_allowed(ip: str) -> bool:
    """True only for public unicast addresses. IPv4-mapped/NAT64/6to4 IPv6 forms are judged by their IPv4."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr.sixtofour is not None:
            addr = addr.sixtofour
        elif addr.teredo is not None:
            return False
        elif addr in ipaddress.ip_network("64:ff9b::/96"):
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return bool(addr.is_global and not (addr.is_multicast or addr.is_reserved or addr.is_loopback
                                        or addr.is_link_local or addr.is_private or addr.is_unspecified))


async def resolve_public(host: str, port: int) -> list[str]:
    """Resolve `host`; every address must be public (one private answer blocks the whole name)."""
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        ips = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise FetchError(f"Couldn't find the website “{host}”. Check the address for typos.") from exc
        ips = list(dict.fromkeys(str(i[4][0]) for i in infos))
    if not ips or not all(ip_allowed(ip) for ip in ips):
        raise FetchError(BLOCKED, 400)
    return ips


@dataclass
class Target:
    url: str
    host: str
    port: int
    scheme: str


def check_url(url: str) -> Target:
    """Static checks (no DNS): scheme, credentials, host, port. Returns the normalised URL."""
    url = (url or "").strip()
    if not url:
        raise FetchError("Paste the address of a web page, starting with https://.")
    if "://" not in url and not url.startswith("//") and re.match(r"^[\w.-]+\.[a-z]{2,}(?:[:/?#]|$)", url, re.I):
        url = "https://" + url
    if len(url) > 2048:
        raise FetchError("That address is too long (over 2,048 characters).")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("That doesn't look like a web address. Paste one that starts with https://.") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise FetchError("Only http:// and https:// web pages can be checked.", 400)
    if parts.username is not None or parts.password is not None:
        raise FetchError("Addresses with a user name or password in them can't be checked. Remove the “name@” part.", 400)
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise FetchError("That address has no website name in it. Paste one like https://example.com/page.")
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError(f"Port {port} isn't allowed. Only the standard web ports (80, 443, 8080, 8443) can be fetched.", 400)
    netloc = (f"[{host}]" if ":" in host else host) + (f":{parts.port}" if parts.port else "")
    clean_url = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return Target(url=clean_url, host=host, port=port, scheme=scheme)


# ------------------------------------------------------------------ guarded transport
class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves and checks the host, then connects to the checked address (TLS SNI and Host stay the name)."""

    def __init__(self) -> None:
        self.inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: Optional[float] = None, local_address: Optional[str] = None,
                          socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        if port not in ALLOWED_PORTS:
            raise FetchError(f"Port {port} isn't allowed.", 400)
        ips = await resolve_public(host, port)
        last: Optional[Exception] = None
        for ip in ips[:4]:
            try:
                return await self.inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                                    socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        raise httpcore.ConnectError(str(last) if last else "connect failed")

    async def connect_unix_socket(self, *a: Any, **kw: Any) -> httpcore.AsyncNetworkStream:  # noqa: ARG002
        raise FetchError(BLOCKED, 400)

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


def make_client(timeout: float = PAGE_DEADLINE_S) -> httpx.AsyncClient:
    s = get_settings()
    headers = {"User-Agent": user_agent(), "Accept-Language": "en;q=0.9, *;q=0.5"}
    tmo = httpx.Timeout(timeout, connect=min(5.0, timeout))
    if s.webfetch_proxy:  # dev sandboxes only: DNS is checked per hop in fetch_page instead
        transport = httpx.AsyncHTTPTransport(proxy=s.webfetch_proxy, trust_env=False, verify=s.webfetch_ca_bundle or True)
    else:
        transport = httpx.AsyncHTTPTransport(trust_env=False)
        transport._pool = httpcore.AsyncConnectionPool(  # noqa: SLF001  (httpx has no public hook for this)
            ssl_context=httpx.create_ssl_context(trust_env=False), max_connections=4,
            network_backend=GuardedBackend())
    return httpx.AsyncClient(transport=transport, headers=headers, timeout=tmo, follow_redirects=False,
                             trust_env=False, max_redirects=0)


async def _precheck(t: Target) -> None:
    """With a proxy the connect-time check can't see the target, so check DNS before each hop."""
    if get_settings().webfetch_proxy:
        await resolve_public(t.host, t.port)


async def _get(client: httpx.AsyncClient, url: str, cap: int, accept: str, too_big: str) -> tuple[httpx.Response, bytes, str]:
    """GET with manual, re-validated redirects and a streamed size cap. Returns (response, body, final url)."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        t = check_url(current)
        await _precheck(t)
        client.cookies.clear()  # never send cookies, not even ones a redirect just set
        async with client.stream("GET", t.url, headers={"Accept": accept}) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = urljoin(t.url, r.headers["location"])
                continue
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > cap:
                raise FetchError(too_big, 413)
            chunks: list[bytes] = []
            size = 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > cap:
                    raise FetchError(too_big, 413)
                chunks.append(chunk)
            return r, b"".join(chunks), t.url
    raise FetchError(f"The page redirected more than {MAX_REDIRECTS} times, so it wasn't fetched.", 422)


# ------------------------------------------------------------------ robots.txt
async def robots_allows(client: httpx.AsyncClient, url: str) -> Optional[bool]:
    """True/False from robots.txt for our user agent; None when robots.txt can't be loaded (we then proceed)."""
    p = urlsplit(url)
    robots_url = urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))
    try:
        r, body, _ = await asyncio.wait_for(_get(client, robots_url, MAX_ROBOTS_BYTES, "text/plain", "robots.txt too big"), 4.0)
    except FetchError as exc:
        if exc.message == BLOCKED:
            raise
        return None
    except (httpx.HTTPError, asyncio.TimeoutError, OSError):
        return None
    if r.status_code != 200:
        return None
    rp = RobotFileParser()
    rp.parse(body.decode("utf-8", "replace").splitlines())
    return rp.can_fetch(BOT_NAME, url)


# ------------------------------------------------------------------ page
@dataclass
class FetchedPage:
    url: str  # what was asked for (normalised)
    final_url: str
    status: int
    html: str
    bytes: int
    elapsed_ms: int
    robots: str  # allowed | no robots.txt
    notes: list[str] = field(default_factory=list)


def _charset(content_type: str, head: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", head[:4096], re.I)
    cs = m.group(1) if m else "utf-8"
    if isinstance(cs, bytes):
        cs = cs.decode("ascii", "ignore")
    try:
        "".encode(cs)
    except LookupError:
        cs = "utf-8"
    return cs


async def fetch_page(url: str) -> FetchedPage:
    """Fetch one HTML page under PAGE_DEADLINE_S. Raises FetchError with a human message."""
    t = check_url(url)
    started = time.perf_counter()
    try:
        async with asyncio.timeout(PAGE_DEADLINE_S):
            async with make_client() as client:
                allowed = await robots_allows(client, t.url)
                if allowed is False:
                    raise FetchError("This site's robots.txt asks bots not to fetch this page, so Rulemint respects that. "
                                     "Paste the page's text instead.", 403)
                r, body, final = await _get(client, t.url, MAX_HTML_BYTES, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                                            "That page is larger than 2 MB, so it wasn't fetched.")
                if final != t.url and urlsplit(final).netloc != urlsplit(t.url).netloc:
                    again = await robots_allows(client, final)
                    if again is False:
                        raise FetchError("The page redirected to a site whose robots.txt asks bots not to fetch it.", 403)
    except TimeoutError as exc:
        raise FetchError("The page took longer than 10 seconds to load, so the fetch was stopped. Try again, "
                         "or paste the text instead.", 504) from exc
    except FetchError:
        raise
    except httpx.HTTPError as exc:
        cause = exc.__cause__ or exc.__context__
        while cause is not None and not isinstance(cause, FetchError):
            cause = cause.__cause__ or cause.__context__
        if isinstance(cause, FetchError):
            raise cause from exc
        log.info("fetch failed: %s", type(exc).__name__)
        raise FetchError("Couldn't connect to that website. Check the address, or try again in a moment.", 502) from exc
    if r.status_code >= 400:
        raise FetchError(f"The website answered with an error ({r.status_code}), so there was nothing to check.", 502)
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    looks_html = body[:1024].lstrip().lower().startswith((b"<!doctype html", b"<html"))
    if ctype and ctype not in _HTML_TYPES and not (ctype == "text/plain" and looks_html):
        raise FetchError(f"That address isn't a web page (it is “{ctype}”). Upload images or PDFs on their own tabs.", 415)
    if not ctype and not looks_html:
        raise FetchError("That address didn't return a web page.", 415)
    html = body.decode(_charset(r.headers.get("content-type", ""), body), "replace")
    return FetchedPage(url=t.url, final_url=final, status=r.status_code, html=html, bytes=len(body),
                       elapsed_ms=int((time.perf_counter() - started) * 1000),
                       robots="allowed" if allowed else "no robots.txt")


# ------------------------------------------------------------------ images
@dataclass
class FetchedImage:
    url: str
    data: bytes
    width: int
    height: int


async def fetch_images(urls: list[str], keep: int = MAX_IMAGES, min_side: int = 120) -> tuple[list[FetchedImage], int]:
    """Fetch candidates (2 at a time) and keep the `keep` largest PNG/JPEG/WEBP images whose short side is at
    least `min_side`. Returns (images in page order, number skipped). Never raises."""
    from app.pipeline import units as U

    sem = asyncio.Semaphore(2)
    got: list[tuple[int, FetchedImage]] = []
    skipped = 0

    async def one(i: int, url: str, client: httpx.AsyncClient) -> None:
        nonlocal skipped
        async with sem:
            try:
                _r, data, _ = await _get(client, url, MAX_IMAGE_BYTES, "image/png,image/jpeg,image/webp;q=0.9", "image too big")
                if _r.status_code != 200 or U.sniff_kind(data) is None or U.sniff_kind(data)[0] != "image":
                    skipped += 1
                    return
                im = U._open_checked(data)  # noqa: SLF001  (header only + the pixel caps)
                w, h = im.size
                im.close()
                if min(w, h) < min_side:
                    skipped += 1
                    return
                got.append((i, FetchedImage(url=url, data=data, width=w, height=h)))
                if len(got) > keep:  # keep memory bounded: drop the smallest so far
                    got.remove(min(got, key=lambda x: x[1].width * x[1].height))
                    skipped += 1
            except Exception:  # noqa: BLE001  (timeouts, caps, blocked hosts, damaged files)
                skipped += 1

    if not urls:
        return [], 0
    try:
        async with asyncio.timeout(IMAGE_DEADLINE_S):
            async with make_client(IMAGE_DEADLINE_S) as client:
                await asyncio.gather(*[one(i, u, client) for i, u in enumerate(urls)])
    except TimeoutError:
        skipped += max(0, len(urls) - len(got) - skipped)
    return [fi for _, fi in sorted(got, key=lambda x: x[0])], skipped
