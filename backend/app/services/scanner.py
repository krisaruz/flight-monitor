from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.config import require_pipeline_ready, settings
from app.database import SessionLocal, engine
from app.models import FlightResult, ScanRun, WatchTask
from app.services.ctrip_verify import (
    playwright_available,
    scrape_google_one_way_days,
    verify_candidates,
)
from app.services.deeplinks import build_verify_links, is_likely_international
from app.services.feishu_notify import build_price_alert_text, push_text_to_feishu_webhook
from app.services.places import expand_codes
from app.services.calendar_match import (
    candidate_pool_size,
    count_calendar_days,
    iter_day_strings,
    outbound_day_range,
    pick_days_for_google_fill,
    return_day_range,
    select_calendar_verify_pool,
)
from app.services.travelpayouts import (
    TravelpayoutsClient,
    ensure_verify_url,
)

_log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=max(1, settings.max_concurrent_scans))
_run_lock = threading.Lock()
_active_task_ids: set[int] = set()
_cancel_requested: set[int] = set()
_schema_ready = False

TERMINAL_STATUSES = frozenset({"done", "partial", "failed", "cancelled"})


class ScanCancelled(Exception):
    """用户请求终止当前扫描。"""


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


def _empty_calendar_error(
    out_hits: int,
    out_days: int,
    ret_hits: int,
    ret_days: int,
    matched: int,
    *,
    domestic: bool,
) -> str:
    """发现阶段无可核验候选时的用户可见错误（含日历统计）。"""
    stats = (
        f"去程有价 {int(out_hits)}/{int(out_days)}、"
        f"回程有价 {int(ret_hits)}/{int(ret_days)}、匹配 {int(matched)}"
    )
    if domestic:
        return (
            f"日期窗内没有可核验的往返组合（{stats}）。"
            "国内均匀日期直核验也未能生成候选；可稍后再试，或放宽日期窗/停留天数。"
        )
    return (
        f"日期窗内没有「去程+回程都有日历价」的组合可核验（{stats}）。"
        "可稍后再试，或放宽日期窗/停留天数。"
    )


def estimate_combinations(task: WatchTask) -> int:
    start = datetime.strptime(task.start_date, "%Y-%m-%d")
    end = datetime.strptime(task.end_date, "%Y-%m-%d")
    try:
        origins, dests = _task_od_codes(task)
        od_n = max(1, len(origins) * len(dests))
    except Exception:
        od_n = 1
    cal_days = count_calendar_days(start, end, task.stay_min, task.stay_max) * od_n
    target_n = max(10, min(max(1, task.top_n), settings.verify_top_k))
    day_max = max(0, int(getattr(settings, "calendar_google_day_max", 16) or 16))
    cand_k = candidate_pool_size(
        target_n, int(getattr(settings, "calendar_candidate_k", 20) or 20)
    )
    # 粗估：日历天数 + 每 OD 每方向最多 day_max 补洞 + Top 核验
    return cal_days + day_max * 2 * od_n + cand_k


def request_cancel_run(task_id: int, run_id: int) -> ScanRun:
    """标记运行中的扫描为取消；工作线程在下一步检查点退出。"""
    ensure_schema()
    db = SessionLocal()
    try:
        run = (
            db.query(ScanRun)
            .filter(ScanRun.id == run_id, ScanRun.task_id == task_id)
            .first()
        )
        if not run:
            raise RuntimeError("扫描记录不存在")
        if run.status in TERMINAL_STATUSES:
            return run
        if run.status not in {"pending", "running"}:
            raise RuntimeError(f"当前状态不可取消: {run.status}")
        with _run_lock:
            _cancel_requested.add(run_id)
        run.progress_message = "正在取消…"
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    finally:
        db.close()


def _cancel_requested_for(run_id: int) -> bool:
    with _run_lock:
        return run_id in _cancel_requested


def _clear_cancel(run_id: int) -> None:
    with _run_lock:
        _cancel_requested.discard(run_id)


def _raise_if_cancelled(run_id: int) -> None:
    if _cancel_requested_for(run_id):
        raise ScanCancelled()


def _finalize_cancelled(db: Session, run_id: int, task_id: int, note: str = "") -> None:
    run = db.query(ScanRun).filter(ScanRun.id == run_id).first()
    if not run:
        return
    if run.status in TERMINAL_STATUSES and run.status != "cancelled":
        return
    run.status = "cancelled"
    run.phase = ""
    run.error = (note or "用户取消")[:2000]
    run.progress_message = "已取消"
    run.finished_at = datetime.utcnow()
    db.add(run)
    task = db.query(WatchTask).filter(WatchTask.id == task_id).first()
    if task:
        task.last_run_at = run.finished_at
        # 公开单次任务不自动排队；保留字段以免调度误触发
        if task.enabled:
            task.next_run_at = run.finished_at + timedelta(hours=max(1, task.interval_hours))
        db.add(task)
    db.commit()
    _clear_cancel(run_id)
    _log.info("扫描已取消 task=%s run=%s", task_id, run_id)


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


def _clean_leg_summary(text: str) -> str:
    s = (text or "").strip()
    if not s or s in {"N/A", "待核验", "见核对", "回程见核对链接"}:
        return ""
    if s.startswith("Google往返价"):
        return ""
    return s


def _persist_leg_summaries(opt) -> tuple[str, str]:
    out_sum = _clean_leg_summary(
        getattr(opt, "summary_outbound", "") or getattr(opt, "outbound_summary", "")
    )
    ret_sum = _clean_leg_summary(
        getattr(opt, "summary_return", "") or getattr(opt, "return_summary", "")
    )
    return out_sum, ret_sum


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
            out_sum, ret_sum = _persist_leg_summaries(opt)
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
                    outbound_summary=out_sum,
                    return_summary=ret_sum,
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
        run.progress_message = "日历扫价：准备拉取去/回程按天最低价…"
        db.commit()

        origins, dests = _task_od_codes(task)
        origin = origins[0]
        dest = ",".join(dests)
        start = datetime.strptime(task.start_date, "%Y-%m-%d")
        end = datetime.strptime(task.end_date, "%Y-%m-%d")
        od_pairs = [(o, d) for o in origins for d in dests]
        target_n = max(10, min(max(1, task.top_n), settings.verify_top_k))
        day_max = max(0, int(getattr(settings, "calendar_google_day_max", 16) or 16))
        cand_k = candidate_pool_size(
            target_n, int(getattr(settings, "calendar_candidate_k", 20) or 20)
        )
        refine_top = max(0, int(getattr(settings, "ctrip_h5_refine_top", 0) or 0))
        budget_sec = max(120, int(getattr(settings, "verify_budget_sec", 720) or 720))

        out0, out1 = outbound_day_range(start, end)
        ret0, ret1 = return_day_range(start, end, task.stay_min, task.stay_max)
        out_days = iter_day_strings(out0, out1)
        ret_days = iter_day_strings(ret0, ret1)
        cal_steps = max(1, (len(out_days) + len(ret_days)) * len(od_pairs))
        fill_budget = day_max * 2 * len(od_pairs)
        progress_total = cal_steps + fill_budget + cand_k + (refine_top if refine_top else 0)

        od_hint = f"{task.origin}→{task.dest}"
        if len(dests) > 1:
            od_hint += f"（{len(dests)} 个机场）"
        _set_progress(
            run_id,
            0,
            progress_total,
            "discovering",
            f"日历发现：{cal_steps} 天步（{od_hint}）",
        )

        client = TravelpayoutsClient(
            settings.travelpayouts_token,
            request_delay_sec=settings.travelpayouts_request_delay,
            market=settings.travelpayouts_market,
        )

        all_candidates: list = []
        done_base = 0
        g_delay = float(getattr(settings, "google_verify_delay_sec", 0.2) or 0.2)
        budget_deadline = time.time() + budget_sec
        # 日历诊断：供空候选错误文案 / 国内骨架回落决策
        cal_out_hits = 0
        cal_ret_hits = 0
        cal_out_days_n = 0
        cal_ret_days_n = 0
        matched_n = 0
        used_skeleton_pad = False

        for oi, (o_code, d_code) in enumerate(od_pairs):
            _raise_if_cancelled(run_id)

            def on_out(done: int, total: int, _base=done_base, _o=o_code, _d=d_code) -> None:
                _raise_if_cancelled(run_id)
                _set_progress(
                    run_id,
                    _base + done,
                    progress_total,
                    "discovering",
                    f"去程日历 {_base + done}/{cal_steps} · {_o}→{_d}",
                )

            def on_ret(done: int, total: int, _o=o_code, _d=d_code) -> None:
                _raise_if_cancelled(run_id)
                base = done_base + len(out_days)
                _set_progress(
                    run_id,
                    base + done,
                    progress_total,
                    "discovering",
                    f"回程日历 {base + done}/{cal_steps} · {_d}→{_o}",
                )

            try:
                out_cal = client.scan_leg_calendar(
                    o_code,
                    d_code,
                    out0,
                    out1,
                    currency=task.currency,
                    on_progress=on_out,
                )
            except ScanCancelled:
                raise
            except RuntimeError as e:
                msg = str(e)
                if "not flightable" in msg or "unknown location" in msg or "HTTP 400" in msg:
                    _log.warning("跳过不可用航线(去程日历) %s→%s: %s", o_code, d_code, msg[:200])
                    done_base += len(out_days) + len(ret_days)
                    continue
                raise

            try:
                ret_cal = client.scan_leg_calendar(
                    d_code,
                    o_code,
                    ret0,
                    ret1,
                    currency=task.currency,
                    on_progress=on_ret,
                )
            except ScanCancelled:
                raise
            except RuntimeError as e:
                msg = str(e)
                if "not flightable" in msg or "unknown location" in msg or "HTTP 400" in msg:
                    _log.warning("跳过不可用航线(回程日历) %s→%s: %s", d_code, o_code, msg[:200])
                    done_base += len(out_days) + len(ret_days)
                    continue
                raise

            done_base += len(out_days) + len(ret_days)
            _raise_if_cancelled(run_id)

            missing_out = [d for d in out_days if d not in out_cal]
            missing_ret = [d for d in ret_days if d not in ret_cal]
            cheap_out = sorted(out_cal, key=lambda d: out_cal[d])[:5]
            cheap_ret = sorted(ret_cal, key=lambda d: ret_cal[d])[:5]
            fill_out = pick_days_for_google_fill(missing_out, day_max, cheap_seeds=cheap_out)
            fill_ret = pick_days_for_google_fill(missing_ret, day_max, cheap_seeds=cheap_ret)

            fill_phase_base = cal_steps + oi * day_max * 2
            if fill_out and time.time() < budget_deadline:
                _set_progress(
                    run_id,
                    fill_phase_base,
                    progress_total,
                    "discovering",
                    f"Google 单程补洞去程 {len(fill_out)} 天 · {o_code}→{d_code}",
                )

                def on_fill_out(done, total, day=None, price=None, _b=fill_phase_base):
                    _set_progress(
                        run_id,
                        _b + done,
                        progress_total,
                        "discovering",
                        f"去程补洞 {done}/{total}"
                        + (f" · {day} ¥{int(price)}" if day and price else ""),
                    )

                try:
                    got = scrape_google_one_way_days(
                        o_code,
                        d_code,
                        fill_out,
                        adults=task.adults,
                        timeout_ms=settings.ctrip_verify_timeout_ms,
                        delay_sec=max(0.2, g_delay),
                        on_each=on_fill_out,
                        should_stop=lambda: _cancel_requested_for(run_id),
                        deadline_ts=budget_deadline,
                    )
                    out_cal.update(got)
                except Exception as e:
                    _log.warning("去程 Google 补洞失败 %s→%s: %s", o_code, d_code, e)

            if fill_ret and time.time() < budget_deadline:
                ret_fill_base = fill_phase_base + day_max
                _set_progress(
                    run_id,
                    ret_fill_base,
                    progress_total,
                    "discovering",
                    f"Google 单程补洞回程 {len(fill_ret)} 天 · {d_code}→{o_code}",
                )

                def on_fill_ret(done, total, day=None, price=None, _b=ret_fill_base):
                    _set_progress(
                        run_id,
                        _b + done,
                        progress_total,
                        "discovering",
                        f"回程补洞 {done}/{total}"
                        + (f" · {day} ¥{int(price)}" if day and price else ""),
                    )

                try:
                    got = scrape_google_one_way_days(
                        d_code,
                        o_code,
                        fill_ret,
                        adults=task.adults,
                        timeout_ms=settings.ctrip_verify_timeout_ms,
                        delay_sec=max(0.2, g_delay),
                        on_each=on_fill_ret,
                        should_stop=lambda: _cancel_requested_for(run_id),
                        deadline_ts=budget_deadline,
                    )
                    ret_cal.update(got)
                except Exception as e:
                    _log.warning("回程 Google 补洞失败 %s→%s: %s", d_code, o_code, e)

            _raise_if_cancelled(run_id)
            cal_out_hits += len(out_cal)
            cal_ret_hits += len(ret_cal)
            cal_out_days_n += len(out_days)
            cal_ret_days_n += len(ret_days)
            pool = select_calendar_verify_pool(
                out_cal,
                ret_cal,
                start,
                end,
                task.stay_min,
                task.stay_max,
                o_code,
                d_code,
                adults=task.adults,
                currency=task.currency,
                target_n=target_n,
                candidate_k=cand_k,
                pad_skeletons=False,
            )
            matched_n += len(pool)
            all_candidates.extend(pool)
            _log.info(
                "日历匹配 %s→%s：去程有价 %s/%s 回程有价 %s/%s 候选 %s",
                o_code,
                d_code,
                len(out_cal),
                len(out_days),
                len(ret_cal),
                len(ret_days),
                len(pool),
            )

        domestic = not any(
            is_likely_international(o_code, d_code) for o_code, d_code in od_pairs
        )
        if not all_candidates and domestic:
            # 国内稀航线：TP/Google 补洞凑不出双边有价时，均匀骨架直进 OTA 核验（非 mock）
            _set_progress(
                run_id,
                cal_steps + fill_budget,
                progress_total,
                "discovering",
                "缓存/补洞无双边组合 · 国内均匀日期直核验",
            )
            for o_code, d_code in od_pairs:
                pool = select_calendar_verify_pool(
                    {},
                    {},
                    start,
                    end,
                    task.stay_min,
                    task.stay_max,
                    o_code,
                    d_code,
                    adults=task.adults,
                    currency=task.currency,
                    target_n=target_n,
                    candidate_k=cand_k,
                    pad_skeletons=True,
                )
                all_candidates.extend(pool)
            used_skeleton_pad = bool(all_candidates)
            _log.info(
                "国内骨架回落：去程有价 %s/%s 回程有价 %s/%s 匹配 %s → 骨架候选 %s",
                cal_out_hits,
                cal_out_days_n or len(out_days),
                cal_ret_hits,
                cal_ret_days_n or len(ret_days),
                matched_n,
                len(all_candidates),
            )

        if not all_candidates:
            raise RuntimeError(
                _empty_calendar_error(
                    cal_out_hits,
                    cal_out_days_n or len(out_days),
                    cal_ret_hits,
                    cal_ret_days_n or len(ret_days),
                    matched_n,
                    domestic=domestic,
                )
            )

        all_candidates.sort(key=lambda o: (o.cache_price or o.total_price or 1e18))
        candidates = all_candidates[:cand_k]
        for opt in candidates:
            _attach_all_links(opt, origin, opt.dest_code or dests[0], task.adults)
            if not opt.verify_url:
                opt.verify_url = ensure_verify_url(
                    opt, opt.origin_code or origin, opt.dest_code or dests[0], task.adults
                )
            if not opt.cache_price and opt.total_price > 0:
                opt.cache_price = opt.total_price

        _raise_if_cancelled(run_id)
        phase_offset = cal_steps + fill_budget
        from app.services.ctrip_verify import _ctrip_cticket

        verify_providers = ["google"]
        if domestic and _ctrip_cticket():
            verify_providers = ["google", "ctrip_h5"]
        provider_label = "+".join(verify_providers)
        if used_skeleton_pad:
            discover_label = (
                f"缓存/补洞无双边组合 · 国内均匀日期直核验 {len(candidates)}"
            )
        else:
            discover_label = f"日历匹配完成 · 双边有价候选 {len(candidates)}"
        _set_progress(
            run_id,
            phase_offset,
            progress_total,
            "verifying",
            f"{discover_label} · {provider_label} 核验 Top-{target_n}"
            f"（预算 {budget_sec}s）",
        )

        live_ok: list = []
        verify_errors: list[str] = []

        def on_verify_factory(label: str, base: int, total_hint: int):
            def on_verify(done: int, total: int, opt=None) -> None:
                if _cancel_requested_for(run_id):
                    return
                if opt is not None and getattr(opt, "verify_status", "") == "ok" and opt.verified_price:
                    key = (opt.outbound_date, opt.return_date, (opt.dest_code or "").upper())
                    live_ok[:] = [
                        x
                        for x in live_ok
                        if (x.outbound_date, x.return_date, (x.dest_code or "").upper()) != key
                    ]
                    live_ok.append(opt)
                    _persist_live_results(
                        run_id, live_ok, task.currency, origin, dests[0], task.adults
                    )
                    price = float(opt.verified_price)
                    dest_tag = getattr(opt, "dest_code", "") or ""
                    src = getattr(opt, "source", "") or ""
                    msg = (
                        f"{label} {done}/{total_hint or total} · 成功池 {len(live_ok)} · "
                        f"最新 {task.currency} {price:,.0f} [{src}]"
                        f"（{opt.outbound_date}→{opt.return_date}"
                        + (f" · {dest_tag}" if dest_tag else "")
                        + "）"
                    )
                elif opt is not None and getattr(opt, "verify_status", "") == "failed":
                    msg = f"{label} {done}/{total_hint or total} · 本组未通过（池内 {len(live_ok)}）"
                else:
                    msg = f"{label} {done}/{total_hint or total} · 池内 {len(live_ok)}"
                _set_progress(run_id, base + done, progress_total, "verifying", msg)

            return on_verify

        verify_timeout = settings.ctrip_verify_timeout_ms
        if "ctrip_h5" in verify_providers:
            verify_timeout = max(verify_timeout, 90000)
        verified_a, err_a = verify_candidates(
            candidates,
            origin,
            dests[0],
            adults=task.adults,
            timeout_ms=verify_timeout,
            delay_sec=max(0.2, g_delay),
            on_each=on_verify_factory("核验", phase_offset, len(candidates)),
            target_ok=target_n,
            should_stop=lambda: _cancel_requested_for(run_id),
            providers=verify_providers,
            deadline_ts=budget_deadline,
            google_mode="full",
        )
        verify_errors.extend(err_a)
        if _cancel_requested_for(run_id):
            raise ScanCancelled()

        google_ok = [o for o in verified_a if o.verify_status == "ok" and o.verified_price]
        google_ok.sort(key=lambda o: o.verified_price or o.total_price)
        phase_offset = cal_steps + fill_budget + len(candidates)

        refined: list = []
        if refine_top > 0 and google_ok and time.time() < budget_deadline:
            import copy

            from app.services.ctrip_verify import _ctrip_cticket

            if _ctrip_cticket():
                refine_list = [copy.copy(o) for o in google_ok[:refine_top]]
                for opt in refine_list:
                    opt.verify_status = "pending"
                    opt.verified_price = None
                h5_delay = float(getattr(settings, "ctrip_h5_verify_delay_sec", 2.5) or 2.5)
                _set_progress(
                    run_id,
                    phase_offset,
                    progress_total,
                    "verifying",
                    f"携程H5 精修头部 {len(refine_list)} 组",
                )
                refined, err_b = verify_candidates(
                    refine_list,
                    origin,
                    dests[0],
                    adults=task.adults,
                    timeout_ms=max(settings.ctrip_verify_timeout_ms, 90000),
                    delay_sec=max(1.0, h5_delay),
                    on_each=on_verify_factory("携程精修", phase_offset, len(refine_list)),
                    target_ok=len(refine_list),
                    should_stop=lambda: _cancel_requested_for(run_id),
                    providers=["ctrip_h5"],
                    deadline_ts=budget_deadline,
                )
                verify_errors.extend(err_b)
            else:
                verify_errors.append("跳过携程精修：未配置 CTRIP_CTICKET")
        if _cancel_requested_for(run_id):
            raise ScanCancelled()

        from app.services.ctrip_verify import _looks_like_schedule_summary

        merged: dict = {}
        for o in google_ok:
            k = (o.outbound_date, o.return_date, (o.dest_code or "").upper())
            merged[k] = o
        for o in refined:
            if o.verify_status != "ok":
                continue
            k = (o.outbound_date, o.return_date, (o.dest_code or "").upper())
            base = merged.get(k)
            h5_out = getattr(o, "summary_outbound", "") or ""
            h5_ret = getattr(o, "summary_return", "") or ""
            if base is None:
                if o.verified_price:
                    merged[k] = o
                continue
            if _looks_like_schedule_summary(h5_out):
                base.summary_outbound = h5_out
            if _looks_like_schedule_summary(h5_ret):
                base.summary_return = h5_ret
            if not base.verified_price and o.verified_price:
                base.verified_price = o.verified_price
                base.total_price = o.total_price
                base.verify_status = "ok"
                base.source = o.source

        ok_options = [
            o for o in merged.values() if o.verify_status == "ok" and o.verified_price
        ]
        ok_options.sort(key=lambda o: o.verified_price or o.total_price)
        ok_options = ok_options[:target_n]
        verified_all = list(merged.values())

        if not ok_options:
            detail = "; ".join(verify_errors[:5]) or "未知原因"
            raise RuntimeError(
                f"OTA 核验全部失败（Google/H5），不得使用未核验缓存价作为最优结果。{detail}"
            )

        for opt in ok_options:
            _attach_all_links(opt, opt.origin_code or origin, opt.dest_code or dests[0], task.adults)

        db.query(FlightResult).filter(FlightResult.run_id == run_id).delete()
        for i, opt in enumerate(ok_options, 1):
            out_sum, ret_sum = _persist_leg_summaries(opt)
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
                    outbound_summary=out_sum,
                    return_summary=ret_sum,
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
        _clear_cancel(run_id)
        _log.info(
            "扫描完成 task=%s run=%s status=%s min_price=%s verified=%s failed=%s",
            task_id,
            run_id,
            run.status,
            min_price,
            len(ok_options),
            failed_n,
        )
    except ScanCancelled:
        _log.info("扫描取消 run=%s", run_id)
        try:
            _finalize_cancelled(db, run_id, task_id)
        except Exception:
            _log.exception("写入取消状态出错")
    except Exception as e:
        if _cancel_requested_for(run_id):
            _log.info("扫描取消(异常路径) run=%s err=%s", run_id, e)
            try:
                _finalize_cancelled(db, run_id, task_id)
            except Exception:
                _log.exception("写入取消状态出错")
        else:
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
        _clear_cancel(run_id)
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
