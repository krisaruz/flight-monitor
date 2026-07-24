"""深链工具单测（核对跳转用，不再作为扫价成功路径）。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.deeplinks import (
    build_verify_links,
    ctrip_round_trip_url,
    enumerate_date_combos,
    is_likely_international,
    qunar_round_trip_url,
)


def test_is_likely_international() -> None:
    assert is_likely_international("HKG", "KIX") is True
    assert is_likely_international("PVG", "PEK") is False


def test_ctrip_and_qunar_urls() -> None:
    c = ctrip_round_trip_url("HKG", "KIX", "2026-11-25", "2026-11-30")
    assert "ctrip.com" in c
    assert "round-hkg-osa" in c
    assert "depdate=2026-11-25" in c
    assert "retdate=2026-11-30" in c
    assert "arrdate=" not in c
    q = qunar_round_trip_url("SHA", "BJS", "2026-05-01", "2026-05-05")
    assert "flight.qunar.com" in q
    assert "searchType=roundTrip" in q
    assert "2026-05-01" in q
    assert "2026-05-05" in q
    # 中文城市名（URL 编码后仍可还原）
    from urllib.parse import unquote

    assert "上海" in unquote(q) and "北京" in unquote(q)


def test_build_verify_links_has_three() -> None:
    links = build_verify_links("HKG", "KIX", "2026-11-25", "2026-11-30")
    assert "ctrip" in links and "qunar" in links and "google" in links


def test_enumerate_date_combos_helper_still_works() -> None:
    opts = enumerate_date_combos(
        "香港",
        "大阪",
        datetime(2026, 11, 15),
        datetime(2026, 11, 16),
        stay_min=3,
        stay_max=5,
        limit=20,
    )
    assert len(opts) == 6
    assert all(o.source == "DeepLink" for o in opts)
    assert all(o.verify_url_ctrip and o.verify_url_qunar and o.verify_url for o in opts)
