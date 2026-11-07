"""ALL Jev calls go through here. OWNER: pipeline-agent.

- `ask()` makes one System One call: semaphore (8), 20 s timeout, one SDK retry on 429/5xx,
  never raises. A failed call returns `ok=False` + `error`, and callers treat it as an ERROR
  (a pipeline warning or a human-band finding), never as a "no".
- Every call is recorded in a `CallLog` (provider "typesafe", model, purpose, version/unit id,
  input tokens, latency, cost). run.py writes the log to the `aicall` table. Content is never logged:
  the SDK's own logger (which logs request bodies at DEBUG) is pinned to WARNING.
- `available()` is False without TYPESAFE_API_KEY; the pipeline then skips Tier 1 cleanly.
- Question wording lives in questions.py; bands live in bands.py.

Tests replace `_call_api` (the raw SDK call) with a fake; nothing in the app fabricates answers.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from app.config import get_settings

log = logging.getLogger("brandgate.jev")
logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)  # it logs request bodies at DEBUG

PROVIDER = "typesafe"
CONCURRENCY = 8  # default; JEV_CONCURRENCY overrides it
TIMEOUT_S = 20.0
HARD_TIMEOUT_S = 45.0  # outer guard: 20 s + one retry with backoff


@dataclass
class ChoiceAnswer:
    choice: str
    confidence: Optional[float]
    probabilities: dict[str, float]


@dataclass
class ScoreAnswer:
    score: float  # the API's expected level index (0-based)
    confidence: Optional[float]
    probabilities: dict[str, float]  # level index as a string ("0", "1", ...) -> probability


@dataclass
class JevResult:
    ok: bool
    nouls: dict[str, float] = field(default_factory=dict)
    choices: dict[str, ChoiceAnswer] = field(default_factory=dict)
    scores: dict[str, ScoreAnswer] = field(default_factory=dict)
    input_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""
    error: Optional[str] = None

    @property
    def judgments(self) -> int:
        return len(self.nouls) + len(self.choices) + len(self.scores)


@dataclass
class CallRecord:
    provider: str
    model: str
    purpose: str
    version_id: Optional[int]
    unit_id: Optional[int]
    input_tokens: int
    latency_ms: int
    cost_usd: float
    ok: bool


@dataclass
class CallLog:
    """In-memory record of external calls for one check (persisted by run.py). No content, ever."""

    version_id: Optional[int] = None
    records: list[CallRecord] = field(default_factory=list)
    judgments: int = 0

    def add(self, rec: CallRecord, judgments: int = 0) -> None:
        self.records.append(rec)
        self.judgments += judgments

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)


def available() -> bool:
    return get_settings().jev_configured


def is_configured() -> bool:  # backwards-compatible alias
    return available()


def model_name() -> str:
    return get_settings().typesafe_default_model or "jev-latest"


def cost_for(tokens: int) -> float:
    return tokens * get_settings().jev_usd_per_m_input / 1_000_000


# ------------------------------------------------------------------ client + semaphore (per event loop)
_clients: dict[int, Any] = {}
_sems: dict[int, asyncio.Semaphore] = {}


def _loop_key() -> int:
    return id(asyncio.get_running_loop())


def _semaphore() -> asyncio.Semaphore:
    k = _loop_key()
    if k not in _sems:
        _sems[k] = asyncio.Semaphore(max(1, int(get_settings().jev_concurrency or CONCURRENCY)))
    return _sems[k]


def retry_policy() -> Any:
    """One retry on 429/5xx (the SDK honours Retry-After). We don't add a second retry layer."""
    from typesafe_sdk import RetryPolicy

    return RetryPolicy(max_retries=1, backoff_initial=0.5, backoff_max=2.0, http_statuses={429, 500, 502, 503, 504},
                       api_timeout_error=False, api_connection_error=True, timeout=TIMEOUT_S)


def _make_client(api_key: Optional[str] = None, transport: Any = None) -> Any:
    from typesafe_sdk import AsyncTypeSafeClient

    return AsyncTypeSafeClient(
        api_key=(api_key if api_key is not None else get_settings().typesafe_api_key).strip(),  # SDK alone reads only os.environ
        model=model_name(), timeout=TIMEOUT_S, retry=retry_policy(), transport=transport, compression=False,
    )


def _client() -> Any:
    k = _loop_key()
    if k not in _clients:
        _clients[k] = _make_client()
    return _clients[k]


async def _call_api(state: Any, questions: Mapping[str, Any], *, model: Optional[str],
                    extra_headers: Optional[Mapping[str, str]], timeout: float) -> Any:
    """The raw SDK call. The only function tests replace."""
    return await _client().system_one(state=state, questions=dict(questions), model=model,
                                      extra_headers=dict(extra_headers) if extra_headers else None, timeout=timeout)


def _normalize_scores(res: Any) -> dict[str, ScoreAnswer]:
    return {k: ScoreAnswer(score=float(v.score), confidence=getattr(v, "confidence", None),
                           probabilities={str(a): float(b) for a, b in (getattr(v, "probabilities", None) or {}).items()})
            for k, v in (getattr(res, "scores", None) or {}).items()}


def _normalize(res: Any) -> tuple[dict[str, float], dict[str, ChoiceAnswer], int, str]:
    nouls = {k: float(v.noul) for k, v in (getattr(res, "nouls", None) or {}).items()}
    choices = {k: ChoiceAnswer(choice=str(v.choice), confidence=getattr(v, "confidence", None),
                               probabilities={str(a): float(b) for a, b in (getattr(v, "probabilities", None) or {}).items()})
               for k, v in (getattr(res, "choices", None) or {}).items()}
    usage = getattr(res, "usage", None)
    tokens = int(getattr(usage, "input_tokens", None) or 0)
    return nouls, choices, tokens, str(getattr(res, "model", "") or "")


def _error_text(exc: BaseException) -> str:
    """Short, content-free error description for warnings."""
    status = getattr(exc, "status", None)
    name = type(exc).__name__
    if status:
        return f"{name} (HTTP {status})"
    if isinstance(exc, asyncio.TimeoutError):
        return "timed out"
    return name


async def ask(
    state: Any,
    questions: Mapping[str, Any],
    *,
    purpose: str,
    calls: Optional[CallLog] = None,
    version_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    images: bool = False,
    model: Optional[str] = None,
) -> JevResult:
    """One System One call. Never raises; `ok=False` means the answers are missing (an error)."""
    if not questions:
        return JevResult(ok=True)
    if not available():
        return JevResult(ok=False, error="Jev not configured")
    headers = None
    if images:
        from app.pipeline.questions import IMAGE_HEADERS

        headers = IMAGE_HEADERS
    t0 = time.perf_counter()
    async with _semaphore():
        t0 = time.perf_counter()  # latency excludes queueing
        try:
            res = await asyncio.wait_for(
                _call_api(state, questions, model=model, extra_headers=headers, timeout=TIMEOUT_S), HARD_TIMEOUT_S)
            nouls, choices, tokens, used_model = _normalize(res)
            scores = _normalize_scores(res)
            ok, err = True, None
        except Exception as exc:  # noqa: BLE001  (SDK errors, timeouts, bad responses)
            nouls, choices, tokens, used_model = {}, {}, 0, ""
            scores = {}
            ok, err = False, _error_text(exc)
            log.warning("jev call failed: purpose=%s unit=%s error=%s", purpose, unit_id, err)
    latency = int((time.perf_counter() - t0) * 1000)
    result = JevResult(ok=ok, nouls=nouls, choices=choices, scores=scores, input_tokens=tokens, latency_ms=latency,
                       cost_usd=cost_for(tokens), model=used_model or model or model_name(), error=err)
    missing = [q for q in questions if q not in nouls and q not in choices and q not in scores]
    if ok and missing:
        result.ok, result.error = False, f"missing answers for {len(missing)} question(s)"
    if calls is not None:
        calls.add(CallRecord(PROVIDER, result.model, purpose, version_id if version_id is not None else calls.version_id,
                             unit_id, tokens, latency, result.cost_usd, result.ok), judgments=result.judgments)
    return result


# ------------------------------------------------------------------ images
MIN_SIDE, MAX_SIDE = 280, 1568  # API limits (docs.typesafe.ai/images)
SEND_MAX_SIDE = 1024  # smaller than the limit to keep tokens (cost) down; IMAGE_MAX_SIDE can lower it


def prepare_image(path: str) -> bytes:
    """PNG/JPEG only, upright, each side within 280..1568 px. WEBP and others are converted to PNG."""
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
        w, h = im.size
        send_max = max(MIN_SIDE, min(SEND_MAX_SIDE, int(get_settings().image_max_side or SEND_MAX_SIDE)))
        scale = min(1.0, send_max / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        w, h = im.size
        if w < MIN_SIDE or h < MIN_SIDE:
            if max(w, h) < MIN_SIDE:  # upscale tiny images, then pad the short side
                f = MIN_SIDE / max(w, h)
                im = im.resize((max(1, round(w * f)), max(1, round(h * f))), Image.LANCZOS)
                w, h = im.size
            canvas = Image.new("RGB", (max(w, MIN_SIDE), max(h, MIN_SIDE)), (255, 255, 255))
            canvas.paste(im, ((canvas.width - w) // 2, (canvas.height - h) // 2))
            im = canvas
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
