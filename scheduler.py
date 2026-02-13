"""
scheduler.py — Background job scheduler
Uses APScheduler for periodic tasks: email monitoring, follow-up reminders,
network decay, variant analysis, and cleanup.
"""

import os
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

# Try importing APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.executors.pool import ThreadPoolExecutor
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    logger.warning("APScheduler not installed. Background jobs disabled.")


_scheduler = None


def get_scheduler():
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    if not HAS_SCHEDULER:
        return None

    from tracker import DB_PATH

    jobstore_path = DB_PATH.replace("applications.db", "scheduler_jobs.db")

    jobstores = {
        "default": SQLAlchemyJobStore(url=f"sqlite:///{jobstore_path}"),
    }
    executors = {
        "default": ThreadPoolExecutor(4),
    }
    job_defaults = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 300,
    }

    _scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
    )
    return _scheduler


def _get_db_session():
    """Create a new DB session for scheduler jobs (avoids pickle issues)."""
    from tracker import SessionLocal
    return SessionLocal()


def init_scheduler(db_session_factory=None):
    """Initialize and start the scheduler with all jobs."""
    scheduler = get_scheduler()
    if not scheduler:
        logger.info("Scheduler not available (APScheduler not installed)")
        return

    from tracker import get_setting

    db = _get_db_session()
    try:
        email_interval = int(get_setting(db, "email_check_interval", "30"))
        reminder_hour = int(get_setting(db, "reminder_hour", "9"))
    finally:
        db.close()

    # Job 1: Email monitoring
    scheduler.add_job(
        _email_monitoring_wrapper,
        "interval",
        minutes=email_interval,
        id="email_monitoring",
        name="Email Monitoring",
        replace_existing=True,
        jitter=300,
    )

    # Job 2: Follow-up reminders (daily)
    scheduler.add_job(
        _followup_reminder_wrapper,
        "cron",
        hour=reminder_hour,
        minute=0,
        id="followup_reminders",
        name="Follow-up Reminders",
        replace_existing=True,
    )

    # Job 3: Network relationship decay (weekly, Sunday midnight)
    scheduler.add_job(
        _network_decay_wrapper,
        "cron",
        day_of_week="sun",
        hour=0,
        id="network_decay",
        name="Network Relationship Decay",
        replace_existing=True,
    )

    # Job 4: Variant analysis (weekly, Monday morning)
    scheduler.add_job(
        _variant_analysis_wrapper,
        "cron",
        day_of_week="mon",
        hour=8,
        id="variant_analysis",
        name="A/B Variant Analysis",
        replace_existing=True,
    )

    # Job 5: Cleanup (monthly, 1st of month)
    scheduler.add_job(
        _cleanup_wrapper,
        "cron",
        day=1,
        hour=3,
        id="monthly_cleanup",
        name="Monthly Cleanup",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None


# ─────────────────────────────────────────────────────────
# Job wrappers
# ─────────────────────────────────────────────────────────

def _email_monitoring_wrapper():
    """Wrapper for email monitoring job with error handling."""
    try:
        from email_monitor import email_monitoring_job
        stats = email_monitoring_job(_get_db_session)
        if stats:
            logger.info(f"Email monitoring: {stats}")
    except Exception as e:
        logger.error(f"Email monitoring job failed: {e}")


def _followup_reminder_wrapper():
    """Check for due follow-ups and log reminders."""
    try:
        from tracker import get_all_applications, get_setting, set_setting

        db = _get_db_session()
        try:
            today = date.today()
            apps = get_all_applications(db)

            due_count = 0
            overdue_count = 0

            for app in apps:
                for fu in app.follow_ups:
                    if fu.completed:
                        continue
                    if fu.scheduled_date <= today:
                        if fu.scheduled_date < today:
                            overdue_count += 1
                        else:
                            due_count += 1

            if due_count or overdue_count:
                logger.info(f"Follow-up reminders: {due_count} due today, {overdue_count} overdue")
                set_setting(db, "last_reminder_check", datetime.utcnow().isoformat())
                set_setting(db, "pending_reminders", str(due_count + overdue_count))
            else:
                set_setting(db, "pending_reminders", "0")
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Follow-up reminder job failed: {e}")


def _network_decay_wrapper():
    """Decay relationship strengths for inactive contacts."""
    try:
        from network_manager import decay_relationships
        from tracker import get_setting

        db = _get_db_session()
        try:
            warm_days = int(get_setting(db, "network_warm_decay_days", "180"))
            close_days = int(get_setting(db, "network_close_decay_days", "120"))
            decayed = decay_relationships(db, warm_days, close_days)
            if decayed:
                logger.info(f"Network decay: {decayed} relationships downgraded")
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Network decay job failed: {e}")


def _variant_analysis_wrapper():
    """Run weekly variant performance analysis and cache results."""
    try:
        from ab_testing import analyze_variant_performance
        from tracker import set_setting
        import json

        db = _get_db_session()
        try:
            analysis = analyze_variant_performance(db)
            # Cache results for fast analytics page load
            set_setting(db, "variant_analysis_cache", json.dumps(analysis, default=str))
            set_setting(db, "variant_analysis_last_run", datetime.utcnow().isoformat())

            if analysis.get("winner") and analysis.get("confidence") == "high":
                logger.info(f"Variant analysis: winner is '{analysis['winner']}' with high confidence")
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Variant analysis job failed: {e}")


def _cleanup_wrapper():
    """Monthly cleanup: remove old temp files, vacuum database."""
    try:
        import tempfile
        import shutil

        # Clean temp pipeline output files older than 30 days
        temp_dir = os.path.join(tempfile.gettempdir(), "career_pipeline")
        if os.path.exists(temp_dir):
            cutoff = datetime.utcnow() - timedelta(days=30)
            cleaned = 0
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                if os.path.isdir(item_path):
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(item_path))
                        if mtime < cutoff:
                            shutil.rmtree(item_path)
                            cleaned += 1
                    except Exception:
                        pass
            if cleaned:
                logger.info(f"Cleanup: removed {cleaned} old temp directories")

        # Vacuum database
        from tracker import engine
        with engine.connect() as conn:
            conn.execute("VACUUM")
        logger.info("Cleanup: database vacuumed")

    except Exception as e:
        logger.error(f"Cleanup job failed: {e}")


# ─────────────────────────────────────────────────────────
# Job status reporting
# ─────────────────────────────────────────────────────────

def get_job_status() -> list:
    """Get status of all scheduled jobs."""
    scheduler = get_scheduler()
    if not scheduler or not scheduler.running:
        return []

    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run.isoformat() if next_run else "paused",
            "trigger": str(job.trigger),
        })
    return jobs


def pause_job(job_id: str):
    """Pause a specific job."""
    scheduler = get_scheduler()
    if scheduler:
        scheduler.pause_job(job_id)


def resume_job(job_id: str):
    """Resume a paused job."""
    scheduler = get_scheduler()
    if scheduler:
        scheduler.resume_job(job_id)


def run_job_now(job_id: str):
    """Trigger immediate execution of a job."""
    scheduler = get_scheduler()
    if scheduler:
        job = scheduler.get_job(job_id)
        if job:
            job.modify(next_run_time=datetime.utcnow())
