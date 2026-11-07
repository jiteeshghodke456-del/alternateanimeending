"""Regression: POST /api/demo/reset re-triggers the idempotent demo pre-run, so the dashboard
isn't empty after a reset. Jev is faked (run_check is replaced); nothing external is called."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_engine
from app.models import Version
from app.pipeline import run
from conftest import login


def _fake_env(monkeypatch) -> tuple[list[Any], list[int]]:  # noqa: ANN001
    scheduled: list[Any] = []
    checked: list[int] = []

    async def fake_check(vid: int) -> None:
        checked.append(vid)
        with Session(get_engine()) as s:
            v = s.get(Version, vid)
            v.check_status = "done"
            s.add(v)
            s.commit()
        run._running.discard(vid)

    monkeypatch.setattr(run, "get_settings", lambda: SimpleNamespace(demo_mode=True, jev_configured=True))
    monkeypatch.setattr(run, "_schedule", scheduled.append)
    monkeypatch.setattr(run, "run_check", fake_check)
    monkeypatch.setattr(run, "_prerun_started", True)  # the startup pre-run already happened
    return scheduled, checked


def test_reset_restarts_prerun_and_checks_seeded_versions(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    scheduled, checked = _fake_env(monkeypatch)
    login(client, "admin")
    r = client.post("/api/demo/reset")
    assert r.status_code == 200 and "checking the demo items" in r.json()["message"]
    assert len(scheduled) == 1
    asyncio.run(scheduled[0])
    with Session(get_engine()) as s:
        left = s.exec(select(Version).where(Version.check_status == "not_run")).all()
    assert checked and not left
    # idempotent: a second pre-run in the same generation is refused
    assert run.start_prerun() is False


def test_older_prerun_stops_after_a_reset(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    scheduled, checked = _fake_env(monkeypatch)
    login(client, "admin")
    assert client.post("/api/demo/reset").status_code == 200
    old = scheduled[0]
    assert client.post("/api/demo/reset").status_code == 200
    asyncio.run(old)  # belongs to the first reset: must not check anything
    assert checked == []
    asyncio.run(scheduled[1])
    assert checked


def test_reset_without_jev_does_not_prerun(client: TestClient) -> None:
    login(client, "admin")
    r = client.post("/api/demo/reset")
    assert r.status_code == 200 and "checking" not in r.json()["message"]
