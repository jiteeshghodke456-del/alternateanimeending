"""Brand Kit -> Channels: per-channel Brand Score targets and channel-limit overrides (pipeline/channels.py).

Stored in Brand.channel_targets[channel] = {"max_grade": .., "target_score": 85, "limits": {"caption_max_chars": 2000}},
so every workspace has its own settings. Reads: any signed-in user. Writes: admin only, audited.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import CHANNELS, Brand
from app.pipeline import channels as CH
from app.schemas import ChannelLimitOut, ChannelSettingsIn, ChannelSettingsOut, ChannelsOut, CitationOut
from app.services import NoBrandError, audit, current_brand

router = APIRouter(tags=["channels"])


def _brand(session) -> Brand:  # noqa: ANN001
    try:
        return current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def channel_settings(targets: dict[str, Any], channel: str) -> ChannelSettingsOut:
    eff = CH.effective_limits(channel, targets)
    limits = []
    for code, spec in CH.checks_for(channel).items():
        key = spec["limit_key"]
        limits.append(ChannelLimitOut(
            code=code, key=key, statement=spec["statement"], value=eff[key], default=spec["default"],
            overridden=eff[key] != spec["default"], kind=spec["kind"], severity=spec["severity"],
            citation=CitationOut.model_validate(CH.citation(code))))
    return ChannelSettingsOut(channel=channel, target_score=CH.target_score(channel, targets),
                              default_target_score=CH.default_target_score(), limits=limits)


def channels_out(brand: Brand) -> ChannelsOut:
    targets = dict(brand.channel_targets or {})
    return ChannelsOut(channels=[channel_settings(targets, c) for c in CHANNELS],
                       not_included=list(CH.catalog().get("not_included", [])))


@router.get("/brand/channels", response_model=ChannelsOut)
def get_channels(session: SessionDep, user: CurrentUser) -> ChannelsOut:
    return channels_out(_brand(session))


@router.put("/brand/channels/{channel}", response_model=ChannelsOut)
def put_channel(channel: str, body: ChannelSettingsIn, session: SessionDep, user: AdminUser) -> ChannelsOut:
    if channel not in CHANNELS:
        raise HTTPException(status_code=404, detail="That channel doesn't exist.")
    brand = _brand(session)
    defaults = CH.defaults_for(channel)
    targets = {k: dict(v) for k, v in (brand.channel_targets or {}).items()}
    cur = dict(targets.get(channel) or {})
    limits = dict(cur.get("limits") or {})
    for key, value in body.limits.items():
        if key not in defaults:
            raise HTTPException(status_code=422, detail=f"{channel} has no limit called “{key}”.")
        if value is None or value == defaults[key]:
            limits.pop(key, None)
        elif not CH._valid_limit(key, value):  # noqa: SLF001
            what = "two ratios, smallest first (e.g. 0.8 and 1.91)" if key == "aspect_ratio" else "a positive number"
            raise HTTPException(status_code=422, detail=f"The {key.replace('_', ' ')} must be {what}.")
        else:
            limits[key] = list(value) if key == "aspect_ratio" else value
    if limits:
        cur["limits"] = limits
    else:
        cur.pop("limits", None)
    if body.target_score is None:
        cur.pop("target_score", None)
    else:
        cur["target_score"] = int(body.target_score)
    targets[channel] = cur
    brand.channel_targets = targets  # reassign: JSON columns don't track in-place changes
    session.add(brand)
    audit(session, "channel_settings_updated", user_id=user.id,
          detail={"channel": channel, "target_score": cur.get("target_score"), "limits": limits})
    session.commit()
    session.refresh(brand)
    return channels_out(brand)
