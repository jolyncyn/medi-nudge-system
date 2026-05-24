"""Analytics and nudge campaign routes."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import timedelta
from app.core.database import get_db
from app.core.timezone import now_sgt
from app.core.security import get_current_user
from app.models.models import (
    NudgeCampaign, EscalationCase, User, DoseLog, Medication,
    Patient, PatientMedication, DispensingRecord,
)
from app.schemas.schemas import NudgeCampaignOut
from app.services import refill_gap_service
from app.services.daily_reminder_service import send_scheduled_reminders

router = APIRouter(tags=["campaigns & analytics"])


@router.get("/api/nudge-campaigns", response_model=list[NudgeCampaignOut])
def list_campaigns(
    patient_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(NudgeCampaign)
    if patient_id:
        q = q.filter(NudgeCampaign.patient_id == patient_id)
    if status:
        q = q.filter(NudgeCampaign.status == status)
    return q.order_by(NudgeCampaign.created_at.desc()).all()


@router.get("/api/analytics/adherence")
def adherence_analytics(
    days: int = Query(default=90, ge=7, le=365),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Weekly adherence rate: % campaigns resolved with 'confirmed' response."""
    since = now_sgt() - timedelta(days=days)
    campaigns = (
        db.query(NudgeCampaign)
        .filter(NudgeCampaign.created_at >= since)
        .all()
    )
    # Bucket by ISO week
    weekly: dict[str, dict] = {}
    for c in campaigns:
        week = c.created_at.strftime("%Y-W%W")
        if week not in weekly:
            weekly[week] = {"week": week, "total": 0, "confirmed": 0}
        weekly[week]["total"] += 1
        if c.response_type == "confirmed":
            weekly[week]["confirmed"] += 1
    result = []
    for w in sorted(weekly.keys()):
        d = weekly[w]
        rate = round(d["confirmed"] / d["total"] * 100, 1) if d["total"] else 0.0
        result.append({"week": w, "total": d["total"], "confirmed": d["confirmed"], "adherence_rate": rate})
    return result


@router.get("/api/analytics/escalations")
def escalation_analytics(
    days: int = Query(default=90, ge=7, le=365),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Weekly escalation volume by priority."""
    since = now_sgt() - timedelta(days=days)
    cases = db.query(EscalationCase).filter(EscalationCase.created_at >= since).all()
    weekly: dict[str, dict] = {}
    for c in cases:
        week = c.created_at.strftime("%Y-W%W")
        if week not in weekly:
            weekly[week] = {"week": week, "total": 0, "urgent": 0, "high": 0, "normal": 0, "low": 0}
        weekly[week]["total"] += 1
        priority = c.priority if c.priority in ("urgent", "high", "normal", "low") else "normal"
        weekly[week][priority] += 1
    return [weekly[w] for w in sorted(weekly.keys())]


@router.post("/api/nudge-campaigns/trigger")
def trigger_nudge_campaigns(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Manually trigger refill gap detection and nudge campaign creation for all patients."""
    return refill_gap_service.detect_and_trigger(db)


@router.post("/api/reminders/trigger")
def trigger_daily_reminders(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Manually trigger daily medication reminders for all active patients."""
    return send_scheduled_reminders(db, skip_window=True)


@router.post("/api/patients/{patient_id}/nudge/trigger")
def trigger_patient_nudge(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Fire all pending nudge campaigns for a patient immediately (testing / manual override)."""
    from app.services.nudge_campaign_service import fire_campaign
    from app.models.models import NudgeCampaign

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Patient not found")

    pending = (
        db.query(NudgeCampaign)
        .filter(
            NudgeCampaign.patient_id == patient_id,
            NudgeCampaign.status == "pending",
        )
        .all()
    )

    results = {"campaigns_fired": 0, "campaigns_failed": 0, "errors": 0}
    for campaign in pending:
        try:
            fire_campaign(db, campaign)
            if campaign.status == "sent":
                results["campaigns_fired"] += 1
            else:
                results["campaigns_failed"] += 1
        except Exception:
            results["errors"] += 1

    return results


@router.post("/api/patients/{patient_id}/reminder/trigger")
def trigger_patient_reminder(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Manually trigger daily medication reminder for a single patient."""
    from app.services.daily_reminder_service import _send_due_reminders, FREQUENCY_DEFAULTS
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from sqlalchemy.orm import joinedload

    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.is_active == True).first()
    if not patient:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Patient not found")

    now_sgt = _dt.now(ZoneInfo("Asia/Singapore"))
    results = {"patients_checked": 1, "reminders_sent": 0, "skipped": 0, "errors": 0}
    try:
        _send_due_reminders(db, patient, now_sgt, results, skip_window=True)
    except Exception:
        results["errors"] += 1
    return results


@router.get("/api/dashboard/summary")
def dashboard_summary(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Dashboard KPIs: adherence, high-risk count, pending refills, at-risk patients, escalations."""
    now = now_sgt()
    since_30d = now - timedelta(days=30)
    since_60d = now - timedelta(days=60)

    # Overall dose adherence (last 30 days)
    dose_logs_30d = db.query(DoseLog).filter(DoseLog.logged_at >= since_30d).all()
    taken_30d = sum(1 for d in dose_logs_30d if d.status == "taken")
    total_30d = len(dose_logs_30d)
    overall_adherence = round(taken_30d / total_30d * 100, 1) if total_30d else 0.0

    # Adherence trend (compare last 30d vs prior 30d)
    dose_logs_prior = db.query(DoseLog).filter(
        DoseLog.logged_at >= since_60d, DoseLog.logged_at < since_30d
    ).all()
    taken_prior = sum(1 for d in dose_logs_prior if d.status == "taken")
    total_prior = len(dose_logs_prior)
    prior_adherence = round(taken_prior / total_prior * 100, 1) if total_prior else 0.0
    adherence_trend = round(overall_adherence - prior_adherence, 1)

    # High risk patient count
    high_risk_count = db.query(Patient).filter(
        Patient.is_active == True, Patient.risk_level == "high"
    ).count()

    # Pending refills (campaigns in "sent" state = awaiting patient response)
    pending_refills = db.query(NudgeCampaign).filter(
        NudgeCampaign.status == "sent"
    ).count()

    # Pre-compute critical medication IDs
    critical_med_ids = set(
        m.id for m in db.query(Medication).filter(Medication.is_critical == True).all()
    )

    # At-risk patients: top 10 by worst dose adherence
    active_patients = db.query(Patient).filter(
        Patient.is_active == True, Patient.onboarding_state == "complete"
    ).all()
    patient_adherence = []
    patient_compliance = {}
    critical_missed_total = 0
    for p in active_patients:
        logs = [d for d in dose_logs_30d if d.patient_id == p.id]
        total = len(logs)
        taken = sum(1 for d in logs if d.status == "taken")
        rate = round(taken / total * 100, 1) if total else 100.0

        critical_missed = sum(
            1 for d in logs if d.status == "missed" and d.medication_id in critical_med_ids
        )
        critical_missed_total += critical_missed

        patient_compliance[p.id] = {
            "compliance_score": rate,
            "critical_missed_count": critical_missed,
        }

        # Last refill date
        last_disp = (
            db.query(DispensingRecord)
            .filter(DispensingRecord.patient_id == p.id)
            .order_by(DispensingRecord.dispensed_at.desc())
            .first()
        )
        last_refill = last_disp.dispensed_at.isoformat() if last_disp else None

        # Days overdue (from most recent campaign)
        latest_campaign = (
            db.query(NudgeCampaign)
            .filter(NudgeCampaign.patient_id == p.id)
            .order_by(NudgeCampaign.created_at.desc())
            .first()
        )
        days_overdue = latest_campaign.days_overdue if latest_campaign and latest_campaign.status == "sent" else 0

        patient_adherence.append({
            "id": p.id,
            "full_name": p.full_name,
            "risk_level": p.risk_level,
            "adherence_rate": rate,
            "critical_missed_count": critical_missed,
            "last_refill": last_refill,
            "days_overdue": days_overdue,
        })
    patient_adherence.sort(key=lambda x: x["adherence_rate"])
    at_risk_patients = patient_adherence[:10]

    # Pending escalations (open status)
    open_escalations = (
        db.query(EscalationCase)
        .filter(EscalationCase.status == "open")
        .order_by(EscalationCase.created_at.desc())
        .limit(10)
        .all()
    )
    escalations = []
    for e in open_escalations:
        patient = db.query(Patient).filter(Patient.id == e.patient_id).first()
        escalations.append({
            "id": e.id,
            "patient_id": e.patient_id,
            "patient_name": patient.full_name if patient else "Unknown",
            "reason": e.reason,
            "priority": e.priority,
            "notes": e.notes,
            "created_at": e.created_at.isoformat(),
        })

    return {
        "overall_adherence": overall_adherence,
        "adherence_trend": adherence_trend,
        "high_risk_count": high_risk_count,
        "pending_refills": pending_refills,
        "critical_missed_total": critical_missed_total,
        "at_risk_patients": at_risk_patients,
        "patient_compliance": patient_compliance,
        "pending_escalations": escalations,
    }


@router.get("/api/patients/{patient_id}/dose-history")
def get_dose_history(
    patient_id: int,
    days: int = Query(default=30, ge=1, le=365),
    medication_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Per-patient dose history with optional medication filter."""
    since = now_sgt() - timedelta(days=days)
    q = db.query(DoseLog).filter(
        DoseLog.patient_id == patient_id,
        DoseLog.logged_at >= since,
    )
    if medication_id:
        q = q.filter(DoseLog.medication_id == medication_id)
    logs = q.order_by(DoseLog.logged_at.desc()).all()

    result = []
    for log in logs:
        med = db.query(Medication).filter(Medication.id == log.medication_id).first()
        result.append({
            "id": log.id,
            "patient_id": log.patient_id,
            "medication_id": log.medication_id,
            "medication_name": med.name if med else f"Medication #{log.medication_id}",
            "status": log.status,
            "source": log.source,
            "logged_at": log.logged_at.isoformat(),
            "created_at": log.created_at.isoformat(),
        })
    return result


@router.get("/api/analytics/dose-adherence")
def dose_adherence_analytics(
    days: int = Query(default=90, ge=7, le=365),
    group_by: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Dose-level adherence analytics. Optionally group_by=medication."""
    since = now_sgt() - timedelta(days=days)
    logs = db.query(DoseLog).filter(DoseLog.logged_at >= since).all()

    if group_by == "medication":
        by_med: dict[int, dict] = {}
        for log in logs:
            mid = log.medication_id
            if mid not in by_med:
                med = db.query(Medication).filter(Medication.id == mid).first()
                by_med[mid] = {
                    "medication_id": mid,
                    "medication_name": med.name if med else f"#{mid}",
                    "total": 0, "taken": 0, "missed": 0,
                }
            by_med[mid]["total"] += 1
            if log.status == "taken":
                by_med[mid]["taken"] += 1
            elif log.status == "missed":
                by_med[mid]["missed"] += 1
        result = []
        for d in by_med.values():
            d["adherence_rate"] = round(d["taken"] / d["total"] * 100, 1) if d["total"] else 0.0
            result.append(d)
        return sorted(result, key=lambda x: x["adherence_rate"])

    # Default: weekly buckets
    weekly: dict[str, dict] = {}
    for log in logs:
        week = log.logged_at.strftime("%Y-W%W")
        if week not in weekly:
            weekly[week] = {"week": week, "total": 0, "taken": 0, "missed": 0}
        weekly[week]["total"] += 1
        if log.status == "taken":
            weekly[week]["taken"] += 1
        elif log.status == "missed":
            weekly[week]["missed"] += 1
    result = []
    for w in sorted(weekly.keys()):
        d = weekly[w]
        d["adherence_rate"] = round(d["taken"] / d["total"] * 100, 1) if d["total"] else 0.0
        result.append(d)
    return result


@router.get("/api/analytics/critical-adherence")
def critical_adherence_analytics(
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Weekly adherence split by critical vs non-critical medications."""
    critical_med_ids = set(
        m.id for m in db.query(Medication).filter(Medication.is_critical == True).all()
    )
    since = now_sgt() - timedelta(days=days)
    logs = db.query(DoseLog).filter(DoseLog.logged_at >= since).all()

    weekly: dict[str, dict] = {}
    for log in logs:
        week = log.logged_at.strftime("%Y-W%W")
        if week not in weekly:
            weekly[week] = {"week": week, "critical_total": 0, "critical_taken": 0, "non_critical_total": 0, "non_critical_taken": 0}
        is_crit = log.medication_id in critical_med_ids
        if is_crit:
            weekly[week]["critical_total"] += 1
            if log.status == "taken":
                weekly[week]["critical_taken"] += 1
        else:
            weekly[week]["non_critical_total"] += 1
            if log.status == "taken":
                weekly[week]["non_critical_taken"] += 1

    result = []
    for w in sorted(weekly.keys()):
        d = weekly[w]
        d["critical_adherence"] = round(d["critical_taken"] / d["critical_total"] * 100, 1) if d["critical_total"] else 0.0
        d["non_critical_adherence"] = round(d["non_critical_taken"] / d["non_critical_total"] * 100, 1) if d["non_critical_total"] else 0.0
        result.append(d)
    return result


@router.get("/api/analytics/missed-dose-heatmap")
def missed_dose_heatmap(
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Missed dose counts grouped by day of week and time of day."""
    since = now_sgt() - timedelta(days=days)
    logs = db.query(DoseLog).filter(DoseLog.logged_at >= since, DoseLog.status == "missed").all()

    days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    slots = ["Morning", "Afternoon", "Evening", "Night"]
    counts: dict[tuple[str, str], int] = {}
    for day in days_order:
        for slot in slots:
            counts[(day, slot)] = 0

    for log in logs:
        dow = log.logged_at.weekday()
        day_name = days_order[dow]
        hour = log.logged_at.hour
        if 6 <= hour < 12:
            slot = "Morning"
        elif 12 <= hour < 17:
            slot = "Afternoon"
        elif 17 <= hour < 22:
            slot = "Evening"
        else:
            slot = "Night"
        counts[(day_name, slot)] += 1

    return [{"day": day, "time_slot": slot, "missed_count": counts[(day, slot)]} for day in days_order for slot in slots]
