"""jev.py: semaphore, logging, errors-as-errors, retry policy, image preparation. Jev is faked here only."""
from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.pipeline import jev
from app.pipeline.questions import IMAGE_HEADERS
from typesafe_sdk import Noul


def fake_response(nouls: dict[str, float] | None = None, choices: dict[str, str] | None = None, tokens: int = 400):
    return SimpleNamespace(
        model="jev-1.14.0",
        nouls={k: SimpleNamespace(noul=v) for k, v in (nouls or {}).items()},
        choices={k: SimpleNamespace(choice=v, confidence=0.9, probabilities={v: 0.9}) for k, v in (choices or {}).items()},
        usage=SimpleNamespace(input_tokens=tokens),
    )


@pytest.fixture
def jev_on(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(jev, "available", lambda: True)
    return monkeypatch


def test_unavailable_returns_error_without_calling(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(jev, "available", lambda: False)

    async def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("must not call")

    monkeypatch.setattr(jev, "_call_api", boom)
    log = jev.CallLog()
    r = asyncio.run(jev.ask("x", {"q": Noul(instructions="?")}, purpose="rules", calls=log))
    assert not r.ok and r.error == "Jev not configured" and log.records == []


def test_ask_normalizes_and_logs_cost(jev_on) -> None:  # noqa: ANN001
    async def fake(state, questions, **kw):  # noqa: ANN001, ANN003, ANN202
        return fake_response({"a": 0.9}, {"tone": "warm"}, tokens=1_000_000)

    jev_on.setattr(jev, "_call_api", fake)
    log = jev.CallLog(version_id=7)
    r = asyncio.run(jev.ask({"text": "hi"}, {"a": Noul(instructions="?"), "tone": Noul(instructions="?")},
                            purpose="rules", calls=log, unit_id=3))
    assert r.ok and r.nouls == {"a": 0.9} and r.choices["tone"].choice == "warm"
    rec = log.records[0]
    assert (rec.provider, rec.model, rec.purpose, rec.version_id, rec.unit_id, rec.input_tokens) == ("typesafe", "jev-1.14.0", "rules", 7, 3, 1_000_000)
    assert rec.cost_usd == pytest.approx(0.042) and log.judgments == 2
    assert "hi" not in json.dumps(rec.__dict__)  # never content


def test_failure_is_an_error_not_a_no(jev_on) -> None:  # noqa: ANN001
    from typesafe_sdk import TypeSafeRateLimitError
    import httpx2

    async def fake(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise TypeSafeRateLimitError(429, {"error": "slow down"}, httpx2.Headers())

    jev_on.setattr(jev, "_call_api", fake)
    log = jev.CallLog()
    r = asyncio.run(jev.ask("secret text", {"q": Noul(instructions="?")}, purpose="rules", calls=log))
    assert not r.ok and r.nouls == {} and "429" in (r.error or "") and "secret" not in (r.error or "")
    assert log.records[0].ok is False


def test_missing_answers_are_an_error(jev_on) -> None:  # noqa: ANN001
    async def fake(*a, **k):  # noqa: ANN002, ANN003, ANN202
        return fake_response({"a": 0.2})

    jev_on.setattr(jev, "_call_api", fake)
    r = asyncio.run(jev.ask("x", {"a": Noul(instructions="?"), "b": Noul(instructions="?")}, purpose="rules"))
    assert not r.ok


def test_semaphore_limits_concurrency_to_8(jev_on) -> None:  # noqa: ANN001
    state = {"now": 0, "max": 0}

    async def fake(*a, **k):  # noqa: ANN002, ANN003, ANN202
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return fake_response({"q": 0.1})

    jev_on.setattr(jev, "_call_api", fake)

    async def main() -> None:
        await asyncio.gather(*[jev.ask("x", {"q": Noul(instructions="?")}, purpose="rules") for _ in range(30)])

    asyncio.run(main())
    assert state["max"] == jev.CONCURRENCY


def test_image_calls_send_danger_zone_header(jev_on) -> None:  # noqa: ANN001
    seen = {}

    async def fake(state, questions, *, model, extra_headers, timeout):  # noqa: ANN001, ANN202
        seen["headers"], seen["timeout"] = extra_headers, timeout
        return fake_response({"q": 0.1})

    jev_on.setattr(jev, "_call_api", fake)
    asyncio.run(jev.ask({"image": "img"}, {"q": Noul(instructions="?")}, purpose="rules", images=True))
    assert seen == {"headers": IMAGE_HEADERS, "timeout": 20.0}


def test_sdk_retries_exactly_once_on_429_and_5xx() -> None:
    import httpx2

    ok_body = {"model": "jev-1.14.0", "answers": {"q": {"type": "noul", "noul": 0.8}}, "usage": {"input_tokens": 10, "output_tokens": 1}}

    def run(statuses: list[int]) -> tuple[int, object]:
        calls = {"n": 0}

        def handler(request):  # noqa: ANN001, ANN202
            st = statuses[min(calls["n"], len(statuses) - 1)]
            calls["n"] += 1
            if st == 200:
                return httpx2.Response(200, json=ok_body)
            return httpx2.Response(st, json={"error": "nope"}, headers={"retry-after-ms": "1"})

        async def go():  # noqa: ANN202
            client = jev._make_client(api_key="test-key", transport=httpx2.MockTransport(handler))
            try:
                return await client.system_one(state="x", questions={"q": Noul(instructions="?")})
            except Exception as exc:  # noqa: BLE001
                return exc

        res = asyncio.run(go())
        return calls["n"], res

    n, res = run([429, 200])
    assert n == 2 and res.nouls["q"].noul == 0.8
    n, res = run([503, 503, 200])
    assert n == 2 and isinstance(res, Exception)
    n, res = run([400, 200])
    assert n == 1 and isinstance(res, Exception)


@pytest.mark.parametrize("size,fmt", [((100, 60), "PNG"), ((3000, 500), "JPEG"), ((800, 600), "WEBP")])
def test_prepare_image_fits_api_limits(tmp_path, size, fmt) -> None:  # noqa: ANN001
    p = tmp_path / f"x.{fmt.lower()}"
    Image.new("RGB", size, "#F28C28").save(p, format=fmt)
    data = jev.prepare_image(str(p))
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "PNG"
        assert all(jev.MIN_SIDE <= s <= jev.MAX_SIDE for s in im.size)
