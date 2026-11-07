"""Demo seed loader. OWNER: backend (scaffold agent).

Seed directory resolution (first match wins):
1. env SEED_DIR, if set;
2. backend/app/seed/ if it contains BOTH users.json and rules.json (the real demo brand);
3. backend/app/seed/_placeholder/ (tiny stand-in so the app runs before the real files land).
We never mix the two directories, so ids referenced across files stay consistent.

Every file is optional except users.json; missing files are skipped. Accepted formats:
- brand.json      {name, tone_labels[], channel_targets{channel:{max_grade:int}}}
- users.json      [{id, name, role}]                  (id coerced to str)
- rules.json      [{code|id, category, severity, statement, source_quote, page, check_method,
                    params{}, examples_violating[], examples_ok[], enabled?}]
- terms.json      [{kind: banned|preferred|capitalise, value, replacement}]
- palette.json    [{name, hex}]
- fonts.json      [str]
- facts.csv       code|id, statement, value, source, valid_until (ISO date or empty)
- content/items.json [{title, key?, channel, creator, state, confidential, text, file, caption?, alt_text?}]
                  key -> ContentItem.key (e.g. "demo_flawed_post", found via GET /api/items?key=...)
                  creator = user id or user name; file relative to <seed>/content/
- logo.png, logo_alt_wrong.png, guideline.pdf -> Asset rows (path points into the seed dir)
- personas.json   not loaded into the DB; read it with seed_file("personas.json") (240, each has `segment`)
Rule params are stored verbatim (e.g. params.vague=true marks the 5 planted vague rules).
"""
from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from app.config import get_settings
from app.db import drop_db, get_engine, init_db
from app.models import (
    CHANNELS,
    CHECK_METHODS,
    ITEM_STATES,
    ROLES,
    SEVERITIES,
    TERM_KINDS,
    Asset,
    Brand,
    ContentItem,
    Fact,
    Rule,
    Term,
    User,
)

log = logging.getLogger("brandgate.seed")

SEED_ROOT = Path(__file__).resolve().parent
PLACEHOLDER_DIR = SEED_ROOT / "_placeholder"

_ROLE_ALIASES = {"brand admin": "admin", "brand_admin": "admin", "administrator": "admin"}


def seed_dir() -> Path:
    override = get_settings().seed_dir.strip()
    if override:
        return Path(override)
    if (SEED_ROOT / "users.json").is_file() and (SEED_ROOT / "rules.json").is_file():
        return SEED_ROOT
    return PLACEHOLDER_DIR


def is_placeholder() -> bool:
    return seed_dir().resolve() == PLACEHOLDER_DIR.resolve()


def seed_file(name: str) -> Optional[Path]:
    """Path to a file in the active seed dir, or None if absent."""
    p = seed_dir() / name
    return p if p.is_file() else None


def _json(name: str, default: Any) -> Any:
    p = seed_file(name)
    if p is None:
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        log.exception("seed: could not parse %s; skipping", name)
        return default


def _list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    s = str(value or "").strip().lower()
    return s if s in allowed else default


def _date(s: Any) -> Optional[date]:
    s = str(s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        log.warning("seed: bad valid_until %r; leaving empty", s)
        return None


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def db_is_empty(session: Session) -> bool:
    return session.exec(select(User)).first() is None


def load_seed(session: Session) -> str:
    """Insert the demo brand into an EMPTY database. Returns a short summary string."""
    from app.services import audit, create_version
    from app.pipeline.units import UploadError

    d = seed_dir()
    log.info("seed: loading from %s", d)

    # users
    users: dict[str, User] = {}
    for u in _list(_json("users.json", [])):
        role = str(u.get("role", "")).strip().lower()
        role = _ROLE_ALIASES.get(role, role)
        if role not in ROLES:
            log.warning("seed: user %r has unknown role %r; skipped", u.get("id"), u.get("role"))
            continue
        user = User(id=str(u["id"]), name=str(u.get("name") or u["id"]), role=role)
        session.add(user)
        users[user.id] = user

    # brand
    b = _json("brand.json", {}) or {}
    brand = Brand(
        name=str(b.get("name") or "Demo Brand"),
        tone_labels=[str(t) for t in _list(b.get("tone_labels"))],
        channel_targets=dict(b.get("channel_targets") or {}),
    )
    session.add(brand)
    session.flush()

    # rules
    n_rules = 0
    seen_codes: set[str] = set()
    for r in _list(_json("rules.json", [])):
        code = str(r.get("code") or r.get("id") or "").strip()
        if not code or code in seen_codes or not r.get("statement"):
            log.warning("seed: rule without code/statement or duplicate %r; skipped", code)
            continue
        seen_codes.add(code)
        session.add(Rule(
            brand_id=brand.id, code=code, version=_int(r.get("version")) or 1,
            category=str(r.get("category") or "general"),
            severity=_pick(r.get("severity"), SEVERITIES, "minor"),
            statement=str(r["statement"]), source_quote=str(r.get("source_quote") or ""),
            page=_int(r.get("page")), check_method=_pick(r.get("check_method"), CHECK_METHODS, "jev"),
            params=dict(r.get("params") or {}),
            examples_violating=[str(x) for x in _list(r.get("examples_violating"))],
            examples_ok=[str(x) for x in _list(r.get("examples_ok"))],
            enabled=bool(r.get("enabled", True)),
        ))
        n_rules += 1

    # terms
    n_terms = 0
    for t in _list(_json("terms.json", [])):
        kind = str(t.get("kind", "")).strip().lower()
        if kind not in TERM_KINDS or not t.get("value"):
            continue
        session.add(Term(brand_id=brand.id, kind=kind, value=str(t["value"]),
                         replacement=(str(t["replacement"]) if t.get("replacement") else None)))
        n_terms += 1

    # facts
    n_facts = 0
    fp = seed_file("facts.csv")
    if fp:
        with fp.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                code = row.get("code") or row.get("id")
                if not code or not row.get("statement"):
                    continue
                session.add(Fact(brand_id=brand.id, code=code, statement=row["statement"], value=row.get("value", ""),
                                 source=row.get("source", ""), valid_until=_date(row.get("valid_until"))))
                n_facts += 1

    # assets
    for c in _list(_json("palette.json", [])):
        if isinstance(c, dict) and c.get("hex"):
            hx = str(c["hex"]).strip()
            hx = hx if hx.startswith("#") else f"#{hx}"
            session.add(Asset(brand_id=brand.id, kind="palette", meta={"name": str(c.get("name") or hx), "hex": hx.upper()}))
    for f in _list(_json("fonts.json", [])):
        name = f if isinstance(f, str) else (f.get("name") if isinstance(f, dict) else None)
        if name:
            session.add(Asset(brand_id=brand.id, kind="font", meta={"name": str(name)}))
    for fname, variant in (("logo.png", "approved"), ("logo_alt_wrong.png", "alt_wrong")):
        if p := seed_file(fname):
            session.add(Asset(brand_id=brand.id, kind="logo", path=str(p.resolve()), meta={"variant": variant}))
    if p := seed_file("guideline.pdf"):
        session.add(Asset(brand_id=brand.id, kind="guideline", path=str(p.resolve()), meta={"name": "guideline.pdf"}))
    session.flush()

    # content items
    by_name = {u.name.lower(): u for u in users.values()}
    n_items = 0
    content_dir = d / "content"
    for it in _list(_json("content/items.json", [])):
        creator_key = str(it.get("creator") or "")
        creator = users.get(creator_key) or by_name.get(creator_key.lower())
        if creator is None:
            creator = next((u for u in users.values() if u.role == "creator"), None)
        if creator is None:
            continue
        item = ContentItem(
            brand_id=brand.id, title=str(it.get("title") or "Untitled"), key=(str(it["key"]) if it.get("key") else None),
            channel=_pick(it.get("channel"), CHANNELS, "instagram"), creator_id=creator.id,
            state=_pick(it.get("state"), ITEM_STATES, "draft"), confidential=bool(it.get("confidential", False)),
        )
        if item.state == "checking":  # nothing is running at boot
            item.state = "draft"
        session.add(item)
        session.flush()
        file_bytes, filename = None, ""
        if it.get("file"):
            fpath = content_dir / str(it["file"])
            if fpath.is_file():
                file_bytes, filename = fpath.read_bytes(), fpath.name
            else:
                log.warning("seed: content file %s missing", fpath)
        try:
            v = create_version(
                session, item, n=1, text=it.get("text") or "", file_bytes=file_bytes, filename=filename,
                caption=it.get("caption") or (it.get("text") if file_bytes else None), alt_text=it.get("alt_text"),
                check_status="not_run",
            )
        except UploadError as exc:
            log.warning("seed: item %r skipped: %s", item.title, exc.message)
            session.delete(item)
            session.flush()
            continue
        audit(session, "created", item_id=item.id, version_id=v.id, user_id=creator.id, detail={"seeded": True})
        n_items += 1

    audit(session, "seeded", detail={"seed_dir": d.name, "placeholder": is_placeholder()})
    session.commit()
    summary = f"{len(users)} users, {n_rules} rules, {n_terms} terms, {n_facts} facts, {n_items} items ({'placeholder' if is_placeholder() else 'demo'} seed)"
    log.info("seed: %s", summary)
    return summary


def seed_if_empty() -> Optional[str]:
    init_db()
    with Session(get_engine()) as s:
        if db_is_empty(s):
            return load_seed(s)
    return None


def reset_and_seed() -> str:
    """Drop everything, wipe uploads, re-create tables and seed."""
    drop_db()
    up = Path(get_settings().upload_dir)
    if up.is_dir():
        shutil.rmtree(up, ignore_errors=True)
    init_db()
    with Session(get_engine()) as s:
        return load_seed(s)
