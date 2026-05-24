"""
Escalation service — creates and transitions EscalationCase records.
A side_effect escalation MUST always be created; it can never be suppressed.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import (
    EscalationCase, ESCALATION_VALID_TRANSITIONS, ESCALATION_PRIORITY_ORDER,
)

REASON_PRIORITY_MAP = {
    "side_effect": "urgent",
    "post_discharge_safety": "urgent",
    "no_response": "high",
    "repeated_non_adherence": "high",
    "onboarding_drop_off": "normal",
    "patient_question": "normal",
    "routine": "low",
}


def create_escalation(
    db: Session,
    patient_id: int,
    reason: str,
    nudge_campaign_id: int | None = None,
    priority: str | None = None,
) -> EscalationCase:
    """Create an EscalationCase. Priority is derived from reason if not provided."""
    resolved_priority = priority or REASON_PRIORITY_MAP.get(reason, "normal")
    case = EscalationCase(
        nudge_campaign_id=nudge_campaign_id,
        patient_id=patient_id,
        reason=reason,
        priority=resolved_priority,
        status="open",
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def transition_escalation(db: Session, case: EscalationCase, new_status: str) -> EscalationCase:
    allowed = ESCALATION_VALID_TRANSITIONS.get(case.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition EscalationCase from '{case.status}' to '{new_status}'"
        )
    case.status = new_status
    if new_status == "resolved":
        case.resolved_at = now_sgt()
    db.commit()
    db.refresh(case)
    return case


def update_escalation(
    db: Session,
    case: EscalationCase,
    status: str | None = None,
    assigned_to: str | None = None,
    notes: str | None = None,
) -> EscalationCase:
    if status and status != case.status:
        case = transition_escalation(db, case, status)
    if assigned_to is not None:
        case.assigned_to = assigned_to
    if notes is not None:
        case.notes = notes
    db.commit()
    db.refresh(case)
    return case
