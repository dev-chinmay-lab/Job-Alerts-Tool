"""
scheduler.py
APScheduler-based background runner.
Pipeline: Scrape → Filter → Save → Alert
"""

import logging
import signal
import sys
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
import database
import filter as job_filter
import notifier as tg
import scraper

logger = logging.getLogger(__name__)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline() -> dict:
    """
    Full scrape → filter → save → notify cycle.
    Returns a summary dict. Safe to call directly for testing.
    """
    start = datetime.now(timezone.utc)
    logger.info("=" * 55)
    logger.info("Pipeline started at %s", start.strftime("%Y-%m-%d %H:%M:%S UTC"))
    logger.info("=" * 55)

    summary = {
        "started": start.isoformat(),
        "scraped": 0, "passed": 0,
        "new": 0, "sent": 0, "errors": [],
    }

    run_id = database.start_run("all")

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    try:
        raw_jobs = scraper.scrape_all_sync()
        summary["scraped"] = len(raw_jobs)
        logger.info("Step 1 — Scraped: %d raw jobs", len(raw_jobs))
    except Exception as e:
        msg = f"Scrape failed: {e}"
        logger.error(msg, exc_info=True)
        summary["errors"].append(msg)
        database.finish_run(run_id, 0, 0, 0, msg)
        return summary

    if not raw_jobs:
        logger.info("No jobs found this cycle.")
        database.finish_run(run_id, 0, 0, 0)
        return summary

    # ── Step 2: Filter ────────────────────────────────────────────────────────
    try:
        accepted, rejected = job_filter.filter_jobs(raw_jobs)
        summary["passed"] = len(accepted)
        logger.info(
            "Step 2 — Filtered: %d accepted / %d rejected",
            len(accepted), len(rejected),
        )
    except Exception as e:
        msg = f"Filter failed: {e}"
        logger.error(msg, exc_info=True)
        summary["errors"].append(msg)
        accepted = raw_jobs   # fail-safe: use all

    # ── Step 3: Save to DB (deduplication) ───────────────────────────────────
    new_count = 0
    saved_jobs = []
    try:
        for job in accepted:
            job_id = database.save_job(job)
            if job_id is not None:
                job["_db_id"] = job_id
                saved_jobs.append(job)
                new_count += 1
        summary["new"] = new_count
        logger.info(
            "Step 3 — Saved: %d new, %d duplicates skipped",
            new_count, len(accepted) - new_count,
        )
    except Exception as e:
        msg = f"DB save failed: {e}"
        logger.error(msg, exc_info=True)
        summary["errors"].append(msg)

    # ── Step 4: Send Telegram alerts ─────────────────────────────────────────
    sent = 0
    if saved_jobs:
        try:
            sent, failed = tg.send_jobs_sync(saved_jobs)
            summary["sent"] = sent

            # Mark sent jobs in DB
            for job in saved_jobs[:sent]:
                if "_db_id" in job:
                    database.mark_sent(job["_db_id"])

            logger.info("Step 4 — Alerts: %d sent, %d failed", sent, failed)
        except Exception as e:
            msg = f"Telegram send failed: {e}"
            logger.error(msg, exc_info=True)
            summary["errors"].append(msg)
    else:
        logger.info("Step 4 — No new jobs to alert.")

    # ── Finalise ──────────────────────────────────────────────────────────────
    database.finish_run(
        run_id,
        found=summary["scraped"],
        new=summary["new"],
        sent=summary["sent"],
        error="; ".join(summary["errors"]) or None,
    )

    stats = database.get_stats()
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info("=" * 55)
    logger.info("Pipeline done  | %.1fs elapsed", elapsed)
    logger.info("  Scraped : %d", summary["scraped"])
    logger.info("  Passed  : %d", summary["passed"])
    logger.info("  New     : %d", summary["new"])
    logger.info("  Sent    : %d", summary["sent"])
    logger.info("  DB total: %d (walkins: %d)", stats["total"], stats["walkins"])
    if summary["errors"]:
        logger.warning("  Errors  : %s", summary["errors"])
    logger.info("=" * 55)

    return summary


# ── Scheduler ─────────────────────────────────────────────────────────────────

class JobAlertScheduler:
    def __init__(self, interval_hours: int = None):
        self.interval_hours = interval_hours or config.SCRAPE_INTERVAL_HOURS
        self._scheduler = BackgroundScheduler(
            job_defaults={"coalesce": True, "max_instances": 1},
            timezone="Asia/Kolkata",
        )

    def start(self, run_now: bool = True):
        logger.info(
            "Scheduler starting — every %d hour(s)", self.interval_hours
        )

        self._scheduler.add_job(
            run_pipeline,
            trigger=IntervalTrigger(hours=self.interval_hours),
            id="job_alert",
            name="Pune Fresher Job Alert",
            replace_existing=True,
        )
        self._scheduler.start()

        if run_now:
            logger.info("Running pipeline immediately on startup…")
            run_pipeline()

        # Graceful shutdown on SIGINT / SIGTERM
        signal.signal(signal.SIGINT,  self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        logger.info("Scheduler running. Next run in %dh. Ctrl+C to stop.",
                    self.interval_hours)
        try:
            while True:
                time.sleep(60)
        except (KeyboardInterrupt, SystemExit):
            self._stop()

    def _stop(self, *_):
        logger.info("Shutting down scheduler…")
        self._scheduler.shutdown(wait=False)
        sys.exit(0)
