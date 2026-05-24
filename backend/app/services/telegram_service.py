"""
Telegram messaging service via Bot API.
Validates outbound delivery and handles send failures gracefully.
"""
import logging
from datetime import datetime
import httpx
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.timezone import now_sgt
from app.models.models import OutboundMessage

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


def _api_url(method: str) -> str:
    return f"{TELEGRAM_API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN)}/{method}"


def send_text(
    db: Session,
    patient_id: int,
    to_phone: str,
    body: str,
    campaign_id: int | None = None,
    chat_id: str | None = None,
) -> OutboundMessage:
    """Send a Telegram text message and record an OutboundMessage."""
    msg = OutboundMessage(
        campaign_id=campaign_id,
        patient_id=patient_id,
        content=body,
        delivery_mode="text",
        status="sent",
        sent_at=now_sgt(),
    )
    db.add(msg)
    db.flush()

    target_chat_id = chat_id or to_phone

    try:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN not set — message logged but not sent")
            msg.status = "simulated"
            db.commit()
            db.refresh(msg)
            return msg

        response = httpx.post(
            _api_url("sendMessage"),
            json={
                "chat_id": target_chat_id,
                "text": body,
                "parse_mode": "Markdown",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            msg.telegram_message_id = str(data["result"]["message_id"])
        else:
            logger.error("Telegram API error: %s", data.get("description"))
            msg.status = "failed"
    except Exception as exc:
        logger.error(
            "Telegram send failed for patient_id=%s campaign_id=%s: %s",
            patient_id,
            campaign_id,
            exc,
        )
        msg.status = "failed"

    db.commit()
    db.refresh(msg)
    return msg


def send_voice(
    db: Session,
    patient_id: int,
    to_phone: str,
    ogg_path: str,
    campaign_id: int | None = None,
    chat_id: str | None = None,
) -> OutboundMessage:
    """Send a Telegram voice note (.ogg) and record an OutboundMessage."""
    msg = OutboundMessage(
        campaign_id=campaign_id,
        patient_id=patient_id,
        content=f"[voice:{ogg_path}]",
        delivery_mode="audio",
        status="sent",
        sent_at=now_sgt(),
    )
    db.add(msg)
    db.flush()

    target_chat_id = chat_id or to_phone

    try:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN not set — voice message logged but not sent")
            msg.status = "simulated"
            db.commit()
            db.refresh(msg)
            return msg

        with open(ogg_path, "rb") as f:
            response = httpx.post(
                _api_url("sendVoice"),
                data={"chat_id": target_chat_id},
                files={"voice": (f"nudge.ogg", f, "audio/ogg")},
                timeout=30,
            )
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            msg.telegram_message_id = str(data["result"]["message_id"])
        else:
            logger.error("Telegram sendVoice API error: %s", data.get("description"))
            msg.status = "failed"
    except Exception as exc:
        logger.error(
            "Telegram sendVoice failed for patient_id=%s campaign_id=%s: %s",
            patient_id, campaign_id, exc,
        )
        msg.status = "failed"

    db.commit()
    db.refresh(msg)
    return msg


def send_keyboard(
    db: Session,
    patient_id: int,
    to_phone: str,
    body: str,
    buttons: list,
    campaign_id: int | None = None,
    chat_id: str | None = None,
) -> OutboundMessage:
    """Send a Telegram message with inline keyboard buttons and record an OutboundMessage.

    ``buttons`` must be a list[list[dict]] in Telegram InlineKeyboardMarkup format,
    e.g. [[{"text": "English", "callback_data": "1"}, ...], ...].
    """
    msg = OutboundMessage(
        campaign_id=campaign_id,
        patient_id=patient_id,
        content=body,
        delivery_mode="text",
        status="sent",
        sent_at=now_sgt(),
    )
    db.add(msg)
    db.flush()

    target_chat_id = chat_id or to_phone

    try:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN not set — keyboard message logged but not sent")
            msg.status = "simulated"
            db.commit()
            db.refresh(msg)
            return msg

        response = httpx.post(
            _api_url("sendMessage"),
            json={
                "chat_id": target_chat_id,
                "text": body,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            msg.telegram_message_id = str(data["result"]["message_id"])
        else:
            logger.error("Telegram API error: %s", data.get("description"))
            msg.status = "failed"
    except Exception as exc:
        logger.error(
            "Telegram send_keyboard failed for patient_id=%s: %s",
            patient_id,
            exc,
        )
        msg.status = "failed"

    db.commit()
    db.refresh(msg)
    return msg


def answer_callback_query(callback_query_id: str) -> None:
    """Acknowledge a Telegram callback query to dismiss the button loading state."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    try:
        httpx.post(
            _api_url("answerCallbackQuery"),
            json={"callback_query_id": callback_query_id},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("answer_callback_query failed for id=%s: %s", callback_query_id, exc)


def validate_telegram_token(token: str) -> bool:
    """Validate the X-Telegram-Bot-Api-Secret-Token header.
    When TELEGRAM_WEBHOOK_SECRET is not configured, skip validation (local dev).
    """
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        return True  # no secret configured — allow (local dev / polling mode)
    return token == settings.TELEGRAM_WEBHOOK_SECRET
