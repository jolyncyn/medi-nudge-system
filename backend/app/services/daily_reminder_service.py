"""
Daily medication-taking reminder service.
Sends Telegram reminders to patients based on their per-medication schedule.
Runs every 30 minutes; fires reminders whose scheduled time falls within the current window.
Tracks consecutive missed doses and escalates to caregiver when threshold is reached.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session, joinedload
from app.core.config import settings
from app.core.timezone import now_sgt
from app.core.database import SessionLocal
from app.models.models import Patient, PatientMedication
from app.services import telegram_service, tts_service
from app.services.nudge_generator import generate_daily_reminder
from app.services import caregiver_service

logger = logging.getLogger(__name__)
SGT = ZoneInfo("Asia/Singapore")

_TAKEN_LABELS = {
    "en": "✅ Taken",
    "zh": "✅ 已服",
    "ms": "✅ Sudah",
    "ta": "✅ எடுத்தேன்",
}


def _taken_button(lang: str | None) -> list[list[dict]]:
    """Return an inline keyboard with a single 'Taken' button (callback_data: TAKEN)."""
    label = _TAKEN_LABELS.get(lang or "en", _TAKEN_LABELS["en"])
    return [[{"text": label, "callback_data": "TAKEN"}]]

# Default reminder times by frequency (SGT)
FREQUENCY_DEFAULTS: dict[str, list[str]] = {
    "once_daily":        ["08:00"],
    "twice_daily":       ["08:00", "20:00"],
    "three_times_daily": ["08:00", "13:00", "20:00"],
    "every_other_day":   ["08:00"],
    "weekly":            ["08:00"],
    "as_needed":         [],
}


def _in_window(time_str: str, now_sgt: datetime, window_minutes: int = 14) -> bool:
    """Return True if the HH:MM time falls within ±window_minutes of now (SGT)."""
    try:
        h, m = map(int, time_str.split(":"))
    except ValueError:
        return False
    scheduled = now_sgt.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = abs((now_sgt - scheduled).total_seconds())
    return delta <= window_minutes * 60


def send_scheduled_reminders(db: Session | None = None, *, skip_window: bool = False) -> dict:
    """
    Called every 30 minutes. For each patient medication, check if any
    reminder_time falls within the current 30-minute window and send the nudge.
    Groups all due medications for a patient into a single message.

    When skip_window=True, sends reminders for ALL active medications regardless
    of scheduled time (useful for manual triggers).
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    now_sgt = datetime.now(SGT)
    results = {"patients_checked": 0, "reminders_sent": 0, "skipped": 0, "errors": 0}

    try:
        patients = (
            db.query(Patient)
            .filter(
                Patient.is_active == True,  # noqa: E712
                Patient.onboarding_state == "complete",
            )
            .all()
        )

        for patient in patients:
            results["patients_checked"] += 1
            try:
                _send_due_reminders(db, patient, now_sgt, results, skip_window=skip_window)
            except Exception as exc:
                logger.error("Reminder error for patient %s: %s", patient.id, exc)
                results["errors"] += 1

    finally:
        if owns_session:
            db.close()

    logger.info("Scheduled reminders (%s SGT): %s", now_sgt.strftime("%H:%M"), results)
    return results


def _send_due_reminders(
    db: Session, patient: Patient, now_sgt: datetime, results: dict,
    *, skip_window: bool = False,
) -> None:
    active_meds = (
        db.query(PatientMedication)
        .options(joinedload(PatientMedication.medication))
        .filter(
            PatientMedication.patient_id == patient.id,
            PatientMedication.is_active == True,  # noqa: E712
        )
        .all()
    )

    if not active_meds:
        results["skipped"] += 1
        return

    # Collect medications that are due right now
    due_meds_pms: list[PatientMedication] = []
    for pm in active_meds:
        if skip_window:
            due_meds_pms.append(pm)
        else:
            times = pm.reminder_times or FREQUENCY_DEFAULTS.get(pm.frequency, [])
            if any(_in_window(t, now_sgt) for t in times):
                due_meds_pms.append(pm)

    if not due_meds_pms:
        results["skipped"] += 1
        return

    # --- Missed dose tracking ---
    # For each due medication, check if the LAST reminder was acknowledged (patient said TAKEN).
    # If not, it counts as a miss. Accumulate meds that hit the threshold for caregiver alert.
    caregiver_alert_meds: list[str] = []
    threshold = settings.MISSED_DOSE_ESCALATION_THRESHOLD
    grace = timedelta(hours=4)  # Allow up to 4h after reminder_time for a TAKEN response

    for pm in due_meds_pms:
        was_missed = False
        if pm.last_reminded_at:
            # If last_taken_at is absent or predates the last reminder (plus grace window)
            ack_deadline = pm.last_reminded_at + grace
            if now_sgt.replace(tzinfo=None) > ack_deadline.replace(tzinfo=None) if ack_deadline.tzinfo is None else now_sgt > ack_deadline:
                took_it = pm.last_taken_at and pm.last_taken_at >= pm.last_reminded_at
                if not took_it:
                    was_missed = True

        if was_missed:
            pm.consecutive_missed_doses = (pm.consecutive_missed_doses or 0) + 1
            from app.services.dose_log_service import log_dose
            log_dose(db, patient.id, pm.medication_id, "missed", "system_detected", patient_medication_id=pm.id)
            logger.debug(
                "Patient %s missed %s (streak: %d)",
                patient.id, pm.medication.name if pm.medication else pm.medication_id,
                pm.consecutive_missed_doses,
            )
            if pm.consecutive_missed_doses >= threshold:
                med_name = pm.medication.name if pm.medication else f"Medication #{pm.medication_id}"
                caregiver_alert_meds.append(med_name)
        # Always update last_reminded_at AFTER the window check
        pm.last_reminded_at = now_sgt()

    db.commit()

    # Notify caregiver if any medication hit threshold
    if caregiver_alert_meds and patient.caregiver_telegram_id:
        max_streak = max(pm.consecutive_missed_doses for pm in due_meds_pms)
        caregiver_service.notify_caregiver(
            db=db,
            patient=patient,
            missed_medications=caregiver_alert_meds,
            consecutive_count=max_streak,
        )

    # Send the actual reminder to the patient
    due_med_names = []
    for pm in due_meds_pms:
        med_name = pm.medication.name if pm.medication else f"Medication #{pm.medication_id}"
        dosage_suffix = f" ({pm.dosage})" if pm.dosage else ""
        due_med_names.append(f"{med_name}{dosage_suffix}")

    message = generate_daily_reminder(
        name=patient.full_name.split()[0],
        medications=due_med_names,
        language=patient.language_preference,
        conditions=patient.conditions,
    )

    chat_target = patient.telegram_chat_id or patient.phone_number
    send_text = patient.nudge_delivery_mode in ("text", "both")
    send_voice = patient.nudge_delivery_mode in ("voice", "both")

    if send_text or not send_voice:
        telegram_service.send_keyboard(
            db=db,
            patient_id=patient.id,
            to_phone=chat_target,
            body=message,
            buttons=_taken_button(patient.language_preference),
        )

    if send_voice:
        ogg_path = tts_service.generate_voice_message(
            text=message,
            voice_id=patient.selected_voice_id,
            patient_id=patient.id,
        )
        if ogg_path:
            telegram_service.send_voice(
                db=db,
                patient_id=patient.id,
                to_phone=chat_target,
                ogg_path=ogg_path,
            )
        elif not send_text:
            # Voice failed, no text sent yet — fall back with button
            telegram_service.send_keyboard(
                db=db,
                patient_id=patient.id,
                to_phone=chat_target,
                body=message,
                buttons=_taken_button(patient.language_preference),
            )

    results["reminders_sent"] += 1
    logger.debug(
        "Sent reminder to patient %s for: %s (time: %s SGT)",
        patient.id, due_med_names, now_sgt.strftime("%H:%M"),
    )
