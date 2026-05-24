"""
All SQLAlchemy ORM models for Medi-Nudge.
Privacy rules enforced here:
  - NRIC is never stored in plaintext (nric_hash is SHA-256 only)
  - IP addresses are SHA-256 hashed before storage
  - image_path / file_path are server-side paths, never returned raw to clients
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.core.timezone import now_sgt


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------

class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nric_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)  # SHA-256 only
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phone_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)  # E.164
    language_preference: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    conditions: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Onboarding
    onboarding_state: Mapped[str] = mapped_column(String(50), default="invited", nullable=False)
    consent_obtained_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consent_channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contact_window_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # e.g. "15:00"
    contact_window_end: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Telegram linkage (set when patient scans QR or /start token is validated)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(30), unique=True, nullable=True)
    # Caregiver contact (notified when patient misses doses repeatedly)
    caregiver_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    caregiver_phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # E.164 — used to send invite
    caregiver_telegram_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # auto-set when caregiver links via bot
    # Voice nudge preferences
    nudge_delivery_mode: Mapped[str] = mapped_column(String(10), default="text", nullable=False)  # text | voice | both
    selected_voice_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # ElevenLabs voice ID
    # Conversation state — tracks what the bot is waiting for from this patient
    pending_action: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g. "voice_consent"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, onupdate=now_sgt, nullable=False)

    medications: Mapped[list["PatientMedication"]] = relationship("PatientMedication", back_populates="patient")
    dispensing_records: Mapped[list["DispensingRecord"]] = relationship("DispensingRecord", back_populates="patient")
    nudge_campaigns: Mapped[list["NudgeCampaign"]] = relationship("NudgeCampaign", back_populates="patient")
    escalation_cases: Mapped[list["EscalationCase"]] = relationship("EscalationCase", back_populates="patient")
    prescription_scans: Mapped[list["PrescriptionScan"]] = relationship("PrescriptionScan", back_populates="patient")
    onboarding_tokens: Mapped[list["OnboardingToken"]] = relationship("OnboardingToken", back_populates="patient")


# ---------------------------------------------------------------------------
# Condition catalog & condition → medication mapping
# ---------------------------------------------------------------------------

class Condition(Base):
    __tablename__ = "conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    medications: Mapped[list["ConditionMedication"]] = relationship("ConditionMedication", back_populates="condition")


class ConditionMedication(Base):
    __tablename__ = "condition_medications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    condition_id: Mapped[int] = mapped_column(Integer, ForeignKey("conditions.id"), nullable=False)
    medication_id: Mapped[int] = mapped_column(Integer, ForeignKey("medications.id"), nullable=False)

    condition: Mapped["Condition"] = relationship("Condition", back_populates="medications")
    medication: Mapped["Medication"] = relationship("Medication")

    __table_args__ = (UniqueConstraint("condition_id", "medication_id"),)


# ---------------------------------------------------------------------------
# Medication catalog
# ---------------------------------------------------------------------------

class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    generic_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    default_refill_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    missed_dose_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    patient_medications: Mapped[list["PatientMedication"]] = relationship("PatientMedication", back_populates="medication")
    dispensing_records: Mapped[list["DispensingRecord"]] = relationship("DispensingRecord", back_populates="medication")


# ---------------------------------------------------------------------------
# PatientMedication (junction)
# ---------------------------------------------------------------------------

class PatientMedication(Base):
    __tablename__ = "patient_medications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    medication_id: Mapped[int] = mapped_column(Integer, ForeignKey("medications.id"), nullable=False)
    dosage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    refill_interval_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    frequency: Mapped[str] = mapped_column(String(50), default="once_daily", nullable=False)
    # JSON list of HH:MM strings in SGT, e.g. ["08:00"] or ["08:00", "20:00"]
    reminder_times: Mapped[Optional[list]] = mapped_column(JSON, default=list, nullable=True)
    # Missed-dose tracking
    consecutive_missed_doses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    med_info_card_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="medications")
    medication: Mapped["Medication"] = relationship("Medication", back_populates="patient_medications")
    dose_logs: Mapped[list["DoseLog"]] = relationship(
        "DoseLog",
        foreign_keys="[DoseLog.patient_medication_id]",
        order_by="DoseLog.logged_at.desc()",
    )


# ---------------------------------------------------------------------------
# DispensingRecord
# ---------------------------------------------------------------------------

class DispensingRecord(Base):
    __tablename__ = "dispensing_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    medication_id: Mapped[int] = mapped_column(Integer, ForeignKey("medications.id"), nullable=False)
    dispensed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    days_supply: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="dispensing_records")
    medication: Mapped["Medication"] = relationship("Medication", back_populates="dispensing_records")


# ---------------------------------------------------------------------------
# NudgeCampaign
# ---------------------------------------------------------------------------

CAMPAIGN_VALID_TRANSITIONS = {
    "pending": {"sent", "failed"},
    "sent": {"responded", "escalated", "resolved", "failed"},
    "responded": set(),
    "escalated": {"resolved"},
    "resolved": set(),
    "failed": set(),
}


class NudgeCampaign(Base):
    __tablename__ = "nudge_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    medication_id: Mapped[int] = mapped_column(Integer, ForeignKey("medications.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    days_overdue: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    message_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, onupdate=now_sgt, nullable=False)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fire_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    campaign_type: Mapped[str] = mapped_column(String(50), default="refill_reminder", nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="nudge_campaigns")
    outbound_messages: Mapped[list["OutboundMessage"]] = relationship("OutboundMessage", back_populates="campaign")
    escalation_cases: Mapped[list["EscalationCase"]] = relationship("EscalationCase", back_populates="nudge_campaign")


# ---------------------------------------------------------------------------
# EscalationCase
# ---------------------------------------------------------------------------

ESCALATION_VALID_TRANSITIONS = {
    "open": {"in_progress", "resolved"},
    "in_progress": {"resolved"},
    "resolved": set(),
}

ESCALATION_PRIORITY_ORDER = {"urgent": 4, "high": 3, "normal": 2, "low": 1}


class EscalationCase(Base):
    __tablename__ = "escalation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nudge_campaign_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("nudge_campaigns.id"), nullable=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="open", nullable=False)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, onupdate=now_sgt, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="escalation_cases")
    nudge_campaign: Mapped[Optional["NudgeCampaign"]] = relationship("NudgeCampaign", back_populates="escalation_cases")


# ---------------------------------------------------------------------------
# CaregiverNote
# ---------------------------------------------------------------------------

class CaregiverNote(Base):
    __tablename__ = "caregiver_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    author_role: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient")


# ---------------------------------------------------------------------------
# OutboundMessage
# ---------------------------------------------------------------------------

class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("nudge_campaigns.id"), nullable=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    telegram_message_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_mode: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    audio_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="sent", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    campaign: Mapped[Optional["NudgeCampaign"]] = relationship("NudgeCampaign", back_populates="outbound_messages")


# ---------------------------------------------------------------------------
# DoseLog (tracks every dose event: taken, missed, skipped)
# ---------------------------------------------------------------------------

class DoseLog(Base):
    __tablename__ = "dose_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    medication_id: Mapped[int] = mapped_column(Integer, ForeignKey("medications.id"), nullable=False)
    patient_medication_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("patient_medications.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # taken | missed | skipped
    source: Mapped[str] = mapped_column(String(30), nullable=False)  # patient_reply | campaign_confirmed | caregiver | system_detected
    logged_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient")
    medication: Mapped["Medication"] = relationship("Medication")


# ---------------------------------------------------------------------------
# PrescriptionScan
# ---------------------------------------------------------------------------

SCAN_VALID_TRANSITIONS = {
    "pending":           {"review", "rejected", "patient_pending"},
    "patient_pending":   {"patient_confirmed", "review"},
    "patient_confirmed": set(),
    "review":            {"confirmed", "rejected"},
    "confirmed":         set(),
    "rejected":          set(),
}


class PrescriptionScan(Base):
    __tablename__ = "prescription_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)  # Never exposed to clients directly
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 for dedup
    source: Mapped[str] = mapped_column(String(50), default="web_upload", nullable=False)
    ocr_engine: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    raw_extracted_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    confirmed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    uploaded_by_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA-256 hashed

    patient: Mapped["Patient"] = relationship("Patient", back_populates="prescription_scans")
    fields: Mapped[list["ExtractedMedicationField"]] = relationship("ExtractedMedicationField", back_populates="scan")


class ExtractedMedicationField(Base):
    __tablename__ = "extracted_medication_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    scan_id: Mapped[int] = mapped_column(Integer, ForeignKey("prescription_scans.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    extracted_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_corrected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    corrected_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scan: Mapped["PrescriptionScan"] = relationship("PrescriptionScan", back_populates="fields")


# ---------------------------------------------------------------------------
# User (care coordinator)
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="admin", nullable=False)
    patient_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("patients.id"), nullable=True)
    own_patient_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("patients.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    caregiver_patient_links: Mapped[list["CaregiverPatientLink"]] = relationship(
        "CaregiverPatientLink",
        back_populates="caregiver_user",
    )
    device_tokens: Mapped[list["UserDeviceToken"]] = relationship(
        "UserDeviceToken",
        back_populates="user",
    )


# ---------------------------------------------------------------------------
# UserDeviceToken
# ---------------------------------------------------------------------------

class UserDeviceToken(Base):
    __tablename__ = "user_device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    bundle_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, onupdate=now_sgt, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="device_tokens")


# ---------------------------------------------------------------------------
# CaregiverPatientLink
# ---------------------------------------------------------------------------

class CaregiverPatientLink(Base):
    __tablename__ = "caregiver_patient_links"
    __table_args__ = (UniqueConstraint("caregiver_user_id", "patient_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    caregiver_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    link_relationship: Mapped[Optional[str]] = mapped_column("relationship", String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    caregiver_user: Mapped["User"] = relationship("User", back_populates="caregiver_patient_links")
    patient: Mapped["Patient"] = relationship("Patient")


# ---------------------------------------------------------------------------
# OnboardingToken (one-time QR deep-link tokens)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# VoiceProfile (caregiver voice clone for personalised nudges)
# ---------------------------------------------------------------------------

class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    donor_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    donor_telegram_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    elevenlabs_voice_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sample_file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    patient_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    donor_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient")


class OnboardingToken(Base):
    __tablename__ = "onboarding_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_sgt, nullable=False)
    is_caregiver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # True = caregiver invite token

    patient: Mapped["Patient"] = relationship("Patient", back_populates="onboarding_tokens")
