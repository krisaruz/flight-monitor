"""去程/回程按天最低价日历 → 窗内匹配最优往返组合。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from app.services.flight_search import FlightOption


def iter_day_strings(start: datetime, end: datetime) -> list[str]:
    if end < start:
        return []
    out: list[str] = []
    day = start
    while day <= end:
        out.append(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)
    return out


def iter_stay_combos(
    start: datetime,
    end: datetime,
    stay_min: int,
    stay_max: int,
) -> list[tuple[str, str]]:
    """出发窗 × 停留天数 → (outbound, return)。本地实现，避免与 travelpayouts 循环依赖。"""
    if end < start or stay_max < stay_min:
        return []
    stay_min = max(0, int(stay_min))
    stay_max = max(stay_min, int(stay_max))
    combos: list[tuple[str, str]] = []
    day = start
    while day <= end:
        for stay in range(stay_min, stay_max + 1):
            ret = day + timedelta(days=stay)
            combos.append((day.strftime("%Y-%m-%d"), ret.strftime("%Y-%m-%d")))
        day += timedelta(days=1)
    return combos


def outbound_day_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    return start, end


def return_day_range(
    start: datetime,
    end: datetime,
    stay_min: int,
    stay_max: int,
) -> tuple[datetime, datetime]:
    """回程可能落在 [start+stay_min, end+stay_max]。"""
    stay_min = max(0, int(stay_min))
    stay_max = max(stay_min, int(stay_max))
    return start + timedelta(days=stay_min), end + timedelta(days=stay_max)


def count_calendar_days(
    start: datetime,
    end: datetime,
    stay_min: int,
    stay_max: int,
) -> int:
    """去程天数 + 回程天数（发现阶段步数估算）。"""
    o0, o1 = outbound_day_range(start, end)
    r0, r1 = return_day_range(start, end, stay_min, stay_max)
    return max(1, len(iter_day_strings(o0, o1)) + len(iter_day_strings(r0, r1)))


def candidate_pool_size(target_n: int, calendar_candidate_k: int = 20) -> int:
    target_n = max(1, int(target_n))
    k = max(1, int(calendar_candidate_k))
    return max(target_n, min(target_n * 2, k))


def match_calendar_combos(
    out_cal: dict[str, float],
    ret_cal: dict[str, float],
    stay_min: int,
    stay_max: int,
) -> list[tuple[str, str, float]]:
    """
    合法停留内按 out_min + ret_min 升序。
    缺任一腿价格则跳过该组合。
    """
    stay_min = max(0, int(stay_min))
    stay_max = max(stay_min, int(stay_max))
    scored: list[tuple[str, str, float]] = []
    for out_s, out_p in out_cal.items():
        if out_p is None or float(out_p) <= 0:
            continue
        try:
            out_d = datetime.strptime(out_s, "%Y-%m-%d")
        except ValueError:
            continue
        for stay in range(stay_min, stay_max + 1):
            ret_d = out_d + timedelta(days=stay)
            ret_s = ret_d.strftime("%Y-%m-%d")
            ret_p = ret_cal.get(ret_s)
            if ret_p is None or float(ret_p) <= 0:
                continue
            scored.append((out_s, ret_s, float(out_p) + float(ret_p)))
    scored.sort(key=lambda x: (x[2], x[0], x[1]))
    return scored


def even_sample_days(days: list[str], max_n: int) -> list[str]:
    """均匀抽样日期（保持顺序）。"""
    if max_n <= 0 or not days:
        return []
    if len(days) <= max_n:
        return list(days)
    if max_n == 1:
        return [days[len(days) // 2]]
    out: list[str] = []
    last = len(days) - 1
    for i in range(max_n):
        idx = round(i * last / (max_n - 1))
        d = days[idx]
        if not out or out[-1] != d:
            out.append(d)
    # 去重后若不足，按原序补
    if len(out) < max_n:
        seen = set(out)
        for d in days:
            if d not in seen:
                out.append(d)
                seen.add(d)
            if len(out) >= max_n:
                break
    return out[:max_n]


def neighbor_days(seed: str, all_days: Iterable[str], radius: int = 1) -> list[str]:
    day_set = set(all_days)
    try:
        base = datetime.strptime(seed, "%Y-%m-%d")
    except ValueError:
        return []
    out: list[str] = []
    for delta in range(-radius, radius + 1):
        if delta == 0:
            continue
        d = (base + timedelta(days=delta)).strftime("%Y-%m-%d")
        if d in day_set:
            out.append(d)
    return out


def pick_days_for_google_fill(
    missing_days: list[str],
    max_n: int,
    cheap_seeds: list[str] | None = None,
    neighbor_radius: int = 1,
) -> list[str]:
    """
    先均匀抽，再对已有低价种子邻域 ±1 加密；总数不超过 max_n。
    missing_days 应已按时间排序。
    """
    if max_n <= 0 or not missing_days:
        return []
    ordered = sorted(set(missing_days))
    picked = even_sample_days(ordered, max_n)
    if len(picked) >= max_n:
        return picked[:max_n]
    have = set(picked)
    for seed in cheap_seeds or []:
        for d in neighbor_days(seed, ordered, radius=neighbor_radius):
            if d in have:
                continue
            if d not in set(ordered):
                continue
            picked.append(d)
            have.add(d)
            if len(picked) >= max_n:
                return picked[:max_n]
    # 仍不足则按序补剩余空洞
    for d in ordered:
        if d in have:
            continue
        picked.append(d)
        have.add(d)
        if len(picked) >= max_n:
            break
    return picked[:max_n]


def build_matched_options(
    matches: list[tuple[str, str, float]],
    origin: str,
    dest: str,
    adults: int,
    currency: str,
    limit: int,
) -> list[FlightOption]:
    """日历匹配结果 → FlightOption（cache_price=两腿和）。"""
    from app.services.travelpayouts import make_skeleton_option

    opts: list[FlightOption] = []
    for out_d, ret_d, score in matches[: max(0, limit)]:
        opt = make_skeleton_option(
            origin,
            dest,
            out_d,
            ret_d,
            adults=adults,
            cache_price=float(score),
            currency=currency,
        )
        opt.total_price = float(score)
        opt.source = "CalendarMatch"
        opts.append(opt)
    return opts


def pad_with_uniform_skeletons(
    existing: list[FlightOption],
    start: datetime,
    end: datetime,
    stay_min: int,
    stay_max: int,
    origin: str,
    dest: str,
    adults: int,
    currency: str,
    target: int,
) -> list[FlightOption]:
    """有价匹配不足 target 时，均匀日期骨架补洞。"""
    from app.services.travelpayouts import make_skeleton_option

    target = max(1, int(target))
    if len(existing) >= target:
        return existing[:target]

    have = {(o.outbound_date, o.return_date) for o in existing}
    combos = iter_stay_combos(start, end, stay_min, stay_max)
    if not combos:
        return existing

    need = target - len(existing)
    sampled = even_sample_days(
        [f"{a}|{b}" for a, b in combos],
        min(need * 3, len(combos)),
    )
    out = list(existing)
    for key in sampled:
        if "|" not in key:
            continue
        dep, ret = key.split("|", 1)
        if (dep, ret) in have:
            continue
        have.add((dep, ret))
        out.append(
            make_skeleton_option(
                origin,
                dest,
                dep,
                ret,
                adults=adults,
                cache_price=0,
                currency=currency,
            )
        )
        if len(out) >= target:
            break

    if len(out) < target:
        for dep, ret in combos:
            if (dep, ret) in have:
                continue
            have.add((dep, ret))
            out.append(
                make_skeleton_option(
                    origin,
                    dest,
                    dep,
                    ret,
                    adults=adults,
                    cache_price=0,
                    currency=currency,
                )
            )
            if len(out) >= target:
                break
    return out[:target]


def select_calendar_verify_pool(
    out_cal: dict[str, float],
    ret_cal: dict[str, float],
    start: datetime,
    end: datetime,
    stay_min: int,
    stay_max: int,
    origin: str,
    dest: str,
    adults: int,
    currency: str,
    target_n: int,
    candidate_k: int,
    pad_skeletons: bool = False,
) -> list[FlightOption]:
    """
    日历匹配 Top 候选（默认只保留「去+回都有日历价」的组合）。
    pad_skeletons=True 时才用均匀骨架补洞（默认关闭，避免空日期硬核验）。
    """
    pool_n = candidate_pool_size(target_n, candidate_k)
    matches = match_calendar_combos(out_cal, ret_cal, stay_min, stay_max)
    opts = build_matched_options(matches, origin, dest, adults, currency, pool_n)
    if pad_skeletons and len(opts) < target_n:
        opts = pad_with_uniform_skeletons(
            opts,
            start,
            end,
            stay_min,
            stay_max,
            origin,
            dest,
            adults,
            currency,
            target_n,
        )
    return opts[:pool_n]
