"""日历匹配纯逻辑单测（无网络）。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.calendar_match import (
    candidate_pool_size,
    count_calendar_days,
    even_sample_days,
    match_calendar_combos,
    pick_days_for_google_fill,
    select_calendar_verify_pool,
)


def test_match_calendar_combos_picks_cheapest_valid_stay() -> None:
    out_cal = {
        "2026-08-10": 800.0,
        "2026-08-11": 500.0,  # 最低去程
        "2026-08-12": 900.0,
    }
    ret_cal = {
        "2026-08-13": 700.0,
        "2026-08-14": 400.0,  # 与 08-11 组合 stay=3 → 900
        "2026-08-15": 600.0,
        "2026-08-16": 350.0,  # 与 08-11 stay=5 → 850 最优
    }
    matches = match_calendar_combos(out_cal, ret_cal, stay_min=3, stay_max=5)
    assert matches
    assert matches[0] == ("2026-08-11", "2026-08-16", 850.0)
    # 非法停留（如 stay=2）不应出现
    assert all(3 <= (datetime.strptime(r, "%Y-%m-%d") - datetime.strptime(o, "%Y-%m-%d")).days <= 5 for o, r, _ in matches)


def test_match_skips_missing_leg() -> None:
    out_cal = {"2026-08-10": 500.0, "2026-08-11": 400.0}
    ret_cal = {"2026-08-14": 300.0}  # 08-10+stay4 与 08-11+stay3 均合法
    matches = match_calendar_combos(out_cal, ret_cal, stay_min=3, stay_max=4)
    assert ("2026-08-11", "2026-08-14", 700.0) in matches
    assert ("2026-08-10", "2026-08-14", 800.0) in matches
    assert matches[0][2] == 700.0
    # 无回程价的去程日不会凭空出组合
    assert all(r == "2026-08-14" for _, r, _ in matches)


def test_even_sample_and_google_fill_budget() -> None:
    days = [f"2026-08-{i:02d}" for i in range(1, 21)]
    sampled = even_sample_days(days, 5)
    assert len(sampled) == 5
    assert sampled[0] == "2026-08-01"
    assert sampled[-1] == "2026-08-20"

    missing = days[5:]  # 缺后半
    picked = pick_days_for_google_fill(missing, max_n=4, cheap_seeds=["2026-08-10"])
    assert len(picked) <= 4
    assert len(picked) == 4


def test_candidate_pool_and_pad() -> None:
    assert candidate_pool_size(10, 20) == 20
    assert candidate_pool_size(15, 20) == 20
    assert candidate_pool_size(12, 20) == 20
    assert candidate_pool_size(5, 20) == 10

    start = datetime(2026, 8, 10)
    end = datetime(2026, 8, 12)
    # 默认不补骨架：空日历 → 空候选
    opts = select_calendar_verify_pool(
        {},
        {},
        start,
        end,
        stay_min=3,
        stay_max=3,
        origin="ZUH",
        dest="SIA",
        adults=1,
        currency="CNY",
        target_n=3,
        candidate_k=20,
        pad_skeletons=False,
    )
    assert opts == []

    # 显式允许骨架补洞
    opts2 = select_calendar_verify_pool(
        {},
        {},
        start,
        end,
        stay_min=3,
        stay_max=3,
        origin="ZUH",
        dest="SIA",
        adults=1,
        currency="CNY",
        target_n=3,
        candidate_k=20,
        pad_skeletons=True,
    )
    assert len(opts2) == 3
    assert all(o.outbound_date and o.return_date for o in opts2)


def test_count_calendar_days() -> None:
    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 3)
    # out 3 天；ret [start+2, end+4] = 8/3..8/7 → 5 天
    assert count_calendar_days(start, end, 2, 4) == 3 + 5
