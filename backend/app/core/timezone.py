"""Timezone helpers for app-domain timestamps.

The product is scoped to Singapore, so user-facing and domain timestamps are
stored and returned as naive Asia/Singapore datetimes. Protocol timestamps
such as JWT expiry and APNs auth tokens should continue to use UTC/epoch time.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")


def now_sgt() -> datetime:
    """Return current Singapore time as a naive datetime for existing DB columns."""
    return datetime.now(SGT).replace(tzinfo=None)


def as_sgt_naive(value: datetime | None) -> datetime | None:
    """Normalise an incoming datetime to naive Singapore time."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(SGT).replace(tzinfo=None)


def sgt_isoformat(value: datetime | None) -> str | None:
    normalised = as_sgt_naive(value)
    return normalised.isoformat() if normalised else None
