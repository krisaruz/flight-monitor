"""核心逻辑单测（无需 Amadeus）。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.flight_search import (
    FlightOption,
    FlightSegment,
    count_departure_dates,
    count_scan_combinations,
    dedupe_flight_options,
    generate_demo_data,
    list_departure_dates,
    resolve_airport,
)


def test_resolve_airport() -> None:
    assert resolve_airport("香港") == "HKG"
    assert resolve_airport("hkg") == "HKG"
    assert resolve_airport("nrt") == "NRT"
    assert resolve_airport("大阪") == "KIX"


def test_list_departure_dates_includes_end() -> None:
    s = datetime(2026, 11, 15)
    e = datetime(2026, 11, 17)
    dates = list_departure_dates(s, e)
    assert len(dates) == 3
    assert dates[0].day == 15
    assert dates[-1].day == 17


def test_count_departure_dates_new_semantics() -> None:
    """出发窗只约束出发日，不因停留天数缩短窗长。"""
    s = datetime(2026, 5, 1)
    e = datetime(2026, 5, 10)
    assert count_departure_dates(s, e) == 10


def test_count_scan_combinations_stay_range() -> None:
    s = datetime(2026, 11, 15)
    e = datetime(2026, 11, 16)  # 2 个出发日
    # 停留 3-5 = 3 档 → 2 * 3 = 6
    assert count_scan_combinations(s, e, 3, 5) == 6


def test_demo_allows_return_outside_window() -> None:
    s = datetime(2026, 12, 5)
    e = datetime(2026, 12, 7)
    rows = generate_demo_data("HKG", "KIX", s, e, stay_min=5, stay_max=5)
    assert rows
    # 12.7 出发 + 5 天 → 回程 12.12，可出窗
    last_departs = [r for r in rows if r.outbound_date == "2026-12-07"]
    assert last_departs
    assert all(r.return_date == "2026-12-12" for r in last_departs)


def test_dedupe_flight_options() -> None:
    seg_o = FlightSegment("CX", "CX100", "HKG", "KIX", "2026-05-01T10:00:00", "2026-05-01T14:00:00", "PT4H", 0)
    seg_r = FlightSegment("CX", "CX101", "KIX", "HKG", "2026-05-06T10:00:00", "2026-05-06T14:00:00", "PT4H", 0)
    a = FlightOption("2026-05-01", "2026-05-06", 2000.0, "CNY", [seg_o], [seg_r], "ECONOMY", "Amadeus")
    b = FlightOption("2026-05-01", "2026-05-06", 2000.0, "CNY", [seg_o], [seg_r], "ECONOMY", "Amadeus")
    assert len(dedupe_flight_options([a, b])) == 1


def test_generate_demo_data_stay_range() -> None:
    s = datetime(2026, 5, 1)
    e = datetime(2026, 5, 2)
    rows = generate_demo_data("HKG", "KIX", s, e, stay_min=3, stay_max=5)
    stays = {r.trip_days for r in rows}
    assert stays == {3, 4, 5}
