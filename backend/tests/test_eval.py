"""Eval runner: span matching, metrics, tuning guard, and the eval API."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.eval import run as ER
from app.routers import eval as eval_router
from conftest import login


def test_label_spans_and_overlap_matching() -> None:
    text = "A cheap thrill. Also a cheap way."
    labels = ER.label_spans(text, [{"code": "R-TERM-02", "span": "cheap thrill"}, {"code": "R-TERM-02", "span": "cheap way"}])
    assert [(lab["start"], lab["end"]) for lab in labels] == [(2, 14), (23, 32)]
    preds = [ER.Pred({"R-TERM-02"}, "terminology", "flag", 2, 7, "cheap", 1.0),
             ER.Pred({"R-TERM-01"}, "terminology", "flag", 23, 28, "cheap", 1.0),
             ER.Pred({"R-CLAIM-02", "F-13"}, "claims", "flag", None, None, "", 0.9)]
    p_ok, l_ok = ER.match(preds, labels)
    assert p_ok == [True, False, False] and l_ok == [True, False]
    # a finding carries rule and fact code: either label code matches
    p_ok, _ = ER.match(preds[2:], [{"code": "F-13", "start": 0, "end": 3}])
    assert p_ok == [True]


def test_prf_and_percentile() -> None:
    assert ER.prf(3, 4, 3, 6) == {"precision": 0.75, "recall": 0.5, "f1": 0.6, "n_findings": 4, "n_labels": 6}
    assert ER.percentile([100, 200, 300, 400], 0.5) == 250


def test_expected_claim_status() -> None:
    labels = [{"code": "F-12", "start": 0, "end": 10}, {"code": "R-CLAIM-01", "start": 20, "end": 25}]
    assert ER.expected_claim_status(2, 5, labels) == "outdated"
    assert ER.expected_claim_status(21, 22, labels) == "unsupported"
    assert ER.expected_claim_status(40, 45, labels) == "supported"


def test_tier0_only_eval_runs_and_scores(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    out = asyncio.run(ER.evaluate("dev", use_jev=False, limit=8, write=False))
    m = out["metrics"]
    assert m["n_items"] == 8 and 0 <= m["precision"] <= 1 and 0 <= m["recall"] <= 1
    assert "latency_median_ms" in m and m["cost_per_item_usd"] == 0
    assert out["models"]["jev"].startswith("not configured")


def test_tune_refuses_test_split() -> None:
    with pytest.raises(SystemExit):
        ER.main(["--split", "test", "--tune"])


def test_eval_latest_not_run_and_run_is_admin_only(client: TestClient, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(eval_router, "LATEST", tmp_path / "latest.json")
    login(client, "creator")
    r = client.get("/api/eval/latest").json()
    assert r["available"] is False and r["status"] == "not_run"
    assert client.post("/api/eval/run").status_code == 403
    (tmp_path / "latest.json").write_text(json.dumps({"run_at": "2026-11-07T10:00:00Z", "split": "test", "models": {"jev": "x"},
                                                     "metrics": {"f1": 0.5}, "misses": []}))
    r = client.get("/api/eval/latest").json()
    assert r["available"] is True and r["status"] == "ok" and r["metrics"]["f1"] == 0.5
