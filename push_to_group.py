"""一次性搜索并将结果卡片推送到飞书群（Webhook）。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from flight_monitor import load_env

from feishu_flight_bot import (
    build_flight_card,
    parse_flight_command,
    push_to_feishu_webhook,
    run_search,
)


def main() -> None:
    load_env()

    p = argparse.ArgumentParser(description="搜索机票并推送到飞书 Webhook")
    p.add_argument("--origin", default=os.environ.get("PUSH_ORIGIN", "香港"))
    p.add_argument("--dest", default=os.environ.get("PUSH_DEST", "大阪"))
    p.add_argument("--start", default=os.environ.get("PUSH_START", ""))
    p.add_argument("--end", default=os.environ.get("PUSH_END", ""))
    p.add_argument("--days", type=int, default=int(os.environ.get("PUSH_DAYS", "5")))
    p.add_argument("--top", type=int, default=int(os.environ.get("PUSH_TOP", "10")))
    p.add_argument("--demo", action="store_true", help="使用演示数据")
    p.add_argument(
        "--no-demo",
        action="store_true",
        help="使用 Amadeus 真实数据（需配置 AMADEUS_CLIENT_ID/SECRET）",
    )
    p.add_argument(
        "--production",
        action="store_true",
        help="Amadeus 生产环境",
    )
    args = p.parse_args()

    if args.demo and args.no_demo:
        print("不能同时指定 --demo 与 --no-demo", file=sys.stderr)
        sys.exit(2)
    if args.no_demo:
        use_demo = False
    elif args.demo:
        use_demo = True
    else:
        use_demo = os.environ.get("USE_DEMO", "1").lower() in ("1", "true", "yes", "")

    start, end = args.start, args.end
    if not start or not end:
        from datetime import datetime, timedelta

        now = datetime.now()
        start = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        end = (now + timedelta(days=61)).strftime("%Y-%m-%d")

    query = f"搜机票 {args.origin}到{args.dest} {start}~{end} {args.days}天"
    parsed = parse_flight_command(query)
    if not parsed:
        print("无法解析搜索条件，请检查城市名与日期。", file=sys.stderr)
        sys.exit(2)

    webhook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        print("未设置 FEISHU_WEBHOOK，请在 .env 或环境中配置。", file=sys.stderr)
        sys.exit(1)

    results = run_search(
        parsed,
        use_demo=use_demo,
        amadeus_production=args.production,
        max_results_per_date=3,
    )
    card = build_flight_card(
        results,
        parsed["origin"],
        parsed["dest"],
        parsed["start"],
        parsed["end"],
        parsed["days"],
        top_n=max(1, args.top),
    )
    push_to_feishu_webhook(webhook, card)


if __name__ == "__main__":
    main()
