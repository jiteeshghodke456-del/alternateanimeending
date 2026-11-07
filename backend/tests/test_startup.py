"""Cold start (a deploy or a Render wake): the server answers at once and says it is starting, instead
of failing requests. While the demo is being built, /api/health reports ready=false with a stage and
every other /api call is a quick 503 with Retry-After and X-BG-Starting: 1. No external calls."""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app import readiness
from app.config import get_settings


def test_health_and_gate_while_starting(client: TestClient) -> None:
    readiness.starting("building demo")
    try:
        h = client.get("/api/health")
        assert h.status_code == 200 and h.json()["ok"] is True
        assert h.json()["ready"] is False and h.json()["stage"] == "building demo"
        for method, path in (("GET", "/api/me"), ("POST", "/api/demo/login"), ("POST", "/api/workspaces"), ("GET", "/api/items")):
            r = client.request(method, path, json={})
            assert r.status_code == 503, (path, r.status_code)
            assert r.headers["retry-after"] == "2" and r.headers["x-bg-starting"] == "1"
            assert "starting up" in r.json()["detail"] and "Traceback" not in r.text
        assert client.get("/api/features").status_code == 200  # tiny feature list stays open
        assert client.get("/api/openapi.json").status_code == 200
        assert client.get("/").status_code == 200  # the page itself (splash) is served
    finally:
        readiness.mark_ready()
    h = client.get("/api/health").json()
    assert h["ready"] is True and h["stage"] is None
    assert client.get("/api/me").status_code == 200


def test_features_endpoint(client: TestClient) -> None:
    f = client.get("/api/features").json()
    assert set(f) >= {"workspaces", "brand_edit", "palette", "logo", "facts_import", "pdf_import"}
    assert f["workspaces"] is False  # the unit-test server runs with WORKSPACES_ENABLED=0


def test_background_boot_answers_health_before_the_demo_is_ready(monkeypatch) -> None:  # noqa: ANN001
    """With STARTUP_IN_BACKGROUND=1 the lifespan returns at once; health says ready=false until the
    (slow, here: held) template build finishes, then ready=true."""
    from app import main

    gate = threading.Event()
    real_seed = __import__("app.seed", fromlist=["seed_if_empty"]).seed_if_empty

    def slow_seed() -> str:
        gate.wait(10)
        return real_seed()

    monkeypatch.setattr("app.seed.seed_if_empty", slow_seed)
    import app.pipeline.run as run

    monkeypatch.setattr(run, "on_startup", lambda: None)
    s = get_settings()
    monkeypatch.setattr(s, "startup_in_background", True)
    try:
        with TestClient(main.app) as c:
            h = c.get("/api/health").json()
            assert h["ready"] is False and h["stage"] == "loading demo"
            assert c.get("/api/me").status_code == 503
            gate.set()
            t0 = time.time()
            while not c.get("/api/health").json()["ready"]:
                assert time.time() - t0 < 10
                time.sleep(0.05)
            assert c.get("/api/me").status_code == 200
    finally:
        gate.set()
        readiness.mark_ready()
