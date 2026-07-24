from __future__ import annotations

"""中国大陆可飞城市目录（Travelpayouts 城市码，本地 JSON）。"""

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data" / "cn_cities.json"

# 「中国大陆（不限机场）」只扩主要枢纽，避免数百 OD 组合拖垮扫价
CN_MAJOR_HUB_CODES: tuple[str, ...] = (
    "BJS",
    "SHA",
    "CAN",
    "SZX",
    "CTU",
    "CKG",
    "HGH",
    "NKG",
    "SIA",  # 西安（Travelpayouts 城市码；机场码 XIY 见别名）
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


def cn_city_alias_map() -> dict[str, str]:
    """中文名 / 英文别名 / 小写三字码 → 城市码。"""
    m: dict[str, str] = {}
    for row in load_cn_cities():
        code = str(row["code"])
        m[code.lower()] = code
        m[str(row["name_zh"]).strip().lower()] = code
        for a in row["aliases"]:
            m[str(a).strip().lower()] = code
    # 常见机场码 → 城市码
    m.update(
        {
            "pek": "BJS",
            "pkx": "BJS",
            "pvg": "SHA",
            "sha": "SHA",
            "tfu": "CTU",
            "ctu": "CTU",
            "xiy": "SIA",
            "sia": "SIA",
        }
    )
    return m
