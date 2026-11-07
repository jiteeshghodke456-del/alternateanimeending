"""Fake Jev and LLM. They live ONLY in tests and replace the raw API calls (jev._call_api,
explain._chat), so everything else (semaphores, logging, parsing, bands) is the real code."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional


def _text_of(state: Any) -> str:
    if isinstance(state, dict):
        return " ".join(str(v) for v in state.values() if isinstance(v, str))
    return str(state)


@dataclass
class FakeJev:
    """Answers by question name. `rules`: {rule_code: p or callable(text)->p}. `support`: {fact_code: p}."""

    rules: dict[str, Any] = field(default_factory=dict)
    support: dict[str, float] = field(default_factory=dict)
    default: float = 0.05
    sleep: float = 0.0
    fail_images: bool = False
    fail_all: bool = False
    meaning: float = 0.95
    tokens: int = 430
    calls: list[dict] = field(default_factory=list)

    async def __call__(self, state: Any, questions: dict, *, model: Optional[str], extra_headers: Any, timeout: float) -> Any:
        self.calls.append({"state": state, "questions": list(questions), "headers": extra_headers})
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.fail_all or (self.fail_images and extra_headers):
            from typesafe_sdk import TypeSafeInternalServerError
            import httpx2

            raise TypeSafeInternalServerError(500, {"error": "boom"}, httpx2.Headers())
        text = _text_of(state)
        revised = state.get("revised") if isinstance(state, dict) else None
        nouls, choices = {}, {}
        for name, q in questions.items():
            if q.type == "choice":
                opts = list(q.criteria.keys())
                pick = "pushy" if ("now" in text.lower() and "pushy" in opts) else opts[0]
                choices[name] = SimpleNamespace(choice=pick, confidence=0.9, probabilities={pick: 0.9})
                continue
            if name.startswith("support_"):
                p = self.support.get(name[len("support_"):], 0.02)
            elif name in ("same_topic", "keeps_cta"):
                p = self.meaning
            elif name == "original_has_cta":
                p = 0.9
            elif name == "has_claim":
                p = 0.9 if re.search(r"\d|best|#1", text) else 0.1
            elif name in self.rules:
                v = self.rules[name]
                p = v(revised if revised is not None else text) if callable(v) else v
            else:
                p = self.default
            nouls[name] = SimpleNamespace(noul=float(p))
        return SimpleNamespace(model="jev-1.14.0", nouls=nouls, choices=choices, usage=SimpleNamespace(input_tokens=self.tokens))


@dataclass
class FakeLLM:
    """`fixes`: list of callables(excerpt) -> fix, used in order per excerpt (first try, retry)."""

    fix_for: Callable[[str, int], Optional[str]] = lambda excerpt, attempt: "a coffee we love"
    sleep: float = 0.0
    bad_json_first: int = 0
    calls: list[dict] = field(default_factory=list)

    async def __call__(self, messages: list, *, model: str) -> tuple[str, int, int]:
        user = messages[-1]["content"]
        excerpt = re.findall(r"<content>(.*?)</content>", user, re.S)
        quote = excerpt[1] if len(excerpt) > 1 else (excerpt[0] if excerpt else "")
        attempt = sum(1 for c in self.calls if c["quote"] == quote and c["ok"])
        self.calls.append({"quote": quote, "ok": True, "retry": "previous fix was rejected" in user})
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.bad_json_first > 0:
            self.bad_json_first -= 1
            self.calls[-1]["ok"] = False
            return "not json", 300, 20
        code = re.search(r"Brand rule (\S+)", user)
        return json.dumps({"explanation": f"Breaks {code.group(1) if code else 'a rule'}.", "fix": self.fix_for(quote, attempt)}), 300, 40


def install(monkeypatch, jev_fake: Optional[FakeJev] = None, llm_fake: Optional[FakeLLM] = None) -> None:  # noqa: ANN001
    from app.pipeline import explain, jev

    if jev_fake is not None:
        monkeypatch.setattr(jev, "available", lambda: True)
        monkeypatch.setattr(jev, "_call_api", jev_fake)
    if llm_fake is not None:
        monkeypatch.setattr(explain, "available", lambda: True)
        monkeypatch.setattr(explain, "_chat", llm_fake)
