"""Each test session gets a fresh SQLite DB + upload dir; the app seeds on startup."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

_TMP = tempfile.mkdtemp(prefix="brandgate-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["DEMO_MODE"] = "1"
# Empty strings (not pop): pydantic-settings would otherwise read real keys from the repo .env,
# and unit tests must never call the real APIs. Live tests (-m live) read the .env themselves.
os.environ["TYPESAFE_API_KEY"] = ""
os.environ["LLM_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def login(client: TestClient, role: str) -> dict:
    users = client.get("/api/me").json()["users"]
    user = next(u for u in users if u["role"] == role)
    r = client.post("/api/demo/login", json={"user_id": user["id"]})
    assert r.status_code == 200, r.text
    return r.json()
