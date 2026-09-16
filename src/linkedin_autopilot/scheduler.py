"""The long-running daemon.

Two cron jobs: one that drafts a post at the configured times, one that scans
the feed a few times a day. Both are given a random offset so they never fire at
exactly the same minute two days running.
"""

from __future__ import annotations

import random
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .logging_setup import get_logger
from .runner import run_engagement_job, run_post_job
from .safety import Guard
from .store import Store

log = get_logger(__name__)

# Feed scans spread across the working day rather than clustered.
DEFAULT_ENGAGEMENT_HOURS = (10, 14, 17)


def _jittered_sleep(guard: Guard) -> None:
    offset = guard.schedule_jitter()
    if offset:
        log.info("Waiting %ds before starting (schedule jitter)", offset)
        time.sleep(offset)


def build_scheduler(config: Config, store: Store, guard: Guard) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=config.tz)

    def post_job() -> None:
        if guard.kill_switch_engaged():
            log.warning("Kill switch is present; skipping the post job")
            return
        _jittered_sleep(guard)
        report = run_post_job(config, store, guard)
        log.info("Post job: %s", report.summary)

    def engagement_job() -> None:
        if guard.kill_switch_engaged():
            log.warning("Kill switch is present; skipping the engagement job")
            return
        _jittered_sleep(guard)
        report = run_engagement_job(config, store, guard)
        log.info("Engagement job: %s", report.summary)

    if config.posting.enabled:
        day_expression = ",".join(config.posting.days)
        for slot in config.posting.times:
            hour, minute = slot.split(":")
            scheduler.add_job(
                post_job,
                CronTrigger(day_of_week=day_expression, hour=int(hour), minute=int(minute)),
                id=f"post-{slot}",
                name=f"draft a post at {slot}",
                misfire_grace_time=3600,
                coalesce=True,
                max_instances=1,
            )
            log.info("Scheduled the post job at %s on %s", slot, day_expression)

    if config.engagement.enabled and config.engagement.mode != "off":
        for scan_hour in DEFAULT_ENGAGEMENT_HOURS:
            scan_minute = random.randint(0, 59)
            scheduler.add_job(
                engagement_job,
                CronTrigger(day_of_week="mon,tue,wed,thu,fri", hour=scan_hour, minute=scan_minute),
                id=f"engage-{scan_hour}",
                name=f"scan the feed at {scan_hour:02d}:{scan_minute:02d}",
                misfire_grace_time=1800,
                coalesce=True,
                max_instances=1,
            )
            log.info("Scheduled a feed scan at %02d:%02d", scan_hour, scan_minute)

    return scheduler


def run_forever(config: Config, store: Store, guard: Guard) -> None:
    scheduler = build_scheduler(config, store, guard)

    if not scheduler.get_jobs():
        log.warning(
            "Nothing is scheduled: posting and engagement are both disabled in %s",
            config.source_path,
        )
        return

    log.info("Scheduler running in %s. Press Ctrl-C to stop.", config.timezone)
    log.info("Touch %s at any time to halt all activity.", config.kill_switch_file)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
