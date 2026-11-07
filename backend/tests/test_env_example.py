""".env.example must start the app when passed as `docker run --env-file .env.example`.

Docker's env-file format takes KEY=VALUE literally to the end of the line: no quotes, no inline comments.
A trailing "# comment" becomes part of the value and pydantic-settings rejects it (audit #10).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import REPO_DIR, Settings, SettingsConfigDict


def docker_env_file(path: Path) -> dict[str, str]:
    """Parse the way `docker run --env-file` does: skip blank and '#' lines, split on the first '='."""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.lstrip()
        if not line or line.startswith("#"):
            continue
        key, _, value = raw.partition("=")
        env[key.strip()] = value  # value kept literally, trailing spaces and all
    return env


def test_env_example_has_no_inline_comments() -> None:
    env = docker_env_file(REPO_DIR / ".env.example")
    assert env, "no settings parsed"
    for key, value in env.items():
        assert "#" not in value, f"{key} has an inline comment; move it to its own line"
        assert value == value.strip(), f"{key} has surrounding whitespace in its value"


def test_env_example_values_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    env = docker_env_file(REPO_DIR / ".env.example")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class DockerSettings(Settings):
        model_config = SettingsConfigDict(env_file=None, extra="ignore")

    s = DockerSettings()
    assert s.demo_mode is True
    assert s.max_concurrent_checks == 2
    assert not s.jev_configured and not s.llm_configured
    # A blank SESSION_SECRET= must not sign sessions with an empty key.
    assert s.session_secret
