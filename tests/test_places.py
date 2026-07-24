"""地点目录：国家不限机场 / 建议列表。"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.cn_cities import load_cn_cities
from app.services.flight_search import resolve_airport
from app.services.places import expand_codes, list_suggestions, normalize_place


def test_cambodia_country_expands_airports() -> None:
    codes = expand_codes("", "柬埔寨")
    assert "PNH" in codes
    assert "REP" in codes
    assert len(codes) >= 2


def test_suggest_cambodia() -> None:
    rows = list_suggestions("柬埔寨", limit=20)
    kinds = {r.kind for r in rows}
    assert "country" in kinds
    country = next(r for r in rows if r.kind == "country" and r.id == "KH")
    assert len(country.codes) >= 2
    assert any(r.id == "PNH" for r in rows if r.kind == "airport")


def test_normalize_place_from_codes() -> None:
    label, codes = normalize_place("柬埔寨（不限机场）", "PNH,REP,KOS")
    assert "柬埔寨" in label
    assert codes == "PNH,REP,KOS"


def test_cn_cities_catalog_covers_mainland() -> None:
    cities = load_cn_cities()
    assert len(cities) >= 200
    by_code = {str(c["code"]): c for c in cities}
    assert by_code["ZUH"]["name_zh"] == "珠海"
    assert by_code["SIA"]["name_zh"] == "西安"
    assert by_code["RIZ"]["name_zh"] == "日照"


def test_suggest_zhuhai_and_english_alias() -> None:
    rows = list_suggestions("珠海", limit=20)
    airport = next(r for r in rows if r.kind == "airport" and r.id == "ZUH")
    assert airport.label == "珠海"
    assert airport.codes == ["ZUH"]
    assert expand_codes("", "珠海") == ["ZUH"]
    en = list_suggestions("Zhuhai", limit=20)
    assert any(r.id == "ZUH" for r in en if r.kind == "airport")


def test_china_country_uses_major_hubs_only() -> None:
    codes = expand_codes("", "中国大陆（不限机场）")
    assert "BJS" in codes and "SHA" in codes and "ZUH" in codes
    assert len(codes) < 40
    # 全量城市可搜，但不因「不限机场」一次展开
    assert len(load_cn_cities()) > len(codes)


def test_resolve_airport_cn_catalog() -> None:
    assert resolve_airport("珠海") == "ZUH"
    assert resolve_airport("西安") == "SIA"
    assert resolve_airport("XIY") == "SIA"
    assert resolve_airport("日照") == "RIZ"
