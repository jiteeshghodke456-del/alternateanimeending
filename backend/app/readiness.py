"""Startup readiness (cold start after a deploy or a Render wake).

The app starts listening at once; building the workspace templates and the demo pre-run setup happen
in the background (`main.lifespan`). Until they finish, GET /api/health says
`{"ok": true, "ready": false, "stage": "building demo"}` and every other /api call gets 503 with
`Retry-After` and `X-BG-Starting: 1`, so the UI can show "Starting the server…" instead of an error.
"""
from __future__ import annotations

import json
from typing import Any

ALWAYS_OPEN = ("/api/health", "/api/features", "/api/docs", "/api/openapi.json")
RETRY_AFTER_S = 2
STARTING_HEADER = "x-bg-starting"

_state: dict[str, Any] = {"ready": True, "stage": None, "failed": False}


def is_ready() -> bool:
    return bool(_state["ready"])


def stage() -> str | None:
    return _state["stage"]


def failed() -> bool:
    return bool(_state["failed"])


def starting(stage_name: str) -> None:
    _state.update(ready=False, stage=stage_name, failed=False)


def set_stage(stage_name: str) -> None:
    _state["stage"] = stage_name


def mark_ready(failed: bool = False) -> None:
    _state.update(ready=True, stage="startup failed" if failed else None, failed=failed)


def busy_message() -> str:
    what = f" ({_state['stage']})" if _state["stage"] else ""
    return f"The server is starting up{what}. This takes a few seconds; please try again."


class StartupGate:
    """Pure ASGI: while not ready, /api/* (except ALWAYS_OPEN) answers 503 + Retry-After at once."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if _state["ready"] or not path.startswith("/api/") or path in ALWAYS_OPEN:
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": busy_message(), "starting": True}).encode()
        await send({"type": "http.response.start", "status": 503, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(RETRY_AFTER_S).encode()), (STARTING_HEADER.encode(), b"1"),
            (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
