"""FastAPI app: everything JSON lives under /api; frontend/dist is served at / with SPA fallback."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routers import auth, brand, content, eval as eval_router, innovations, insights, review, workspace
from app.workspaces import WorkspaceMiddleware
from app.schemas import HealthOut

APP_VERSION = "0.1.0"
log = logging.getLogger("brandgate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    from app import workspaces
    from app.pipeline.run import on_startup  # pipeline-agent: bind loop, clean stale checks, demo pre-run

    if workspaces.enabled():
        # Templates (demo + blank); the demo pre-run checks the seeded items once, on the template.
        workspaces.startup()
        with workspaces.use(workspaces.TEMPLATE_IDS["demo"]):
            on_startup()
    else:
        from app.seed import seed_if_empty

        summary = seed_if_empty()
        if summary:
            log.info("Seeded empty database: %s", summary)
        on_startup()
    yield


app = FastAPI(title="Brand Gate API", version=APP_VERSION, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

app.add_middleware(WorkspaceMiddleware)  # per-visitor workspace from the bg_ws cookie (app/workspaces.py)

# ------------------------------------------------------------------ errors -> {"detail": human message}
_FIELD_NAMES = {"user_id": "user", "title": "title", "channel": "channel", "body": "message", "text": "text", "reason": "reason"}


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path", "form")]
    field = _FIELD_NAMES.get(loc[-1], loc[-1].replace("_", " ")) if loc else "request"
    msg = f"Please check the {field}: {first.get('msg', 'invalid value').rstrip('.')}."
    return JSONResponse(status_code=422, content={"detail": msg, "errors": jsonable_errors(errs)})


def jsonable_errors(errs: list) -> list:
    return [{"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))} for e in errs]


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
    if exc.status_code == 404 and detail == "Not Found":
        detail = "That page or API route doesn't exist."
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. Please try again."})


# ------------------------------------------------------------------ API
api = APIRouter(prefix="/api")


@api.get("/health", response_model=HealthOut, tags=["health"])
def health() -> HealthOut:
    s = get_settings()
    return HealthOut(ok=True, version=APP_VERSION, jev_configured=s.jev_configured,
                     llm_configured=s.llm_configured, demo_mode=s.demo_mode)


for r in (auth.router, brand.router, content.router, review.router, insights.router, eval_router.router, innovations.router,
          workspace.router):
    api.include_router(r)


@api.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str) -> None:
    raise HTTPException(status_code=404, detail=f"Unknown API route: /api/{rest}")


app.include_router(api)

# ------------------------------------------------------------------ frontend (SPA)
_NO_BUILD_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Brand Gate</title></head>
<body style="font-family:system-ui;max-width:640px;margin:4rem auto;padding:0 1rem">
<h1>Brand Gate API is running</h1><p>The frontend hasn't been built yet (no <code>frontend/dist</code>).
See <a href="/api/docs">/api/docs</a> and <a href="/api/health">/api/health</a>.</p></body></html>"""


def _dist() -> Path:
    return Path(get_settings().frontend_dist)


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):  # noqa: ANN201
    dist = _dist()
    index = dist / "index.html"
    if not index.is_file():
        return HTMLResponse(_NO_BUILD_HTML)
    if full_path:
        candidate = (dist / full_path).resolve()
        if candidate.is_file() and dist.resolve() in candidate.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{full_path}" else None
            return FileResponse(candidate, headers=headers)
        if "." in full_path.rsplit("/", 1)[-1]:  # a missing static file, not a client route
            raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
