from __future__ import annotations

"""携程 / 去哪儿 / Google Flights 搜索深链（国内 + 国际/地区）。

链接目标是「真实浏览器可打开的搜索页」，不编造价格。
携程对自动化常返回 WhaleGuard(432)，真实用户 Chrome 可访问；
去哪儿旧 round_list 已失效，改用官网带参首页。
"""

from datetime import datetime, timedelta
from urllib.parse import quote

from app.services.flight_search import FlightOption, list_departure_dates, resolve_airport
from app.services.travelpayouts import google_flights_url

# 常见「偏国际/地区」机场：优先强调 Google Flights + 携程国际搜索
REGIONAL_OR_INTL = {
    "HKG", "MFM", "TPE", "KHH", "NRT", "HND", "KIX", "ITM", "ICN", "GMP",
    "BKK", "SIN", "KUL", "MNL", "SGN", "HAN", "PUS", "FUK", "NGO", "CTS", "OKA",
}

# 机场码 → 携程城市码（多机场城市合并；其余回落为机场小写）
CTRIP_CITY_CODE: dict[str, str] = {
    "HKG": "hkg",
    "MFM": "mfm",
    "KIX": "osa",
    "ITM": "osa",
    "OSA": "osa",
    "NRT": "tyo",
    "HND": "tyo",
    "TYO": "tyo",
    "ICN": "sel",
    "GMP": "sel",
    "SEL": "sel",
    "BKK": "bkk",
    "DMK": "bkk",
    "SIN": "sin",
    "TPE": "tpe",
    "KHH": "khh",
    "PVG": "sha",
    "SHA": "sha",
    "PEK": "bjs",
    "PKX": "bjs",
    "BJS": "bjs",
    "CAN": "can",
    "SZX": "szx",
    "CTU": "ctu",
    "TFU": "ctu",
    "CKG": "ckg",
    "HGH": "hgh",
    "NKG": "nkg",
    "XIY": "sia",
    "SIA": "sia",
    "ZUH": "zuh",
    "KMG": "kmg",
    "XMN": "xmn",
    "CSX": "csx",
    "TAO": "tao",
    "DLC": "dlc",
    "TSN": "tsn",
    "CGO": "cgo",
    "WUH": "wuh",
    "NGO": "ngo",
    "FUK": "fuk",
    "CTS": "spk",
    "SPK": "spk",
    "OKA": "oka",
    "PUS": "pus",
    "KUL": "kul",
    "MNL": "mnl",
    "SGN": "sgn",
    "HAN": "han",
    "PNH": "pnh",
    "REP": "rep",
    "KOS": "kos",
    "JKT": "jkt",
    "CGK": "jkt",
    "DPS": "dps",
    "HKT": "hkt",
    "CNX": "cnx",
    "DAD": "dad",
    "DXB": "dxb",
    "SYD": "syd",
    "MEL": "mel",
    "AKL": "akl",
    "TPE": "tpe",
    "VTE": "vte",
    "RGN": "rgn",
}

# 机场码 → 去哪儿中文城市名（官网搜索依赖中文城市，机场三字码会 404）
CITY_CN: dict[str, str] = {
    "HKG": "香港",
    "MFM": "澳门",
    "KIX": "大阪",
    "ITM": "大阪",
    "OSA": "大阪",
    "NRT": "东京",
    "HND": "东京",
    "TYO": "东京",
    "ICN": "首尔",
    "GMP": "首尔",
    "SEL": "首尔",
    "BKK": "曼谷",
    "DMK": "曼谷",
    "SIN": "新加坡",
    "TPE": "台北",
    "KHH": "高雄",
    "PVG": "上海",
    "SHA": "上海",
    "PEK": "北京",
    "PKX": "北京",
    "BJS": "北京",
    "CAN": "广州",
    "SZX": "深圳",
    "CTU": "成都",
    "TFU": "成都",
    "CKG": "重庆",
    "HGH": "杭州",
    "NKG": "南京",
    "XIY": "西安",
    "SIA": "西安",
    "ZUH": "珠海",
    "KMG": "昆明",
    "XMN": "厦门",
    "CSX": "长沙",
    "TAO": "青岛",
    "DLC": "大连",
    "TSN": "天津",
    "CGO": "郑州",
    "WUH": "武汉",
    "NGO": "名古屋",
    "FUK": "福冈",
    "CTS": "札幌",
    "SPK": "札幌",
    "OKA": "冲绳",
    "PUS": "釜山",
    "KUL": "吉隆坡",
    "MNL": "马尼拉",
    "SGN": "胡志明市",
    "HAN": "河内",
    "PNH": "金边",
    "REP": "暹粒",
    "KOS": "西哈努克",
    "JKT": "雅加达",
    "CGK": "雅加达",
    "DPS": "巴厘岛",
    "HKT": "普吉",
    "CNX": "清迈",
    "DAD": "岘港",
    "DXB": "迪拜",
    "SYD": "悉尼",
    "MEL": "墨尔本",
    "AKL": "奥克兰",
    "VTE": "万象",
    "RGN": "仰光",
}


def is_likely_international(origin: str, dest: str) -> bool:
    o, d = resolve_airport(origin), resolve_airport(dest)
    return o in REGIONAL_OR_INTL or d in REGIONAL_OR_INTL


def ctrip_city_code(place: str) -> str:
    code = resolve_airport(place)
    return CTRIP_CITY_CODE.get(code, code.lower())


def city_display_name(place: str) -> str:
    """去哪儿等中文站用的城市名；已是中文则原样返回。"""
    raw = (place or "").strip()
    if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        return raw
    code = resolve_airport(raw)
    return CITY_CN.get(code, code)


def ctrip_round_trip_url(origin: str, dest: str, outbound: str, ret: str, adults: int = 1) -> str:
    """携程往返列表页：城市码 + depdate/retdate（arrdate 已不可靠）。"""
    o, d = ctrip_city_code(origin), ctrip_city_code(dest)
    return (
        f"https://flights.ctrip.com/online/list/round-{o}-{d}"
        f"?depdate={outbound}&retdate={ret}&cabin=y_s&adult={adults}&child=0&infant=0"
    )


def qunar_round_trip_url(origin: str, dest: str, outbound: str, ret: str) -> str:
    """去哪儿往返列表页（旧 round_list.htm 已 404；首页带参常不触发搜价）。"""
    o_cn, d_cn = city_display_name(origin), city_display_name(dest)
    from_code = ctrip_city_code(origin).upper()
    to_code = ctrip_city_code(dest).upper()
    return (
        "https://flight.qunar.com/site/roundtrip_list.htm?"
        f"searchDepartureAirport={quote(o_cn)}&searchArrivalAirport={quote(d_cn)}"
        f"&searchDepartureTime={outbound}&searchArrivalTime={ret}"
        f"&nextNDays=0&startSearch=true&fromCode={from_code}&toCode={to_code}"
        f"&from=flight_home_search&lowestPrice=null"
    )


def fliggy_city_code(place: str) -> str:
    """飞猪常用大写城市码（与携程城市码同系：PEK→BJS、PVG→SHA）。"""
    return ctrip_city_code(place).upper()


def fliggy_round_trip_url(
    origin: str,
    dest: str,
    outbound: str,
    ret: str,
    adults: int = 1,
) -> str:
    """飞猪机票搜索（往返）。优先 www 入口，sjipiao 子域在部分网络会被重置。"""
    o, d = fliggy_city_code(origin), fliggy_city_code(dest)
    o_cn, d_cn = city_display_name(origin), city_display_name(dest)
    return (
        "https://www.fliggy.com/flight/search?"
        f"tripType=1&depCityCode={o}&arrCityCode={d}"
        f"&depCityName={quote(o_cn)}&arrCityName={quote(d_cn)}"
        f"&depDate={outbound}&arrDate={ret}&adultNum={adults}&childNum=0"
    )


def build_verify_links(
    origin: str,
    dest: str,
    outbound: str,
    ret: str,
    adults: int = 1,
) -> dict[str, str]:
    return {
        "ctrip": ctrip_round_trip_url(origin, dest, outbound, ret, adults=adults),
        "qunar": qunar_round_trip_url(origin, dest, outbound, ret),
        "fliggy": fliggy_round_trip_url(origin, dest, outbound, ret, adults=adults),
        "google": google_flights_url(origin, dest, outbound, ret, adults=adults),
    }


def enumerate_date_combos(
    origin: str,
    dest: str,
    start_date: datetime,
    end_date: datetime,
    stay_min: int,
    stay_max: int,
    adults: int = 1,
    limit: int = 90,
) -> list[FlightOption]:
    """枚举出发窗 × 停留天数，不编造价格；附带三大平台核对链接。"""
    options: list[FlightOption] = []
    for dep in list_departure_dates(start_date, end_date):
        for stay in range(stay_min, stay_max + 1):
            ret = dep + timedelta(days=stay)
            out_s = dep.strftime("%Y-%m-%d")
            ret_s = ret.strftime("%Y-%m-%d")
            links = build_verify_links(origin, dest, out_s, ret_s, adults=adults)
            intl = is_likely_international(origin, dest)
            hint = "国际/地区航线 · 点右侧去比价" if intl else "国内航线 · 点右侧去比价"
            options.append(
                FlightOption(
                    outbound_date=out_s,
                    return_date=ret_s,
                    total_price=0,
                    currency="CNY",
                    booking_class="ECONOMY",
                    source="DeepLink",
                    verify_url=links["google"],
                    verify_url_ctrip=links["ctrip"],
                    verify_url_qunar=links["qunar"],
                    summary_outbound=hint,
                    summary_return=f"停留 {stay} 天",
                )
            )
            if len(options) >= limit:
                return options
    return options
