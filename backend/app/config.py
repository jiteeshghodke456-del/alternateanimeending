"""Environment settings. Names match CLAUDE.md "Secrets and env" exactly (case-insensitive)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent          # backend/app
BACKEND_DIR = APP_DIR.parent                        # backend
REPO_DIR = BACKEND_DIR.parent                       # repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Jev (typesafe-sdk reads TYPESAFE_API_KEY itself; we only check presence)
    typesafe_api_key: str = ""
    typesafe_default_model: str = "jev-latest"

    # Writer LLM (OpenAI-compatible)
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # Cost meter
    jev_usd_per_m_input: float = 0.042

    # Demo
    demo_mode: bool = True

    # Feature flags (P1). FEATURE_PDF_IMPORT=0 turns the guideline PDF import endpoints off (501).
    feature_pdf_import: bool = True

    # Storage
    db_path: str = "/tmp/brandgate.db"
    upload_dir: str = "/tmp/brandgate_uploads"
    seed_dir: str = ""  # optional override; default = backend/app/seed (or its _placeholder)

    # Auth
    session_secret: str = "dev-only-insecure-session-secret-change-me"
    session_max_age_s: int = 7 * 24 * 3600

    # Frontend build served by FastAPI
    frontend_dist: str = str(REPO_DIR / "frontend" / "dist")

    # Upload limits (CLAUDE.md "Don'ts")
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 20
    max_text_chars: int = 20_000

    # Memory budget (Render free tier = 512 MiB; DECISIONS.md "Memory budget on Render free tier")
    max_concurrent_checks: int = 2  # checks + audience runs + PDF imports running at once; the rest queue
    prerun_delay_s: float = 15.0  # startup demo pre-run waits this long so the server is healthy first
    image_max_side: int = 1024  # images are downscaled to this before Jev (and colour work)
    sift_max_side: int = 640  # logo matching: largest piece one SIFT pass sees (~225 bytes/pixel); bigger images are tiled
    jev_concurrency: int = 8  # Jev calls in flight per process

    @property
    def jev_configured(self) -> bool:
        return bool(self.typesafe_api_key.strip())

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
