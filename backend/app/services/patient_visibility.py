"""Shared patient visibility rules for demo-facing registry views."""
from sqlalchemy.orm import Query, Session

from app.models.models import Patient

INCOMPLETE_SELF_REGISTRATION_STATES = {
    "self_lang",
    "self_consent",
    "self_name",
    "self_nric",
    "self_condition",
    "self_registering",
}


def visible_patients_query(db: Session) -> Query:
    return db.query(Patient).filter(
        Patient.onboarding_state.notin_(INCOMPLETE_SELF_REGISTRATION_STATES)
    )
