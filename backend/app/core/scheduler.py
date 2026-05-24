"""
APScheduler setup.
Jobs:
  - Daily refill gap detection
  - 48h no-reply campaign retry
"""
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from app.core.timezone import now_sgt

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def start_scheduler():
    """Register all jobs and start the scheduler."""
    from app.core.config import settings
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false — skipping startup")
        return
    # Daily at 08:00 SGT (UTC+8)
    scheduler.add_job(
        _run_refill_detection,
        CronTrigger(hour=8, minute=0, timezone="Asia/Singapore"),
        id="daily_refill_detection",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Every hour — check for 48h no-reply campaigns
    scheduler.add_job(
        _run_no_reply_check,
        IntervalTrigger(hours=1),
        id="no_reply_check",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Every 6 hours — check for onboarding drop-offs (no reply for 24h)
    scheduler.add_job(
        _run_onboarding_drop_off_check,
        IntervalTrigger(hours=6),
        id="onboarding_drop_off_check",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Every 30 minutes — fire pending nudge campaigns whose fire_at <= now
    scheduler.add_job(
        _run_fire_due_campaigns,
        IntervalTrigger(minutes=30),
        id="fire_due_campaigns",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Every 30 minutes — send medication reminders based on each patient's schedule (SGT)
    scheduler.add_job(
        _run_daily_medication_reminder,
        IntervalTrigger(minutes=30),
        id="daily_medication_reminder",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Daily at 09:05 SGT — side-effect check-in for medications started 3 days ago
    scheduler.add_job(
        _run_side_effect_checkin,
        CronTrigger(hour=9, minute=5, timezone="Asia/Singapore"),
        id="side_effect_checkin",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    from app.core.config import settings
    if not settings.SCHEDULER_ENABLED:
        return
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


def _run_refill_detection():
    """Triggered daily — detect overdue medications and fire nudge campaigns."""
    logger.info("Running daily refill gap detection ...")
    try:
        from app.services.refill_gap_service import detect_and_trigger
        results = detect_and_trigger()
        logger.info("Refill detection results: %s", results)
    except Exception as exc:
        logger.error("Refill gap detection failed: %s", exc)


def _run_no_reply_check():
    """Check for campaigns in 'sent' state with no reply for 48+ hours."""
    logger.info("Running 48h no-reply check ...")
    try:
        from app.core.database import SessionLocal
        from app.models.models import NudgeCampaign
        from app.services.nudge_campaign_service import retry_or_escalate

        db = SessionLocal()
        threshold = now_sgt() - timedelta(hours=48)
        stale_campaigns = (
            db.query(NudgeCampaign)
            .filter(
                NudgeCampaign.status == "sent",
                NudgeCampaign.last_sent_at <= threshold,
            )
            .all()
        )
        for campaign in stale_campaigns:
            try:
                retry_or_escalate(db, campaign)
            except Exception as exc:
                logger.error("Error retrying campaign %s: %s", campaign.id, exc)
        db.close()
    except Exception as exc:
        logger.error("No-reply check failed: %s", exc)


def _run_onboarding_drop_off_check():
    """Re-invite patients stuck in onboarding (no reply for 24+ hours)."""
    logger.info("Running onboarding drop-off check ...")
    try:
        from app.core.database import SessionLocal
        from app.models.models import Patient, OutboundMessage
        from app.services.onboarding_service import handle_drop_off

        db = SessionLocal()
        threshold = now_sgt() - timedelta(hours=24)

        stale_patients = (
            db.query(Patient)
            .filter(
                Patient.is_active == True,  # noqa: E712
                Patient.onboarding_state.in_(["invited", "consent_pending"]),
            )
            .all()
        )

        for patient in stale_patients:
            # Check last outbound message time
            last_msg = (
                db.query(OutboundMessage)
                .filter(OutboundMessage.patient_id == patient.id)
                .order_by(OutboundMessage.sent_at.desc())
                .first()
            )
            if last_msg and last_msg.sent_at <= threshold:
                retry_count = (
                    db.query(OutboundMessage)
                    .filter(OutboundMessage.patient_id == patient.id)
                    .count()
                )
                try:
                    handle_drop_off(db, patient, retry_count)
                except Exception as exc:
                    logger.error(
                        "Error handling drop-off for patient %s: %s",
                        patient.id, exc,
                    )
        db.close()
    except Exception as exc:
        logger.error("Onboarding drop-off check failed: %s", exc)


def _run_fire_due_campaigns():
    """Fire all pending nudge campaigns whose fire_at <= now."""
    try:
        from app.services.nudge_campaign_service import fire_due_campaigns
        results = fire_due_campaigns()
        if results["fired"] or results["failed"]:
            logger.info("fire_due_campaigns: %s", results)
    except Exception as exc:
        logger.error("fire_due_campaigns failed: %s", exc)


def _run_side_effect_checkin():
    """Check for medications started 3-4 days ago and send one-time check-in messages."""
    logger.info("Running side-effect check-in ...")
    try:
        from app.services.side_effect_checkin_service import run_side_effect_checkin_check
        results = run_side_effect_checkin_check()
        logger.info("Side-effect check-in results: %s", results)
    except Exception as exc:
        logger.error("Side-effect check-in failed: %s", exc)


def _run_daily_medication_reminder():
    """Send scheduled medication-taking reminders based on each patient's frequency & times."""
    logger.info("Running scheduled medication reminder ...")
    try:
        from app.services.daily_reminder_service import send_scheduled_reminders
        results = send_scheduled_reminders()
        logger.info("Medication reminder results: %s", results)
    except Exception as exc:
        logger.error("Medication reminder failed: %s", exc)
