"""发现阶段空日历错误文案与国内骨架回落相关单测。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.calendar_match import select_calendar_verify_pool
from app.services.scanner import _empty_calendar_error


def test_empty_calendar_error_includes_stats_international() -> None:
    msg = _empty_calendar_error(0, 10, 0, 12, 0, domestic=False)
    assert "去程有价 0/10" in msg
    assert "回程有价 0/12" in msg
    assert "匹配 0" in msg
    assert "去程+回程都有日历价" in msg


def test_empty_calendar_error_domestic_mentions_skeleton() -> None:
    msg = _empty_calendar_error(2, 10, 1, 12, 0, domestic=True)
    assert "去程有价 2/10" in msg
    assert "国内均匀日期直核验" in msg


def test_domestic_empty_calendars_pad_skeletons_for_verify() -> None:
    """模拟 scanner 国内回落：双边空日历 + pad_skeletons=True → 有候选。"""
    start = datetime(2026, 9, 20)
    end = datetime(2026, 9, 29)
    opts = select_calendar_verify_pool(
        {},
        {},
        start,
        end,
        stay_min=2,
        stay_max=4,
        origin="ZUH",
        dest="DAT",
        adults=1,
        currency="CNY",
        target_n=10,
        candidate_k=20,
        pad_skeletons=True,
    )
    assert len(opts) >= 10
    assert all(o.outbound_date and o.return_date for o in opts)
    assert all((o.cache_price or 0) == 0 for o in opts)
