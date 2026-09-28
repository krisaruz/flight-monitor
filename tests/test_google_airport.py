from app.services.flight_search import canonical_airport_code, resolve_google_airport
from app.services.travelpayouts import google_flights_url, to_city_code


def test_osa_maps_to_kix_for_google() -> None:
    assert canonical_airport_code("OSA") == "KIX"
    assert resolve_google_airport("大阪") == "KIX"
    url = google_flights_url("ZUH", "OSA", "2026-11-20", "2026-11-24")
    assert "KIX" in url
    assert "to%20OSA" not in url
    # TP 边界仍转回城市码
    assert to_city_code("KIX") == "OSA"
