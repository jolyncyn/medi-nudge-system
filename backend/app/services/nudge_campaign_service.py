"""
NudgeCampaign service.
Manages campaign lifecycle: create, schedule, fire, handle responses, retry, escalate.

Key functions:
  create_campaign()   — create a pending campaign with a scheduled fire_at time
  fire_campaign()     — send the message immediately, transition to "sent"
  fire_due_campaigns() — fire all pending campaigns whose fire_at <= now (called by scheduler)
  create_and_send()   — convenience: create with fire_at=now and fire immediately
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import (
    NudgeCampaign, Patient, Medication, CAMPAIGN_VALID_TRANSITIONS,
)
from app.services import nudge_generator, telegram_service, escalation_service, tts_service
from app.core.config import settings
from app.core.timezone import now_sgt

import logging
logger = logging.getLogger(__name__)


def _transition(db: Session, campaign: NudgeCampaign, new_status: str) -> NudgeCampaign:
    allowed = CAMPAIGN_VALID_TRANSITIONS.get(campaign.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition NudgeCampaign from '{campaign.status}' to '{new_status}'"
        )
    campaign.status = new_status
    db.commit()
    db.refresh(campaign)
    return campaign


_COLLECTED_LABELS = {
    "en": "✅ Collected",
    "zh": "✅ 已领取",
    "ms": "✅ Sudah ambil",
    "ta": "✅ எடுத்தேன்",
}

def _collected_button(lang: str | None) -> list[list[dict]]:
    label = _COLLECTED_LABELS.get(lang or "en", _COLLECTED_LABELS["en"])
    return [[{"text": label, "callback_data": "YES"}]]


def create_campaign(
    db: Session,
    patient: Patient,
    medication: Medication,
    days_overdue: int,
    fire_at: datetime,
    attempt: int = 1,
    campaign_type: str = "refill_reminder",
    message_content: str | None = None,
) -> NudgeCampaign:
    """Create a NudgeCampaign with a scheduled fire time. Does NOT send immediately.

    If a pending or sent campaign already exists for this patient+medication+type,
    the existing one is returned (and fire_at / days_overdue updated if still pending).
    """
    existing = (
        db.query(NudgeCampaign)
        .filter(
            NudgeCampaign.patient_id == patient.id,
            NudgeCampaign.medication_id == medication.id,
            NudgeCampaign.campaign_type == campaign_type,
            NudgeCampaign.status.in_(["pending", "sent"]),
        )
        .first()
    )
    if existing:
        if existing.status == "pending":
            existing.days_overdue = days_overdue
            existing.fire_at = fire_at
            db.commit()
        return existing

    campaign = NudgeCampaign(
        patient_id=patient.id,
        medication_id=medication.id,
        status="pending",
        days_overdue=days_overdue,
        attempt_number=attempt,
        message_content=message_content,
        language=patient.language_preference,
        fire_at=fire_at,
        campaign_type=campaign_type,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def fire_campaign(db: Session, campaign: NudgeCampaign) -> NudgeCampaign:
    """Send the campaign message to the patient and transition to 'sent'.

    Can be called directly (manual trigger) or by fire_due_campaigns().
    """
    if campaign.status != "pending":
        return campaign

    patient = db.query(Patient).filter(Patient.id == campaign.patient_id).first()
    medication = db.query(Medication).filter(Medication.id == campaign.medication_id).first()
    if not patient or not medication:
        _transition(db, campaign, "failed")
        return campaign

    # Generate message text if not already stored
    if not campaign.message_content:
        condition = ", ".join(patient.conditions) if patient.conditions else ""
        try:
            campaign.message_content = nudge_generator.generate_nudge_message(
                name=patient.full_name,
                medication=medication.name,
                days_overdue=campaign.days_overdue,
                language=patient.language_preference,
                attempt=campaign.attempt_number,
                condition=condition,
            )
        except Exception as exc:
            logger.warning("Message generation failed for campaign %s: %s", campaign.id, exc)
            _transition(db, campaign, "failed")
            return campaign
        db.commit()

    message = campaign.message_content
    lang = patient.language_preference
    chat_target = patient.telegram_chat_id or patient.phone_number
    do_text = patient.nudge_delivery_mode in ("text", "both")
    do_voice = patient.nudge_delivery_mode in ("voice", "both")

    out_msg = None

    if campaign.campaign_type == "side_effect_checkin":
        # Plain conversational message — no buttons, free-text reply expected.
        # Respects delivery mode (text or voice) same as refill nudges.
        if do_text or not do_voice:
            out_msg = telegram_service.send_text(
                db=db, patient_id=patient.id, to_phone=chat_target,
                body=message, campaign_id=campaign.id,
            )
        if do_voice:
            ogg_path = tts_service.generate_voice_message(
                text=message, voice_id=patient.selected_voice_id,
                patient_id=patient.id, medication_id=medication.id,
                attempt=campaign.attempt_number,
            )
            if ogg_path:
                telegram_service.send_voice(
                    db=db, patient_id=patient.id, to_phone=chat_target,
                    ogg_path=ogg_path, campaign_id=campaign.id,
                )
            elif not out_msg:
                out_msg = telegram_service.send_text(
                    db=db, patient_id=patient.id, to_phone=chat_target,
                    body=message, campaign_id=campaign.id,
                )
    else:
        # Refill reminder — inline Collected button + optional voice.
        if do_text or not do_voice:
            out_msg = telegram_service.send_keyboard(
                db=db, patient_id=patient.id, to_phone=chat_target,
                body=message, buttons=_collected_button(lang), campaign_id=campaign.id,
            )
        if do_voice:
            ogg_path = tts_service.generate_voice_message(
                text=message, voice_id=patient.selected_voice_id,
                patient_id=patient.id, medication_id=medication.id,
                attempt=campaign.attempt_number,
            )
            if ogg_path:
                telegram_service.send_voice(
                    db=db, patient_id=patient.id, to_phone=chat_target,
                    ogg_path=ogg_path, campaign_id=campaign.id,
                )
            elif not out_msg:
                out_msg = telegram_service.send_keyboard(
                    db=db, patient_id=patient.id, to_phone=chat_target,
                    body=message, buttons=_collected_button(lang), campaign_id=campaign.id,
                )

    if out_msg is None:
        _transition(db, campaign, "sent")
        campaign.last_sent_at = now_sgt()
        db.commit()
    elif out_msg.status == "failed":
        _transition(db, campaign, "failed")
    else:
        _transition(db, campaign, "sent")
        campaign.last_sent_at = now_sgt()
        db.commit()

    if campaign.attempt_number >= settings.MAX_NUDGE_ATTEMPTS and campaign.status == "sent":
        escalation_service.create_escalation(
            db=db, patient_id=patient.id, reason="no_response",
            nudge_campaign_id=campaign.id, priority="high",
        )

    return campaign


def fire_due_campaigns(db: Session | None = None) -> dict:
    """Fire all pending campaigns whose fire_at <= now. Called by the scheduler."""
    from app.core.database import SessionLocal
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    results = {"fired": 0, "failed": 0, "errors": 0}
    try:
        due = (
            db.query(NudgeCampaign)
            .filter(
                NudgeCampaign.status == "pending",
                NudgeCampaign.fire_at <= now_sgt(),
            )
            .all()
        )
        for campaign in due:
            try:
                fire_campaign(db, campaign)
                if campaign.status == "sent":
                    results["fired"] += 1
                else:
                    results["failed"] += 1
            except Exception as exc:
                logger.error("Error firing campaign %s: %s", campaign.id, exc)
                results["errors"] += 1
    finally:
        if owns_session:
            db.close()

    return results


def create_and_send(
    db: Session,
    patient: Patient,
    medication: Medication,
    days_overdue: int,
    attempt: int = 1,
) -> NudgeCampaign:
    """Create a campaign with fire_at=now and fire it immediately.

    Used by the daily refill detection job (medication already overdue)
    and retry flow.
    """
    campaign = create_campaign(
        db=db, patient=patient, medication=medication,
        days_overdue=days_overdue, fire_at=now_sgt(), attempt=attempt,
    )
    if campaign.status == "pending":
        fire_campaign(db, campaign)
    return campaign


def handle_response(
    db: Session,
    campaign: NudgeCampaign,
    response_text: str,
    response_type: str,
) -> NudgeCampaign:
    """Process a classified inbound response and update campaign state."""
    campaign.response = response_text
    campaign.response_type = response_type

    if response_type == "confirmed":
        _transition(db, campaign, "resolved")
        if campaign.campaign_type == "side_effect_checkin":
            from app.services.dose_log_service import log_dose
            log_dose(db, campaign.patient_id, campaign.medication_id, "no_issue", "checkin_ok")

    elif response_type == "side_effect":
        # MUST always escalate — never silently drop
        escalation_service.create_escalation(
            db=db,
            patient_id=campaign.patient_id,
            reason="side_effect",
            nudge_campaign_id=campaign.id,
            priority="urgent",
        )
        _transition(db, campaign, "escalated")
        # Send safety acknowledgement
        patient = db.query(Patient).filter(Patient.id == campaign.patient_id).first()
        if patient:
            ack = nudge_generator.get_safety_ack(patient.language_preference)
            telegram_service.send_text(
                db=db, patient_id=patient.id, to_phone=patient.telegram_chat_id or patient.phone_number, body=ack
            )

    elif response_type == "question":
        escalation_service.create_escalation(
            db=db,
            patient_id=campaign.patient_id,
            reason="patient_question",
            nudge_campaign_id=campaign.id,
        )
        _transition(db, campaign, "escalated")
        patient = db.query(Patient).filter(Patient.id == campaign.patient_id).first()
        if patient:
            ack = nudge_generator.get_question_ack(patient.language_preference)
            telegram_service.send_text(
                db=db, patient_id=patient.id, to_phone=patient.telegram_chat_id or patient.phone_number, body=ack
            )

    elif response_type in ("negative", "opt_out"):
        _transition(db, campaign, "responded")

    db.commit()
    db.refresh(campaign)
    return campaign


def retry_or_escalate(db: Session, campaign: NudgeCampaign) -> None:
    """Called by the 48h scheduler job for campaigns with no response."""
    if campaign.status != "sent":
        return

    if campaign.attempt_number < settings.MAX_NUDGE_ATTEMPTS:
        patient = db.query(Patient).filter(Patient.id == campaign.patient_id).first()
        medication = db.query(Medication).filter(Medication.id == campaign.medication_id).first()
        if not patient or not medication:
            return
        # Mark current as failed; create new attempt
        _transition(db, campaign, "failed")
        create_and_send(
            db=db,
            patient=patient,
            medication=medication,
            days_overdue=campaign.days_overdue,
            attempt=campaign.attempt_number + 1,
        )
    else:
        # Max attempts exhausted
        escalation_service.create_escalation(
            db=db,
            patient_id=campaign.patient_id,
            reason="no_response",
            nudge_campaign_id=campaign.id,
            priority="high",
        )
        _transition(db, campaign, "escalated")
