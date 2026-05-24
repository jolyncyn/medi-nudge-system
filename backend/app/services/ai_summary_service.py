"""AI-powered patient adherence summary using OpenAI."""
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.timezone import now_sgt
from app.models.models import Patient, PatientMedication, Medication, DoseLog

logger = logging.getLogger(__name__)

_cache: dict[int, tuple[str, datetime]] = {}
CACHE_TTL_HOURS = 24


def generate_patient_summary(db: Session, patient_id: int, force_refresh: bool = False) -> dict:
    now = now_sgt()

    if not force_refresh and patient_id in _cache:
        summary, generated_at = _cache[patient_id]
        if (now - generated_at).total_seconds() < CACHE_TTL_HOURS * 3600:
            return {"summary": summary, "generated_at": generated_at.isoformat(), "cached": True}

    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return {"summary": "Patient not found.", "generated_at": now.isoformat(), "cached": False}

    since_30d = now - timedelta(days=30)
    dose_logs = (
        db.query(DoseLog)
        .filter(DoseLog.patient_id == patient_id, DoseLog.logged_at >= since_30d)
        .all()
    )

    patient_meds = (
        db.query(PatientMedication)
        .filter(PatientMedication.patient_id == patient_id, PatientMedication.is_active == True)
        .all()
    )

    med_map = {}
    for pm in patient_meds:
        med = db.query(Medication).filter(Medication.id == pm.medication_id).first()
        if med:
            med_map[med.id] = med

    per_med_stats = {}
    for med_id, med in med_map.items():
        logs = [d for d in dose_logs if d.medication_id == med_id]
        total = len(logs)
        taken = sum(1 for d in logs if d.status == "taken")
        missed = total - taken
        rate = round(taken / total * 100, 1) if total else 100.0

        missed_times = [d.logged_at.strftime("%H:%M") for d in logs if d.status == "missed"]
        missed_weekdays = [d.logged_at.strftime("%A") for d in logs if d.status == "missed"]

        per_med_stats[med.name] = {
            "generic": med.generic_name,
            "is_critical": med.is_critical,
            "missed_dose_info": med.missed_dose_info,
            "total": total,
            "taken": taken,
            "missed": missed,
            "adherence_rate": rate,
            "missed_times": missed_times,
            "missed_weekdays": missed_weekdays,
        }

    total_doses = len(dose_logs)
    total_taken = sum(1 for d in dose_logs if d.status == "taken")
    overall_rate = round(total_taken / total_doses * 100, 1) if total_doses else 100.0

    med_summary_lines = []
    for name, stats in per_med_stats.items():
        critical_tag = " [CRITICAL]" if stats["is_critical"] else ""
        med_summary_lines.append(
            f"- {name}{critical_tag}: {stats['adherence_rate']}% adherence "
            f"({stats['taken']} taken, {stats['missed']} missed out of {stats['total']} doses)"
        )
        if stats["missed_weekdays"]:
            from collections import Counter
            weekday_counts = Counter(stats["missed_weekdays"]).most_common(3)
            pattern = ", ".join(f"{day} ({count})" for day, count in weekday_counts)
            med_summary_lines.append(f"  Most missed on: {pattern}")
        if stats["missed_dose_info"]:
            med_summary_lines.append(f"  Consequence of missing: {stats['missed_dose_info']}")

    med_summary_text = "\n".join(med_summary_lines)

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are a clinical analytics assistant for a medication adherence system in Singapore. "
                "Generate a concise 2-3 sentence actionable summary for a care coordinator reviewing this patient. "
                "Focus on: (1) which specific medications are being missed and when, (2) any concerning patterns "
                "(e.g. irregular timing suggesting self-adjustment, weekday vs weekend differences), "
                "(3) one specific actionable recommendation. "
                "If a medication is marked [CRITICAL] and has missed dose consequences listed, "
                "briefly mention the real-world impact (e.g. 'blood sugar may stay too high'). "
                "Use the patient's first name. Be factual, not alarmist. Do not use medical jargon."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Patient: {patient.full_name}, {patient.age or 'unknown'} years old\n"
                f"Conditions: {', '.join(patient.conditions) if patient.conditions else 'None recorded'}\n"
                f"Risk Level: {patient.risk_level}\n"
                f"Overall 30-day adherence: {overall_rate}% ({total_taken} taken / {total_doses} total doses)\n\n"
                f"Per-medication breakdown:\n{med_summary_text}"
            ),
        },
    ]

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY or "ollama",
            base_url=settings.LLM_BASE_URL or None,
        )
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=prompt_messages,
            max_tokens=200,
            temperature=0.3,
        )
        summary = response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("OpenAI summary generation failed for patient %s: %s", patient_id, exc)
        summary = f"Unable to generate AI summary. Overall 30-day adherence: {overall_rate}%."

    _cache[patient_id] = (summary, now)
    return {"summary": summary, "generated_at": now.isoformat(), "cached": False}
