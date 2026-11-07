"""Precedent memory is per workspace: a dismissal in one visitor's workspace never lowers a band in another's."""
from __future__ import annotations

from fastapi.testclient import TestClient

from test_workspaces import clients, login, post_text, wait_done, ws_env  # noqa: F401  (fixtures)

TEXT = "Our cold brew is cheap and cheerful for the whole family. Find your nearest cafe."


def _cheap(c: TestClient, vid: int) -> dict:
    return next(f for f in c.get(f"/api/versions/{vid}/findings").json() if f["rule_code"] == "R-TERM-02")


def test_precedents_stay_in_their_workspace(clients: list[TestClient]) -> None:  # noqa: F811
    a, b = clients[0], clients[1]
    login(a, "creator")
    first = post_text(a, TEXT)
    wait_done(a, first["version_id"])
    f = _cheap(a, first["version_id"])
    login(a, "reviewer")
    assert a.post(f"/api/findings/{f['id']}/dismiss", json={"reason": "false_alarm", "note": "cheerful"}).status_code == 200
    assert any(p["rule_code"] == "R-TERM-02" for p in a.get("/api/precedents").json()["precedents"])

    login(a, "creator")
    again = post_text(a, TEXT)
    wait_done(a, again["version_id"])
    assert _cheap(a, again["version_id"])["precedent"] is not None

    login(b, "creator")
    other = post_text(b, TEXT)
    wait_done(b, other["version_id"])
    g = _cheap(b, other["version_id"])
    assert g["precedent"] is None and g["band"] == "flag"
    assert not any(p["rule_code"] == "R-TERM-02" for p in b.get("/api/precedents").json()["precedents"])
