"""Per-visitor workspaces (DECISIONS.md "Workspaces").

Each visitor gets their own SQLite file and uploads folder under WORKSPACES_DIR, so judges never
see each other's actions. A new workspace is a copy of a template built once at startup:

- "demo":  the seeded demo brand (or SEED_DIR's), pre-checked once on the template by the demo pre-run;
- "blank": four neutral users and an empty brand ("Your brand"), for setting up your own brand.

How a request finds its workspace: `WorkspaceMiddleware` reads the signed `bg_ws` cookie and puts a
`WsRef` in a context variable. `db.get_engine()` and `services.upload_dir()` read it, so routers and
pipeline code that open `Session(get_engine())` need no change. A request without a valid cookie
gets a new demo workspace the first time it touches the DB (lazily, so /api/health, unknown routes
and static files never create one), and the middleware sets the cookie on the response.

Background work (checks, audience runs, PDF import, eval) captures the workspace when it is
scheduled (`bind()`, used by pipeline.run._schedule) and keeps using it. In-memory state that is
keyed by DB ids (running checks, SSE subscribers, audience runs) uses `key(id)`, which is
`(workspace_id, id)` in workspace mode and the plain id otherwise.

Limits: at most MAX_WORKSPACES on disk (least recently used are deleted, visitors with only one or
two requests first), deleted after WORKSPACE_TTL_H idle, at most ENGINE_CACHE open engines (NullPool,
so an idle workspace holds no connection).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings

log = logging.getLogger("brandgate.workspaces")

COOKIE_NAME = "bg_ws"
_SALT = "brandgate-workspace-v1"
KINDS = ("demo", "blank")
ENGINE_CACHE = 8
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
TEMPLATE_IDS = {"demo": "tpl-demo", "blank": "tpl-blank"}
DB_NAME = "brandgate.db"
META_NAME = "meta.json"
TOUCH_PERSIST_S = 60.0  # write last_used to disk at most this often per workspace
ESTABLISHED_HITS = 3  # workspaces with fewer requests that have been idle ONE_OFF_IDLE_S are evicted first
ONE_OFF_IDLE_S = 600.0
SKIP_PATHS = ("/api/health", "/api/docs", "/api/openapi.json")

BLANK_USERS = (("admin", "Admin", "admin"), ("writer", "Writer", "creator"),
               ("reviewer", "Reviewer", "reviewer"), ("approver", "Approver", "approver"))
BLANK_BRAND_NAME = "Your brand"


# ------------------------------------------------------------------ request context
@dataclass
class WsRef:
    """The workspace of the current request or background task. `id` is None until resolved."""

    id: Optional[str] = None
    created: bool = False  # a new workspace was made for this request: the response sets the cookie
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


_current: contextvars.ContextVar[Optional[WsRef]] = contextvars.ContextVar("brandgate_ws", default=None)


def enabled() -> bool:
    return bool(get_settings().workspaces_enabled)


def current_ref() -> Optional[WsRef]:
    return _current.get()


def ensure(ref: WsRef) -> str:
    """Resolve the ref's workspace, creating a demo workspace if it has none (first DB access)."""
    if ref.id is not None:
        return ref.id
    with ref.lock:
        if ref.id is None:
            ref.id = create("demo").id
            ref.created = True
    return ref.id


def current_id() -> Optional[str]:
    ref = _current.get()
    return ensure(ref) if ref is not None else None


def current_kind() -> Optional[str]:
    """"demo" | "blank" for the current workspace; None without workspaces."""
    ws = current_id()
    m = get_meta(ws) if ws else None
    return m.kind if m else None


def key(obj_id: Any) -> Any:
    """Key for in-memory state about a DB row: (workspace, id) in workspace mode, else the id."""
    ws = current_id()
    return obj_id if ws is None else (ws, obj_id)


@contextmanager
def use(ws_id: Optional[str]) -> Iterator[None]:
    """Run the block against workspace `ws_id` (None = the default DB at DB_PATH)."""
    token = _current.set(WsRef(id=ws_id) if ws_id else None)
    try:
        yield
    finally:
        _current.reset(token)


def bind(coro: Any) -> Any:
    """Wrap a coroutine so it runs in the workspace current *now* (captured at scheduling time)."""
    ref = _current.get()
    if ref is None:
        return coro
    ws_id = ensure(ref)

    async def _bound() -> Any:
        with use(ws_id):
            return await coro

    return _bound()


def bind_sync(fn: Callable[..., Any]) -> Callable[..., Any]:
    ws_id = current_id()

    def _run(*a: Any, **kw: Any) -> Any:
        with use(ws_id):
            return fn(*a, **kw)

    return _run


# ------------------------------------------------------------------ paths
def root() -> Path:
    p = Path(get_settings().workspaces_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dir(ws_id: str) -> Path:
    if ws_id in TEMPLATE_IDS.values():
        return root() / "_templates" / ws_id
    if not ID_RE.match(ws_id):
        raise ValueError("bad workspace id")
    return root() / ws_id


def db_file(ws_id: str) -> Path:
    return _dir(ws_id) / DB_NAME


def upload_dir(ws_id: str) -> Path:
    return _dir(ws_id) / "uploads"


def current_upload_dir() -> Optional[Path]:
    ws = current_id()
    return upload_dir(ws) if ws else None


# ------------------------------------------------------------------ registry
@dataclass
class Meta:
    id: str
    kind: str
    created_at: float
    last_used: float
    hits: int = 0
    persisted_at: float = 0.0

    def created_iso(self) -> str:
        return _iso(self.created_at)

    def expires_iso(self) -> str:
        return _iso(self.last_used + ttl_s())


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


_meta: dict[str, Meta] = {}
_lock = threading.RLock()
_engines: "OrderedDict[str, Any]" = OrderedDict()
_opened: set[str] = set()  # workspaces opened at least once by this process (stale-check cleanup done)
busy_checks: list[Callable[[str], bool]] = []  # hooks: is background work running in this workspace?


def ttl_s() -> float:
    return max(0.01, float(get_settings().workspace_ttl_h)) * 3600.0


def max_workspaces() -> int:
    return max(1, int(get_settings().max_workspaces))


def _write_meta(m: Meta) -> None:
    m.persisted_at = time.time()
    data = {k: v for k, v in asdict(m).items() if k != "persisted_at"}
    p = _dir(m.id) / META_NAME
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(p)


def get_meta(ws_id: str) -> Optional[Meta]:
    with _lock:
        return _meta.get(ws_id)


def all_meta() -> list[Meta]:
    with _lock:
        return list(_meta.values())


def _expired(m: Meta, now: float) -> bool:
    return now - m.last_used > ttl_s()


def lookup(ws_id: Optional[str]) -> Optional[str]:
    """The id if that workspace exists and hasn't expired, else None."""
    if not ws_id:
        return None
    with _lock:
        m = _meta.get(ws_id)
        if m is None:
            return None
        if _expired(m, time.time()):
            _evict(ws_id, "expired")
            return None
        if not db_file(ws_id).is_file():
            _meta.pop(ws_id, None)
            return None
        return ws_id


def touch(ws_id: str) -> None:
    now = time.time()
    with _lock:
        m = _meta.get(ws_id)
        if m is None:
            return
        m.last_used = now
        m.hits += 1
        if now - m.persisted_at > TOUCH_PERSIST_S or m.hits == ESTABLISHED_HITS:
            try:
                _write_meta(m)
            except OSError:
                pass


# ------------------------------------------------------------------ cookie
def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt=_SALT)


def sign(ws_id: str) -> str:
    return _serializer().dumps(ws_id)


def unsign(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        v = _serializer().loads(value)
    except BadSignature:
        return None
    return v if isinstance(v, str) and ID_RE.match(v) else None


def cookie_header(ws_id: str) -> bytes:
    from http.cookies import SimpleCookie

    c: SimpleCookie = SimpleCookie()
    c[COOKIE_NAME] = sign(ws_id)
    m = c[COOKIE_NAME]
    m["path"] = "/"
    m["httponly"] = True
    m["samesite"] = "lax"
    m["max-age"] = str(int(ttl_s()) + 3600)
    return m.OutputString().encode("latin-1")


# ------------------------------------------------------------------ engines
def engine_for(ws_id: str) -> Any:
    with _lock:
        eng = _engines.get(ws_id)
        if eng is not None:
            _engines.move_to_end(ws_id)
            return eng
        from sqlalchemy.pool import NullPool
        from sqlmodel import create_engine

        from app.db import install_pragmas

        eng = create_engine(f"sqlite:///{db_file(ws_id)}", poolclass=NullPool,
                            connect_args={"check_same_thread": False, "timeout": 30})
        install_pragmas(eng)
        _engines[ws_id] = eng
        while len(_engines) > ENGINE_CACHE:
            _, old = _engines.popitem(last=False)
            old.dispose()
        first = ws_id not in _opened and ws_id not in TEMPLATE_IDS.values()
        _opened.add(ws_id)
    if first:
        _on_first_open(ws_id)
    return eng


def _on_first_open(ws_id: str) -> None:
    """A workspace from an earlier process: add new columns, mark its interrupted checks as stale."""
    try:
        from app.db import add_missing_columns
        from app.pipeline import run

        with use(ws_id):
            add_missing_columns()
            run._mark_stale()
    except Exception:  # noqa: BLE001
        log.exception("workspace %s: first-open cleanup failed", ws_id)


def _dispose(ws_id: str) -> None:
    with _lock:
        eng = _engines.pop(ws_id, None)
    if eng is not None:
        eng.dispose()


# ------------------------------------------------------------------ create / copy / evict
def _copy_db(src: Path, dst: Path) -> None:
    """Consistent copy with SQLite's backup API (safe while the template is being written)."""
    s = sqlite3.connect(str(src), timeout=30)
    d = sqlite3.connect(str(dst), timeout=30)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _link_tree(src: Path, dst: Path) -> None:
    """Uploads are write-once, so hard links are a free copy (falls back to a real copy)."""
    if not src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        return

    def _link(a: str, b: str) -> None:
        try:
            os.link(a, b)
        except OSError:
            shutil.copy2(a, b)

    shutil.copytree(src, dst, copy_function=_link, dirs_exist_ok=True)


def _fix_copied(dst_db: Path, old_uploads: Path, new_uploads: Path) -> None:
    """Point file paths at the copy's uploads; checks running on the template can't finish in the copy."""
    old, new = str(old_uploads), str(new_uploads)
    con = sqlite3.connect(str(dst_db), timeout=30)
    try:
        for table, col in (("version", "file_path"), ("unit", "image_path"), ("asset", "path")):
            con.execute(f'UPDATE "{table}" SET "{col}" = ? || substr("{col}", ?) WHERE substr("{col}", 1, ?) = ?',
                        (new, len(old) + 1, len(old), old))
        con.execute("UPDATE version SET check_status = 'not_run' WHERE check_status IN ('queued', 'running')")
        con.execute("UPDATE contentitem SET state = 'draft' WHERE state = 'checking'")
        con.commit()
    finally:
        con.close()


def _fill_from_template(ws_id: str, kind: str) -> None:
    tpl = TEMPLATE_IDS[kind]
    if not db_file(tpl).is_file():
        build_template(kind)
    up = upload_dir(ws_id)
    if up.exists():
        shutil.rmtree(up, ignore_errors=True)
    _link_tree(upload_dir(tpl), up)
    _copy_db(db_file(tpl), db_file(ws_id))
    _fix_copied(db_file(ws_id), upload_dir(tpl), up)


def create(kind: str = "demo") -> Meta:
    if kind not in KINDS:
        raise ValueError(f"unknown workspace kind {kind!r}")
    t0 = time.perf_counter()
    with _lock:
        _enforce_limits(room_for=1)
        ws_id = secrets.token_urlsafe(12)
        while ws_id in _meta or not ID_RE.match(ws_id):
            ws_id = secrets.token_urlsafe(12)
        d = _dir(ws_id)
        d.mkdir(parents=True, exist_ok=True)
        now = time.time()
        m = Meta(id=ws_id, kind=kind, created_at=now, last_used=now)
        try:
            _fill_from_template(ws_id, kind)
            _write_meta(m)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        _meta[ws_id] = m
    log.info("workspace %s… created (%s) in %d ms; %d on disk", ws_id[:4], kind,
             int((time.perf_counter() - t0) * 1000), len(_meta))
    return m


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")') if r[1] != "id"]


def _copy_version_results(ws_db: Path, tpl_db: Path, version_id: int) -> bool:
    """Copy one finished template check (version status/stats, findings, AI calls, check audit events)
    into a workspace whose copy of that version was never checked. Seeded ids are identical in every
    copy of the template, so unit ids and version ids line up."""
    con = sqlite3.connect(str(ws_db), timeout=30)
    try:
        con.execute("ATTACH DATABASE ? AS t", (str(tpl_db),))
        cur = con.execute(
            "UPDATE version SET check_status = (SELECT check_status FROM t.version WHERE id = :v), "
            "stats = (SELECT stats FROM t.version WHERE id = :v) "
            "WHERE id = :v AND check_status = 'not_run' "
            "AND (SELECT check_status FROM t.version WHERE id = :v) = 'done'", {"v": version_id})
        if cur.rowcount != 1:
            con.rollback()
            return False
        for table, extra in (("finding", ""), ("aicall", ""),
                             ("auditevent", " AND action IN ('check_started', 'check_done', 'check_failed')")):
            cols = ", ".join(f'"{c}"' for c in _columns(con, table))
            con.execute(f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM t."{table}" WHERE version_id = ?{extra}',
                        (version_id,))
        con.commit()
        return True
    finally:
        con.close()


def propagate_template_check(version_id: int) -> int:
    """The demo template's pre-run finished checking a seeded version: give the result to every demo
    workspace copied before it was ready (a cold start with an early visitor). Returns how many."""
    tpl_db = db_file(TEMPLATE_IDS["demo"])
    with _lock:
        targets = [m.id for m in _meta.values() if m.kind == "demo"]
    n = 0
    for ws_id in targets:
        try:
            if db_file(ws_id).is_file() and _copy_version_results(db_file(ws_id), tpl_db, version_id):
                n += 1
        except Exception:  # noqa: BLE001  (workspace deleted meanwhile, locked, ...)
            log.warning("workspace %s…: could not copy the template check of version %s", ws_id[:4], version_id)
    if n:
        log.info("template check of version %s copied to %d workspace(s)", version_id, n)
    return n


def reset(ws_id: str) -> Meta:
    """Back to a fresh copy of the workspace's own template kind (same id, same cookie)."""
    with _lock:
        m = _meta.get(ws_id)
        if m is None:
            raise KeyError(ws_id)
        _dispose(ws_id)
        # backup() writes into the existing file in place, so connections still open elsewhere
        # (a background task) see the new content instead of a deleted inode / stale -wal file.
        _fill_from_template(ws_id, m.kind)
        m.last_used = time.time()
        _write_meta(m)
    return m


def _busy(ws_id: str) -> bool:
    return any(fn(ws_id) for fn in busy_checks)


def _evict(ws_id: str, why: str) -> None:
    _dispose(ws_id)
    _meta.pop(ws_id, None)
    _opened.discard(ws_id)
    shutil.rmtree(_dir(ws_id), ignore_errors=True)
    log.info("workspace %s… deleted (%s)", ws_id[:4], why)


def _enforce_limits(room_for: int = 0) -> None:
    now = time.time()
    for m in list(_meta.values()):
        if _expired(m, now):
            _evict(m.id, "expired")
    excess = len(_meta) + room_for - max_workspaces()
    if excess <= 0:
        return
    # Stale one-off workspaces (crawlers, a single page view) go first, then plain LRU; busy ones last.
    def _order(m: Meta) -> tuple:
        one_off = m.hits < ESTABLISHED_HITS and now - m.last_used > ONE_OFF_IDLE_S
        return (not one_off, _busy(m.id), m.last_used)

    order = sorted(_meta.values(), key=_order)
    for m in order[:excess]:
        _evict(m.id, "over MAX_WORKSPACES, least recently used")


def enforce_limits() -> None:
    with _lock:
        _enforce_limits()


# ------------------------------------------------------------------ templates
def _seed_signature() -> str:
    from app.seed import seed_dir

    h = hashlib.sha256()
    app_dir = Path(__file__).resolve().parent
    for f in (app_dir / "models.py", app_dir / "seed" / "__init__.py", app_dir / "services.py"):
        h.update(f.read_bytes())
    d = seed_dir().resolve()
    h.update(str(d).encode())
    for p in sorted(d.rglob("*")):
        if p.is_file() and "build" not in p.parts:
            st = p.stat()
            h.update(f"{p.relative_to(d)}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def build_template(kind: str) -> str:
    """(Re)build one template DB + uploads. The demo template is kept across restarts when the seed
    and schema are unchanged (its pre-run results survive); the blank one is cheap and always rebuilt."""
    from sqlmodel import Session

    from app.db import init_db

    tpl = TEMPLATE_IDS[kind]
    d = _dir(tpl)
    sig = _seed_signature() if kind == "demo" else "blank-v1"
    sig_file = d / "signature.txt"
    if kind == "demo" and db_file(tpl).is_file() and sig_file.is_file() and sig_file.read_text().strip() == sig:
        with use(tpl):
            init_db()
        return "kept"
    _dispose(tpl)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    with use(tpl):
        init_db()
        with Session(engine_for(tpl)) as s:
            if kind == "demo":
                from app.seed import load_seed

                summary = load_seed(s)
            else:
                summary = load_blank(s)
    sig_file.write_text(sig)
    log.info("template %s built: %s", kind, summary)
    return summary


def load_blank(session: Any) -> str:
    from app.models import Brand, User
    from app.services import audit

    for uid, name, role in BLANK_USERS:
        session.add(User(id=uid, name=name, role=role))
    session.add(Brand(name=BLANK_BRAND_NAME, tagline="", description="", tone_labels=[], channel_targets={}))
    session.flush()
    audit(session, "seeded", detail={"template": "blank"})
    session.commit()
    return f"{len(BLANK_USERS)} users, empty brand"


def _load_registry() -> None:
    for d in root().iterdir():
        if not d.is_dir() or d.name.startswith("_") or not ID_RE.match(d.name):
            continue
        try:
            data = json.loads((d / META_NAME).read_text(encoding="utf-8"))
            m = Meta(id=d.name, kind=str(data.get("kind", "demo")), created_at=float(data["created_at"]),
                     last_used=float(data.get("last_used", data["created_at"])), hits=int(data.get("hits", 0)))
            if m.kind not in KINDS or not (d / DB_NAME).is_file():
                raise ValueError("incomplete")
            m.persisted_at = time.time()
            _meta[m.id] = m
        except Exception:  # noqa: BLE001
            shutil.rmtree(d, ignore_errors=True)


def startup() -> None:
    """Build the templates, load the workspaces left by an earlier process, apply the limits."""
    with _lock:
        _meta.clear()
        _load_registry()
        _enforce_limits()
    for kind in KINDS:
        build_template(kind)
    log.info("workspaces: root %s, %d on disk, max %d, ttl %.1f h", root(), len(_meta), max_workspaces(),
             get_settings().workspace_ttl_h)


def clear_for_tests() -> None:
    with _lock:
        for ws_id in list(_engines):
            _dispose(ws_id)
        _meta.clear()
        _opened.clear()


# ------------------------------------------------------------------ ASGI middleware
class WorkspaceMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware), so the context variable reaches the endpoint, its
    threadpool dependencies and any task it starts."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if not path.startswith("/api/") or path in SKIP_PATHS or not enabled():
            await self.app(scope, receive, send)
            return
        from starlette.requests import HTTPConnection

        ws_id = lookup(unsign(HTTPConnection(scope).cookies.get(COOKIE_NAME)))
        if ws_id:
            touch(ws_id)
        ref = WsRef(id=ws_id)
        token = _current.set(ref)

        async def _send(msg: dict) -> None:
            if msg.get("type") == "http.response.start" and ref.created and ref.id:
                msg = {**msg, "headers": [*msg.get("headers", []), (b"set-cookie", cookie_header(ref.id))]}
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            _current.reset(token)
