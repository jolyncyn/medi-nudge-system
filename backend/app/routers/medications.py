"""Medication catalog and patient prescription routes."""
import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session, joinedload
from app.core.database import get_db
from app.core.timezone import as_sgt_naive, now_sgt
from app.core.security import get_current_user
from app.models.models import DoseLog, Medication, PatientMedication, Patient, DispensingRecord, User
from app.schemas.schemas import (
    MedicationCreate, MedicationOut,
    PatientMedicationCreate, PatientMedicationOut,
    DispensingRecordCreate, DispensingRecordOut,
    DoseEventCreate, DoseLogOut,
)

router = APIRouter(tags=["medications"])


# ---------------------------------------------------------------------------
# Medication catalog
# ---------------------------------------------------------------------------

@router.post("/api/medications", response_model=MedicationOut, status_code=201)
def create_medication(
    payload: MedicationCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if db.query(Medication).filter(Medication.generic_name == payload.generic_name).first():
        raise HTTPException(status_code=409, detail="Medication with this generic name already exists")
    med = Medication(**payload.model_dump())
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


@router.get("/api/medications", response_model=list[MedicationOut])
def list_medications(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return db.query(Medication).all()


@router.get("/api/medications/{med_id}", response_model=MedicationOut)
def get_medication(med_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found")
    return med


# ---------------------------------------------------------------------------
# Patient prescriptions (PatientMedication)
# ---------------------------------------------------------------------------

@router.post("/api/patients/{patient_id}/medications", response_model=PatientMedicationOut, status_code=201)
def assign_medication(
    patient_id: int,
    payload: PatientMedicationCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if not db.query(Medication).filter(Medication.id == payload.medication_id).first():
        raise HTTPException(status_code=404, detail="Medication not found")
    pm = PatientMedication(patient_id=patient_id, **payload.model_dump())
    db.add(pm)
    db.commit()
    db.refresh(pm)
    return pm


@router.get("/api/patients/{patient_id}/medications", response_model=list[PatientMedicationOut])
def list_patient_medications(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return (
        db.query(PatientMedication)
        .options(joinedload(PatientMedication.dose_logs))
        .filter(PatientMedication.patient_id == patient_id, PatientMedication.is_active == True)
        .all()
    )


@router.patch("/api/patients/{patient_id}/medications/{pm_id}", response_model=PatientMedicationOut)
def update_patient_medication(
    patient_id: int,
    pm_id: int,
    is_active: bool,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    pm = db.query(PatientMedication).filter(
        PatientMedication.id == pm_id, PatientMedication.patient_id == patient_id
    ).first()
    if not pm:
        raise HTTPException(status_code=404, detail="PatientMedication not found")
    pm.is_active = is_active
    db.commit()
    db.refresh(pm)
    return pm


@router.post(
    "/api/patients/{patient_id}/medications/{pm_id}/dose-events",
    response_model=DoseLogOut,
    status_code=201,
)
def create_dose_event(
    patient_id: int,
    pm_id: int,
    payload: DoseEventCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient_medication = (
        db.query(PatientMedication)
        .filter(PatientMedication.id == pm_id, PatientMedication.patient_id == patient_id)
        .first()
    )
    if not patient_medication:
        raise HTTPException(status_code=404, detail="PatientMedication not found")

    logged_at = as_sgt_naive(payload.logged_at or payload.scheduled_time) or now_sgt()
    event = DoseLog(
        patient_id=patient_id,
        medication_id=patient_medication.medication_id,
        patient_medication_id=patient_medication.id,
        status=payload.status,
        source=payload.source,
        logged_at=logged_at,
    )
    db.add(event)

    if payload.status == "taken":
        patient_medication.last_taken_at = logged_at
        patient_medication.consecutive_missed_doses = 0
    elif payload.status == "missed":
        patient_medication.consecutive_missed_doses = (
            patient_medication.consecutive_missed_doses or 0
        ) + 1
    # "snoozed" records intent to take later; it should not count as taken or missed.

    db.commit()
    db.refresh(event)

    medication = db.query(Medication).filter(Medication.id == event.medication_id).first()
    return DoseLogOut(
        id=event.id,
        patient_id=event.patient_id,
        medication_id=event.medication_id,
        status=event.status,
        source=event.source,
        logged_at=event.logged_at,
        created_at=event.created_at,
        medication_name=medication.name if medication else None,
    )


# ---------------------------------------------------------------------------
# Dispensing records
# ---------------------------------------------------------------------------

@router.post("/api/dispensing-records", response_model=DispensingRecordOut, status_code=201)
def create_dispensing_record(
    payload: DispensingRecordCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    record = DispensingRecord(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)

    # Pre-schedule a refill nudge campaign for when this medication becomes overdue.
    # fire_at = dispensed_at + days_supply + WARNING_DAYS
    try:
        from datetime import timedelta
        from app.models.models import Patient, Medication as MedModel, PatientMedication
        from app.services.nudge_campaign_service import create_campaign

        patient = db.query(Patient).filter(Patient.id == record.patient_id).first()
        medication = db.query(MedModel).filter(MedModel.id == record.medication_id).first()
        pm = db.query(PatientMedication).filter(
            PatientMedication.patient_id == record.patient_id,
            PatientMedication.medication_id == record.medication_id,
            PatientMedication.is_active == True,  # noqa: E712
        ).first()
        if patient and medication and pm and patient.onboarding_state == "complete":
            from app.core.config import settings as _s
            fire_at = record.dispensed_at + timedelta(days=record.days_supply + _s.WARNING_DAYS)
            create_campaign(
                db=db, patient=patient, medication=medication,
                days_overdue=0, fire_at=fire_at,
            )
    except Exception:
        pass  # Never fail the API call due to background job errors

    return record


@router.get("/api/patients/{patient_id}/dispensing-records", response_model=list[DispensingRecordOut])
def list_dispensing_records(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return (
        db.query(DispensingRecord)
        .filter(DispensingRecord.patient_id == patient_id)
        .order_by(DispensingRecord.dispensed_at.desc())
        .all()
    )


@router.post("/api/dispensing-records/import", status_code=201)
def import_dispensing_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """
    Bulk import dispensing records from a CSV.
    Expected columns: patient_id, medication_id, dispensed_at (ISO8601), days_supply, quantity, source
    """
    content = file.file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    created, skipped, errors = 0, 0, []

    for row in reader:
        try:
            dispensed_at = as_sgt_naive(datetime.fromisoformat(row["dispensed_at"]))
            # Dedup check
            exists = db.query(DispensingRecord).filter(
                DispensingRecord.patient_id == int(row["patient_id"]),
                DispensingRecord.medication_id == int(row["medication_id"]),
                DispensingRecord.dispensed_at == dispensed_at,
            ).first()
            if exists:
                skipped += 1
                continue
            record = DispensingRecord(
                patient_id=int(row["patient_id"]),
                medication_id=int(row["medication_id"]),
                dispensed_at=dispensed_at,
                days_supply=int(row["days_supply"]),
                quantity=int(row.get("quantity") or 0) or None,
                source=row.get("source", "pharmacy"),
            )
            db.add(record)
            created += 1
        except Exception as exc:
            errors.append({"row": row, "error": str(exc)})

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}
