"""
Dose log service.
Centralised helper for recording dose events (taken, missed, skipped).
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.timezone import as_sgt_naive, now_sgt
from app.models.models import DoseLog

logger = logging.getLogger(__name__)


def log_dose(
    db: Session,
    patient_id: int,
    medication_id: int,
    status: str,
    source: str,
    patient_medication_id: int | None = None,
    logged_at: datetime | None = None,
) -> DoseLog:
    """Create a DoseLog record."""
    entry = DoseLog(
        patient_id=patient_id,
        medication_id=medication_id,
        patient_medication_id=patient_medication_id,
        status=status,
        source=source,
        logged_at=as_sgt_naive(logged_at) or now_sgt(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.debug(
        "DoseLog: patient=%s med=%s status=%s source=%s",
        patient_id, medication_id, status, source,
    )
    return entry
