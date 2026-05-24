"""
WhatsApp messaging service via Twilio.
Validates outbound delivery and handles send failures gracefully.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.timezone import now_sgt
from app.models.models import OutboundMessage

logger = logging.getLogger(__name__)


def _get_twilio_client():
    from twilio.rest import Client
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_text(
    db: Session,
    patient_id: int,
    to_phone: str,
    body: str,
    campaign_id: int | None = None,
) -> OutboundMessage:
    """Send a WhatsApp text message and record an OutboundMessage."""
    msg = OutboundMessage(
        campaign_id=campaign_id,
        patient_id=patient_id,
        content=body,
        delivery_mode="text",
        status="sent",
        sent_at=now_sgt(),
    )
    db.add(msg)
    db.flush()  # get id before Twilio call to ensure record exists

    try:
        client = _get_twilio_client()
        twilio_msg = client.messages.create(
            body=body,
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{to_phone}",
        )
        msg.whatsapp_message_id = twilio_msg.sid
    except Exception as exc:
        logger.error(
            "Twilio send failed for patient_id=%s campaign_id=%s: %s",
            patient_id,
            campaign_id,
            exc,
        )
        msg.status = "failed"

    db.commit()
    db.refresh(msg)
    return msg


def validate_twilio_signature(request_url: str, post_params: dict, signature: str) -> bool:
    """Validate the X-Twilio-Signature HMAC-SHA1 header."""
    from twilio.request_validator import RequestValidator
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    return validator.validate(request_url, post_params, signature)
