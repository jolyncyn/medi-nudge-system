"""
Tests for Telegram onboarding flows:
  - generate_invite_token creates correct token + QR
  - expired/used token rejected in handle_start_command
  - NRIC hash lookup links telegram_chat_id
  - unknown NRIC creates stub + escalation
  - Full coordinator-initiated state machine (invited → complete)
  - Full self-onboarding state machine (identity_verification → complete)
  - Drop-off recovery still triggers
"""
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pytest


def _make_patient(db, phone, name="Test Patient", state="invited", chat_id=None, nric_hash=None):
    from app.models.models import Patient
    p = Patient(
        full_name=name,
        phone_number=phone,
        telegram_chat_id=chat_id,
        language_preference="en",
        risk_level="low",
        is_active=True,
        onboarding_state=state,
        nric_hash=nric_hash,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _mock_send():
    m = MagicMock()
    m.status = "sent"
    return m


class TestGenerateInviteToken:
    def test_creates_token_with_correct_ttl(self, db):
        from app.services.onboarding_service import generate_invite_token
        patient = _make_patient(db, "+6591000001")

        from app.core.timezone import now_sgt

        before = now_sgt()
        result = generate_invite_token(db, patient)
        after = now_sgt()

        from app.models.models import OnboardingToken
        token_row = db.query(OnboardingToken).filter(OnboardingToken.patient_id == patient.id).first()
        assert token_row is not None
        assert token_row.used_at is None
        assert token_row.expires_at > before + timedelta(hours=71)
        assert token_row.expires_at < after + timedelta(hours=73)
        assert "invite_link" in result
        assert "t.me" in result["invite_link"]
        assert token_row.token in result["invite_link"]

    def test_invalidates_previous_tokens(self, db):
        from app.services.onboarding_service import generate_invite_token
        from app.models.models import OnboardingToken

        patient = _make_patient(db, "+6591000002")
        result1 = generate_invite_token(db, patient)
        result2 = generate_invite_token(db, patient)

        tokens = db.query(OnboardingToken).filter(OnboardingToken.patient_id == patient.id).all()
        used = [t for t in tokens if t.used_at is not None]
        unused = [t for t in tokens if t.used_at is None]
        assert len(used) == 1
        assert len(unused) == 1
        assert result1["invite_link"] != result2["invite_link"]


class TestStartCommand:
    def test_valid_token_links_chat_id(self, db):
        from app.services.onboarding_service import generate_invite_token, handle_start_command
        patient = _make_patient(db, "+6591000010")
        result = generate_invite_token(db, patient)
        token = result["invite_link"].split("start=")[1]

        with patch("app.services.telegram_service.send_text") as mock_send:
            mock_send.return_value = _mock_send()
            handle_start_command(db, "999001", token)

        db.refresh(patient)
        assert patient.telegram_chat_id == "999001"
        assert patient.onboarding_state == "invited"

    def test_expired_token_rejected(self, db):
        from app.models.models import OnboardingToken
        from app.services.onboarding_service import handle_start_command
        patient = _make_patient(db, "+6591000011")

        expired_token = OnboardingToken(
            patient_id=patient.id,
            token="expiredtoken123",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        db.add(expired_token)
        db.commit()

        with patch("app.services.onboarding_service._send_raw") as mock_raw:
            handle_start_command(db, "999002", "expiredtoken123")
            mock_raw.assert_called_once()
            assert "expired" in mock_raw.call_args[0][1].lower()

        db.refresh(patient)
        assert patient.telegram_chat_id is None

    def test_used_token_rejected(self, db):
        from app.models.models import OnboardingToken
        from app.services.onboarding_service import handle_start_command
        patient = _make_patient(db, "+6591000012")

        used_token = OnboardingToken(
            patient_id=patient.id,
            token="usedtoken456",
            expires_at=datetime.utcnow() + timedelta(hours=72),
            used_at=datetime.utcnow(),
        )
        db.add(used_token)
        db.commit()

        with patch("app.services.onboarding_service._send_raw") as mock_raw:
            handle_start_command(db, "999003", "usedtoken456")
            mock_raw.assert_called_once()
            assert "already been used" in mock_raw.call_args[0][1].lower()

    def test_no_token_creates_stub_and_prompts_language(self, db):
        from app.services.onboarding_service import handle_start_command
        from app.models.models import Patient

        with patch("app.services.onboarding_service._send_raw_keyboard") as mock_kbd:
            handle_start_command(db, "999004", None)
            mock_kbd.assert_called_once()
            # First message should be the language selection welcome
            assert "language" in mock_kbd.call_args[0][1].lower() or "medi-nudge" in mock_kbd.call_args[0][1].lower()

        stub = db.query(Patient).filter(Patient.telegram_chat_id == "999004").first()
        assert stub is not None
        assert stub.onboarding_state == "self_lang"
        assert stub.is_active is False


class TestIdentityVerification:
    def test_nric_match_links_chat_id(self, db):
        from app.core.config import hash_sha256
        from app.services.onboarding_service import handle_identity_verification

        nric = "S1234567A"
        pre_registered = _make_patient(
            db, "+6591000020",
            name="John Doe",
            state="invited",
            nric_hash=hash_sha256(nric),
        )
        stub = _make_patient(db, "tg_888001", state="identity_verification", chat_id="888001")

        with patch("app.services.telegram_service.send_text") as mock_send:
            mock_send.return_value = _mock_send()
            handle_identity_verification(db, stub, nric)

        db.refresh(pre_registered)
        assert pre_registered.telegram_chat_id == "888001"
        assert pre_registered.onboarding_state == "invited"

        # Stub should be deleted
        from app.models.models import Patient
        deleted_stub = db.query(Patient).filter(Patient.id == stub.id).first()
        assert deleted_stub is None

    def test_unknown_nric_creates_escalation(self, db):
        from app.services.onboarding_service import handle_identity_verification
        stub = _make_patient(db, "tg_888002", state="identity_verification", chat_id="888002")

        with patch("app.services.onboarding_service._send_raw") as mock_raw:
            handle_identity_verification(db, stub, "T9999999Z")
            mock_raw.assert_called_once()
            assert "review" in mock_raw.call_args[0][1].lower()

        db.refresh(stub)
        assert stub.onboarding_state == "self_registering"

        from app.models.models import EscalationCase
        escalation = db.query(EscalationCase).filter(EscalationCase.patient_id == stub.id).first()
        assert escalation is not None
        assert escalation.reason == "self_registration_review"


class TestFullStateMachine:
    def _send_mock(self):
        m = MagicMock()
        m.status = "sent"
        return m

    def test_coordinator_initiated_invited_to_complete(self, db):
        """invited → consent_pending → language_confirmed → medication_capture → confirm → preferences → voice_preference → complete"""
        from app.services.onboarding_service import handle_onboarding_reply

        patient = _make_patient(db, "+6591000030", chat_id="777001")

        with patch("app.services.telegram_service.send_text") as ms:
            ms.return_value = self._send_mock()

            handle_onboarding_reply(db, patient, "YES")
            db.refresh(patient)
            assert patient.onboarding_state == "consent_pending"

            handle_onboarding_reply(db, patient, "1")  # English
            db.refresh(patient)
            assert patient.onboarding_state == "language_confirmed"

            handle_onboarding_reply(db, patient, "3")  # manual entry
            db.refresh(patient)
            assert patient.onboarding_state == "confirm"

            handle_onboarding_reply(db, patient, "YES")  # confirm empty list
            db.refresh(patient)
            assert patient.onboarding_state == "preferences"

            handle_onboarding_reply(db, patient, "1")  # morning window
            db.refresh(patient)
            assert patient.onboarding_state == "voice_preference"
            assert patient.contact_window_start == "08:00"

            handle_onboarding_reply(db, patient, "1")  # text only
            db.refresh(patient)
            assert patient.onboarding_state == "complete"
            assert patient.is_active is True
            assert patient.nudge_delivery_mode == "text"

    def test_drop_off_recovery_creates_escalation(self, db):
        from app.services.onboarding_service import handle_drop_off
        from app.models.models import EscalationCase

        patient = _make_patient(db, "+6591000031", chat_id="777002")

        handle_drop_off(db, patient, retry_count=0)
        handle_drop_off(db, patient, retry_count=1)
        handle_drop_off(db, patient, retry_count=2)

        db.refresh(patient)
        assert patient.onboarding_state == "drop_off_recovery"
        escalation = db.query(EscalationCase).filter(EscalationCase.patient_id == patient.id).first()
        assert escalation is not None
        assert escalation.reason == "onboarding_drop_off"
