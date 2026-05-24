"""
Telegram inbound webhook endpoint.
Validates the X-Telegram-Bot-Api-Secret-Token header before processing.
"""
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.timezone import now_sgt
from app.core.config import settings
from app.models.models import Patient, NudgeCampaign, OutboundMessage, PatientMedication, VoiceProfile, PrescriptionScan
from app.services.response_classifier import classify_response
from app.services import nudge_campaign_service, onboarding_service, ocr_service, agent_service, escalation_service
from app.services.telegram_service import validate_telegram_token, send_text, answer_callback_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

ONBOARDING_STATES = onboarding_service.ONBOARDING_STATES


def _validate_secret(request: Request) -> None:
    """Reject requests with invalid Telegram webhook secret with 403."""
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not validate_telegram_token(token):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


@router.post("/telegram")
async def inbound_telegram(
    request: Request,
    db: Session = Depends(get_db),
):
    """Main inbound Telegram message handler (Telegram Bot API webhook)."""
    _validate_secret(request)

    update = await request.json()

    # Handle inline keyboard button taps before the message guard
    callback_query = update.get("callback_query")
    if callback_query:
        _handle_callback_query(db, callback_query)
        return {"ok": True}

    message = update.get("message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    text = message.get("text", "").strip()
    from_user = message.get("from", {})

    if not chat_id:
        return {"ok": True}

    # Handle /start [TOKEN] before patient lookup
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        token_arg = parts[1].strip() if len(parts) > 1 else None
        onboarding_service.handle_start_command(db, chat_id, token_arg)
        return {"ok": True}

    # Check if this is a caregiver (voice sample or consent reply)
    # Only treat as caregiver if this chat_id is NOT also a patient's own telegram_chat_id
    is_patient = db.query(Patient).filter(Patient.telegram_chat_id == chat_id).first() is not None
    if not is_patient:
        caregiver_patient = db.query(Patient).filter(Patient.caregiver_telegram_id == chat_id).first()
        if caregiver_patient:
            voice_msg = message.get("voice")
            if voice_msg:
                _handle_caregiver_voice(db, caregiver_patient, chat_id, voice_msg)
                return {"ok": True}
            if text and _handle_caregiver_text(db, caregiver_patient, chat_id, text):
                return {"ok": True}

    # Look up patient by telegram_chat_id
    patient = db.query(Patient).filter(Patient.telegram_chat_id == chat_id).first()

    if not patient:
        # Route to self-onboarding
        onboarding_service.handle_start_command(db, chat_id, None)
        return {"ok": True}

    # --- Pending action takes priority (bot asked a question, this is the reply) ---
    if patient.pending_action:
        handled = _handle_pending_action(db, patient, message, text)
        if handled:
            return {"ok": True}

    # Photo → OCR pipeline (always takes priority over text routing)
    if message.get("photo"):
        _handle_photo(db, patient, message)
        return {"ok": True}

    # Voice message from patient — route to self-cloning flow
    voice_msg = message.get("voice")
    if voice_msg and patient.onboarding_state == "complete":
        _handle_patient_voice(db, patient, voice_msg)
        return {"ok": True}

    if not text:
        return {"ok": True}

    # Medication confirmation pending (from onboarding manual-entry verification)
    if patient.onboarding_state == "medication_confirm_pending":
        agent_service.handle_medication_confirm_reply(patient, text, db)
        return {"ok": True}

    # OCR fast-path confirmation pending
    if patient.onboarding_state == "patient_pending_ocr_confirmation":
        upper = text.strip().upper()
        if upper == "CONFIRM":
            _handle_ocr_confirm(db, patient)
        elif upper == "EDIT":
            _handle_ocr_edit(db, patient)
        else:
            # Re-prompt with buttons
            _re_prompt_ocr_confirmation(db, patient)
        return {"ok": True}

    # Onboarding flow
    if patient.onboarding_state in ONBOARDING_STATES:
        onboarding_service.handle_onboarding_reply(db, patient, text)
        return {"ok": True}

    # Active patient — route through agentic handler (LLM or rule-based fallback)
    agent_service.run(patient, text, db)
    return {"ok": True}


def _handle_callback_query(db: Session, callback_query: dict) -> None:
    """Route an inline keyboard button tap as if the patient had typed the callback_data."""
    cq_id = callback_query.get("id", "")
    cq_message = callback_query.get("message", {})
    chat_id = str(cq_message.get("chat", {}).get("id", ""))
    data = callback_query.get("data", "").strip()

    if not chat_id:
        return

    # Acknowledge immediately to dismiss the loading state on the button
    answer_callback_query(cq_id)

    if not data:
        return

    patient = db.query(Patient).filter(Patient.telegram_chat_id == chat_id).first()
    if not patient:
        onboarding_service.handle_start_command(db, chat_id, None)
        return

    # Route callback_data through the same path as a text message
    if patient.onboarding_state == "medication_confirm_pending":
        agent_service.handle_medication_confirm_reply(patient, data, db)
        return

    if patient.onboarding_state == "patient_pending_ocr_confirmation":
        upper = data.strip().upper()
        if upper == "CONFIRM":
            _handle_ocr_confirm(db, patient)
        elif upper == "EDIT":
            _handle_ocr_edit(db, patient)
        return

    if patient.onboarding_state in ONBOARDING_STATES:
        onboarding_service.handle_onboarding_reply(db, patient, data)
        return

    # Active patient (onboarding complete)
    agent_service.run(patient, data, db)


def _handle_pending_action(db: Session, patient: Patient, message: dict, text: str) -> bool:
    """
    Route reply based on what the bot last asked the patient.
    Returns True if handled, False to fall through to normal routing.
    """
    action = patient.pending_action

    if action == "schedule_confirm":
        if not text:
            return False
        return _handle_schedule_confirm_reply(db, patient, text)

    if action == "voice_consent":
        if not text:
            return False  # They sent a non-text message, let normal routing handle
        return _handle_voice_consent_reply(db, patient, text)

    if action == "voice_sample_pending":
        voice_msg = message.get("voice")
        if voice_msg:
            _handle_patient_voice(db, patient, voice_msg)
            return True
        if text:
            _send_reply(
                patient.telegram_chat_id,
                "Please send a *voice message* (hold the microphone button). Text won't work for voice cloning.",
            )
            return True
        return False

    # Unknown action — clear it and fall through
    logger.warning("Unknown pending_action '%s' for patient %s, clearing", action, patient.id)
    patient.pending_action = None
    db.commit()
    return False


def _handle_schedule_confirm_reply(db: Session, patient: Patient, text: str) -> bool:
    """Handle the patient's reply to the reminder-schedule confirmation prompt."""
    from app.services import telegram_service as _tg

    lower = text.strip().lower()
    pending = json.loads(patient.consent_channel or "{}")
    pm_id = pending.get("schedule_pm_id")
    pm = db.query(PatientMedication).filter(PatientMedication.id == pm_id).first() if pm_id else None

    if any(k in lower for k in ("ok", "okay", "yes", "ya", "好", "是", "setuju")):
        # Patient confirmed the inferred schedule — nothing to change
        patient.pending_action = None
        patient.consent_channel = None
        db.commit()
        lang = patient.language_preference or "en"
        ACK = {"en": "Got it! ✅", "zh": "好的！✅", "ms": "Baik! ✅", "ta": "சரி! ✅"}
        _tg.send_text(
            db=db, patient_id=patient.id,
            to_phone=patient.telegram_chat_id or patient.phone_number,
            body=ACK.get(lang, ACK["en"]),
        )
        return True

    # Attempt to parse custom times from the patient's reply
    custom_times = _parse_custom_times(text)
    if custom_times and pm:
        pm.reminder_times = custom_times
        db.commit()
        patient.pending_action = None
        patient.consent_channel = None
        db.commit()
        lang = patient.language_preference or "en"
        labels = " and ".join(
            datetime.strptime(t, "%H:%M").strftime("%-I:%M %p") for t in custom_times
        )
        SAVED = {
            "en": f"Got it — I'll remind you at {labels}. ✅",
            "zh": f"好的，我将在 {labels} 提醒您。✅",
            "ms": f"Baik — saya akan mengingatkan anda pada {labels}. ✅",
            "ta": f"சரி — {labels} நேரத்தில் நினைவூட்டுவேன். ✅",
        }
        _tg.send_text(
            db=db, patient_id=patient.id,
            to_phone=patient.telegram_chat_id or patient.phone_number,
            body=SAVED.get(lang, SAVED["en"]),
        )
        return True

    # Could not parse — ask coordinator
    from app.services import escalation_service as _esc
    _esc.create_escalation(db=db, patient_id=patient.id, reason="patient_question", priority="low")
    patient.pending_action = None
    patient.consent_channel = None
    db.commit()
    _tg.send_text(
        db=db, patient_id=patient.id,
        to_phone=patient.telegram_chat_id or patient.phone_number,
        body="Thank you — your care coordinator will confirm your reminder schedule shortly.",
    )
    return True


def _parse_custom_times(text: str) -> list[str]:
    """Parse freeform time strings like '7am and 9pm' into ['07:00', '21:00']."""
    import re
    pattern = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)'
    matches = re.findall(pattern, text.lower())
    times = []
    for h, m, period in matches:
        hour = int(h)
        minute = int(m) if m else 0
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        times.append(f"{hour:02d}:{minute:02d}")
    return times if times else []


def _handle_voice_consent_reply(db: Session, patient: Patient, text: str) -> bool:
    """Handle the reply to a voice consent question."""
    from datetime import datetime as _dt

    lower = text.strip().lower()

    profile = (
        db.query(VoiceProfile)
        .filter(
            VoiceProfile.patient_id == patient.id,
            VoiceProfile.is_active == True,
        )
        .order_by(VoiceProfile.created_at.desc())
        .first()
    )

    if any(k in lower for k in ("yes", "ya", "同意", "是", "ok", "okay")):
        patient.pending_action = None
        if profile:
            profile.patient_consent_at = now_sgt()
            if profile.donor_name == "self":
                profile.donor_consent_at = now_sgt()
        db.commit()

        if profile and profile.sample_file_path:
            from app.services.voice_clone_service import clone_voice
            success = clone_voice(db, profile)
            if success:
                _send_reply(patient.telegram_chat_id, "Your voice has been set up for your medication reminders!")
            else:
                _send_reply(patient.telegram_chat_id, "Thank you for your consent. We'll set up voice reminders shortly.")
        else:
            _send_reply(patient.telegram_chat_id, "Thank you for your consent. Please send a voice message (60-90 seconds) to complete the setup.")
            patient.pending_action = "voice_sample_pending"
            db.commit()
        return True

    elif any(k in lower for k in ("no", "tidak", "不", "拒绝", "nope", "cancel")):
        patient.pending_action = None
        if profile:
            profile.is_active = False
        db.commit()
        _send_reply(patient.telegram_chat_id, "No problem. Voice cloning has been cancelled.")
        return True

    else:
        # Didn't understand — re-ask
        _send_reply(patient.telegram_chat_id, "Please reply *YES* to consent or *NO* to decline.")
        return True


def _handle_patient_voice(db: Session, patient: Patient, voice: dict) -> None:
    """Download patient's own voice sample for self-cloning."""
    logger.info("Processing patient voice message for self-clone, patient_id=%s", patient.id)
    import os
    from pathlib import Path

    chat_id = patient.telegram_chat_id

    # Find or create a VoiceProfile for self-cloning
    profile = (
        db.query(VoiceProfile)
        .filter(VoiceProfile.patient_id == patient.id, VoiceProfile.is_active == True)
        .first()
    )
    if not profile:
        profile = VoiceProfile(
            patient_id=patient.id,
            donor_name="self",
            donor_telegram_id=chat_id,
            is_active=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

    file_id = voice.get("file_id")
    if not file_id:
        return

    try:
        import httpx
        token = settings.TELEGRAM_BOT_TOKEN
        file_resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        file_resp.raise_for_status()
        file_path = file_resp.json()["result"]["file_path"]

        dl_resp = httpx.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=30,
        )
        dl_resp.raise_for_status()

        sample_dir = os.path.join(settings.MEDIA_STORAGE_PATH, "voice_samples")
        Path(sample_dir).mkdir(parents=True, exist_ok=True)
        sample_path = os.path.join(sample_dir, f"{profile.id}.ogg")
        with open(sample_path, "wb") as f:
            f.write(dl_resp.content)

        profile.sample_file_path = sample_path
        patient.pending_action = "voice_consent"
        db.commit()

        _send_reply(
            chat_id,
            "Thank you for recording! Do you consent to your voice being used "
            "for your medication reminders?\n\n"
            "Reply *YES* to consent or *NO* to decline.",
        )
        logger.info("Voice sample saved for patient %s, awaiting consent", patient.id)
    except Exception as exc:
        logger.error("Failed to download patient voice for patient %s: %s", patient.id, exc)


def _handle_caregiver_voice(db: Session, patient: Patient, chat_id: str, voice: dict) -> None:
    """Download caregiver voice sample and store for cloning."""
    import os
    from pathlib import Path

    # Find or create a pending VoiceProfile
    profile = (
        db.query(VoiceProfile)
        .filter(VoiceProfile.patient_id == patient.id, VoiceProfile.is_active == True)
        .first()
    )
    if not profile:
        profile = VoiceProfile(
            patient_id=patient.id,
            donor_name=patient.caregiver_name,
            donor_telegram_id=chat_id,
            is_active=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

    file_id = voice.get("file_id")
    if not file_id:
        return

    try:
        import httpx
        token = settings.TELEGRAM_BOT_TOKEN
        file_resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        file_resp.raise_for_status()
        file_path = file_resp.json()["result"]["file_path"]

        dl_resp = httpx.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=30,
        )
        dl_resp.raise_for_status()

        sample_dir = os.path.join(settings.MEDIA_STORAGE_PATH, "voice_samples")
        Path(sample_dir).mkdir(parents=True, exist_ok=True)
        sample_path = os.path.join(sample_dir, f"{profile.id}.ogg")
        with open(sample_path, "wb") as f:
            f.write(dl_resp.content)

        profile.sample_file_path = sample_path
        db.commit()

        # Ask for consent
        _send_reply(
            chat_id,
            f"Thank you for recording! Do you consent to your voice being used "
            f"for medication reminders for {patient.full_name.split()[0]}?\n\n"
            f"Reply *YES* to consent or *NO* to decline.",
        )
    except Exception as exc:
        logger.error("Failed to download caregiver voice for patient %s: %s", patient.id, exc)


def _handle_caregiver_text(db: Session, patient: Patient, chat_id: str, text: str) -> bool:
    """Handle caregiver consent reply for voice cloning. Returns True if handled."""
    profile = (
        db.query(VoiceProfile)
        .filter(
            VoiceProfile.patient_id == patient.id,
            VoiceProfile.donor_telegram_id == chat_id,
            VoiceProfile.is_active == True,
            VoiceProfile.sample_file_path.isnot(None),
            VoiceProfile.donor_consent_at.is_(None),
        )
        .first()
    )
    if not profile:
        return False  # Not in consent flow — let normal routing handle

    from datetime import datetime as _dt
    lower = text.strip().lower()
    if any(k in lower for k in ("yes", "ya", "同意", "是")):
        profile.donor_consent_at = now_sgt()
        db.commit()

        # If patient consent is also present, trigger cloning
        if profile.patient_consent_at:
            from app.services.voice_clone_service import clone_voice
            success = clone_voice(db, profile)
            if success:
                _send_reply(chat_id, "Your voice has been set up for reminders. Thank you!")
            else:
                _send_reply(chat_id, "Thank you for your consent. We'll set up the voice reminders shortly.")
        else:
            _send_reply(chat_id, "Thank you for your consent! Voice reminders will be activated once the patient also consents.")
        return True
    elif any(k in lower for k in ("no", "tidak", "不", "拒绝")):
        profile.is_active = False
        db.commit()
        _send_reply(chat_id, "No problem. Voice cloning has been cancelled.")
        return True

    return False  # Not a consent reply


def _handle_ocr_confirm(db: Session, patient: Patient) -> None:
    """Patient confirmed the OCR-extracted fields. Auto-populate medications and advance onboarding."""
    from app.services import telegram_service as _tg
    pending = json.loads(patient.consent_channel or "{}")
    scan_id = pending.get("pending_scan_id")
    scan = db.query(PrescriptionScan).filter(PrescriptionScan.id == scan_id).first() if scan_id else None

    if not scan:
        logger.error("OCR confirm: no pending scan for patient %s", patient.id)
        _send_reply(patient.telegram_chat_id, "Something went wrong. Please send the photo again.")
        return

    scan.status = "patient_confirmed"
    db.commit()

    # Background alert for coordinator (non-blocking)
    escalation_service.create_escalation(
        db=db,
        patient_id=patient.id,
        reason="ocr_patient_confirmed",
        priority="low",
    )

    # Auto-populate Medication, PatientMedication, DispensingRecord
    ocr_service._auto_populate_medication(db, scan)

    # Advance onboarding
    patient.onboarding_state = "confirm"
    patient.consent_channel = None
    db.commit()

    lang = patient.language_preference or "en"
    CONFIRM_ACK = {
        "en": "Great, medications added! ✅",
        "zh": "好的，药物已添加！✅",
        "ms": "Bagus, ubat telah ditambah! ✅",
        "ta": "சரி, மருந்துகள் சேர்க்கப்பட்டன! ✅",
    }
    _tg.send_text(
        db=db,
        patient_id=patient.id,
        to_phone=patient.telegram_chat_id or patient.phone_number,
        body=CONFIRM_ACK.get(lang, CONFIRM_ACK["en"]),
    )

    # Reminder-time auto-setup (Phase 3)
    _setup_reminder_times_from_scan(db, patient, scan)

    # Medication info card (Phase 4)
    _send_medication_info_card(db, patient)


def _handle_ocr_edit(db: Session, patient: Patient) -> None:
    """Patient chose to have the scan reviewed by coordinator."""
    pending = json.loads(patient.consent_channel or "{}")
    scan_id = pending.get("pending_scan_id")
    if scan_id:
        scan = db.query(PrescriptionScan).filter(PrescriptionScan.id == scan_id).first()
        if scan:
            scan.status = "review"
            db.commit()
    patient.onboarding_state = "medication_capture"
    patient.consent_channel = None
    db.commit()
    send_text(
        db=db,
        patient_id=patient.id,
        to_phone=patient.telegram_chat_id or patient.phone_number,
        body="Understood — your care team will review this and update your records shortly.",
    )


def _re_prompt_ocr_confirmation(db: Session, patient: Patient) -> None:
    """Patient sent an unrecognised reply while in OCR confirmation state — re-send buttons."""
    pending = json.loads(patient.consent_channel or "{}")
    scan_id = pending.get("pending_scan_id")
    scan = db.query(PrescriptionScan).filter(PrescriptionScan.id == scan_id).first() if scan_id else None
    if scan:
        onboarding_service.send_ocr_confirmation_prompt(db, patient, scan)


def _setup_reminder_times_from_scan(db: Session, patient: Patient, scan: "PrescriptionScan") -> None:
    """Parse OCR frequency and set PatientMedication.reminder_times; prompt patient to confirm."""
    from app.services import telegram_service as _tg

    field_map = {f.field_name: f.extracted_value for f in scan.fields if f.extracted_value}
    frequency = field_map.get("frequency")
    times = ocr_service._parse_frequency_to_times(frequency)

    # Find the most recently created active PatientMedication for this patient
    pm = (
        db.query(PatientMedication)
        .filter(PatientMedication.patient_id == patient.id, PatientMedication.is_active == True)  # noqa: E712
        .order_by(PatientMedication.created_at.desc())
        .first()
    )
    if not pm:
        return

    if times:
        pm.reminder_times = times
        db.commit()
        # Store pm.id so schedule_confirm handler can find it
        patient.consent_channel = json.dumps({"schedule_pm_id": pm.id, "reminder_times": times})
        patient.pending_action = "schedule_confirm"
        db.commit()

        lang = patient.language_preference or "en"
        time_labels = " and ".join(
            datetime.strptime(t, "%H:%M").strftime("%-I:%M %p") for t in times
        )
        SCHEDULE_PROMPTS = {
            "en": f"⏰ I've set up your reminders: {time_labels} daily.\n\nReply OK to keep this schedule or type your preferred times (e.g. \"7am and 9pm\").",
            "zh": f"⏰ 我已设置提醒时间：每天 {time_labels}。\n\n回复「好的」保留此安排，或输入您的首选时间（例如：早上7点和晚上9点）。",
            "ms": f"⏰ Saya telah tetapkan peringatan anda: {time_labels} setiap hari.\n\nBalas OK untuk kekal atau taip waktu pilihan anda (cth. \"7 pagi dan 9 malam\").",
            "ta": f"⏰ நினைவூட்டல்களை அமைத்தேன்: தினமும் {time_labels}.\n\nஇந்த அட்டவணையை வைத்திருக்க OK என்று பதிலளிக்கவும் அல்லது விரும்பிய நேரங்களை தட்டச்சு செய்யவும்.",
        }
        _tg.send_text(
            db=db,
            patient_id=patient.id,
            to_phone=patient.telegram_chat_id or patient.phone_number,
            body=SCHEDULE_PROMPTS.get(lang, SCHEDULE_PROMPTS["en"]),
        )


def _send_medication_info_card(db: Session, patient: Patient) -> None:
    """Send a medication info card for the most recently added medication (if not yet sent)."""
    from app.services import telegram_service as _tg
    from app.services.medication_info_service import generate_info_card

    pm = (
        db.query(PatientMedication)
        .filter(PatientMedication.patient_id == patient.id, PatientMedication.is_active == True)  # noqa: E712
        .order_by(PatientMedication.created_at.desc())
        .first()
    )
    if not pm or pm.med_info_card_sent_at:
        return

    from app.models.models import Medication as MedModel
    med = db.query(MedModel).filter(MedModel.id == pm.medication_id).first()
    if not med:
        return

    lang = patient.language_preference or "en"
    condition = patient.conditions[0] if patient.conditions else None
    card = generate_info_card(med.name, lang, condition=condition)
    _tg.send_text(
        db=db,
        patient_id=patient.id,
        to_phone=patient.telegram_chat_id or patient.phone_number,
        body=card,
    )
    pm.med_info_card_sent_at = now_sgt()
    db.commit()


def _handle_photo(db: Session, patient: Patient, message: dict) -> None:
    """Download Telegram photo and route to OCR pipeline."""
    import httpx
    photos = message.get("photo", [])
    if not photos:
        return
    # Use the largest photo (last in the array)
    file_id = photos[-1].get("file_id")
    if not file_id:
        return
    try:
        token = settings.TELEGRAM_BOT_TOKEN
        # Get file path from Telegram
        file_resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        file_resp.raise_for_status()
        file_path = file_resp.json()["result"]["file_path"]

        # Download the file
        dl_resp = httpx.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=30,
        )
        dl_resp.raise_for_status()

        scan = ocr_service.ingest_image(
            db=db,
            patient_id=patient.id,
            image_bytes=dl_resp.content,
            source="telegram_photo",
        )
        # OCR fast-path: patient self-confirmation for high-confidence scans during onboarding
        if (
            patient.onboarding_state == "medication_capture"
            and ocr_service._is_high_confidence(scan)
        ):
            scan.status = "patient_pending"
            patient.onboarding_state = "patient_pending_ocr_confirmation"
            patient.consent_channel = json.dumps({"pending_scan_id": scan.id})
            db.commit()
            onboarding_service.send_ocr_confirmation_prompt(db, patient, scan)
        else:
            send_text(
                db=db,
                patient_id=patient.id,
                to_phone=patient.telegram_chat_id or patient.phone_number,
                body=(
                    "I've received your prescription. "
                    "Your care team will review it and get back to you shortly."
                ),
            )
    except Exception as exc:
        logger.error("Failed to download/process Telegram photo for patient %s: %s", patient.id, exc)


def _send_reply(chat_id: str, text: str) -> None:
    """Quick reply via Telegram (no DB record, for unknown users)."""
    import httpx
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


TAKEN_ACK: dict[str, str] = {
    "en": "Great job! 👍 Your medication has been recorded as taken. Keep it up!",
    "zh": "做得好！👍 您的服药记录已更新。继续保持！",
    "ms": "Bagus sekali! 👍 Ubat anda telah direkodkan sebagai sudah diambil. Teruskan!",
    "ta": "சரிதான்! 👍 உங்கள் மருந்து எடுத்தது பதிவு செய்யப்பட்டது. தொடர்ந்து வாருங்கள்!",
}


def _handle_taken(db: Session, patient: Patient) -> None:
    """Reset missed-dose streak for patient, log dose events, and send ack."""
    from datetime import datetime as _dt
    from app.services.dose_log_service import log_dose
    active_meds = (
        db.query(PatientMedication)
        .filter(
            PatientMedication.patient_id == patient.id,
            PatientMedication.is_active == True,  # noqa: E712
        )
        .all()
    )
    for pm in active_meds:
        pm.last_taken_at = now_sgt()
        pm.consecutive_missed_doses = 0
        log_dose(db, patient.id, pm.medication_id, "taken", "patient_reply", patient_medication_id=pm.id)
    db.commit()

    lang = patient.language_preference if patient.language_preference in TAKEN_ACK else "en"
    send_text(db, patient.id, patient.telegram_chat_id or patient.phone_number, TAKEN_ACK[lang])
