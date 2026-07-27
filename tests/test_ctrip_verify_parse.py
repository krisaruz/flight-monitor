"""携程价格解析单测（不启动浏览器）。"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.ctrip_verify import (
    _is_blocked_page,
    extract_prices_from_json,
    extract_prices_from_text,
    parse_google_outbound_cards,
    pick_lowest,
    pick_ota_price,
)


def test_extract_prices_from_text_yuan() -> None:
    text = "经济舱 ￥1,288 起 另有 ¥999 和 CNY 2100"
    prices = extract_prices_from_text(text)
    assert 1288 in prices
    assert 999 in prices
    assert 2100 in prices
    assert pick_lowest(prices) == 999


def test_extract_prices_from_json_nested() -> None:
    data = {
        "data": {
            "flightItineraryList": [
                {"price": 1560, "segments": []},
                {"lowestPrice": 1320},
            ]
        }
    }
    prices = extract_prices_from_json(data)
    assert 1560 in prices
    assert 1320 in prices
    assert pick_lowest(prices) == 1320


def test_pick_lowest_empty() -> None:
    assert pick_lowest([]) is None


def test_is_blocked_page_whaleguard() -> None:
    assert _is_blocked_page("WhaleGuard block page")
    assert _is_blocked_page("请完成安全验证后继续")
    assert not _is_blocked_page("经济舱 ¥1288 起")


def test_pick_ota_price_prefers_api_and_rejects_noise() -> None:
    assert pick_ota_price([1288, 1560], [206, 99]) == 1288.0
    # 仅噪声低价 / 单一可疑 DOM 价 → 拒绝
    assert pick_ota_price([], [206, 206]) is None
    assert pick_ota_price([], [999, 1100, 1200]) == 999.0


def test_parse_google_outbound_cards() -> None:
    text = """
按热门航班排序
14:45
 –
19:20
香港快运航空
3 小时 35 分钟
HKG–KIX
直达
¥1,547
往返票价
01:45
 –
06:10
乐桃航空
3 小时 25 分钟
HKG–KIX
直达
¥1,869
往返票价
"""
    cards = parse_google_outbound_cards(text)
    assert len(cards) >= 2
    assert cards[0]["price"] == 1547.0
    assert cards[0]["dep_time"] == "14:45"
    assert cards[0]["arr_time"] == "19:20"
    assert "快运" in cards[0]["airline"] or "香港" in cards[0]["airline"]
