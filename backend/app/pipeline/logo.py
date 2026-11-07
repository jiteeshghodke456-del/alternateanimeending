"""P1 Logo fidelity: SIFT + RANSAC (packfix method, credit in NOTICE.md). OWNER: pipeline-agent.

Intended API (local only; also runs for confidential items):

    @dataclass
    class LogoMatch:
        inliers: int
        verdict: Literal["genuine", "altered", "not_found"]   # >=40 / 10-39 / <10
        quad: list[tuple[float, float]] | None                # projected corners, as image fractions
        colour_hex: str | None                                # mean colour inside the quad

    def match_logo(image_path: str, logo_path: str, ratio: float = 0.75) -> LogoMatch
        cv2.SIFT_create(); BFMatcher knnMatch k=2 + Lowe ratio test; cv2.findHomography(RANSAC).
    The approved logo path: Asset(kind="logo", meta.variant="approved").path
"""
from __future__ import annotations


def match_logo(image_path: str, logo_path: str, ratio: float = 0.75):  # noqa: ANN201
    raise NotImplementedError("pipeline-agent: implement logo fidelity (P1)")
