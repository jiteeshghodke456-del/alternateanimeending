from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import login


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["jev_configured"] is False
    assert body["llm_configured"] is False


def test_me_lists_demo_users_and_requires_login(client: TestClient) -> None:
    client.cookies.clear()
    me = client.get("/api/me").json()
    assert me["user"] is None
    assert {u["role"] for u in me["users"]} == {"admin", "creator", "reviewer", "approver"}
    r = client.get("/api/brand")
    assert r.status_code == 401
    assert "sign in" in r.json()["detail"].lower()


def test_login_cookie_and_bearer(client: TestClient) -> None:
    out = login(client, "creator")
    assert client.get("/api/me").json()["user"]["role"] == "creator"
    client.cookies.clear()
    r = client.get("/api/me", headers={"Authorization": f"Bearer {out['token']}"})
    assert r.json()["user"]["id"] == out["user"]["id"]


def test_brand_get(client: TestClient) -> None:
    login(client, "reviewer")
    r = client.get("/api/brand")
    assert r.status_code == 200
    b = r.json()
    assert b["name"] and b["rule_count"] >= 5 and b["palette"]
    rules = client.get("/api/rules").json()
    assert all("source_quote" in x and "page" in x for x in rules)


def test_rule_edit_bumps_version_admin_only(client: TestClient) -> None:
    login(client, "creator")
    rule = client.get("/api/rules").json()[0]
    assert client.patch(f"/api/rules/{rule['id']}", json={"statement": "x"}).status_code == 403
    login(client, "admin")
    r = client.patch(f"/api/rules/{rule['id']}", json={"statement": rule["statement"] + " (edited)"})
    assert r.status_code == 200
    assert r.json()["version"] == rule["version"] + 1
    created = client.post("/api/rules", json={"code": "R-TEST-99", "category": "words", "statement": "Never say 'cheap'"})
    assert created.status_code == 201 and created.json()["version"] == 1


def test_create_item_with_text(client: TestClient) -> None:
    login(client, "creator")
    r = client.post("/api/items", data={"title": "Test post", "channel": "instagram",
                                        "text": "India's #1 coffee!\n\nBuy now, it's cheap."})
    assert r.status_code == 201, r.text
    ids = r.json()
    v = client.get(f"/api/versions/{ids['version_id']}").json()
    assert v["n"] == 1 and v["kind"] == "text"
    assert [u["text"] for u in v["units"]] == ["India's #1 coffee!", "Buy now, it's cheap."]
    u2 = v["units"][1]
    assert v["text"][u2["char_start"]:u2["char_end"]] == u2["text"]
    item = client.get(f"/api/items/{ids['item_id']}").json()
    assert item["item"]["state"] == "checking"
    assert item["actions"]["comment"]["allowed"] is True
    assert client.get(f"/api/versions/{ids['version_id']}/findings").json() == []


def test_create_item_rejects_bad_file(client: TestClient) -> None:
    login(client, "creator")
    r = client.post("/api/items", data={"title": "Bad", "channel": "instagram"},
                    files={"file": ("x.gif", b"GIF89a....", "image/gif")})
    assert r.status_code == 415
    assert "PNG" in r.json()["detail"]


def test_demo_item_key_lookup(client: TestClient) -> None:
    login(client, "creator")
    r = client.get("/api/items", params={"key": "demo_flawed_post"})
    assert r.status_code == 200
    # the real seed has it; the placeholder seed doesn't, so only check shape
    assert all(i["key"] == "demo_flawed_post" for i in r.json())


def test_unknown_api_route_is_json_404(client: TestClient) -> None:
    r = client.get("/api/nope")
    assert r.status_code == 404 and "detail" in r.json()


def test_reset_admin_only(client: TestClient) -> None:  # keep last: it re-seeds the DB
    login(client, "creator")
    assert client.post("/api/demo/reset").status_code == 403
    login(client, "admin")
    r = client.post("/api/demo/reset")
    assert r.status_code == 200 and "Demo reset" in r.json()["message"]
    assert client.get("/api/me").json()["user"]["role"] == "admin"  # session survives (stable ids)
