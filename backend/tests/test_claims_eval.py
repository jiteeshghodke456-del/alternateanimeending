"""The claim-flagging eval set and its scoring (app/eval/claims_run.py)."""
from __future__ import annotations

import asyncio

from app.eval import claims_run as R

VALID = {"flag", "human", "none"}


def test_dataset_is_well_formed() -> None:
    rows = R.load_rows(resolve_refs=False)  # offline: real-brand rows keep only a reference to the page
    facts = {f.code for f in R.load_facts()}
    assert 40 <= len(rows) <= 60 and len({r["id"] for r in rows}) == len(rows)
    assert {r["source"] for r in rows} == {"swiggy_blog", "synthetic"} and {r["split"] for r in rows} == {"dev", "test"}
    for r in rows:
        assert r["labelled_by"] == "draft-ai" and set(r["facts"]) <= facts
        if r["source"] == "swiggy_blog":  # third-party text is never stored, only a span of the live page
            assert not r["text"] and {"url", "start", "end", "sha1"} <= set(r["ref"])
            continue
        for lab in R.label_rows(r["text"], r["labels"]):
            assert lab["band"] in VALID and lab["end"] > lab["start"]


def test_scoring_counts_dragged_down_figures_and_false_alarms() -> None:
    text = "Rajkot 10x, Ludhiana 7x."
    labels = R.label_rows(text, [{"span": "10x", "band": "none"}, {"span": "7x", "band": "human"}])
    one_big = [{"start": 7, "end": 22, "band": "flag"}]  # the whole clause, as before atomic claims
    rows = R.score_item(labels, one_big)
    assert [(r["expected"], r["predicted"]) for r in rows] == [("none", "flag"), ("human", "flag")]
    atomic = [{"start": 21, "end": 23, "band": "human"}]
    assert [(r["expected"], r["predicted"]) for r in R.score_item(labels, atomic)] == [("none", "none"), ("human", "human")]
    stray = R.score_item([], [{"start": 0, "end": 6, "band": "flag", "quote": "Rajkot"}])
    assert [(r["expected"], r["predicted"]) for r in stray] == [("none", "flag")]
    m = R.metrics(rows)
    assert m["flag"]["precision"] == 0.0 and m["shown"]["recall"] == 1.0 and m["band_accuracy"] == 0.0


def test_runner_works_code_only() -> None:
    ids = {r["id"] for r in R.load_rows(resolve_refs=False) if r["source"] == "synthetic"}  # no network in tests
    out = asyncio.run(R.evaluate("all", use_jev=False, limit=6, ids=ids))
    assert out["n_items"] == 6 and out["jev_calls"] == 0 and out["metrics"]["all"]["n_rows"] >= 6
