"""
Refill gap detection service.
Runs daily via APScheduler to detect overdue medications and trigger nudges.
"""
from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.timezone import now_sgt
from app.core.database import SessionLocal
from app.models.models import (
    Patient, PatientMedication, Medication, DispensingRecord,
    NudgeCampaign,
)
from app.services import escalation_service, nudge_campaign_service

logger = logging.getLogger(__name__)


def compute_days_overdue(last_dispensed, supply_days: int):
    """Return how many days past the refill due date, or None if last_dispensed is None."""
    if last_dispensed is None:
        return None
    from datetime import date as _date
    today = _date.today()
    if isinstance(last_dispensed, datetime):
        last_dispensed = last_dispensed.date()
    due = last_dispensed + timedelta(days=supply_days)
    return (today - due).days


def detect_and_trigger(
    db: Session | None = None,
    patient_id: int | None = None,
) -> dict:
    """
    Scan active PatientMedication records for refill gaps.
    When ``patient_id`` is provided, only that patient's medications are checked.
    Returns summary counts for observability.
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    results = {"checked": 0, "campaigns_created": 0, "escalated": 0, "errors": 0}

    try:
        today = now_sgt().date()
        query = (
            db.query(PatientMedication)
            .join(Patient, PatientMedication.patient_id == Patient.id)
            .filter(PatientMedication.is_active == True, Patient.is_active == True)
        )
        if patient_id is not None:
            query = query.filter(PatientMedication.patient_id == patient_id)
        active_pms = query.all()

        for pm in active_pms:
            results["checked"] += 1
            try:
                _process_patient_medication(db, pm, today, results)
            except Exception as exc:
                logger.error("Error processing PatientMedication id=%s: %s", pm.id, exc)
                results["errors"] += 1

    finally:
        if owns_session:
            db.close()

    logger.info("Refill gap detection complete: %s", results)
    return results


def _process_patient_medication(
    db: Session,
    pm: PatientMedication,
    today,
    results: dict,
) -> None:
    # Find last dispensing record
    last_record: DispensingRecord | None = (
        db.query(DispensingRecord)
        .filter(
            DispensingRecord.patient_id == pm.patient_id,
            DispensingRecord.medication_id == pm.medication_id,
        )
        .order_by(DispensingRecord.dispensed_at.desc())
        .first()
    )

    if not last_record:
        return  # No dispensing data — skip silently

    refill_interval = pm.refill_interval_days or (
        db.query(Medication).filter(Medication.id == pm.medication_id).first().default_refill_days
    )
    due_date = (last_record.dispensed_at + timedelta(days=last_record.days_supply)).date()
    days_overdue = (today - due_date).days

    if days_overdue < settings.WARNING_DAYS:
        return  # Within window — no action

    patient = db.query(Patient).filter(Patient.id == pm.patient_id).first()
    medication = db.query(Medication).filter(Medication.id == pm.medication_id).first()

    if days_overdue >= settings.ESCALATION_DAYS:
        # Auto-escalate regardless of campaign state
        existing_escalation = None  # check not required by spec; always create if threshold met
        escalation_service.create_escalation(
            db=db,
            patient_id=pm.patient_id,
            reason="repeated_non_adherence",
            priority="high",
        )
        results["escalated"] += 1

    # Create/update nudge campaign
    nudge_campaign_service.create_and_send(
        db=db,
        patient=patient,
        medication=medication,
        days_overdue=days_overdue,
    )
    results["campaigns_created"] += 1
