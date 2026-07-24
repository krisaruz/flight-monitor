from __future__ import annotations

"""出发地 / 目的地目录：支持城市机场与「国家 · 不限机场」。"""

from dataclasses import dataclass

from app.services.cn_cities import CN_MAJOR_HUB_CODES, load_cn_cities
from app.services.flight_search import resolve_airport


@dataclass(frozen=True)
class AirportPlace:
    code: str
    name_zh: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CountryPlace:
    iso2: str
    name_zh: str
    aliases: tuple[str, ...]
    airports: tuple[AirportPlace, ...]


# 面向中国旅客的常用目的地（城市码优先，供 Travelpayouts / OTA 深链）
COUNTRIES: tuple[CountryPlace, ...] = (
    CountryPlace(
        "JP",
        "日本",
        ("japan", "jp", "霓虹"),
        (
            AirportPlace("TYO", "东京", ("东京", "tokyo", "nrt", "hnd")),
            AirportPlace("OSA", "大阪", ("大阪", "osaka", "kix", "itm")),
            AirportPlace("NGO", "名古屋", ("名古屋", "nagoya")),
            AirportPlace("FUK", "福冈", ("福冈", "fukuoka")),
            AirportPlace("SPK", "札幌", ("札幌", "sapporo", "cts")),
            AirportPlace("OKA", "冲绳", ("冲绳", "okinawa", "那霸")),
        ),
    ),
    CountryPlace(
        "KR",
        "韩国",
        ("korea", "south korea", "kr", "南韩"),
        (
            AirportPlace("SEL", "首尔", ("首尔", "seoul", "icn", "gmp")),
            AirportPlace("PUS", "釜山", ("釜山", "busan", "pus")),
            AirportPlace("CJU", "济州", ("济州", "jeju")),
        ),
    ),
    CountryPlace(
        "TH",
        "泰国",
        ("thailand", "th", "暹罗"),
        (
            AirportPlace("BKK", "曼谷", ("曼谷", "bangkok", "dmk")),
            AirportPlace("HKT", "普吉", ("普吉", "phuket")),
            AirportPlace("CNX", "清迈", ("清迈", "chiang mai")),
            AirportPlace("CEI", "清莱", ("清莱",)),
        ),
    ),
    CountryPlace(
        "VN",
        "越南",
        ("vietnam", "vn"),
        (
            AirportPlace("SGN", "胡志明市", ("胡志明", "saigon", "霍志明")),
            AirportPlace("HAN", "河内", ("河内", "hanoi")),
            AirportPlace("DAD", "岘港", ("岘港", "danang")),
            AirportPlace("CXR", "芽庄", ("芽庄", "nha trang")),
        ),
    ),
    CountryPlace(
        "KH",
        "柬埔寨",
        ("cambodia", "kh", "柬", "高棉"),
        (
            AirportPlace("PNH", "金边", ("金边", "phnom penh")),
            AirportPlace("REP", "暹粒", ("暹粒", "siem reap", "吴哥")),
            AirportPlace("KOS", "西哈努克", ("西哈努克", "sihanoukville")),
        ),
    ),
    CountryPlace(
        "MY",
        "马来西亚",
        ("malaysia", "my", "大马"),
        (
            AirportPlace("KUL", "吉隆坡", ("吉隆坡", "kuala lumpur")),
            AirportPlace("PEN", "槟城", ("槟城", "penang")),
            AirportPlace("BKI", "亚庇", ("亚庇", "kota kinabalu", "沙巴")),
        ),
    ),
    CountryPlace(
        "SG",
        "新加坡",
        ("singapore", "sg", "狮城"),
        (AirportPlace("SIN", "新加坡", ("新加坡", "singapore")),),
    ),
    CountryPlace(
        "ID",
        "印度尼西亚",
        ("indonesia", "id", "印尼"),
        (
            AirportPlace("JKT", "雅加达", ("雅加达", "jakarta", "cgk")),
            AirportPlace("DPS", "巴厘岛", ("巴厘", "bali", "denpasar")),
        ),
    ),
    CountryPlace(
        "PH",
        "菲律宾",
        ("philippines", "ph", "菲"),
        (
            AirportPlace("MNL", "马尼拉", ("马尼拉", "manila")),
            AirportPlace("CEB", "宿务", ("宿务", "cebu")),
        ),
    ),
    CountryPlace(
        "LA",
        "老挝",
        ("laos", "la", "寮国"),
        (
            AirportPlace("VTE", "万象", ("万象", "vientiane")),
            AirportPlace("LPQ", "琅勃拉邦", ("琅勃拉邦", "luang prabang")),
        ),
    ),
    CountryPlace(
        "MM",
        "缅甸",
        ("myanmar", "burma", "mm", "缅"),
        (AirportPlace("RGN", "仰光", ("仰光", "yangon")),),
    ),
    CountryPlace(
        "TW",
        "中国台湾",
        ("taiwan", "tw", "台湾"),
        (
            AirportPlace("TPE", "台北", ("台北", "taipei")),
            AirportPlace("KHH", "高雄", ("高雄", "kaohsiung")),
        ),
    ),
    CountryPlace(
        "AU",
        "澳大利亚",
        ("australia", "au", "澳洲"),
        (
            AirportPlace("SYD", "悉尼", ("悉尼", "sydney")),
            AirportPlace("MEL", "墨尔本", ("墨尔本", "melbourne")),
            AirportPlace("BNE", "布里斯班", ("布里斯班", "brisbane")),
        ),
    ),
    CountryPlace(
        "NZ",
        "新西兰",
        ("new zealand", "nz"),
        (
            AirportPlace("AKL", "奥克兰", ("奥克兰", "auckland")),
            AirportPlace("CHC", "基督城", ("基督城", "christchurch")),
        ),
    ),
    CountryPlace(
        "AE",
        "阿联酋",
        ("uae", "ae", "迪拜酋长国"),
        (
            AirportPlace("DXB", "迪拜", ("迪拜", "dubai")),
            AirportPlace("AUH", "阿布扎比", ("阿布扎比", "abu dhabi")),
        ),
    ),
)


def _cn_all_airports() -> tuple[AirportPlace, ...]:
    return tuple(
        AirportPlace(
            str(row["code"]),
            str(row["name_zh"]),
            tuple(row["aliases"]),
        )
        for row in load_cn_cities()
    )


def _cn_by_code() -> dict[str, AirportPlace]:
    return {a.code: a for a in _cn_all_airports()}


# 港澳（TP 记为 HK/MO，不在 CN 城市库）+ 大陆全量可飞城市
HK_MO: tuple[AirportPlace, ...] = (
    AirportPlace("HKG", "香港", ("香港", "hongkong", "hk")),
    AirportPlace("MFM", "澳门", ("澳门", "macau", "macao")),
)


def _china_major_airports() -> tuple[AirportPlace, ...]:
    by = _cn_by_code()
    out: list[AirportPlace] = []
    for code in CN_MAJOR_HUB_CODES:
        a = by.get(code)
        if a:
            out.append(a)
    return tuple(out)


CHINA = CountryPlace(
    "CN",
    "中国大陆",
    ("china", "cn", "内地", "大陆", "中国"),
    _china_major_airports(),
)


@dataclass
class PlaceSuggestion:
    kind: str  # country | airport
    id: str
    label: str
    subtitle: str
    display: str
    codes: list[str]


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _match_text(q: str, *parts: str) -> bool:
    if not q:
        return True
    return any(q in _norm(p) for p in parts if p)


def list_suggestions(query: str = "", limit: int = 20) -> list[PlaceSuggestion]:
    q = _norm(query)
    items: list[PlaceSuggestion] = []

    countries = (CHINA,) + COUNTRIES
    for c in countries:
        country_hit = bool(q) and _match_text(q, c.name_zh, c.iso2, *c.aliases)
        codes = [a.code for a in c.airports]
        # 空查询：只展示国家级建议，避免一次塞入数百机场
        if country_hit or not q:
            items.append(
                PlaceSuggestion(
                    kind="country",
                    id=c.iso2,
                    label=c.name_zh,
                    subtitle=f"不限机场 · {len(codes)} 个主要机场",
                    display=f"{c.name_zh}（不限机场）",
                    codes=codes,
                )
            )
        # 国家命中时附带该国「主要」机场；具体城市靠下文全量检索
        if country_hit:
            for a in c.airports:
                items.append(
                    PlaceSuggestion(
                        kind="airport",
                        id=a.code,
                        label=a.name_zh,
                        subtitle=f"{a.code} · {c.name_zh}",
                        display=a.name_zh,
                        codes=[a.code],
                    )
                )
        elif q:
            for a in c.airports:
                if _match_text(q, a.name_zh, a.code, *a.aliases):
                    items.append(
                        PlaceSuggestion(
                            kind="airport",
                            id=a.code,
                            label=a.name_zh,
                            subtitle=f"{a.code} · {c.name_zh}",
                            display=a.name_zh,
                            codes=[a.code],
                        )
                    )

    # 中国大陆全量可飞城市（含港澳）
    if q:
        for a in HK_MO:
            if _match_text(q, a.name_zh, a.code, *a.aliases):
                items.append(
                    PlaceSuggestion(
                        kind="airport",
                        id=a.code,
                        label=a.name_zh,
                        subtitle=f"{a.code} · 中国",
                        display=a.name_zh,
                        codes=[a.code],
                    )
                )
        for a in _cn_all_airports():
            if _match_text(q, a.name_zh, a.code, *a.aliases):
                items.append(
                    PlaceSuggestion(
                        kind="airport",
                        id=a.code,
                        label=a.name_zh,
                        subtitle=f"{a.code} · 中国大陆",
                        display=a.name_zh,
                        codes=[a.code],
                    )
                )

    # 去重：同 id+kind 只留一条
    seen: set[tuple[str, str]] = set()
    uniq: list[PlaceSuggestion] = []
    for it in items:
        key = (it.kind, it.id)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    uniq.sort(key=lambda x: (0 if x.kind == "country" else 1, x.label))
    if q:
        def score(it: PlaceSuggestion) -> tuple[int, str]:
            lab = _norm(it.label)
            if lab.startswith(q) or q == _norm(it.id):
                return (0, it.label)
            if q in lab:
                return (1, it.label)
            return (2, it.label)

        uniq.sort(key=score)

    return uniq[: max(1, min(50, limit))]


def expand_codes(codes_csv: str | None, label: str | None = None) -> list[str]:
    """解析任务存储的 codes，或从中文标签扩展为国家全部机场。"""
    raw = (codes_csv or "").strip()
    if raw:
        out: list[str] = []
        for part in raw.split(","):
            p = part.strip()
            if not p:
                continue
            code = resolve_airport(p)
            if code and code not in out:
                out.append(code)
        if out:
            return out

    label_s = (label or "").strip()
    if not label_s:
        return []

    # 「柬埔寨（不限机场）」→ 国家
    base = label_s.replace("（不限机场）", "").replace("(不限机场)", "").strip()
    q = _norm(base)
    for c in (CHINA,) + COUNTRIES:
        names = {_norm(c.name_zh), _norm(c.iso2), *(_norm(a) for a in c.aliases)}
        if q in names or base == c.name_zh:
            return [a.code for a in c.airports]

    # 海外目录城市
    for c in (CHINA,) + COUNTRIES:
        for a in c.airports:
            names = {_norm(a.name_zh), _norm(a.code), *(_norm(x) for x in a.aliases)}
            if q in names:
                return [a.code]

    # 港澳 + 中国大陆全量
    for a in HK_MO + _cn_all_airports():
        names = {_norm(a.name_zh), _norm(a.code), *(_norm(x) for x in a.aliases)}
        if q in names:
            return [a.code]

    return [resolve_airport(label_s)]


def normalize_place(label: str, codes_csv: str | None = None) -> tuple[str, str]:
    """返回 (展示名, 逗号分隔 IATA)。"""
    codes = expand_codes(codes_csv, label)
    if not codes:
        raise ValueError(f"无法识别地点: {label or codes_csv}")
    codes_s = ",".join(codes)
    lab = (label or "").strip()
    if not lab:
        if len(codes) == 1:
            for c in (CHINA,) + COUNTRIES:
                for a in c.airports:
                    if a.code == codes[0]:
                        return a.name_zh, codes_s
            for a in HK_MO + _cn_all_airports():
                if a.code == codes[0]:
                    return a.name_zh, codes_s
            return codes[0], codes_s
        return codes_s, codes_s
    return lab, codes_s
