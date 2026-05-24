from app.core.timezone import now_sgt
from app.models.models import DoseLog, EscalationCase, Medication, Patient


def test_patient_registry_hides_known_incomplete_telegram_patients(client, auth_headers, db):
    visible = Patient(
        full_name="Visible Patient",
        phone_number="+6591999001",
        onboarding_state="complete",
        is_active=True,
    )
    hidden_one = Patient(
        full_name="tg_51789857",
        phone_number="+6591999002",
        onboarding_state="self_consent",
        is_active=True,
    )
    hidden_two = Patient(
        full_name="tg_1746763759",
        phone_number="+6591999003",
        onboarding_state="self_consent",
        is_active=True,
    )
    db.add_all([visible, hidden_one, hidden_two])
    db.commit()

    resp = client.get("/api/patients", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    names = {patient["full_name"] for patient in body["items"]}
    assert "Visible Patient" in names
    assert "tg_51789857" not in names
    assert "tg_1746763759" not in names
    assert body["total"] == 1


def test_dashboard_summary_excludes_hidden_registry_patients(client, auth_headers, db):
    visible = Patient(
        full_name="Visible Patient",
        phone_number="+6591999001",
        onboarding_state="complete",
        risk_level="normal",
        is_active=True,
    )
    hidden = Patient(
        full_name="tg_51789857",
        phone_number="+6591999002",
        onboarding_state="complete",
        risk_level="high",
        is_active=True,
    )
    med = Medication(name="Demo Med", generic_name="demo-med")
    db.add_all([visible, hidden, med])
    db.commit()
    db.refresh(visible)
    db.refresh(hidden)
    db.refresh(med)

    timestamp = now_sgt()
    db.add_all([
        DoseLog(
            patient_id=visible.id,
            medication_id=med.id,
            status="taken",
            source="test",
            logged_at=timestamp,
        ),
        DoseLog(
            patient_id=hidden.id,
            medication_id=med.id,
            status="missed",
            source="test",
            logged_at=timestamp,
        ),
        EscalationCase(
            patient_id=hidden.id,
            reason="Hidden patient escalation",
            priority="urgent",
            status="open",
        ),
    ])
    db.commit()

    resp = client.get("/api/dashboard/summary", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_adherence"] == 100.0
    assert body["high_risk_count"] == 0
    assert str(hidden.id) not in body["patient_compliance"]
    assert all(p["id"] != hidden.id for p in body["at_risk_patients"])
    assert all(e["patient_id"] != hidden.id for e in body["pending_escalations"])
