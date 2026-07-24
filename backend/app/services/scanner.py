from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.config import require_pipeline_ready, settings
from app.database import SessionLocal, engine
from app.models import FlightResult, ScanRun, WatchTask
from app.services.ctrip_verify import playwright_available, verify_candidates
from app.services.deeplinks import build_verify_links
from app.services.feishu_notify import build_price_alert_text, push_text_to_feishu_webhook
from app.services.flight_search import dedupe_flight_options
from app.services.places import expand_codes
from app.services.travelpayouts import (
    TravelpayoutsClient,
    count_api_batches,
    ensure_verify_url,
    iter_date_combos,
    make_skeleton_option,
    select_verify_pool,
)

_log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=max(1, settings.max_concurrent_scans))
_run_lock = threading.Lock()
_active_task_ids: set[int] = set()
_schema_ready = False

TERMINAL_STATUSES = frozenset({"done", "partial", "failed"})


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with engine.begin() as conn:
        result_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(flight_results)")).fetchall()
        }
        for col, ddl in (
            ("verify_url", "ALTER TABLE flight_results ADD COLUMN verify_url VARCHAR(1024) DEFAULT ''"),
            (
                "verify_url_ctrip",
                "ALTER TABLE flight_results ADD COLUMN verify_url_ctrip VARCHAR(1024) DEFAULT ''",
            ),
            (
                "verify_url_qunar",
                "ALTER TABLE flight_results ADD COLUMN verify_url_qunar VARCHAR(1024) DEFAULT ''",
            ),
            ("cache_price", "ALTER TABLE flight_results ADD COLUMN cache_price FLOAT DEFAULT 0"),
            ("verified_price", "ALTER TABLE flight_results ADD COLUMN verified_price FLOAT"),
            (
                "verify_status",
                "ALTER TABLE flight_results ADD COLUMN verify_status VARCHAR(16) DEFAULT ''",
            ),
        ):
            if col not in result_cols:
                conn.execute(text(ddl))
                _log.info("已为 flight_results 添加 %s 列", col)

        run_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(scan_runs)")).fetchall()}
        for col, ddl in (
            ("phase", "ALTER TABLE scan_runs ADD COLUMN phase VARCHAR(32) DEFAULT ''"),
            (
                "progress_message",
                "ALTER TABLE scan_runs ADD COLUMN progress_message VARCHAR(512) DEFAULT ''",
            ),
        ):
            if col not in run_cols:
                conn.execute(text(ddl))
                _log.info("已为 scan_runs 添加 %s 列", col)

        task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(watch_tasks)")).fetchall()}
        for col, ddl in (
            ("origin_codes", "ALTER TABLE watch_tasks ADD COLUMN origin_codes VARCHAR(128) DEFAULT ''"),
            ("dest_codes", "ALTER TABLE watch_tasks ADD COLUMN dest_codes VARCHAR(128) DEFAULT ''"),
        ):
            if col not in task_cols:
                conn.execute(text(ddl))
                _log.info("已为 watch_tasks 添加 %s 列", col)
    _schema_ready = True


def _task_od_codes(task: WatchTask) -> tuple[list[str], list[str]]:
    origins = expand_codes(getattr(task, "origin_codes", "") or "", task.origin)
    dests = expand_codes(getattr(task, "dest_codes", "") or "", task.dest)
    if not origins or not dests:
        raise RuntimeError("出发地或目的地无法解析为机场代码")
    return origins, dests


def estimate_combinations(task: WatchTask) -> int:
    start = datetime.strptime(task.start_date, "%Y-%m-%d")
    end = datetime.strptime(task.end_date, "%Y-%m-%d")
    try:
        origins, dests = _task_od_codes(task)
        od_n = max(1, len(origins) * len(dests))
    except Exception:
        od_n = 1
    discover = count_api_batches(start, end, task.stay_min, task.stay_max) * od_n
    target_n = max(10, min(max(1, task.top_n), settings.verify_top_k))
    date_n = count_api_batches(start, end, task.stay_min, task.stay_max)
    verify_k = min(max(date_n, date_n * min(3, od_n)), max(target_n * 2, target_n + 5, 20))
    return discover + verify_k


def enqueue_scan(task_id: int, trigger: str = "manual") -> int:
    ensure_schema()
    require_pipeline_ready()
    if not playwright_available():
        raise RuntimeError(
            "未安装 Playwright 核验引擎。请执行: pip install playwright && playwright install chromium"
        )

    with _run_lock:
        if task_id in _active_task_ids:
            raise RuntimeError("该任务正在扫描中")

    db = SessionLocal()
    try:
        task = db.query(WatchTask).filter(WatchTask.id == task_id).first()
        if not task:
            raise RuntimeError("任务不存在")
        total = estimate_combinations(task)
        run = ScanRun(
            task_id=task.id,
            trigger=trigger,
            status="pending",
            phase="",
            progress_done=0,
            progress_total=total,
            started_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
    finally:
        db.close()

    with _run_lock:
        _active_task_ids.add(task_id)

    _executor.submit(_run_scan_job, run_id, task_id)
    return run_id


def _attach_all_links(opt, origin: str, dest: str, adults: int) -> None:
    o = getattr(opt, "origin_code", None) or origin
    d = getattr(opt, "dest_code", None) or dest
    opt.origin_code = o
    opt.dest_code = d
    links = build_verify_links(o, d, opt.outbound_date, opt.return_date, adults=adults)
    # 多机场任务：始终按本条航线重写核对链接，避免串到错误目的地
    opt.verify_url = links["google"]
    opt.verify_url_ctrip = links["ctrip"]
    opt.verify_url_qunar = links["qunar"]


def _set_progress(
    run_id: int,
    done: int,
    total: int,
    phase: str = "",
    message: str = "",
) -> None:
    db = SessionLocal()
    try:
        r = db.query(ScanRun).filter(ScanRun.id == run_id).first()
        if r:
            r.progress_done = done
            r.progress_total = total
            if phase:
                r.phase = phase
            if message:
                r.progress_message = message[:512]
            db.commit()
    finally:
        db.close()


def _persist_live_results(
    run_id: int,
    options: list,
    currency: str,
    origin: str,
    dest: str,
    adults: int,
) -> None:
    """扫描中写入已核验结果，供 SSE/前端实时展示。"""
    ranked = sorted(
        [o for o in options if o.verify_status == "ok" and o.verified_price],
        key=lambda o: o.verified_price or o.total_price,
    )
    db = SessionLocal()
    try:
        db.query(FlightResult).filter(FlightResult.run_id == run_id).delete()
        for i, opt in enumerate(ranked, 1):
            _attach_all_links(opt, origin, dest, adults)
            db.add(
                FlightResult(
                    run_id=run_id,
                    rank=i,
                    outbound_date=opt.outbound_date,
                    return_date=opt.return_date,
                    trip_days=opt.trip_days,
                    total_price=float(opt.verified_price or opt.total_price),
                    cache_price=float(opt.cache_price or 0),
                    verified_price=float(opt.verified_price) if opt.verified_price is not None else None,
                    verify_status=opt.verify_status or "ok",
                    currency=opt.currency or currency,
                    outbound_summary=opt.outbound_summary,
                    return_summary=opt.return_summary,
                    booking_class=opt.booking_class,
                    source=opt.source or "OtaVerified",
                    verify_url=opt.verify_url or "",
                    verify_url_ctrip=opt.verify_url_ctrip or "",
                    verify_url_qunar=opt.verify_url_qunar or "",
                )
            )
        r = db.query(ScanRun).filter(ScanRun.id == run_id).first()
        if r and ranked:
            r.min_price = float(ranked[0].verified_price or ranked[0].total_price)
        db.commit()
    finally:
        db.close()


def _run_scan_job(run_id: int, task_id: int) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        run = db.query(ScanRun).filter(ScanRun.id == run_id).first()
        task = (
            db.query(WatchTask)
            .options(joinedload(WatchTask.user))
            .filter(WatchTask.id == task_id)
            .first()
        )
        if not run or not task:
            return

        require_pipeline_ready()
        if not playwright_available():
            raise RuntimeError(
                "未安装 Playwright 核验引擎。请执行: pip install playwright && playwright install chromium"
            )

        run.status = "running"
        run.phase = "discovering"
        run.error = ""
        run.progress_message = "正在连接 Travelpayouts，准备扫日期窗…"
        db.commit()

        origins, dests = _task_od_codes(task)
        origin = origins[0]
        dest = ",".join(dests)
        start = datetime.strptime(task.start_date, "%Y-%m-%d")
        end = datetime.strptime(task.end_date, "%Y-%m-%d")
        combos = iter_date_combos(start, end, task.stay_min, task.stay_max)
        od_pairs = [(o, d) for o in origins for d in dests]
        discover_total = max(1, len(combos) * len(od_pairs))
        # 产品固定：展示核验价最低的 10 个航班（task.top_n 仅允许调高，默认/下限为 10）
        target_n = max(10, min(max(1, task.top_n), settings.verify_top_k))
        # TP 缓存常稀疏：核验池放大，直到凑满 Top-N 成功条数
        verify_attempts = min(
            max(len(combos), len(combos) * min(3, len(dests))),
            max(target_n * 2, target_n + 5, 20),
        )
        progress_total = discover_total + verify_attempts
        od_hint = f"{task.origin}→{task.dest}"
        if len(dests) > 1:
            od_hint += f"（{len(dests)} 个机场）"
        _set_progress(
            run_id,
            0,
            progress_total,
            "discovering",
            f"发现阶段：扫 {discover_total} 步（{od_hint}）",
        )

        client = TravelpayoutsClient(
            settings.travelpayouts_token,
            request_delay_sec=settings.travelpayouts_request_delay,
            market=settings.travelpayouts_market,
        )

        priced: list = []
        done_base = 0
        for oi, (o_code, d_code) in enumerate(od_pairs):

            def on_discover(done: int, total: int, _base=done_base, _o=o_code, _d=d_code) -> None:
                _set_progress(
                    run_id,
                    _base + done,
                    progress_total,
                    "discovering",
                    f"缓存扫窗 {_base + done}/{discover_total} · {_o}→{_d}",
                )

            try:
                priced.extend(
                    client.scan_window(
                        o_code,
                        d_code,
                        start,
                        end,
                        stay_min=task.stay_min,
                        stay_max=task.stay_max,
                        currency=task.currency,
                        adults=task.adults,
                        on_progress=on_discover,
                        market=settings.travelpayouts_market,
                    )
                )
            except RuntimeError as e:
                # 单机场「不可飞」不拖垮全国扫；网络/鉴权错误仍 fail-closed
                msg = str(e)
                if "not flightable" in msg or "unknown location" in msg or "HTTP 400" in msg:
                    _log.warning("跳过不可用航线 %s→%s: %s", o_code, d_code, msg[:200])
                    done_base += len(combos)
                    _set_progress(
                        run_id,
                        done_base,
                        progress_total,
                        "discovering",
                        f"跳过不可用机场 {o_code}→{d_code}，继续…",
                    )
                    continue
                raise
            done_base += len(combos)

        priced = dedupe_flight_options(priced)
        _log.info(
            "TP 发现有价组合 %s / 日期组合 %s × OD %s（目标 Top-%s）",
            len(priced),
            len(combos),
            len(od_pairs),
            target_n,
        )

        candidates = select_verify_pool(
            priced,
            combos,
            ",".join(origins),
            ",".join(dests),
            adults=task.adults,
            target=target_n,
            currency=task.currency,
            max_attempts=verify_attempts,
        )
        if not candidates:
            raise RuntimeError("日期窗内没有可核验的往返组合")

        for opt in candidates:
            _attach_all_links(opt, origin, opt.dest_code or dests[0], task.adults)
            if not opt.verify_url:
                ensure_o = opt.origin_code or origin
                ensure_d = opt.dest_code or dests[0]
                opt.verify_url = ensure_verify_url(opt, ensure_o, ensure_d, task.adults)
            if not opt.cache_price and opt.total_price > 0:
                opt.cache_price = opt.total_price

        _set_progress(
            run_id,
            discover_total,
            progress_total,
            "verifying",
            f"发现完成：缓存有价 {len(priced)} · 将核验最多 {len(candidates)} 组以凑满 Top-{target_n}",
        )

        live_ok: list = []

        def on_verify(done: int, total: int, opt=None) -> None:
            if opt is not None and getattr(opt, "verify_status", "") == "ok" and opt.verified_price:
                live_ok.append(opt)
                _persist_live_results(
                    run_id, live_ok, task.currency, origin, dests[0], task.adults
                )
                price = float(opt.verified_price)
                dest_tag = getattr(opt, "dest_code", "") or ""
                msg = (
                    f"OTA 核验 {done}/{total} · 已成功 {len(live_ok)}/{target_n} · "
                    f"最新 {task.currency} {price:,.0f}"
                    f"（{opt.outbound_date}→{opt.return_date}"
                    + (f" · {dest_tag}" if dest_tag else "")
                    + "）"
                )
            elif opt is not None and getattr(opt, "verify_status", "") == "failed":
                msg = f"OTA 核验 {done}/{total} · 本组未通过，继续下一组（已成功 {len(live_ok)}）"
            else:
                msg = f"OTA 核验 {done}/{total} · 已成功 {len(live_ok)}/{target_n}"
            _set_progress(run_id, discover_total + done, progress_total, "verifying", msg)

        verified_all, verify_errors = verify_candidates(
            candidates,
            origin,
            dests[0],
            adults=task.adults,
            timeout_ms=settings.ctrip_verify_timeout_ms,
            delay_sec=min(settings.ctrip_verify_delay_sec, 1.0),
            on_each=on_verify,
            target_ok=target_n,
        )
        ok_options = [o for o in verified_all if o.verify_status == "ok" and o.verified_price]
        ok_options.sort(key=lambda o: o.verified_price or o.total_price)
        ok_options = ok_options[:target_n]

        if not ok_options:
            detail = "; ".join(verify_errors[:5]) or "未知原因"
            raise RuntimeError(
                f"OTA 核验全部失败（尝试 {len(verified_all)} 组），不得使用未核验缓存价作为最优结果。{detail}"
            )

        for opt in ok_options:
            _attach_all_links(opt, opt.origin_code or origin, opt.dest_code or dests[0], task.adults)

        db.query(FlightResult).filter(FlightResult.run_id == run_id).delete()
        for i, opt in enumerate(ok_options, 1):
            db.add(
                FlightResult(
                    run_id=run_id,
                    rank=i,
                    outbound_date=opt.outbound_date,
                    return_date=opt.return_date,
                    trip_days=opt.trip_days,
                    total_price=float(opt.verified_price or opt.total_price),
                    cache_price=float(opt.cache_price or 0),
                    verified_price=float(opt.verified_price) if opt.verified_price is not None else None,
                    verify_status=opt.verify_status or "ok",
                    currency=opt.currency or task.currency,
                    outbound_summary=opt.outbound_summary,
                    return_summary=opt.return_summary,
                    booking_class=opt.booking_class,
                    source=opt.source or "OtaVerified",
                    verify_url=opt.verify_url or "",
                    verify_url_ctrip=opt.verify_url_ctrip or "",
                    verify_url_qunar=opt.verify_url_qunar or "",
                )
            )

        min_price = float(ok_options[0].verified_price or ok_options[0].total_price)
        run = db.query(ScanRun).filter(ScanRun.id == run_id).first()
        task = (
            db.query(WatchTask)
            .options(joinedload(WatchTask.user))
            .filter(WatchTask.id == task_id)
            .first()
        )
        assert run and task

        failed_n = len(verify_errors)
        attempted = sum(1 for o in verified_all if o.verify_status in {"ok", "failed"})
        if len(ok_options) >= target_n:
            run.status = "done"
            run.error = ""
        else:
            run.status = "partial"
            run.error = (
                f"核验未凑满 Top-{target_n}：成功 {len(ok_options)}，"
                f"尝试 {attempted} 组，失败 {failed_n}。"
                + ((" " + "；".join(verify_errors[:2])) if verify_errors else "")
            )[:2000]

        run.min_price = min_price
        run.phase = ""
        run.progress_done = progress_total
        run.progress_total = progress_total
        run.progress_message = (
            f"完成 · Top-{len(ok_options)} 最低核验价 {task.currency} {min_price:,.0f}"
        )
        run.finished_at = datetime.utcnow()
        task.last_run_at = run.finished_at
        task.next_run_at = run.finished_at + timedelta(hours=task.interval_hours)

        notify_msg = _maybe_notify(
            db,
            task,
            min_price,
            ok_options[0].outbound_date,
            ok_options[0].return_date,
        )
        if task.best_price_seen is None or min_price < task.best_price_seen:
            task.best_price_seen = min_price

        run.notify_message = notify_msg
        db.commit()
        _log.info(
            "扫描完成 task=%s run=%s status=%s min_price=%s verified=%s failed=%s",
            task_id,
            run_id,
            run.status,
            min_price,
            len(ok_options),
            failed_n,
        )
    except Exception as e:
        _log.exception("扫描失败 run=%s", run_id)
        try:
            run = db.query(ScanRun).filter(ScanRun.id == run_id).first()
            if run:
                run.status = "failed"
                run.phase = ""
                run.error = str(e)[:2000]
                run.finished_at = datetime.utcnow()
                db.commit()
            task = db.query(WatchTask).filter(WatchTask.id == task_id).first()
            if task:
                task.next_run_at = datetime.utcnow() + timedelta(hours=max(1, task.interval_hours))
                db.commit()
        except Exception:
            _log.exception("写入失败状态出错")
    finally:
        db.close()
        with _run_lock:
            _active_task_ids.discard(task_id)


def _maybe_notify(
    db: Session,
    task: WatchTask,
    min_price: float,
    outbound_date: str,
    return_date: str,
) -> str:
    reason = ""
    should = False

    if task.target_price is not None and min_price <= task.target_price:
        if task.last_notified_price is None or abs(task.last_notified_price - min_price) > 0.01:
            should = True
            reason = f"已跌破心理价 {task.currency} {task.target_price:,.0f}"
    elif task.best_price_seen is not None and min_price < task.best_price_seen:
        should = True
        reason = f"创历史新低（此前 {task.currency} {task.best_price_seen:,.0f}）"
    elif task.best_price_seen is None:
        return "首扫完成，已记录核验基准价"

    if not should:
        return ""

    webhook = (task.user.feishu_webhook or "").strip() if task.user else ""
    text = build_price_alert_text(
        origin=task.origin,
        dest=task.dest,
        start_date=task.start_date,
        end_date=task.end_date,
        stay_min=task.stay_min,
        stay_max=task.stay_max,
        min_price=min_price,
        currency=task.currency,
        reason=reason,
        outbound_date=outbound_date,
        return_date=return_date,
    )
    if webhook:
        try:
            push_text_to_feishu_webhook(webhook, text)
            task.last_notified_price = min_price
            db.commit()
            return reason + "（已推飞书）"
        except Exception as e:
            _log.warning("飞书推送失败: %s", e)
            return reason + f"（推送失败: {e}）"
    return reason + "（未配置 Webhook）"
