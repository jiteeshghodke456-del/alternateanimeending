"""Regression for the Render OOM kill (512 MiB): heavy jobs share MAX_CONCURRENT_CHECKS slots, SIFT
runs one pass at a time on tiles no bigger than SIFT_MAX_SIDE, the startup pre-run waits
PRERUN_DELAY_S, and images sent to Jev respect IMAGE_MAX_SIDE. No external calls."""
from __future__ import annotations

import asyncio
import io
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from app.config import get_settings
from app.pipeline import jev, limits, run
from app.pipeline import logo as L

SEED = Path(__file__).resolve().parents[1] / "app" / "seed"
LOGO = str(SEED / "logo.png")


@pytest.fixture
def env(monkeypatch):  # noqa: ANN001, ANN201
    def set_(**kw: Any) -> None:
        for k, v in kw.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()
        limits._sems.clear()
        jev._sems.clear()

    yield set_
    get_settings.cache_clear()
    limits._sems.clear()
    jev._sems.clear()


def test_check_slots_cap_concurrent_heavy_jobs(env) -> None:  # noqa: ANN001
    env(MAX_CONCURRENT_CHECKS=2)
    live, peak = [0], [0]

    async def job() -> None:
        async with limits.check_slot():
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            await asyncio.sleep(0.01)
            live[0] -= 1

    async def main() -> None:
        await asyncio.gather(*[job() for _ in range(6)])

    asyncio.run(main())
    assert peak[0] == 2


def test_run_check_waits_for_a_slot_and_always_clears_running(env, monkeypatch) -> None:  # noqa: ANN001
    env(MAX_CONCURRENT_CHECKS=1)
    live, peak = [0], [0]

    async def fake_inner(vid: int, **_: Any) -> None:
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.01)
        live[0] -= 1
        if vid == 3:
            raise RuntimeError("boom")

    monkeypatch.setattr(run, "_run_check", fake_inner)

    async def main() -> None:
        run._running.update({901, 902, 3})
        await asyncio.gather(run.run_check(901), run.run_check(902), run.run_check(3), return_exceptions=True)

    asyncio.run(main())
    assert peak[0] == 1
    assert not ({901, 902, 3} & run._running)


def test_prerun_waits_for_the_startup_delay(monkeypatch) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    scheduled: list[Any] = []
    slept: list[float] = []
    monkeypatch.setattr(run, "get_settings", lambda: SimpleNamespace(demo_mode=True, jev_configured=True))
    monkeypatch.setattr(run, "_schedule", scheduled.append)
    monkeypatch.setattr(run, "_prerun_started", False)

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        run.reset_prerun_flag()  # a demo reset during the wait: this pre-run must stand down

    monkeypatch.setattr(run.asyncio, "sleep", fake_sleep)
    assert run.start_prerun(delay_s=15) is True
    asyncio.run(scheduled[0])
    assert slept == [15]


def test_sift_tiles_bound_the_piece_size_and_keep_the_verdicts(env, monkeypatch) -> None:  # noqa: ANN001
    env(SIFT_MAX_SIDE=400)
    seen: list[tuple[int, int]] = []
    real = L.sift_features

    def spy(gray: np.ndarray, side: int):  # noqa: ANN202
        assert side == 400
        return real(gray, side)

    import cv2

    orig_create = cv2.SIFT_create

    class Wrapped:
        def __init__(self) -> None:
            self.s = orig_create()

        def detectAndCompute(self, img: np.ndarray, mask: Any):  # noqa: ANN202, N802
            seen.append(img.shape[:2])
            return self.s.detectAndCompute(img, mask)

    monkeypatch.setattr(cv2, "SIFT_create", Wrapped)
    monkeypatch.setattr(L, "sift_features", spy)
    L._logo_features.cache_clear()
    good = L.match_logo(str(SEED / "content" / "ad_good.png"), LOGO)
    wrong = L.match_logo(str(SEED / "content" / "ad_wronglogo.png"), LOGO)
    L._logo_features.cache_clear()
    assert seen and max(max(s) for s in seen) <= 400
    assert L.decide(good).verdict == "genuine" and good.inliers >= 100
    assert wrong.inliers == 0


def test_sift_tiling_keeps_each_keypoint_once() -> None:
    rng = np.random.default_rng(0)
    gray = (rng.random((900, 1100)) * 255).astype(np.uint8)
    gray = np.asarray(Image.fromarray(gray).resize((550, 450)).resize((1100, 900)))
    pts, desc = L.sift_features(gray, 400)
    assert desc is not None and len(pts) == len(desc) > 0
    assert pts[:, 0].max() < 1100 and pts[:, 1].max() < 900
    whole, _ = L.sift_features(gray, 2000)  # one pass, no tiles
    assert 0.85 * len(whole) <= len(pts) <= 1.15 * len(whole)  # overlaps are not double-counted


def test_sift_lock_serialises_passes(monkeypatch) -> None:  # noqa: ANN001
    live, peak = [0], [0]
    lock = threading.Lock()

    def slow(*_: Any, **__: Any) -> L.LogoMatch:
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.02)
        with lock:
            live[0] -= 1
        return L.LogoMatch(inliers=0, raw_inliers=0, good_matches=0, valid_outline=False)

    monkeypatch.setattr(L, "_match_logo", slow)
    ts = [threading.Thread(target=L.match_logo, args=("x", "y")) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1


def test_prepare_image_respects_image_max_side(env, tmp_path: Path) -> None:  # noqa: ANN001
    env(IMAGE_MAX_SIDE=600)
    p = tmp_path / "big.png"
    Image.new("RGB", (1500, 900), (200, 120, 40)).save(p)
    with Image.open(io.BytesIO(jev.prepare_image(str(p)))) as im:
        assert max(im.size) == 600 and im.format == "PNG"


def test_jev_concurrency_comes_from_settings(env) -> None:  # noqa: ANN001
    env(JEV_CONCURRENCY=3)

    async def main() -> int:
        return jev._semaphore()._value

    assert asyncio.run(main()) == 3


def test_trim_never_raises() -> None:
    limits.trim()
    limits.trim()
