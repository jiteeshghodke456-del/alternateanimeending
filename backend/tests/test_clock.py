"""Fact expiry uses the brand's local day (IST by default), not the server's UTC day."""
from datetime import datetime, timedelta, timezone

from app import clock
from app.services import fact_expired


def test_local_today_is_ist_by_default() -> None:
    assert clock.local_today() == datetime.now(timezone(timedelta(minutes=330))).date()


def test_yesterday_in_ist_is_expired_and_today_is_not() -> None:
    today = clock.local_today()
    assert fact_expired(today - timedelta(days=1)) is True
    assert fact_expired(today) is False
    assert fact_expired(None) is False
