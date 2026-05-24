"""Patient management routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.config import hash_sha256
from app.models.models import Patient, User, CaregiverNote
from app.schemas.schemas import PatientCreate, PatientOut, PatientUpdate, PatientListResponse, CaregiverNoteCreate, CaregiverNoteOut
from app.services.onboarding_service import generate_invite_token, generate_caregiver_invite_token

router = APIRouter(prefix="/api/patients", tags=["patients"])

HIDDEN_REGISTRY_PATIENT_NAMES = {"tg_51789857", "tg_1746763759"}


@router.post("", response_model=PatientOut, status_code=201)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Duplicate phone check
    if db.query(Patient).filter(Patient.phone_number == payload.phone_number).first():
        raise HTTPException(status_code=409, detail="A patient with this phone number already exists")

    # NRIC: hash before storage, never persist plaintext
    nric_hash = hash_sha256(payload.nric) if payload.nric else None
    if nric_hash and db.query(Patient).filter(Patient.nric_hash == nric_hash).first():
        raise HTTPException(status_code=409, detail="A patient with this NRIC already exists")

    patient = Patient(
        nric_hash=nric_hash,
        full_name=payload.full_name,
        age=payload.age,
        phone_number=payload.phone_number,
        language_preference=payload.language_preference,
        conditions=payload.conditions,
        risk_level=payload.risk_level,
        caregiver_name=payload.caregiver_name or None,
        caregiver_phone_number=payload.caregiver_phone_number or None,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    # Generate invite token + QR code
    invite_data = {"invite_link": None, "qr_code_png_b64": None}
    try:
        invite_data = generate_invite_token(db, patient)
    except Exception:
        pass  # Don't fail patient creation if token generation fails

    out = PatientOut.model_validate(patient)
    out.invite_link = invite_data.get("invite_link")
    out.onboarding_qr_code = invite_data.get("qr_code_png_b64")
    return out


@router.get("", response_model=PatientListResponse)
def list_patients(
    is_active: bool | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(Patient).filter(Patient.full_name.notin_(HIDDEN_REGISTRY_PATIENT_NAMES))
    if is_active is not None:
        q = q.filter(Patient.is_active == is_active)
    if risk_level:
        q = q.filter(Patient.risk_level == risk_level)
    clean_search = search.strip() if search else ""
    if clean_search:
        pattern = f"%{clean_search}%"
        q = q.filter(
            or_(
                Patient.full_name.ilike(pattern),
                Patient.phone_number.ilike(pattern),
            )
        )
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return PatientListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.get("/{patient_id}/invite-link", response_model=PatientOut)
@router.post("/{patient_id}/invite-link", response_model=PatientOut)
def regenerate_invite_link(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Regenerate a QR invite token for a patient (invalidates previous tokens)."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    invite_data = generate_invite_token(db, patient)
    out = PatientOut.model_validate(patient)
    out.invite_link = invite_data["invite_link"]
    out.onboarding_qr_code = invite_data["qr_code_png_b64"]
    return out


class CaregiverInviteLinkResponse(BaseModel):
    invite_link: str
    patient_id: int


@router.post("/{patient_id}/caregiver-invite-link", response_model=CaregiverInviteLinkResponse)
def generate_caregiver_link(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Generate a fresh caregiver invite link. Returns the link for manual delivery."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    invite_link = generate_caregiver_invite_token(db, patient)
    return {"invite_link": invite_link, "patient_id": patient_id}


@router.patch("/{patient_id}", response_model=PatientOut)
def update_patient(
    patient_id: int,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if payload.phone_number and payload.phone_number != patient.phone_number:
        existing = (
            db.query(Patient)
            .filter(Patient.phone_number == payload.phone_number, Patient.id != patient_id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail="A patient with this phone number already exists")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=204)
def deactivate_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    patient.is_active = False
    db.commit()


@router.get("/{patient_id}/ai-summary")
def get_ai_summary(
    patient_id: int,
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from app.services.ai_summary_service import generate_patient_summary
    return generate_patient_summary(db, patient_id, force_refresh=refresh)


@router.post("/{patient_id}/caregiver-notes", response_model=CaregiverNoteOut, status_code=201)
def create_caregiver_note(
    patient_id: int,
    payload: CaregiverNoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    note = CaregiverNote(
        patient_id=patient_id,
        author_name=user.full_name,
        author_role=user.role,
        category=payload.category,
        content=payload.content,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/{patient_id}/caregiver-notes", response_model=list[CaregiverNoteOut])
def list_caregiver_notes(
    patient_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return (
        db.query(CaregiverNote)
        .filter(CaregiverNote.patient_id == patient_id)
        .order_by(CaregiverNote.created_at.desc())
        .limit(50)
        .all()
    )
