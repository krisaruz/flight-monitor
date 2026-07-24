#!/usr/bin/env python3
"""从 Travelpayouts 公开城市库刷新中国大陆可飞城市目录。

用法（仓库根目录）:
  python scripts/refresh_cn_cities.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "app" / "data" / "cn_cities.json"

# Travelpayouts zh-CN 偶发缺中文名的可飞城市补全
ZH_OVERRIDES = {
    "ZHY": "中卫",
    "TCZ": "腾冲",
    "JXA": "鸡西",
    "GYU": "固原",
    "LDS": "伊春",
    "YIE": "阿尔山",
    "HDG": "邯郸",
    "OHE": "漠河",
    "KGT": "康定",
    "BPL": "博乐",
    "DQA": "大庆",
    "LFQ": "临汾",
    "YZY": "张掖",
    "ENY": "延安",
    "JIC": "金昌",
    "KJI": "布尔津",
    "WNH": "文山",
    "TVS": "唐山",
    "YUS": "玉树",
    "JUH": "池州",
    "NLT": "新源",
    "ERL": "二连浩特",
    "JIQ": "黔江",
    "FYJ": "抚远",
    "RLK": "巴彦淖尔",
    "NGQ": "狮泉河",
    "JGD": "加格达奇",
    "LLB": "荔波",
    "YTY": "扬州",
    "BFJ": "毕节",
    "ZQZ": "张家口",
    "AEB": "百色",
    "TLQ": "吐鲁番",
    "NBS": "白山",
    "HIA": "淮安",
    "RIZ": "日照",
    "DCY": "稻城",
    "RKZ": "日喀则",
}


def _fetch(url: str) -> list[dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    zh = {c["code"]: c for c in _fetch("https://api.travelpayouts.com/data/zh-CN/cities.json")}
    en_list = _fetch("https://api.travelpayouts.com/data/en/cities.json")

    rows: list[dict] = []
    skipped: list[str] = []
    for c in en_list:
        if c.get("country_code") != "CN" or not c.get("has_flightable_airport"):
            continue
        code = (c.get("code") or "").strip().upper()
        if len(code) != 3:
            continue
        z = zh.get(code) or {}
        name_zh = (z.get("name") or "").strip() or ZH_OVERRIDES.get(code, "")
        name_en = (c.get("name") or "").strip().replace("\u202a", "").replace("\u202c", "")
        if not name_zh:
            skipped.append(f"{code}:{name_en}")
            continue
        aliases: list[str] = []
        if name_en and name_en.lower() != name_zh.lower():
            aliases.append(name_en)
        rows.append({"code": code, "name_zh": name_zh, "aliases": aliases})

    rows.sort(key=lambda x: (x["name_zh"], x["code"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "Travelpayouts data/en/cities.json + data/zh-CN/cities.json",
        "country_code": "CN",
        "filter": "has_flightable_airport=true",
        "count": len(rows),
        "cities": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} count={len(rows)}")
    if skipped:
        print("skipped (no zh name):", ", ".join(skipped))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
