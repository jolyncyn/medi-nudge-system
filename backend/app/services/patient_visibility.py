"""Shared patient visibility rules for demo-facing registry views."""
from sqlalchemy.orm import Query, Session

from app.models.models import Patient

HIDDEN_REGISTRY_PATIENT_NAMES = {"tg_51789857", "tg_1746763759"}


def visible_patients_query(db: Session) -> Query:
    return db.query(Patient).filter(
        Patient.full_name.notin_(HIDDEN_REGISTRY_PATIENT_NAMES)
    )
