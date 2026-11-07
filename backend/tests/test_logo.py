"""P1-2 logo fidelity: SIFT + RANSAC on the real seed images (local, no external calls), the
truth table in code, and the engine/API wiring (Jev faked for `has_logo`)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.pipeline import logo as L
from conftest import login
from fakes import FakeJev, FakeLLM, install
from pipeline_helpers import SEED

LOGO = str(SEED / "logo.png")
PALETTE = [(p["name"], p["hex"]) for p in json.loads((SEED / "palette.json").read_text())]


def ad(name: str) -> str:
    return str(SEED / "content" / f"{name}.png")


@pytest.fixture(scope="module")
def matches() -> dict[str, L.LogoMatch]:
    return {n: L.match_logo(ad(n), LOGO, palette=PALETTE) for n in ("ad_good", "ad_offpalette", "ad_wronglogo")}


def test_genuine_logo_matches_with_many_inliers(matches) -> None:  # noqa: ANN001
    for n in ("ad_good", "ad_offpalette"):
        m = matches[n]
        assert m.error is None and m.valid_outline
        assert m.inliers >= 200, (n, m.inliers)  # measured ~296 / ~293
        assert L.decide(m).verdict == "genuine"
        assert L.decide(m, has_logo_p=0.95).band == "pass"
        assert m.off_palette == []  # the approved logo's own colours are on-brand


def test_projected_quad_is_where_the_logo_was_pasted(matches) -> None:  # noqa: ANN001
    # seed/build/ads.py pastes the logo at x 60..440, y 640..1020 of a 1080 px ad
    m = matches["ad_good"]
    exp = {"top": 640 / 1080, "left": 60 / 1080, "bottom": 1020 / 1080, "right": 440 / 1080}
    for k, v in exp.items():
        assert m.box[k] == pytest.approx(v, abs=0.01), k
    (tlx, tly), (trx, _), (brx, bry), (blx, _) = m.quad
    assert tlx < trx and blx < brx and tly < bry
    assert m.aspect_deviation < 0.02


def test_redrawn_logo_has_no_geometric_match(matches) -> None:  # noqa: ANN001
    m = matches["ad_wronglogo"]
    assert m.inliers < L.POSSIBLE_AT and not m.valid_outline and m.quad is None
    # code alone can't tell "redrawn" from "no logo": human look, never a pass
    assert (L.decide(m).verdict, L.decide(m).band) == ("not_found", "human")
    # Jev sees a logo that isn't the approved file -> altered
    assert (L.decide(m, 0.9).verdict, L.decide(m, 0.9).band) == ("altered", "flag")
    assert L.decide(m, 0.5).band == "human"
    assert (L.decide(m, 0.1).verdict, L.decide(m, 0.1).band) == ("not_found", "pass")


def test_degenerate_homography_is_not_counted(tmp_path: Path) -> None:
    """Regression: plain text on a blank canvas scored 17-31 raw RANSAC inliers ("possibly
    altered") because the homography squashed the logo onto a point."""
    blank = np.full((1080, 1080, 3), 240, np.uint8)
    cv2.putText(blank, "Hello coffee", (100, 500), cv2.FONT_HERSHEY_SIMPLEX, 3, (30, 30, 30), 5)
    p = tmp_path / "blank.png"
    cv2.imwrite(str(p), blank)
    m = L.match_logo(str(p), LOGO)
    assert m.inliers == 0 and not m.valid_outline


def _paste(tmp_path: Path, name: str, w: int, h: int, recolour: bool = False) -> str:
    scene = cv2.imread(ad("ad_good"))
    scene[640:1020, 60:440] = scene[700, 700]  # paint the original logo out
    lg = cv2.resize(cv2.imread(LOGO), (w, h), interpolation=cv2.INTER_AREA)
    if recolour:
        hsv = cv2.cvtColor(lg, cv2.COLOR_BGR2HSV)
        hsv[..., 0] = (hsv[..., 0].astype(int) + 80) % 180
        lg = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    scene[650:650 + h, 60:60 + w] = lg
    p = tmp_path / f"{name}.png"
    cv2.imwrite(str(p), scene)
    return str(p)


def test_stretched_logo_is_altered(tmp_path: Path) -> None:
    m = L.match_logo(_paste(tmp_path, "stretch", 420, 300), LOGO)
    v = L.decide(m)
    assert m.inliers >= L.GENUINE_AT and v.verdict == "altered" and v.stretched and v.band == "flag"


def test_recoloured_logo_is_off_palette(tmp_path: Path) -> None:
    m = L.match_logo(_paste(tmp_path, "recol", 300, 300, recolour=True), LOGO, palette=PALETTE)
    assert L.decide(m).verdict == "genuine"  # same drawing ...
    assert m.off_palette and all(c["delta_e"] > 12 for c in m.off_palette)  # ... wrong colours


def test_unreadable_image_is_an_error_not_a_verdict(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"not an image")
    assert L.match_logo(str(p), LOGO).error


# ------------------------------------------------------------------ engine + API
def _upload(client: TestClient, name: str, confidential: bool) -> int:
    login(client, "creator")
    r = client.post("/api/items", data={"title": name, "channel": "instagram", "confidential": str(confidential).lower(),
                                        "caption": "Slow mornings.", "alt_text": "A coffee ad with the brand logo"},
                    files={"file": ("ad.png", Path(ad(name)).read_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    vid = r.json()["version_id"]
    t0 = time.time()
    while client.get(f"/api/versions/{vid}").json()["check_status"] not in ("done", "error"):
        assert time.time() - t0 < 20
        time.sleep(0.05)
    return vid


def test_wrong_logo_flagged_with_jev(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = FakeJev(rules={"has_logo": 0.93})
    install(monkeypatch, fake, FakeLLM())
    vid = _upload(client, "ad_wronglogo", confidential=False)
    fs = client.get(f"/api/versions/{vid}/findings").json()
    logo = [f for f in fs if f["tier"] == "logo"]
    assert len(logo) == 1
    f = logo[0]
    assert f["rule_code"] == "R-VIS-01" and f["severity"] == "major" and f["band"] == "flag"
    assert f["explanation"].startswith("Logo looks redrawn or altered.")
    assert f["rule"]["page"] == 8
    assert any("has_logo" in c["questions"] for c in fake.calls)
    stats = client.get(f"/api/versions/{vid}").json()["stats"]
    assert stats["logo"][0]["verdict"] == "altered" and stats["logo"][0]["has_logo"] == pytest.approx(0.93)


def test_genuine_logo_passes_and_stats_carry_the_quad(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(rules={"has_logo": 0.95}), FakeLLM())
    vid = _upload(client, "ad_good", confidential=False)
    fs = client.get(f"/api/versions/{vid}/findings").json()
    assert not [f for f in fs if f["tier"] == "logo"]
    lg = client.get(f"/api/versions/{vid}").json()["stats"]["logo"][0]
    assert lg["verdict"] == "genuine" and lg["inliers"] >= 200 and len(lg["quad"]) == 4
    assert set(lg["box"]) == {"top", "left", "bottom", "right"}


def test_confidential_runs_logo_locally_without_external_calls(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = FakeJev()
    install(monkeypatch, fake, FakeLLM())
    vid = _upload(client, "ad_wronglogo", confidential=True)
    assert fake.calls == []
    fs = [f for f in client.get(f"/api/versions/{vid}/findings").json() if f["tier"] == "logo"]
    assert len(fs) == 1 and fs[0]["band"] == "human" and fs[0]["explanation"].startswith("No approved logo found.")


def test_recoloured_logo_finding_has_box(client: TestClient, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(rules={"has_logo": 0.95}), FakeLLM())
    login(client, "creator")
    data = Path(_paste(tmp_path, "recol", 300, 300, recolour=True)).read_bytes()
    r = client.post("/api/items", data={"title": "recol", "channel": "instagram", "confidential": "true", "caption": "Hi there."},
                    files={"file": ("ad.png", data, "image/png")})
    vid = r.json()["version_id"]
    t0 = time.time()
    while client.get(f"/api/versions/{vid}").json()["check_status"] not in ("done", "error"):
        assert time.time() - t0 < 20
        time.sleep(0.05)
    fs = [f for f in client.get(f"/api/versions/{vid}/findings").json() if f["tier"] == "logo"]
    assert len(fs) == 1 and fs[0]["severity"] == "minor" and fs[0]["explanation"].startswith("Logo colour off-palette.")
    assert fs[0]["box"]["left"] == pytest.approx(60 / 1080, abs=0.01) and len(fs[0]["quad"]) == 4
