from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal
from app.models import WatchTask
from app.services.scanner import enqueue_scan

_log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _tick() -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due = (
            db.query(WatchTask)
            .filter(WatchTask.enabled.is_(True))
            .filter(WatchTask.next_run_at.isnot(None))
            .filter(WatchTask.next_run_at <= now)
            .all()
        )
        for task in due:
            try:
                enqueue_scan(task.id, trigger="schedule")
                _log.info("调度触发扫描 task=%s", task.id)
            except RuntimeError as e:
                _log.info("跳过 task=%s: %s", task.id, e)
            except Exception:
                _log.exception("调度扫描失败 task=%s", task.id)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_tick, "interval", minutes=1, id="watch_tick", replace_existing=True)
    sched.start()
    _scheduler = sched
    _log.info("APScheduler 已启动（每分钟检查到期任务）")
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
