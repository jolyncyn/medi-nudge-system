from datetime import datetime

from app.models.models import User, UserDeviceToken
from app.services import apns_service


_TOKEN = "a" * 64


class _DummyHttpClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_medication_reminder_payload_matches_ios_contract():
    payload = apns_service.medication_reminder_payload(
        reminder_id="patient-22-med-101-time-08:00",
        medication_name="Warfarin",
        dosage="5mg",
        scheduled_time_label="8:00 AM",
        message="Pak, it's time for your 5mg Warfarin.",
        caregiver_name="Mei Ling",
        caregiver_relationship="your granddaughter",
        caregiver_image_name="Mei_Ling",
        is_critical=True,
        snooze_count=1,
    )

    assert payload["aps"]["category"] == "MEDICATION_REMINDER"
    assert payload["aps"]["interruption-level"] == "time-sensitive"
    assert payload["aps"]["thread-id"] == "patient-22-med-101-time-08:00"
    assert payload["reminderId"] == "patient-22-med-101-time-08:00"
    assert payload["medicationName"] == "Warfarin"
    assert payload["dosage"] == "5mg"
    assert payload["scheduledTimeLabel"] == "8:00 AM"
    assert payload["message"] == "Pak, it's time for your 5mg Warfarin."
    assert payload["caregiverName"] == "Mei Ling"
    assert payload["caregiverRelationship"] == "your granddaughter"
    assert payload["caregiverImageName"] == "Mei_Ling"
    assert payload["isCritical"] is True
    assert payload["snoozeCount"] == 1


def test_send_payload_returns_no_attempts_without_tokens(db):
    result = apns_service.send_payload_to_user(db=db, user_id=999, payload={"aps": {}})

    assert result.attempted == 0
    assert result.sent == 0
    assert result.failed == 0


def test_send_payload_reports_missing_config_with_registered_token(db, monkeypatch):
    user = User(
        email="missing-config@example.com",
        hashed_password="not-used-in-test",
        full_name="Missing Config",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    db.add(
        UserDeviceToken(
            user_id=user.id,
            token=_TOKEN,
            platform="ios",
            bundle_id="com.adheris.Adheris",
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    db.commit()

    monkeypatch.setattr(apns_service, "apns_is_configured", lambda: False)

    result = apns_service.send_payload_to_user(db=db, user_id=user.id, payload={"aps": {}})

    assert result.attempted == 1
    assert result.sent == 0
    assert result.failed == 1
    assert result.errors == ["APNs is not configured"]


def test_bad_device_token_is_deactivated(db, monkeypatch):
    user = User(
        email="push-test@example.com",
        hashed_password="not-used-in-test",
        full_name="Push Test",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = UserDeviceToken(
        user_id=user.id,
        token=_TOKEN,
        platform="ios",
        bundle_id="com.adheris.Adheris",
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(token)
    db.commit()

    monkeypatch.setattr(apns_service, "apns_is_configured", lambda: True)
    monkeypatch.setattr(apns_service.httpx, "Client", _DummyHttpClient)
    monkeypatch.setattr(
        apns_service,
        "_send_to_token",
        lambda *, client, token, payload, priority=10: (False, "BadDeviceToken"),
    )

    result = apns_service.send_payload_to_user(db=db, user_id=user.id, payload={"aps": {}})

    db.refresh(token)
    assert result.attempted == 1
    assert result.failed == 1
    assert result.deactivated == 1
    assert token.is_active is False


def test_test_push_endpoint_sends_to_current_user(client, auth_headers, db, monkeypatch):
    captured = {}

    def fake_send_payload_to_user(*, db, user_id, payload, priority=10):
        captured["user_id"] = user_id
        captured["payload"] = payload
        return apns_service.ApnsSendResult(attempted=1, sent=1)

    monkeypatch.setattr("app.routers.users.send_payload_to_user", fake_send_payload_to_user)

    resp = client.post("/api/users/me/test-push", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["attempted"] == 1
    assert body["sent"] == 1
    assert body["failed"] == 0
    assert captured["payload"]["aps"]["category"] == "MEDICATION_REMINDER"
    assert captured["payload"]["medicationName"] == "Amlodipine"
