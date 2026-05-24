"""Apple Push Notification service integration."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from jose import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import UserDeviceToken

logger = logging.getLogger(__name__)

APNS_CATEGORY_MEDICATION_REMINDER = "MEDICATION_REMINDER"
_TOKEN_TTL_SECONDS = 50 * 60
_cached_jwt: str | None = None
_cached_jwt_created_at = 0.0


@dataclass
class ApnsSendResult:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    deactivated: int = 0
    errors: list[str] = field(default_factory=list)


class ApnsConfigurationError(RuntimeError):
    """Raised when APNs settings are incomplete."""


def apns_is_configured() -> bool:
    return all(
        [
            settings.APNS_PRIVATE_KEY.strip(),
            settings.APNS_KEY_ID.strip(),
            settings.APNS_TEAM_ID.strip(),
            settings.APNS_TOPIC.strip(),
        ]
    )


def _apns_host() -> str:
    environment = settings.APNS_ENVIRONMENT.strip().lower()
    if environment == "sandbox":
        return "api.sandbox.push.apple.com"
    return "api.push.apple.com"


def _private_key() -> str:
    key = settings.APNS_PRIVATE_KEY.strip()
    if not key:
        raise ApnsConfigurationError("APNS_PRIVATE_KEY is not configured")
    return key.replace("\\n", "\n")


def _auth_token() -> str:
    global _cached_jwt, _cached_jwt_created_at
    now = time.time()
    if _cached_jwt and now - _cached_jwt_created_at < _TOKEN_TTL_SECONDS:
        return _cached_jwt

    if not settings.APNS_KEY_ID.strip() or not settings.APNS_TEAM_ID.strip():
        raise ApnsConfigurationError("APNS_KEY_ID and APNS_TEAM_ID must be configured")

    _cached_jwt = jwt.encode(
        {"iss": settings.APNS_TEAM_ID.strip(), "iat": int(now)},
        _private_key(),
        algorithm="ES256",
        headers={"kid": settings.APNS_KEY_ID.strip()},
    )
    _cached_jwt_created_at = now
    return _cached_jwt


def medication_reminder_payload(
    *,
    reminder_id: str,
    medication_name: str,
    dosage: str = "",
    scheduled_time_label: str = "",
    message: str,
    title: str | None = None,
    caregiver_name: str = "",
    caregiver_relationship: str = "",
    caregiver_image_name: str = "",
    is_critical: bool = False,
    snooze_count: int = 0,
    thread_id: str | None = None,
) -> dict[str, Any]:
    push_title = title or f"Adheris — {medication_name}"
    push_thread_id = thread_id or reminder_id
    return {
        "aps": {
            "alert": {
                "title": push_title,
                "body": message,
            },
            "sound": "default",
            "interruption-level": "time-sensitive",
            "category": APNS_CATEGORY_MEDICATION_REMINDER,
            "thread-id": push_thread_id,
        },
        "reminderId": reminder_id,
        "medicationName": medication_name,
        "dosage": dosage,
        "scheduledTimeLabel": scheduled_time_label,
        "message": message,
        "caregiverName": caregiver_name,
        "caregiverRelationship": caregiver_relationship,
        "caregiverImageName": caregiver_image_name,
        "isCritical": is_critical,
        "snoozeCount": snooze_count,
    }


def send_payload_to_user(
    *,
    db: Session,
    user_id: int,
    payload: dict[str, Any],
    priority: int = 10,
) -> ApnsSendResult:
    result = ApnsSendResult()
    tokens = (
        db.query(UserDeviceToken)
        .filter(
            UserDeviceToken.user_id == user_id,
            UserDeviceToken.platform == "ios",
            UserDeviceToken.is_active == True,  # noqa: E712
        )
        .all()
    )
    if not tokens:
        return result

    if not apns_is_configured():
        result.errors.append("APNs is not configured")
        result.failed = len(tokens)
        result.attempted = len(tokens)
        return result

    with httpx.Client(http2=True, timeout=10) as client:
        for token_row in tokens:
            result.attempted += 1
            ok, reason = _send_to_token(
                client=client,
                token=token_row.token,
                payload=payload,
                priority=priority,
            )
            if ok:
                result.sent += 1
                continue

            result.failed += 1
            if reason in {"BadDeviceToken", "Unregistered"}:
                token_row.is_active = False
                result.deactivated += 1
            if reason:
                result.errors.append(reason)

    if result.deactivated:
        db.commit()
    return result


def _send_to_token(
    *,
    client: httpx.Client,
    token: str,
    payload: dict[str, Any],
    priority: int = 10,
) -> tuple[bool, str | None]:
    url = f"https://{_apns_host()}/3/device/{token}"
    headers = {
        "authorization": f"bearer {_auth_token()}",
        "apns-topic": settings.APNS_TOPIC.strip(),
        "apns-push-type": "alert",
        "apns-priority": str(priority),
        "apns-expiration": "0",
    }

    try:
        response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.warning("APNs request failed: %s", exc.__class__.__name__)
        return False, exc.__class__.__name__

    if 200 <= response.status_code < 300:
        return True, None

    reason = _apns_reason(response)
    logger.warning("APNs rejected push with status %s reason %s", response.status_code, reason)
    return False, reason


def _apns_reason(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP_{response.status_code}"
    reason = body.get("reason")
    return str(reason) if reason else f"HTTP_{response.status_code}"
