"""P1-2 Logo fidelity: is the logo in an image the approved logo file? Local only (OpenCV), so it
also runs for confidential items. OWNER: AI/ML (Jiteesh).

Method: SIFT keypoints, Lowe ratio test (0.75), RANSAC homography, inlier count.

    match_logo(image_path, logo_path) -> LogoMatch      # pure measurement, no decisions
    decide(match, has_logo_p) -> LogoVerdict            # the truth table (code decides)

Why a geometry check sits in front of the inlier thresholds (measured 7 Nov on the seed images):
raw RANSAC inlier counts of 11-31 also come from images with NO logo at all (a cream ad with the
logo painted out: 11; plain text on a blank canvas: 17-31). Those homographies are degenerate:
they squash the whole logo onto a point. So inliers only count when the projected logo outline
is a real quadrilateral (convex, at least MIN_SIDE_PX per side, a sane area). With that guard,
no-logo images score 0, the approved logo scores ~296, a 60 px copy ~23 and a 1.6x stretch ~44.

The redrawn logo in `ad_wronglogo.png` scores 13 raw inliers but its homography collapses to a
point, so SIFT alone reads it as "no approved logo here". Saying *altered* needs a second input:
Jev's `has_logo` Noul ("does the image show a logo?"). Visible logo + no geometric match =
"Logo looks redrawn or altered". Without Jev (confidential items), code can only say "No approved
logo found", in the human-look band.
"""
from __future__ import annotations

import functools
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

import numpy as np

# ------------------------------------------------------------------ thresholds (code decides)
RATIO = 0.75  # Lowe ratio test
RANSAC_REPROJ_PX = 5.0
GENUINE_AT = 40  # geometric inliers >= this: the approved logo
POSSIBLE_AT = 10  # 10..39 with a valid outline: possibly altered (or very small), needs a human look
MAX_ASPECT_DEVIATION = 0.10  # projected width/height vs the logo's own, beyond this = stretched
MIN_SIDE_PX = 24  # each side of the projected outline, in scene pixels
MIN_AREA_FRAC = 0.0015  # projected outline must cover at least 0.15% of the image ...
MAX_AREA_FRAC = 1.5  # ... and not hugely more than all of it
MAX_SCENE_SIDE = 1600  # hard upper bound; settings.sift_max_side (default 800) is the working cap (memory)
COLOUR_MIN_SHARE = 0.08  # colour clusters inside the logo smaller than this are ignored (anti-aliasing)
DEFAULT_COLOUR_DELTA_E = 12.0  # same default as the palette rule (R-VIS-02 params.palette_delta_e)
HAS_LOGO_FLAG_AT = 0.70  # Jev `has_logo` p >= this + no geometric match -> "altered" (flag band)
HAS_LOGO_HUMAN_AT = 0.30  # 0.30..0.70 -> "altered" in the human band; below -> no logo seen

Verdict = Literal["genuine", "altered", "not_found"]
# Said when Jev sees a logo but RANSAC found no valid placement (no box/quad can be returned, e.g. ad_wronglogo.png).
NO_OUTLINE = " It doesn't line up closely enough with the approved logo to outline where it is."


@dataclass
class LogoMatch:
    """Measurements only. `quad` is the approved logo's corners (TL, TR, BR, BL) projected into the
    image, as [x, y] fractions of width/height; `box` is its bounding box (top, left, bottom, right)."""

    inliers: int  # geometric inliers: 0 when the homography is degenerate
    raw_inliers: int  # RANSAC inliers before the geometry check (for transparency/debugging)
    good_matches: int  # matches that passed the ratio test
    valid_outline: bool
    quad: Optional[list[list[float]]] = None
    box: Optional[dict[str, float]] = None
    aspect_deviation: Optional[float] = None  # |projected aspect / logo aspect - 1|
    colours: list[dict] = field(default_factory=list)  # inside the logo: {hex, share, delta_e, nearest}
    off_palette: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LogoVerdict:
    verdict: Verdict
    band: Literal["flag", "human", "pass"]
    reason: str  # plain words, shown in the finding
    stretched: bool = False


# ------------------------------------------------------------------ helpers
TILE_MARGIN = 64  # px of context around each SIFT tile; keypoints are kept only from the tile's core


def sift_max_side() -> int:
    """Largest piece of an image one SIFT pass sees. SIFT needs ~225 bytes per pixel at its peak (it
    doubles the image first): 1080 px ~265 MiB, 640 px ~95 MiB. Bigger images are cut into
    overlapping tiles instead of being downscaled, so small logos keep their detail (DECISIONS.md,
    "Memory budget on Render free tier")."""
    from app.config import get_settings

    return max(256, min(MAX_SCENE_SIDE, int(get_settings().sift_max_side)))


def _fit(img: np.ndarray, side: int) -> np.ndarray:
    from app.pipeline.limits import cv2 as _cv

    h0, w0 = img.shape[:2]
    f = min(1.0, side / max(h0, w0))
    if f >= 1.0:
        return img
    return _cv().resize(img, (max(1, round(w0 * f)), max(1, round(h0 * f))), interpolation=_cv().INTER_AREA)


def _spans(n: int, core: int) -> list[tuple[int, int]]:
    k = max(1, -(-n // core))  # ceil
    step = -(-n // k)
    return [(i * step, min(n, (i + 1) * step)) for i in range(k)]


def sift_features(gray: np.ndarray, max_side: int) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """SIFT keypoint positions (N x 2, float32) and descriptors. Images whose long side exceeds
    `max_side` are processed in overlapping tiles (core + TILE_MARGIN of context), keeping each
    keypoint only from the tile whose core contains it, so peak memory follows the tile size."""
    from app.pipeline.limits import cv2 as _cv

    cv2 = _cv()
    h, w = gray.shape[:2]
    if max(h, w) <= max_side:
        kps, desc = cv2.SIFT_create().detectAndCompute(gray, None)
        pts = np.float32([k.pt for k in kps]) if kps else np.zeros((0, 2), np.float32)
        return pts, desc
    core = max(64, max_side - 2 * TILE_MARGIN)
    all_pts: list[np.ndarray] = []
    all_desc: list[np.ndarray] = []
    for y0, y1 in _spans(h, core):
        for x0, x1 in _spans(w, core):
            ty0, ty1, tx0, tx1 = max(0, y0 - TILE_MARGIN), min(h, y1 + TILE_MARGIN), max(0, x0 - TILE_MARGIN), min(w, x1 + TILE_MARGIN)
            kps, desc = cv2.SIFT_create().detectAndCompute(np.ascontiguousarray(gray[ty0:ty1, tx0:tx1]), None)
            if not kps or desc is None:
                continue
            pts = np.float32([k.pt for k in kps]) + np.float32([tx0, ty0])
            keep = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (pts[:, 1] >= y0) & (pts[:, 1] < y1)
            if keep.any():
                all_pts.append(pts[keep])
                all_desc.append(desc[keep])
            del kps, desc
    if not all_pts:
        return np.zeros((0, 2), np.float32), None
    return np.concatenate(all_pts), np.concatenate(all_desc)


@functools.lru_cache(maxsize=8)
def _logo_features(path: str, mtime: float, side: int = MAX_SCENE_SIDE):  # noqa: ANN202  (cv2 types)
    from app.pipeline.limits import cv2 as _cv

    cv2 = _cv()
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError("approved logo could not be read")
    img = _fit(img, side)  # downscaled, not tiled: the whole logo must stay in view for its coarse-scale keypoints
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    pts, desc = sift_features(gray, side)
    colours = _clusters(cv2.cvtColor(img, cv2.COLOR_BGR2RGB).reshape(-1, 3))
    return pts, desc, gray.shape, colours


def _clusters(px: np.ndarray, k: int = 5) -> list[tuple[tuple[float, float, float], float]]:
    """Deterministic k-means on RGB pixels -> [(rgb, share)] sorted by share."""
    import cv2

    px = np.asarray(px, np.float32).reshape(-1, 3)
    if len(px) > 40_000:
        px = px[:: len(px) // 40_000 + 1]
    k = max(1, min(k, len(np.unique(px, axis=0))))
    cv2.setRNGSeed(12345)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centres = cv2.kmeans(px, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    shares = np.bincount(labels.ravel(), minlength=k) / len(px)
    order = np.argsort(-shares)
    return [(tuple(float(c) for c in centres[i]), float(shares[i])) for i in order if shares[i] > 0]


def outline_ok(quad: np.ndarray, scene_w: int, scene_h: int) -> bool:
    """A real logo placement projects to a convex quadrilateral of sane size."""
    import cv2

    if quad is None or not np.all(np.isfinite(quad)):
        return False
    q = quad.reshape(-1, 2).astype(np.float32)
    if not cv2.isContourConvex(q):
        return False
    sides = [float(np.linalg.norm(q[i] - q[(i + 1) % 4])) for i in range(4)]
    if min(sides) < MIN_SIDE_PX:
        return False
    area = abs(cv2.contourArea(q)) / float(scene_w * scene_h)
    return MIN_AREA_FRAC <= area <= MAX_AREA_FRAC


def _aspect_deviation(quad: np.ndarray, logo_w: int, logo_h: int) -> float:
    q = quad.reshape(-1, 2)
    s = [float(np.linalg.norm(q[i] - q[(i + 1) % 4])) for i in range(4)]
    width, height = (s[0] + s[2]) / 2, (s[1] + s[3]) / 2
    return abs((width / max(height, 1e-6)) / (logo_w / logo_h) - 1.0)


def logo_colours(scene_rgb: np.ndarray, H: np.ndarray, logo_shape: tuple[int, int],
                 palette: list[tuple[str, str]], approved: list[tuple[tuple[float, float, float], float]],
                 threshold: float) -> tuple[list[dict], list[dict]]:
    """Warp the logo region back into the logo's own frame, cluster its colours and compare each
    big cluster (CIEDE2000) with the brand palette AND the approved logo's own colours (the
    approved file is on-brand by definition, e.g. its mid-brown coffee surface)."""
    import cv2

    from app.pipeline.tier0 import ciede2000, hex_to_rgb, rgb_to_hex, rgb_to_lab

    lh, lw = logo_shape
    size = (min(lw, 240), min(lh, 240))
    scale = np.diag([size[0] / lw, size[1] / lh, 1.0])
    warped = cv2.warpPerspective(scene_rgb, scale @ np.linalg.inv(H), size, flags=cv2.INTER_AREA)
    refs = [(name, hx, rgb_to_lab(hex_to_rgb(hx))) for name, hx in palette]
    refs += [("approved logo", rgb_to_hex(rgb), rgb_to_lab(rgb)) for rgb, share in approved if share >= 0.01]
    colours, off = [], []
    for rgb, share in _clusters(warped.reshape(-1, 3)):
        lab = rgb_to_lab(rgb)
        d, name, _hx = min(((ciede2000(lab, r[2]), r[0], r[1]) for r in refs), default=(float("inf"), "", ""))
        c = {"hex": rgb_to_hex(rgb), "share": round(share, 3), "delta_e": round(d, 1), "nearest": name}
        colours.append(c)
        if share >= COLOUR_MIN_SHARE and d > threshold:
            off.append(c)
    return colours, off


# ------------------------------------------------------------------ measurement
def match_logo(image_path: str, logo_path: str, *, palette: Optional[list[tuple[str, str]]] = None,
               colour_threshold: float = DEFAULT_COLOUR_DELTA_E, ratio: float = RATIO) -> LogoMatch:
    """SIFT on the approved logo vs the image, ratio test, RANSAC homography, geometry check,
    then the colour check inside the projected logo. Never raises: errors come back in `.error`.
    One SIFT pass at a time per process (limits.SIFT_LOCK): each pass briefly needs 100+ MiB."""
    from app.pipeline import limits

    t0 = time.perf_counter()
    with limits.SIFT_LOCK:
        try:
            return _match_logo(image_path, logo_path, palette, colour_threshold, ratio, t0)
        finally:
            limits.trim()


def _match_logo(image_path: str, logo_path: str, palette: Optional[list[tuple[str, str]]], colour_threshold: float,
                ratio: float, t0: float) -> LogoMatch:
    from app.pipeline.limits import cv2 as _cv

    cv2 = _cv()
    try:
        side = sift_max_side()
        pts1, d1, (lh, lw), approved_colours = _logo_features(logo_path, os.path.getmtime(logo_path), side)
        scene = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if scene is None:
            raise ValueError("image could not be read")
        scene = _fit(scene, MAX_SCENE_SIDE)
        sh, sw = scene.shape[:2]
        pts2, d2 = sift_features(cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY), side)
        out = LogoMatch(inliers=0, raw_inliers=0, good_matches=0, valid_outline=False)
        if d1 is None or d2 is None or len(pts2) < 2:
            out.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return out
        pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < ratio * p[1].distance]
        out.good_matches = len(good)
        if len(good) >= 4:
            src = pts1[[m.queryIdx for m in good]].reshape(-1, 1, 2)
            dst = pts2[[m.trainIdx for m in good]].reshape(-1, 1, 2)
            cv2.setRNGSeed(12345)  # RANSAC is randomised; make results repeatable
            H, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_REPROJ_PX)
            if H is not None and mask is not None:
                out.raw_inliers = int(mask.sum())
                corners = np.float32([[0, 0], [lw, 0], [lw, lh], [0, lh]]).reshape(-1, 1, 2)
                quad = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
                if outline_ok(quad, sw, sh):
                    out.valid_outline = True
                    out.inliers = out.raw_inliers
                    fx = np.clip(quad[:, 0] / sw, 0.0, 1.0)
                    fy = np.clip(quad[:, 1] / sh, 0.0, 1.0)
                    out.quad = [[round(float(x), 4), round(float(y), 4)] for x, y in zip(fx, fy)]
                    out.box = {"top": round(float(fy.min()), 4), "left": round(float(fx.min()), 4),
                               "bottom": round(float(fy.max()), 4), "right": round(float(fx.max()), 4)}
                    out.aspect_deviation = round(_aspect_deviation(quad, lw, lh), 3)
                    if palette:
                        rgb = cv2.cvtColor(scene, cv2.COLOR_BGR2RGB)
                        out.colours, out.off_palette = logo_colours(rgb, H, (lh, lw), palette, approved_colours,
                                                                    colour_threshold)
        out.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return out
    except Exception as exc:  # noqa: BLE001  (unreadable files, OpenCV errors)
        return LogoMatch(inliers=0, raw_inliers=0, good_matches=0, valid_outline=False,
                         elapsed_ms=int((time.perf_counter() - t0) * 1000), error=type(exc).__name__)


# ------------------------------------------------------------------ the truth table
def decide(m: LogoMatch, has_logo_p: Optional[float] = None) -> LogoVerdict:
    """Code decides. `has_logo_p` is Jev's "does the image show a logo?" (None = not asked, e.g.
    confidential items or Jev unavailable).

    | geometric match            | Jev has_logo | verdict    | band  |
    |----------------------------|--------------|------------|-------|
    | >= 40, aspect ok           | any          | genuine    | pass  |
    | >= 40, stretched > 10%     | any          | altered    | flag  |
    | 10-39 (valid outline)      | any          | altered    | human |
    | none / < 10                | >= 0.70      | altered    | flag  |
    | none / < 10                | 0.30-0.70    | altered    | human |
    | none / < 10                | < 0.30       | not_found  | pass  |
    | none / < 10                | not asked    | not_found  | human |
    """
    if m.valid_outline and m.inliers >= GENUINE_AT:
        if (m.aspect_deviation or 0.0) > MAX_ASPECT_DEVIATION:
            return LogoVerdict("altered", "flag", f"The approved logo is there ({m.inliers} matching points) but "
                               f"stretched by about {m.aspect_deviation:.0%}.", stretched=True)
        return LogoVerdict("genuine", "pass", f"Matches the approved logo file ({m.inliers} matching points).")
    if m.valid_outline and m.inliers >= POSSIBLE_AT:
        return LogoVerdict("altered", "human", f"Only {m.inliers} points match the approved logo (a genuine copy "
                           f"usually has {GENUINE_AT}+). It may be altered, or just very small.")
    if has_logo_p is not None and has_logo_p >= HAS_LOGO_FLAG_AT:
        return LogoVerdict("altered", "flag", "The image shows a logo, but it doesn't match the approved logo "
                           "file, so it looks redrawn or altered." + NO_OUTLINE)
    if has_logo_p is not None and has_logo_p >= HAS_LOGO_HUMAN_AT:
        return LogoVerdict("altered", "human", "The image may show a logo, but nothing in it matches the approved "
                           "logo file." + NO_OUTLINE)
    if has_logo_p is not None:
        return LogoVerdict("not_found", "pass", "No logo in this image.")
    return LogoVerdict("not_found", "human", "The approved logo wasn't found in this image. If the image shows a "
                       "logo, it may be redrawn or altered.")
