"""P1-3 Guideline PDF import: page text, chunking, quote verification (code decides) and the API,
with the LLM faked at the raw-call level (pdf_import._chat)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_engine
from app.models import AICall, AuditEvent, Rule
from app.pipeline import explain
from app.pipeline import pdf_import as PI
from app.routers import innovations
from conftest import login

PDF = Path(__file__).resolve().parents[1] / "app" / "seed" / "guideline.pdf"
REAL_QUOTE = "Do not call us the best, the number one or the finest unless an approved fact with a source proves it."
FAKE_QUOTE = "Always mention our award-winning espresso in every single post we publish."


def _reply(pages_in_prompt: str) -> str:
    rules = []
    if 'number="5"' in pages_in_prompt:
        rules = [
            {"statement": "No superlatives without an approved, sourced fact.", "category": "claims", "severity": "blocking",
             "check_method": "claim", "source_quote": REAL_QUOTE, "page": 5,
             "examples_violating": ["India's #1 coffee"], "examples_ok": ["Rated 4.6 on Google"]},
            {"statement": "Mention the espresso award.", "category": "Messaging", "severity": "urgent",
             "check_method": "magic", "source_quote": FAKE_QUOTE, "page": 5},
            {"statement": "No superlatives without an approved, sourced fact.", "category": "claims",
             "source_quote": REAL_QUOTE, "page": 5},  # duplicate
            {"category": "claims"},  # invalid: no statement
        ]
    return json.dumps({"rules": rules})


def install_llm(monkeypatch, fail: bool = False) -> list[str]:  # noqa: ANN001
    prompts: list[str] = []

    async def chat(messages: list, *, model: str) -> tuple[str, int, int]:
        prompts.append(messages[-1]["content"])
        if fail:
            raise RuntimeError("boom")
        return _reply(messages[-1]["content"]), 1000, 300

    monkeypatch.setattr(explain, "available", lambda: True)
    monkeypatch.setattr(PI, "_chat", chat)
    return prompts


def _upload(client: TestClient) -> dict:
    r = client.post("/api/brand/import-pdf", files={"file": ("guideline.pdf", PDF.read_bytes(), "application/pdf")})
    assert r.status_code == 202, r.text
    return r.json()


def _wait(client: TestClient, import_id: str) -> dict:
    t0 = time.time()
    while time.time() - t0 < 20:
        out = client.get(f"/api/brand/import-pdf/{import_id}").json()
        if out["status"] != "running":
            return out
        time.sleep(0.05)
    raise AssertionError("import did not finish")


# ------------------------------------------------------------------ pure parts
def test_extract_pages_and_chunks() -> None:
    pages = PI.extract_pages(PDF.read_bytes())
    assert len(pages) == 10 and REAL_QUOTE[:30] in " ".join(pages[4].split())
    chunks = PI.chunk_pages(pages)
    assert sorted(p for c in chunks for p in c) == list(range(1, 11))
    assert sum(PI.rule_budget(pages, chunks)) <= PI.MAX_RULES + 3 * len(chunks)


def test_non_pdf_is_refused() -> None:
    with pytest.raises(PI.PdfImportError) as e:
        PI.extract_pages(b"hello")
    assert e.value.status_code == 415


def test_verify_quote() -> None:
    page = PI.extract_pages(PDF.read_bytes())[4]
    assert PI.verify_quote(REAL_QUOTE, page) >= PI.VERIFY_AT  # wrapped across lines in the PDF
    assert PI.verify_quote("“" + REAL_QUOTE.replace("'", "’") + "”", page) >= PI.VERIFY_AT
    assert PI.verify_quote(FAKE_QUOTE, page) < PI.VERIFY_AT
    assert PI.verify_quote("best", page) == 0.0  # too short to prove anything


def test_finalise_dedupes_normalises_and_unticks_unverified() -> None:
    pages = PI.extract_pages(PDF.read_bytes())
    rules, dropped = PI.parse_rules(_reply('number="5"'))
    assert dropped == 1
    out = PI.finalise(rules, pages)
    assert [p["id"] for p in out] == ["P-01", "P-02"]
    real, fake = out
    assert real["quote_verified"] and real["selected"] and real["rule"]["page"] == 5
    assert not fake["quote_verified"] and not fake["selected"]
    assert fake["rule"]["category"] == "messaging" and fake["rule"]["severity"] == "minor"
    assert fake["rule"]["check_method"] == "jev"


def test_next_codes() -> None:
    assert PI.next_codes(["R-CLAIM-01", "R-IMP-02", "R-IMP-07"], 2) == ["R-IMP-08", "R-IMP-09"]
    assert PI.next_codes([], 1) == ["R-IMP-01"]


# ------------------------------------------------------------------ API
def test_import_then_confirm(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    prompts = install_llm(monkeypatch)
    login(client, "admin")
    job = _upload(client)
    assert job["status"] == "running" and job["pages"] == 10
    out = _wait(client, job["import_id"])
    assert out["status"] == "done" and len(out["proposals"]) == 2 and out["verified"] == 1
    assert all("<content>" in p for p in prompts)
    r = client.post(f"/api/brand/import-pdf/{job['import_id']}/confirm",
                    json={"selected": ["P-01", "P-02"], "edits": {"P-02": {"statement": "Edited.", "severity": "major"}}})
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert [c["code"] for c in created] == ["R-IMP-01", "R-IMP-02"]
    assert created[1]["statement"] == "Edited." and created[1]["severity"] == "major"
    assert created[0]["page"] == 5 and created[0]["source_quote"] == REAL_QUOTE
    assert not any(c["enabled"] for c in created)  # imported rules start disabled
    with Session(get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "rules_imported")).all()[-1]
        assert ev.detail["codes"] == ["R-IMP-01", "R-IMP-02"] and ev.detail["unverified_quotes"] == ["R-IMP-02"]
        calls = s.exec(select(AICall).where(AICall.purpose == "import_pdf")).all()
        assert calls and all(c.provider == "llm" and c.ok for c in calls)
    again = client.post(f"/api/brand/import-pdf/{job['import_id']}/confirm", json={"selected": ["P-01"]})
    assert again.status_code == 409
    with Session(get_engine()) as s:  # keep later tests on the seeded rule set
        for rule in s.exec(select(Rule).where(Rule.code.startswith("R-IMP-"))).all():
            s.delete(rule)
        s.commit()


def test_enabled_imported_rules_do_not_break_a_check(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_llm(monkeypatch)
    login(client, "admin")
    job = _upload(client)
    _wait(client, job["import_id"])
    r = client.post(f"/api/brand/import-pdf/{job['import_id']}/confirm", json={"selected": ["P-01", "P-02"], "enable": True})
    assert r.status_code == 200 and all(c["enabled"] for c in r.json()["created"])
    login(client, "creator")
    vid = client.post("/api/items", data={"title": "t", "channel": "instagram", "confidential": "false",
                                          "text": "India's #1 coffee. Order now."}).json()["version_id"]
    t0 = time.time()
    while time.time() - t0 < 20:
        status = client.get(f"/api/versions/{vid}").json()["check_status"]
        if status in ("done", "error"):
            break
        time.sleep(0.05)
    assert status == "done"
    with Session(get_engine()) as s:
        for rule in s.exec(select(Rule).where(Rule.code.startswith("R-IMP-"))).all():
            s.delete(rule)
        s.commit()


def test_unknown_ids_and_not_ready(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_llm(monkeypatch)
    login(client, "admin")
    job = _upload(client)
    _wait(client, job["import_id"])
    r = client.post(f"/api/brand/import-pdf/{job['import_id']}/confirm", json={"selected": ["P-99"]})
    assert r.status_code == 422 and "P-99" in r.json()["detail"]
    assert client.get("/api/brand/import-pdf/nope").status_code == 404


def test_llm_failure_is_an_error_not_empty_proposals(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_llm(monkeypatch, fail=True)
    login(client, "admin")
    out = _wait(client, _upload(client)["import_id"])
    assert out["status"] == "error" and out["message"]
    assert client.post(f"/api/brand/import-pdf/{out['import_id']}/confirm", json={"selected": ["P-01"]}).status_code == 409


def test_admin_only_and_feature_flag(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_llm(monkeypatch)
    login(client, "creator")
    r = client.post("/api/brand/import-pdf", files={"file": ("g.pdf", PDF.read_bytes(), "application/pdf")})
    assert r.status_code == 403
    login(client, "admin")
    off = get_settings().model_copy(update={"feature_pdf_import": False})
    monkeypatch.setattr(innovations, "get_settings", lambda: off)
    r = client.post("/api/brand/import-pdf", files={"file": ("g.pdf", PDF.read_bytes(), "application/pdf")})
    assert r.status_code == 501 and "switched off" in r.json()["detail"]
    assert client.get("/api/brand/import-pdf/x").status_code == 501
