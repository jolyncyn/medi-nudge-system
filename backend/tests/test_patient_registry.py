from app.models.models import Patient


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
