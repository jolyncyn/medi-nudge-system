"""Create patient, caregiver, and nurse user accounts for demo."""
import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.models import CaregiverPatientLink, DoseLog, Medication, PatientMedication, User, Patient

DEMO_PASSWORD = "Demo1234!"

ACCOUNTS = [
    # Nurse/Doctor accounts (web portal)
    {"email": "nurse.sarah@medinudge.sg", "full_name": "Sarah Tan (Nurse)", "role": "admin"},
    {"email": "dr.lim@medinudge.sg", "full_name": "Dr. Lim Wei Ming", "role": "admin"},

    # Patient accounts (iOS app) — linked by phone_number lookup
    {"email": "tanweiliang@patient.medinudge.sg", "full_name": "Tan Wei Liang", "role": "patient", "phone": "+6591234001"},
    {"email": "limahkow@patient.medinudge.sg", "full_name": "Lim Ah Kow", "role": "patient", "phone": "+6591234002"},
    {"email": "sitirahimah@patient.medinudge.sg", "full_name": "Siti Rahimah", "role": "patient", "phone": "+6591234003"},
    {"email": "rajan@patient.medinudge.sg", "full_name": "Rajan Krishnamurthy", "role": "patient", "phone": "+6591234004"},
    {"email": "chenmeifong@patient.medinudge.sg", "full_name": "Chen Mei Fong", "role": "patient", "phone": "+6591234005"},

    # Caregiver accounts (iOS app) — linked to same patient as their patient
    {"email": "tanmeiling@caregiver.medinudge.sg", "full_name": "Tan Mei Ling", "role": "caregiver", "phone": "+6591234001"},
    {"email": "ahmadrahimi@caregiver.medinudge.sg", "full_name": "Ahmad Rahimi", "role": "caregiver", "phone": "+6591234003"},
    {"email": "priyarajan@caregiver.medinudge.sg", "full_name": "Priya Rajan", "role": "caregiver", "phone": "+6591234004"},
]

CAREGIVER_LINKS = [
    {
        "caregiver_email": "tanmeiling@caregiver.medinudge.sg",
        "patient_name": "Tan Wei Liang",
        "relationship": "father",
    },
    {
        "caregiver_email": "tanmeiling@caregiver.medinudge.sg",
        "patient_name": "Chen Mei Fong",
        "relationship": "mother",
    },
]

TAN_MEI_LING_PROFILE = {
    "email": "tanmeiling@caregiver.medinudge.sg",
    "full_name": "Tan Mei Ling",
    "phone_number": "+6591234011",
    "language_preference": "en",
    "risk_level": "normal",
    "conditions": ["Hypertension"],
    "medication_generic_name": "Amlodipine",
    "dosage": "5mg",
    "frequency": "once_daily",
    "reminder_times": ["08:00"],
}


def seed():
    db = SessionLocal()
    try:
        created = 0
        for acct in ACCOUNTS:
            existing = db.query(User).filter(User.email == acct["email"]).first()
            if existing:
                continue

            patient_id = None
            if acct.get("phone"):
                patient = db.query(Patient).filter(Patient.phone_number == acct["phone"]).first()
                if patient:
                    patient_id = patient.id
                else:
                    print(f"  WARNING: Patient with phone {acct['phone']} not found for {acct['email']}")

            user = User(
                email=acct["email"],
                full_name=acct["full_name"],
                hashed_password=hash_password(DEMO_PASSWORD),
                role=acct["role"],
                patient_id=patient_id,
            )
            db.add(user)
            created += 1

        db.commit()
        print(f"Created {created} user accounts (password for all: {DEMO_PASSWORD})")
        print("\nAccounts:")
        for acct in ACCOUNTS:
            print(f"  {acct['role']:10} | {acct['email']:45} | {acct['full_name']}")
    finally:
        db.close()


def seed_tan_mei_ling_self_profile():
    """Seed Tan Mei Ling's lightweight own patient profile for iOS self care."""
    db = SessionLocal()
    try:
        caregiver = db.query(User).filter(User.email == TAN_MEI_LING_PROFILE["email"]).first()
        if not caregiver:
            print("  WARNING: Tan Mei Ling caregiver user not found, skipping self profile")
            return

        patient = (
            db.query(Patient)
            .filter(Patient.phone_number == TAN_MEI_LING_PROFILE["phone_number"])
            .first()
        )
        if not patient:
            patient = Patient(
                full_name=TAN_MEI_LING_PROFILE["full_name"],
                phone_number=TAN_MEI_LING_PROFILE["phone_number"],
                language_preference=TAN_MEI_LING_PROFILE["language_preference"],
                risk_level=TAN_MEI_LING_PROFILE["risk_level"],
                conditions=TAN_MEI_LING_PROFILE["conditions"],
                onboarding_state="complete",
                is_active=True,
                consent_obtained_at=datetime.utcnow() - timedelta(days=14),
            )
            db.add(patient)
            db.flush()
        else:
            patient.full_name = TAN_MEI_LING_PROFILE["full_name"]
            patient.language_preference = TAN_MEI_LING_PROFILE["language_preference"]
            patient.risk_level = TAN_MEI_LING_PROFILE["risk_level"]
            patient.conditions = TAN_MEI_LING_PROFILE["conditions"]
            patient.onboarding_state = "complete"
            patient.is_active = True

        caregiver.own_patient_id = patient.id

        medication = (
            db.query(Medication)
            .filter(Medication.generic_name == TAN_MEI_LING_PROFILE["medication_generic_name"])
            .first()
        )
        if not medication:
            print("  WARNING: Amlodipine medication not found, skipping Tan Mei Ling medication")
            db.commit()
            return

        patient_medication = (
            db.query(PatientMedication)
            .filter(
                PatientMedication.patient_id == patient.id,
                PatientMedication.medication_id == medication.id,
            )
            .first()
        )
        if not patient_medication:
            patient_medication = PatientMedication(
                patient_id=patient.id,
                medication_id=medication.id,
                dosage=TAN_MEI_LING_PROFILE["dosage"],
                frequency=TAN_MEI_LING_PROFILE["frequency"],
                reminder_times=TAN_MEI_LING_PROFILE["reminder_times"],
                refill_interval_days=30,
                is_active=True,
            )
            db.add(patient_medication)
            db.flush()
        else:
            patient_medication.dosage = TAN_MEI_LING_PROFILE["dosage"]
            patient_medication.frequency = TAN_MEI_LING_PROFILE["frequency"]
            patient_medication.reminder_times = TAN_MEI_LING_PROFILE["reminder_times"]
            patient_medication.refill_interval_days = 30
            patient_medication.is_active = True

        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        missed_day_offsets = {4, 11}
        created_logs = 0
        for day_offset in range(14, 0, -1):
            logged_at = (now - timedelta(days=day_offset)).replace(hour=8)
            existing = (
                db.query(DoseLog)
                .filter(
                    DoseLog.patient_medication_id == patient_medication.id,
                    DoseLog.logged_at == logged_at,
                )
                .first()
            )
            status = "missed" if day_offset in missed_day_offsets else "taken"
            if existing:
                existing.status = status
                existing.source = "system_detected"
                continue

            db.add(
                DoseLog(
                    patient_id=patient.id,
                    medication_id=medication.id,
                    patient_medication_id=patient_medication.id,
                    status=status,
                    source="system_detected",
                    logged_at=logged_at,
                )
            )
            created_logs += 1

        last_taken_log = (
            db.query(DoseLog)
            .filter(
                DoseLog.patient_medication_id == patient_medication.id,
                DoseLog.status == "taken",
            )
            .order_by(DoseLog.logged_at.desc())
            .first()
        )
        if last_taken_log:
            patient_medication.last_taken_at = last_taken_log.logged_at

        db.commit()
        print(
            "  Seeded Tan Mei Ling self profile "
            f"(patient_id={patient.id}, created_logs={created_logs})"
        )
    finally:
        db.close()


def seed_caregiver_links():
    """Seed demo caregiver-to-patient relationships without hardcoded patient IDs."""
    db = SessionLocal()
    try:
        created = 0
        for link in CAREGIVER_LINKS:
            caregiver = db.query(User).filter(User.email == link["caregiver_email"]).first()
            if not caregiver:
                print(f"  WARNING: Caregiver user {link['caregiver_email']} not found, skipping link")
                continue

            patient = db.query(Patient).filter(Patient.full_name == link["patient_name"]).first()
            if not patient:
                print(f"  WARNING: Patient {link['patient_name']} not found, skipping link")
                continue

            existing = (
                db.query(CaregiverPatientLink)
                .filter(
                    CaregiverPatientLink.caregiver_user_id == caregiver.id,
                    CaregiverPatientLink.patient_id == patient.id,
                )
                .first()
            )
            if existing:
                if existing.link_relationship != link["relationship"]:
                    existing.link_relationship = link["relationship"]
                continue

            db.add(
                CaregiverPatientLink(
                    caregiver_user_id=caregiver.id,
                    patient_id=patient.id,
                    link_relationship=link["relationship"],
                )
            )
            created += 1

        db.commit()
        print(f"  Seeded {created} caregiver-patient links")
    finally:
        db.close()


def seed_demo_notes():
    """Seed realistic caregiver notes for Tan Wei Liang (demo patient)."""
    from app.models.models import CaregiverNote
    from datetime import datetime, timedelta

    db = SessionLocal()
    try:
        tan = db.query(Patient).filter(Patient.full_name == "Tan Wei Liang").first()
        if not tan:
            print("  Tan Wei Liang not found, skipping demo notes")
            return

        existing = db.query(CaregiverNote).filter(CaregiverNote.patient_id == tan.id).count()
        if existing:
            print(f"  Demo notes already exist ({existing}), skipping")
            return

        now = datetime.utcnow()
        notes = [
            CaregiverNote(
                patient_id=tan.id,
                author_name="Tan Mei Ling",
                author_role="caregiver",
                category="behavior",
                content="Mum seemed confused about which pills to take after lunch. Had to remind her twice.",
                created_at=now - timedelta(days=2, hours=3),
            ),
            CaregiverNote(
                patient_id=tan.id,
                author_name="Tan Mei Ling",
                author_role="caregiver",
                category="side_effect",
                content="She said the new medication makes her dizzy in the morning. Lasted about 30 minutes.",
                created_at=now - timedelta(days=1, hours=8),
            ),
            CaregiverNote(
                patient_id=tan.id,
                author_name="Tan Mei Ling",
                author_role="caregiver",
                category="missed_dose",
                content="Checked the pillbox, she missed her afternoon Warfarin again. Says she forgot.",
                created_at=now - timedelta(hours=5),
            ),
            CaregiverNote(
                patient_id=tan.id,
                author_name="Sarah Tan (Nurse)",
                author_role="admin",
                category="general",
                content="Called patient — caregiver confirms patient has been skipping Warfarin on weekends. Will schedule home visit.",
                created_at=now - timedelta(hours=2),
            ),
        ]
        db.add_all(notes)
        db.commit()
        print(f"  Seeded {len(notes)} demo caregiver notes for Tan Wei Liang")
    finally:
        db.close()


def fix_recent_dose_logs():
    """Fix the last 7 days of dose logs for demo patients to show realistic adherence.

    Seeds 5 taken / 2 missed per medication per patient (missed on days 3 and 6 ago)
    so iOS 7-day charts display a non-zero graph instead of all-missed.
    """
    TARGET_PHONES = [
        "+6591234001",  # Tan Wei Liang
        "+6591234005",  # Chen Mei Fong
        "+6591234011",  # Tan Mei Ling (own patient profile)
    ]
    MISSED_DAY_OFFSETS = {3, 6}  # 2 missed out of 7 days

    db = SessionLocal()
    try:
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        updated = 0
        created = 0

        for phone in TARGET_PHONES:
            patient = db.query(Patient).filter(Patient.phone_number == phone).first()
            if not patient:
                print(f"  WARNING: patient {phone} not found, skipping")
                continue

            pms = (
                db.query(PatientMedication)
                .filter(
                    PatientMedication.patient_id == patient.id,
                    PatientMedication.is_active == True,  # noqa: E712
                )
                .all()
            )

            for pm in pms:
                daily = 2 if pm.frequency == "twice_daily" else 1
                last_taken_at = pm.last_taken_at

                for day_offset in range(1, 8):
                    status = "missed" if day_offset in MISSED_DAY_OFFSETS else "taken"
                    day_base = (now - timedelta(days=day_offset)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )

                    for dose_num in range(daily):
                        hour = 8 if dose_num == 0 else 20
                        log_time = day_base.replace(hour=hour)
                        day_start = day_base.replace(hour=hour)
                        day_end = day_start + timedelta(hours=1)

                        existing = (
                            db.query(DoseLog)
                            .filter(
                                DoseLog.patient_medication_id == pm.id,
                                DoseLog.logged_at >= day_start,
                                DoseLog.logged_at < day_end,
                            )
                            .first()
                        )

                        if existing:
                            existing.status = status
                            existing.logged_at = log_time
                            updated += 1
                        else:
                            db.add(
                                DoseLog(
                                    patient_id=patient.id,
                                    medication_id=pm.medication_id,
                                    patient_medication_id=pm.id,
                                    status=status,
                                    source="system_detected",
                                    logged_at=log_time,
                                )
                            )
                            created += 1

                        if status == "taken" and (
                            last_taken_at is None or log_time > last_taken_at
                        ):
                            last_taken_at = log_time

                pm.last_taken_at = last_taken_at

            print(
                f"  {patient.full_name}: fixed last 7 days "
                f"(updated={updated}, created={created})"
            )
            updated = 0
            created = 0

        db.commit()
        print("  Done fixing recent dose logs.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    seed_tan_mei_ling_self_profile()
    seed_caregiver_links()
    seed_demo_notes()
    fix_recent_dose_logs()
