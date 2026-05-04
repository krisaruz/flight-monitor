"""
飞书机票监控机器人 (WebSocket 长连接版)
=========================================
基于飞书 lark-oapi SDK 的 WebSocket 长连接模式，
无需公网 IP、无需服务器，本地运行即可。

配置步骤:
  1. 去 https://open.feishu.cn/app 创建企业自建应用
  2. 添加「机器人」能力
  3. 权限管理 → 添加: im:message, im:message:send_as_bot
  4. 事件与回调 → 事件配置 → 订阅方式选「使用长连接接收事件」
  5. 添加事件: im.message.receive_v1
  6. 发布版本并审批

运行:
  set FEISHU_APP_ID=cli_xxxx
  set FEISHU_APP_SECRET=xxxx
  python feishu_flight_bot.py

  # 或直接传参:
  python feishu_flight_bot.py --app-id cli_xxxx --app-secret xxxx

  # 演示模式 (不需要 Amadeus API 密钥):
  python feishu_flight_bot.py --demo

然后在飞书群中 @机器人:
  搜机票 香港到大阪 4月20到6月30 5天
  帮助
"""

import argparse
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    ReplyMessageRequest, ReplyMessageRequestBody,
)

from flight_monitor import (
    AIRPORT_ALIASES,
    AmadeusFlightSearch,
    FlightOption,
    count_departure_dates,
    generate_demo_data,
    load_env,
    resolve_airport,
    scan_dates,
)

# ─── 日志 ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("flight-bot")


# ─── 全局配置 ─────────────────────────────────────────────────────────────────


@dataclass
class BotConfig:
    app_id: str = ""
    app_secret: str = ""
    use_demo: bool = False
    amadeus_production: bool = False
    max_results_per_date: int = 3
    currency: str = "CNY"
    lark_client: Optional[lark.Client] = None


CFG = BotConfig()


# ─── IATA 名称映射 ───────────────────────────────────────────────────────────

IATA_TO_NAME = {}
for k, v in AIRPORT_ALIASES.items():
    if len(k) > 1 and not k.isascii():
        if v not in IATA_TO_NAME:
            IATA_TO_NAME[v] = k

def _code_to_name(code: str) -> str:
    return IATA_TO_NAME.get(code, code)


# ─── 消息卡片构建 ────────────────────────────────────────────────────────────

def build_flight_card(
    results: list[FlightOption],
    origin: str, dest: str,
    start_date: str, end_date: str,
    trip_days: int, top_n: int = 15,
) -> dict:
    sorted_results = sorted(results, key=lambda x: x.total_price)
    display = sorted_results[:top_n]

    lines = []
    for i, opt in enumerate(display, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"**{i}.**")
        price_text = f"**{opt.currency} {opt.total_price:,.0f}**"
        lines.append(
            f"{medal} {opt.outbound_date} ~ {opt.return_date} ({opt.trip_days}天)\n"
            f"      去: {opt.outbound_summary}\n"
            f"      回: {opt.return_summary}\n"
            f"      💰 {price_text}"
        )

    body_text = "\n\n".join(lines) if lines else "未找到符合条件的航班"
    origin_name, dest_name = _code_to_name(origin), _code_to_name(dest)

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"✈️ {origin_name} → {dest_name} 机票监控"},
            "template": "green" if display else "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"**航线**: {origin}({origin_name}) → {dest}({dest_name})\n"
                f"**日期范围**: {start_date} ~ {end_date}\n"
                f"**行程天数**: {trip_days} 天\n"
                f"**搜索结果**: 共 {len(sorted_results)} 个组合，显示最便宜 {len(display)} 个"
            )}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": body_text}},
        ],
    }

    if display:
        cheapest = display[0]
        card["elements"].extend([
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "lark_md", "content": (
                f"💡 最低价 **{cheapest.currency} {cheapest.total_price:,.0f}** "
                f"| 出发 {cheapest.outbound_date} 返回 {cheapest.return_date}"
            )}]},
        ])

    return card


def build_help_card() -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✈️ 机票监控机器人 - 使用帮助"},
            "template": "blue",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": (
            "**发送以下格式即可搜索机票:**\n\n"
            "📝 `搜机票 香港到大阪 4月20到6月30 5天`\n"
            "📝 `查航班 HKG-KIX 2026-05-01~2026-06-30 往返7天`\n"
            "📝 `搜机票 深圳到东京 5月 3天`\n"
            "📝 `搜 香港到曼谷 五一 4天`\n\n"
            "**支持的城市:**\n"
            "香港(HKG) 大阪(KIX) 东京(NRT) 首尔(ICN)\n"
            "曼谷(BKK) 新加坡(SIN) 台北(TPE) 上海(PVG)\n"
            "北京(PEK) 广州(CAN) 深圳(SZX) 名古屋(NGO)\n"
            "福冈(FUK) 札幌(CTS) 冲绳(OKA)\n\n"
            "发送 **帮助** 查看此信息"
        )}}],
    }


def build_searching_card(params: dict) -> dict:
    origin_name = _code_to_name(params["origin"])
    dest_name = _code_to_name(params["dest"])
    try:
        sd = datetime.strptime(params["start"], "%Y-%m-%d")
        ed = datetime.strptime(params["end"], "%Y-%m-%d")
        n_days = count_departure_dates(sd, ed, params["days"])
    except (ValueError, KeyError):
        n_days = 0
    # 粗略估计：每出发日约 0.15s 间隔 + 网络与 API
    low = max(8, int(n_days * 0.35)) if n_days else 10
    high = max(low + 5, int(n_days * 1.4)) if n_days else 30
    high = min(high, 900)
    eta_line = (
        f"需查询约 **{n_days}** 个出发日，预计 **{low}~{high}** 秒"
        if n_days
        else "正在扫描日期组合，请稍候…"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⏳ 正在搜索中..."},
            "template": "orange",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": (
            f"**{origin_name} → {dest_name}**\n"
            f"日期: {params['start']} ~ {params['end']} | 行程: {params['days']}天\n\n"
            f"{eta_line}"
        )}}],
    }


def build_error_card(msg: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "⚠️ 搜索出错"}, "template": "red"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": msg}}],
    }


# ─── 命令解析 ────────────────────────────────────────────────────────────────

def parse_flight_command(text: str) -> Optional[dict]:
    """解析自然语言航班搜索命令"""
    text = text.strip()

    trigger_patterns = [r"^(?:搜机票|查机票|搜航班|查航班|搜|查|search|find)\s+"]
    matched = False
    for pat in trigger_patterns:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            text = text[m.end():].strip()
            matched = True
            break
    if not matched:
        return None

    result = {"origin": None, "dest": None, "start": None, "end": None, "days": 5}

    route_patterns = [
        r"([\u4e00-\u9fff]{2,4}|[A-Za-z]{3})\s*[-→到]\s*([\u4e00-\u9fff]{2,4}|[A-Za-z]{3})",
    ]
    for pat in route_patterns:
        m = re.search(pat, text)
        if m:
            result["origin"] = resolve_airport(m.group(1))
            result["dest"] = resolve_airport(m.group(2))
            text = text[:m.start()] + text[m.end():]
            break
    if not result["origin"] or not result["dest"]:
        return None

    for pat in [r"(?:往返|行程)?\s*(\d+)\s*天", r"(\d+)\s*(?:日|nights?|days?)"]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            result["days"] = int(m.group(1))
            text = text[:m.start()] + text[m.end():]
            break

    now = datetime.now()
    year = now.year

    holiday_map = {
        "五一": (f"{year}-04-28", f"{year}-05-10"),
        "劳动节": (f"{year}-04-28", f"{year}-05-10"),
        "国庆": (f"{year}-09-25", f"{year}-10-12"),
        "十一": (f"{year}-09-25", f"{year}-10-12"),
        "春节": (f"{year}-01-20", f"{year}-02-15"),
        "端午": (f"{year}-05-25", f"{year}-06-10"),
        "中秋": (f"{year}-09-10", f"{year}-09-25"),
        "暑假": (f"{year}-07-01", f"{year}-08-31"),
        "寒假": (f"{year}-01-15", f"{year}-02-20"),
    }
    for name, (hs, he) in holiday_map.items():
        if name in text:
            result["start"], result["end"] = hs, he
            text = text.replace(name, "")
            break

    if not result["start"]:
        for pat in [
            r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*[~到至-]\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
            r"(\d{1,2})[/月](\d{1,2})[日号]?\s*[~到至-]\s*(\d{1,2})[/月](\d{1,2})[日号]?",
        ]:
            m = re.search(pat, text)
            if m:
                g = m.groups()
                if len(g) == 6:
                    result["start"] = f"{g[0]}-{int(g[1]):02d}-{int(g[2]):02d}"
                    result["end"] = f"{g[3]}-{int(g[4]):02d}-{int(g[5]):02d}"
                elif len(g) == 4:
                    result["start"] = f"{year}-{int(g[0]):02d}-{int(g[1]):02d}"
                    result["end"] = f"{year}-{int(g[2]):02d}-{int(g[3]):02d}"
                break

    if not result["start"]:
        m = re.search(r"(\d{1,2})月\s*[~到至-]\s*(\d{1,2})月", text)
        if m:
            import calendar
            m1, m2 = int(m.group(1)), int(m.group(2))
            result["start"] = f"{year}-{m1:02d}-01"
            result["end"] = f"{year}-{m2:02d}-{calendar.monthrange(year, m2)[1]:02d}"

    if not result["start"]:
        m = re.search(r"(\d{1,2})月", text)
        if m:
            import calendar
            mo = int(m.group(1))
            result["start"] = f"{year}-{mo:02d}-01"
            result["end"] = f"{year}-{mo:02d}-{calendar.monthrange(year, mo)[1]:02d}"

    if not result["start"]:
        result["start"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        result["end"] = (now + timedelta(days=61)).strftime("%Y-%m-%d")

    return result


# ─── Webhook 推送 ────────────────────────────────────────────────────────────

def push_to_feishu_webhook(webhook_url: str, card: dict):
    """通过群机器人 Webhook 推送消息卡片到飞书群"""
    import urllib.request
    body = json.dumps({"msg_type": "interactive", "card": card}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
    ok = result.get("code") == 0 or result.get("StatusCode") == 0
    if ok:
        log.info("Webhook 推送成功")
    else:
        log.error(f"Webhook 推送失败: {result}")
    return result


# ─── 搜索执行 ────────────────────────────────────────────────────────────────

def run_search(
    params: dict,
    *,
    use_demo: Optional[bool] = None,
    amadeus_production: Optional[bool] = None,
    max_results_per_date: Optional[int] = None,
    currency: Optional[str] = None,
) -> list[FlightOption]:
    """执行扫描；可选参数覆盖全局 CFG（便于脚本/测试调用）。"""
    origin, dest = params["origin"], params["dest"]
    start_date = datetime.strptime(params["start"], "%Y-%m-%d")
    end_date = datetime.strptime(params["end"], "%Y-%m-%d")
    trip_days = params["days"]

    demo = CFG.use_demo if use_demo is None else use_demo
    prod = CFG.amadeus_production if amadeus_production is None else amadeus_production
    per_date = CFG.max_results_per_date if max_results_per_date is None else max_results_per_date
    curr = CFG.currency if currency is None else currency

    if demo:
        return generate_demo_data(origin, dest, start_date, end_date, trip_days)

    client_id = os.environ.get("AMADEUS_CLIENT_ID", "")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ValueError("未设置 AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET 环境变量")

    searcher = AmadeusFlightSearch(client_id, client_secret, production=prod)
    return scan_dates(
        searcher,
        origin,
        dest,
        start_date,
        end_date,
        trip_days,
        adults=1,
        currency=curr,
        cabin="",
        results_per_date=per_date,
        verbose=False,
    )


# ─── 飞书消息发送 ────────────────────────────────────────────────────────────

def send_card(chat_id: str, card: dict):
    """发送卡片消息到指定会话"""
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = CFG.lark_client.im.v1.message.create(request)
    if not resp.success():
        log.error(f"发送消息失败: code={resp.code}, msg={resp.msg}")
    return resp


def reply_card(message_id: str, card: dict):
    """回复卡片消息"""
    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = CFG.lark_client.im.v1.message.reply(request)
    if not resp.success():
        log.error(f"回复消息失败: code={resp.code}, msg={resp.msg}")
    return resp


def reply_text(message_id: str, text: str):
    """回复纯文本"""
    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    return CFG.lark_client.im.v1.message.reply(request)


# ─── 后台搜索任务 ────────────────────────────────────────────────────────────

def background_search(chat_id: str, message_id: str, chat_type: str, params: dict):
    """在后台线程执行搜索，完成后发送结果卡片"""
    try:
        log.info(f"开始搜索: {params['origin']}->{params['dest']} "
                 f"{params['start']}~{params['end']} {params['days']}天")
        results = run_search(params)
        log.info(f"搜索完成: 找到 {len(results)} 个组合")

        card = build_flight_card(
            results, params["origin"], params["dest"],
            params["start"], params["end"], params["days"],
        )
    except Exception as e:
        log.exception("搜索失败")
        card = build_error_card(f"搜索失败: {e}")

    send_card(chat_id, card)


# ─── 飞书事件处理 ────────────────────────────────────────────────────────────

def on_message_receive(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """处理接收到的消息事件"""
    try:
        msg = data.event.message
        msg_type = msg.message_type
        message_id = msg.message_id
        chat_id = msg.chat_id
        chat_type = msg.chat_type

        if msg_type != "text":
            reply_text(message_id, "请发送文本消息哦~ 输入「帮助」查看用法")
            return

        content = json.loads(msg.content)
        text = content.get("text", "").strip()

        if msg.mentions:
            for mention in msg.mentions:
                text = text.replace(f"@_{mention.key}", "").strip()

        text = re.sub(r"@\S+\s*", "", text).strip()
        log.info(f"收到消息 [{chat_type}]: {text}")

        if text in ("帮助", "help", "菜单", "menu", "?", "？", ""):
            if chat_type == "p2p":
                send_card(chat_id, build_help_card())
            else:
                reply_card(message_id, build_help_card())
            return

        params = parse_flight_command(text)
        if not params:
            if chat_type == "p2p":
                send_card(chat_id, build_help_card())
            else:
                reply_card(message_id, build_help_card())
            return

        # 先回复"搜索中"，搜索是耗时操作，避免3秒超时
        if chat_type == "p2p":
            send_card(chat_id, build_searching_card(params))
        else:
            reply_card(message_id, build_searching_card(params))

        # 后台线程执行搜索
        t = threading.Thread(
            target=background_search,
            args=(chat_id, message_id, chat_type, params),
            daemon=True,
        )
        t.start()

    except Exception:
        log.exception("处理消息时出错")


# ─── 启动入口 ────────────────────────────────────────────────────────────────

def start_bot(
    app_id: str,
    app_secret: str,
    use_demo: bool = False,
    amadeus_production: bool = False,
    max_results_per_date: int = 3,
    currency: str = "CNY",
    log_level: str = "INFO",
):
    CFG.app_id = app_id
    CFG.app_secret = app_secret
    CFG.use_demo = use_demo
    CFG.amadeus_production = amadeus_production
    CFG.max_results_per_date = max_results_per_date
    CFG.currency = currency

    CFG.lark_client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .build()
    )

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    lark_log = lark.LogLevel.DEBUG if log_level == "DEBUG" else lark.LogLevel.INFO

    _lvl_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING}
    _lvl = _lvl_map.get(log_level, logging.INFO)
    logging.getLogger("flight_monitor").setLevel(_lvl)

    ws_client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark_log,
    )

    data_src = "演示数据 (--demo)" if use_demo else (
        f"Amadeus API ({'生产' if amadeus_production else '测试'}环境)"
    )
    print(f"""
=========================================
  飞书机票监控机器人 (WebSocket 长连接)
=========================================

  App ID:  {app_id[:10]}...
  数据源:  {data_src}
  每日报价条数: {max_results_per_date}  货币: {currency}
  日志级别: {log_level}

  机器人已启动！在飞书中 @机器人 发送:
    搜机票 香港到大阪 4月20到6月30 5天
    帮助

  按 Ctrl+C 停止
""")

    ws_client.start()


def main():
    load_env()

    parser = argparse.ArgumentParser(
        description="飞书机票监控机器人 (WebSocket 长连接版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用步骤:
  1. 飞书开放平台 (open.feishu.cn) 创建应用，添加机器人能力
  2. 权限: im:message, im:message:send_as_bot
  3. 事件订阅 → 选「使用长连接接收事件」→ 添加 im.message.receive_v1
  4. 发布应用版本

  set FEISHU_APP_ID=cli_xxxx
  set FEISHU_APP_SECRET=xxxx
  python feishu_flight_bot.py --demo

测试命令解析:
  python feishu_flight_bot.py test "搜机票 香港到大阪 5月 5天"
        """,
    )

    sub = parser.add_subparsers(dest="command")

    # test 子命令
    test_p = sub.add_parser("test", help="测试命令解析 (不启动机器人)")
    test_p.add_argument("query", nargs="+", help="测试查询")

    # 主程序参数
    parser.add_argument("--app-id", default="", help="飞书 App ID")
    parser.add_argument("--app-secret", default="", help="飞书 App Secret")
    parser.add_argument("--demo", action="store_true", help="使用演示数据，不需要 Amadeus 密钥")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Amadeus 使用生产环境 api.amadeus.com（默认测试环境）",
    )
    parser.add_argument(
        "--max-per-date",
        type=int,
        default=3,
        metavar="N",
        help="每个出发日最多拉取几条报价 (默认: 3)",
    )
    parser.add_argument(
        "--currency",
        default=os.environ.get("AMADEUS_CURRENCY", "CNY"),
        help="报价货币代码 (默认: CNY，可由环境变量 AMADEUS_CURRENCY 覆盖)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])

    args = parser.parse_args()

    if args.command == "test":
        query = " ".join(args.query)
        print(f"\n  输入: {query}")
        result = parse_flight_command(query)
        if result:
            print(f"  解析结果:")
            print(f"    出发: {result['origin']} ({_code_to_name(result['origin'])})")
            print(f"    目的: {result['dest']} ({_code_to_name(result['dest'])})")
            print(f"    日期: {result['start']} ~ {result['end']}")
            print(f"    天数: {result['days']}")
        else:
            print("  [!] 无法解析，请检查格式")
        print()
        return

    app_id = args.app_id or os.environ.get("FEISHU_APP_ID", "")
    app_secret = args.app_secret or os.environ.get("FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        print("""
  [错误] 未配置飞书应用凭证!

  请设置环境变量:
    set FEISHU_APP_ID=cli_xxxx
    set FEISHU_APP_SECRET=xxxx

  或使用参数:
    python feishu_flight_bot.py --app-id cli_xxxx --app-secret xxxx

  如何获取: https://open.feishu.cn/app → 创建应用 → 凭证与基础信息
""")
        sys.exit(1)

    cid = os.environ.get("AMADEUS_CLIENT_ID", "").strip()
    csec = os.environ.get("AMADEUS_CLIENT_SECRET", "").strip()
    if args.demo:
        use_demo = True
    else:
        use_demo = not (bool(cid) and bool(csec))

    start_bot(
        app_id,
        app_secret,
        use_demo=use_demo,
        amadeus_production=args.production,
        max_results_per_date=max(1, args.max_per_date),
        currency=args.currency.strip().upper() or "CNY",
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
