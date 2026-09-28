from __future__ import annotations

"""中国大陆可飞城市目录（Travelpayouts 城市码，本地 JSON）。"""

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data" / "cn_cities.json"

# 「中国大陆（不限机场）」只扩主要枢纽，避免数百 OD 组合拖垮扫价
# 主要枢纽：存 IATA 机场码（北京/西安用 PEK/XIY；上海保留 SHA=虹桥，TP 边界再映射）
CN_MAJOR_HUB_CODES: tuple[str, ...] = (
    "PEK",
    "SHA",
    "CAN",
    "SZX",
    "CTU",
    "CKG",
    "HGH",
    "NKG",
    "XIY",
    "KMG",
    "XMN",
    "WUH",
    "CSX",
    "TAO",
    "DLC",
    "TSN",
    "CGO",
    "URC",
    "SHE",
    "HRB",
    "SYX",
    "NNG",
    "KWL",
    "ZUH",
)


@lru_cache(maxsize=1)
def load_cn_cities() -> tuple[dict[str, str | tuple[str, ...]], ...]:
    """返回 ({code, name_zh, aliases}, ...)。"""
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    cities = raw.get("cities") or []
    out: list[dict[str, str | tuple[str, ...]]] = []
    for row in cities:
        code = str(row.get("code") or "").strip().upper()
        name_zh = str(row.get("name_zh") or "").strip()
        if not code or not name_zh:
            continue
        aliases = tuple(
            str(a).strip() for a in (row.get("aliases") or []) if str(a).strip()
        )
        out.append({"code": code, "name_zh": name_zh, "aliases": aliases})
    return tuple(out)


# JSON 内少数 TP 城市码 → 主机场
CN_JSON_CITY_TO_AIRPORT = {
    "BJS": "PEK",
    "SIA": "XIY",
}


def cn_city_alias_map() -> dict[str, str]:
    """中文名 / 英文别名 / 小写三字码 → 主机场码。"""
    m: dict[str, str] = {}
    for row in load_cn_cities():
        raw = str(row["code"]).strip().upper()
        code = CN_JSON_CITY_TO_AIRPORT.get(raw, raw)
        m[code.lower()] = code
        if raw != code:
            m[raw.lower()] = code
        m[str(row["name_zh"]).strip().lower()] = code
        for a in row["aliases"]:
            m[str(a).strip().lower()] = code
    # 机场码别名
    m.update(
        {
            "pek": "PEK",
            "pkx": "PEK",
            "bjs": "PEK",
            "pvg": "PVG",
            "sha": "SHA",
            "tfu": "TFU",
            "ctu": "CTU",
            "xiy": "XIY",
            "sia": "XIY",
        }
    )
    return m
