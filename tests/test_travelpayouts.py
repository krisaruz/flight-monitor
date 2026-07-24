"""Travelpayouts 解析与批次估算单测（无网络）。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.flight_search import FlightOption
from app.services.travelpayouts import (
    TravelpayoutsClient,
    count_api_batches,
    google_flights_url,
    iter_date_combos,
    months_covering,
    select_verify_pool,
    to_city_code,
)


def test_to_city_code_osaka() -> None:
    assert to_city_code("KIX") == "OSA"
    assert to_city_code("大阪") == "OSA"
    assert to_city_code("HKG") == "HKG"


def test_months_covering_cross_month() -> None:
    s = datetime(2026, 11, 15)
    e = datetime(2026, 12, 7)
    assert months_covering(s, e) == ["2026-11", "2026-12"]


def test_iter_date_combos_and_count() -> None:
    s = datetime(2026, 11, 15)
    e = datetime(2026, 11, 16)
    combos = iter_date_combos(s, e, 3, 5)
    # 2 天 × 3 停留
    assert len(combos) == 6
    assert ("2026-11-15", "2026-11-18") in combos
    assert count_api_batches(s, e, 3, 5) == 6


def test_google_flights_url_contains_dates() -> None:
    url = google_flights_url("HKG", "KIX", "2026-11-25", "2026-11-30")
    assert "google.com/travel/flights" in url
    assert "2026-11-25" in url
    assert "2026-11-30" in url


def test_row_to_option_includes_flight_no_and_times() -> None:
    client = TravelpayoutsClient("dummy")
    row = {
        "origin": "HKG",
        "destination": "OSA",
        "origin_airport": "HKG",
        "destination_airport": "KIX",
        "price": 1451,
        "airline": "UO",
        "flight_number": "822",
        "departure_at": "2026-11-25T20:45:00+08:00",
        "return_at": "2026-11-30T17:15:00+09:00",
        "transfers": 0,
        "return_transfers": 1,
        "duration_to": 215,
        "duration_back": 240,
        "currency": "cny",
    }
    opt = client._row_to_option(row, "HKG", "KIX", "CNY", 1)
    assert opt is not None
    assert opt.outbound_date == "2026-11-25"
    assert opt.return_date == "2026-11-30"
    assert opt.trip_days == 5
    assert opt.total_price == 1451
    assert "UO822" in opt.outbound_summary
    assert "20:45" in opt.outbound_summary
    assert "→" in opt.outbound_summary
    assert "回程" in opt.return_summary
    assert "17:15" in opt.return_summary


def test_scan_window_uses_prices_for_dates() -> None:
    client = TravelpayoutsClient("dummy", request_delay_sec=0.01)

    def fake_fetch(origin, destination, departure_at, return_at, currency="CNY", limit=5):
        if departure_at == "2026-11-20" and return_at == "2026-11-24":
            return [
                {
                    "price": 1500,
                    "airline": "HX",
                    "flight_number": "3",
                    "departure_at": "2026-11-20T12:00:00+08:00",
                    "return_at": "2026-11-24T12:00:00+09:00",
                    "transfers": 0,
                    "return_transfers": 0,
                    "origin_airport": "HKG",
                    "destination_airport": "KIX",
                    "duration_to": 200,
                    "duration_back": 210,
                }
            ]
        return []

    with patch.object(client, "fetch_prices_for_dates", side_effect=fake_fetch):
        opts = client.scan_window(
            "HKG",
            "KIX",
            datetime(2026, 11, 19),
            datetime(2026, 11, 20),
            stay_min=3,
            stay_max=5,
            currency="CNY",
        )
    assert len(opts) == 1
    assert opts[0].total_price == 1500
    assert "HX3" in opts[0].outbound_summary


def test_scan_window_network_failure_is_fatal() -> None:
    client = TravelpayoutsClient("dummy", request_delay_sec=0.01)

    with patch.object(client, "fetch_prices_for_dates", side_effect=RuntimeError("boom")):
        try:
            client.scan_window(
                "HKG",
                "KIX",
                datetime(2026, 11, 15),
                datetime(2026, 11, 15),
                stay_min=3,
                stay_max=3,
            )
            raised = False
        except RuntimeError as e:
            raised = True
            assert "boom" in str(e)
    assert raised


def test_select_verify_pool_fills_to_top_n() -> None:
    combos = iter_date_combos(datetime(2026, 11, 15), datetime(2026, 11, 25), 3, 3)
    priced = [
        FlightOption(
            outbound_date="2026-11-15",
            return_date="2026-11-18",
            total_price=2012,
            currency="CNY",
            cache_price=2012,
            source="Travelpayouts",
        )
    ]
    pool = select_verify_pool(
        priced,
        combos,
        "HKG",
        "KIX",
        adults=1,
        target=10,
        max_attempts=15,
    )
    assert len(pool) == min(15, len(combos))
    assert len(pool) >= 10
    assert pool[0].cache_price == 2012
    assert sum(1 for o in pool if o.source == "DateCombo") >= 9


def test_fetch_prices_for_dates_path() -> None:
    client = TravelpayoutsClient("dummy", market="cn")
    captured: dict = {}

    def fake_get(path: str, params: dict, retries: int = 3):
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    with patch.object(client, "_get", side_effect=fake_get):
        client.fetch_prices_for_dates("HKG", "KIX", "2026-11-15", "2026-11-18")
    assert captured["path"] == "/aviasales/v3/prices_for_dates"
    assert captured["params"]["departure_at"] == "2026-11-15"
    assert captured["params"]["return_at"] == "2026-11-18"
