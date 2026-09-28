from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import settings

# IP -> 最近请求时间戳（秒）
_hits: dict[str, deque[float]] = defaultdict(deque)

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _whitelist_ips() -> set[str]:
    raw = (settings.public_scan_ip_whitelist or "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_scan_whitelisted(ip: str) -> bool:
    """本机 loopback 与配置白名单 IP 不计入公开扫价限流。"""
    if not ip or ip == "unknown":
        return False
    if ip in _LOOPBACK:
        return True
    return ip in _whitelist_ips()


def check_public_scan_limit(request: Request) -> None:
    """公开模式下限制扫价频率，防止滥用 Playwright。"""
    if not settings.public_mode:
        return
    ip = client_ip(request)
    if is_scan_whitelisted(ip):
        return
    limit = max(1, settings.public_scan_limit_per_hour)
    window = 3600.0
    now = time.time()
    q = _hits[ip]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"扫价过于频繁，请约 1 小时后再试（每 IP 每小时最多 {limit} 次）",
        )
    q.append(now)
