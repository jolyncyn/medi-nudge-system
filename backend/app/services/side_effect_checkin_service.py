"""
Side-effect check-in service.
Daily job: for each medication activated 3-4 days ago, send a one-time
"How are you getting on?" check-in campaign to the patient.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.timezone import now_sgt

logger = logging.getLogger(__name__)



def run_side_effect_checkin_check(
    db: Session | None = None,
    patient_id: int | None = None,
) -> dict:
    """Query medications activated 3-4 days ago and send one-time check-in campaigns.

    When ``patient_id`` is provided the date-window filter is skipped and check-ins
    are created immediately for all active medications of that patient. This is used
    to trigger check-ins right after onboarding completes.
    """
    from app.core.database import SessionLocal
    from app.models.models import Patient, PatientMedication, NudgeCampaign, Medication as MedModel

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    results = {"checked": 0, "sent": 0, "skipped": 0, "errors": 0}

    try:
        query = db.query(PatientMedication).filter(
            PatientMedication.is_active == True,  # noqa: E712
        )

        if patient_id is not None:
            # Immediate trigger for a specific patient — skip the date window
            query = query.filter(PatientMedication.patient_id == patient_id)
        else:
            now = now_sgt()
            window_start = now - timedelta(days=4)
            window_end = now - timedelta(days=3)
            query = query.filter(
                PatientMedication.created_at >= window_start,
                PatientMedication.created_at < window_end,
            )

        due_pms = query.all()

        for pm in due_pms:
            results["checked"] += 1
            try:
                patient = db.query(Patient).filter(Patient.id == pm.patient_id).first()
                if not patient or patient.onboarding_state != "complete" or not patient.is_active:
                    results["skipped"] += 1
                    continue

                # One-time only: skip if any check-in campaign already exists for this pair
                existing = (
                    db.query(NudgeCampaign)
                    .filter(
                        NudgeCampaign.patient_id == pm.patient_id,
                        NudgeCampaign.medication_id == pm.medication_id,
                        NudgeCampaign.campaign_type == "side_effect_checkin",
                    )
                    .first()
                )
                if existing:
                    results["skipped"] += 1
                    continue

                med = db.query(MedModel).filter(MedModel.id == pm.medication_id).first()
                if not med:
                    results["skipped"] += 1
                    continue

                lang = patient.language_preference or "en"
                condition = patient.conditions[0] if patient.conditions else None
                from app.services.medication_info_service import generate_checkin_message
                body = generate_checkin_message(
                    medication_name=med.name,
                    language=lang,
                    patient_name=patient.full_name.split()[0],
                    condition=condition,
                )

                # Schedule to fire 3 days after the medication was activated
                fire_at = pm.created_at + timedelta(days=3)

                from app.services.nudge_campaign_service import create_campaign
                campaign = create_campaign(
                    db=db,
                    patient=patient,
                    medication=med,
                    days_overdue=0,
                    fire_at=fire_at,
                    attempt=1,
                    campaign_type="side_effect_checkin",
                    message_content=body,
                )
                results["sent"] += 1  # "scheduled" — will be fired by scheduler at fire_at

            except Exception as exc:
                logger.error("Check-in error for PatientMedication %s: %s", pm.id, exc)
                results["errors"] += 1

    finally:
        if owns_session:
            db.close()

    logger.info("Side-effect check-in results: %s", results)
    return results
