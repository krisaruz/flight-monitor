from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)


def push_text_to_feishu_webhook(webhook_url: str, text: str) -> dict[str, Any]:
    """推送简单文本消息到飞书自定义机器人 Webhook。"""
    body = json.dumps(
        {"msg_type": "text", "content": {"text": text}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    ok = result.get("code") == 0 or result.get("StatusCode") == 0 or result.get("StatusMessage") == "success"
    if ok:
        _log.info("Webhook 推送成功")
    else:
        _log.error("Webhook 推送失败: %s", result)
    return result


def build_price_alert_text(
    *,
    origin: str,
    dest: str,
    start_date: str,
    end_date: str,
    stay_min: int,
    stay_max: int,
    min_price: float,
    currency: str,
    reason: str,
    outbound_date: str = "",
    return_date: str = "",
) -> str:
    trip = f"{outbound_date} ~ {return_date}" if outbound_date else "见 Web 详情"
    return (
        f"【机票盯价提醒】{reason}\n"
        f"航线: {origin} → {dest}\n"
        f"出发窗: {start_date} ~ {end_date}\n"
        f"停留: {stay_min}-{stay_max} 天\n"
        f"当前最低: {currency} {min_price:,.0f}\n"
        f"行程: {trip}\n"
        f"请打开 Flight Monitor Web 查看 Top N。"
    )
