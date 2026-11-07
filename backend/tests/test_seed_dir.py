"""SEED_DIR: a brand kit outside the repo. Files present there win; any file missing there falls
back to backend/app/seed (personas.json especially); unset keeps the default behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import get_settings
from app import seed as S
from app.models import Asset, Brand, ContentItem, Fact, Rule, User

APP_SEED = Path(__file__).resolve().parents[1] / "app" / "seed"


@pytest.fixture
def seed_env(monkeypatch):  # noqa: ANN001, ANN201
    def set_(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("SEED_DIR", raising=False)
        else:
            monkeypatch.setenv("SEED_DIR", value)
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def _kit(tmp: Path) -> Path:
    d = tmp / "kit"
    (d / "content").mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps({"name": "Kit Brand", "tone_labels": ["warm"], "channel_targets": {}}), encoding="utf-8")
    (d / "users.json").write_text(json.dumps([{"id": "kim", "name": "Kim", "role": "admin"},
                                              {"id": "lee", "name": "Lee", "role": "creator"}]), encoding="utf-8")
    (d / "rules.json").write_text(json.dumps([{"code": "R-K-01", "category": "tone", "severity": "minor",
                                               "statement": "Never say 'awesome'.", "check_method": "tier0"}]), encoding="utf-8")
    (d / "facts.csv").write_text("code,statement,value,source,valid_until\nK-01,Kit has 3 shops,3 shops,Kit memo 1 Nov 2026,2026-12-31\n",
                                 encoding="utf-8")
    (d / "palette.json").write_text(json.dumps([{"name": "Charcoal", "hex": "#2B2B2B"}]), encoding="utf-8")
    (d / "content" / "items.json").write_text(json.dumps([
        {"title": "Kit post", "channel": "instagram", "creator": "Lee", "state": "draft", "text": "Hello from Kit."},
        {"title": "Kit ad", "channel": "instagram", "creator": "Lee", "state": "draft", "text": "Ad.", "file": "ad_good.png"},
    ]), encoding="utf-8")
    return d


def _load(tmp: Path) -> Session:
    eng = create_engine(f"sqlite:///{tmp / 'kit.db'}")
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(eng)
    s = Session(eng)
    S.load_seed(s)
    return s


def test_unset_keeps_default_seed(seed_env) -> None:  # noqa: ANN001
    seed_env(None)
    assert S.seed_dir() == S.default_seed_dir() == APP_SEED
    assert S.seed_file("personas.json") == APP_SEED / "personas.json"
    assert S.seed_file("nope.json") is None


def test_seed_dir_files_win_and_missing_files_fall_back(seed_env, tmp_path: Path) -> None:  # noqa: ANN001
    kit = _kit(tmp_path)
    seed_env(str(kit))
    assert S.seed_dir() == kit
    assert not S.is_placeholder()
    assert S.seed_file("brand.json") == kit / "brand.json"
    # not in the kit -> the app's own seed
    assert S.seed_file("personas.json") == APP_SEED / "personas.json"
    assert S.seed_file("content/ad_good.png") == APP_SEED / "content" / "ad_good.png"
    assert S.seed_file("nope.json") is None


def test_audience_personas_fall_back(seed_env, tmp_path: Path) -> None:  # noqa: ANN001
    from app.pipeline import audience

    seed_env(str(_kit(tmp_path)))
    audience.load_personas.cache_clear()
    try:
        assert len(audience.load_personas()) == len(json.loads((APP_SEED / "personas.json").read_text(encoding="utf-8")))
    finally:
        audience.load_personas.cache_clear()


def test_load_seed_from_seed_dir(seed_env, tmp_path: Path) -> None:  # noqa: ANN001
    kit = _kit(tmp_path)
    seed_env(str(kit))
    with _load(tmp_path) as s:
        assert s.exec(select(Brand)).one().name == "Kit Brand"
        assert {u.id for u in s.exec(select(User)).all()} == {"kim", "lee"}
        assert [r.code for r in s.exec(select(Rule)).all()] == ["R-K-01"]
        assert [f.code for f in s.exec(select(Fact)).all()] == ["K-01"]
        pal = [a.meta["hex"] for a in s.exec(select(Asset).where(Asset.kind == "palette")).all()]
        assert pal == ["#2B2B2B"]
        # logo.png / guideline.pdf are missing from the kit: the app seed's files fill in (logged)
        logos = s.exec(select(Asset).where(Asset.kind == "logo")).all()
        assert logos and all(Path(a.path).parent == APP_SEED for a in logos)
        items = {i.title: i for i in s.exec(select(ContentItem)).all()}
        assert set(items) == {"Kit post", "Kit ad"}  # the ad's image came from app/seed/content


def test_seed_dir_expands_home(seed_env, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    kit = _kit(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    seed_env("~/kit")
    assert S.seed_dir() == kit
