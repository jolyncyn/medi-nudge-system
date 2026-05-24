"""User-scoped app sync routes."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import CaregiverPatientLink, Patient, User, UserDeviceToken
from app.schemas.schemas import (
    AckResponse,
    CaregiverLinkCreate,
    CaregiverLinkOut,
    DeviceTokenCreate,
    PushSendResponse,
)
from app.services.apns_service import medication_reminder_payload, send_payload_to_user


router = APIRouter(prefix="/api/users", tags=["users"])

DEFAULT_IOS_BUNDLE_ID = "com.adheris.Adheris"


def _ios_bundle_id() -> str:
    return settings.APNS_TOPIC.strip() or DEFAULT_IOS_BUNDLE_ID


@router.post("/me/caregiver-links", response_model=CaregiverLinkOut, status_code=201)
def create_my_caregiver_link(
    payload: CaregiverLinkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    link = (
        db.query(CaregiverPatientLink)
        .filter(
            CaregiverPatientLink.caregiver_user_id == user.id,
            CaregiverPatientLink.patient_id == patient.id,
        )
        .first()
    )
    if link:
        link.link_relationship = payload.relationship
    else:
        link = CaregiverPatientLink(
            caregiver_user_id=user.id,
            patient_id=patient.id,
            link_relationship=payload.relationship,
        )
        db.add(link)

    db.commit()
    return CaregiverLinkOut(
        patient_id=patient.id,
        name=patient.full_name,
        relationship=link.link_relationship,
    )


@router.post("/me/device-tokens", response_model=AckResponse)
def register_my_device_token(
    payload: DeviceTokenCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(UserDeviceToken).filter(UserDeviceToken.token == payload.token).first()
    now = datetime.utcnow()
    bundle_id = _ios_bundle_id()
    if row:
        row.user_id = user.id
        row.platform = payload.platform
        row.bundle_id = bundle_id
        row.is_active = True
        row.updated_at = now
    else:
        row = UserDeviceToken(
            user_id=user.id,
            token=payload.token,
            platform=payload.platform,
            bundle_id=bundle_id,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(row)

    db.commit()
    return AckResponse(status="ok")


@router.post("/me/test-push", response_model=PushSendResponse)
def send_my_test_push(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a safe test APNs payload to the logged-in user's registered devices."""
    payload = medication_reminder_payload(
        reminder_id=f"user-{user.id}-test-push",
        medication_name="Amlodipine",
        dosage="5mg",
        scheduled_time_label="8:00 AM",
        message="This is a test Adheris medication reminder.",
        caregiver_name="Adheris",
        caregiver_relationship="care team",
        caregiver_image_name="Adheris",
        thread_id=f"user-{user.id}-test-push",
    )
    result = send_payload_to_user(db=db, user_id=user.id, payload=payload)
    status = "ok"
    if result.attempted == 0:
        status = "no_device_tokens"
    elif result.sent == 0 and result.errors:
        status = "failed"
    return PushSendResponse(
        status=status,
        attempted=result.attempted,
        sent=result.sent,
        failed=result.failed,
        deactivated=result.deactivated,
        errors=result.errors,
    )
