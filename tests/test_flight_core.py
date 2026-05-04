"""核心逻辑单测（无需 Amadeus / 飞书）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from flight_monitor import (
    FlightOption,
    FlightSegment,
    count_departure_dates,
    dedupe_flight_options,
    generate_demo_data,
    resolve_airport,
)
def test_resolve_airport() -> None:
    assert resolve_airport("香港") == "HKG"
    assert resolve_airport("hkg") == "HKG"
    assert resolve_airport("nrt") == "NRT"


def test_count_departure_dates() -> None:
    s = datetime(2026, 5, 1)
    e = datetime(2026, 5, 10)
    # 5/1~5/5 出发、5 天行程时回程落在 5/6~5/10，共 5 个可行出发日
    assert count_departure_dates(s, e, trip_days=5) == 5


def test_dedupe_flight_options() -> None:
    seg_o = FlightSegment("CX", "CX100", "HKG", "KIX", "2026-05-01T10:00:00", "2026-05-01T14:00:00", "PT4H", 0)
    seg_r = FlightSegment("CX", "CX101", "KIX", "HKG", "2026-05-06T10:00:00", "2026-05-06T14:00:00", "PT4H", 0)
    a = FlightOption(
        "2026-05-01",
        "2026-05-06",
        2000.0,
        "CNY",
        [seg_o],
        [seg_r],
        "ECONOMY",
        "Amadeus",
    )
    b = FlightOption(
        "2026-05-01",
        "2026-05-06",
        2000.0,
        "CNY",
        [seg_o],
        [seg_r],
        "ECONOMY",
        "Amadeus",
    )
    out = dedupe_flight_options([a, b])
    assert len(out) == 1


def test_generate_demo_data_nonempty() -> None:
    s = datetime(2026, 5, 1)
    e = datetime(2026, 5, 5)
    rows = generate_demo_data("HKG", "KIX", s, e, trip_days=3)
    assert len(rows) >= 1
    assert all(isinstance(x, FlightOption) for x in rows)
