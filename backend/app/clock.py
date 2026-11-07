"""The brand's local "today": fact expiry is judged in local days (default IST), not the server's UTC day."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.config import get_settings


def local_today() -> date:
    off = timedelta(minutes=get_settings().app_utc_offset_minutes)
    return datetime.now(timezone(off)).date()
