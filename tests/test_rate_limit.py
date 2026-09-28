from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import settings
from app import rate_limit


def _req(ip: str, *, forwarded: str | None = None) -> MagicMock:
    req = MagicMock()
    req.headers = {}
    if forwarded:
        req.headers["x-forwarded-for"] = forwarded
    req.client = MagicMock()
    req.client.host = ip
    return req


@pytest.fixture(autouse=True)
def _reset_hits(monkeypatch: pytest.MonkeyPatch):
    rate_limit._hits.clear()
    monkeypatch.setattr(settings, "public_mode", True)
    monkeypatch.setattr(settings, "public_scan_limit_per_hour", 2)
    monkeypatch.setattr(settings, "public_scan_ip_whitelist", "")
    yield
    rate_limit._hits.clear()


def test_loopback_always_whitelisted() -> None:
    assert rate_limit.is_scan_whitelisted("127.0.0.1")
    assert rate_limit.is_scan_whitelisted("::1")
    for _ in range(5):
        rate_limit.check_public_scan_limit(_req("127.0.0.1"))


def test_configured_whitelist_bypasses_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_scan_ip_whitelist", "156.232.29.85, 10.0.0.1")
    assert rate_limit.is_scan_whitelisted("156.232.29.85")
    for _ in range(10):
        rate_limit.check_public_scan_limit(_req("156.232.29.85"))


def test_non_whitelist_hits_limit() -> None:
    req = _req("203.0.113.9")
    rate_limit.check_public_scan_limit(req)
    rate_limit.check_public_scan_limit(req)
    with pytest.raises(HTTPException) as ei:
        rate_limit.check_public_scan_limit(req)
    assert ei.value.status_code == 429


def test_x_forwarded_for_uses_first_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_scan_ip_whitelist", "198.51.100.7")
    req = _req("10.0.0.1", forwarded="198.51.100.7, 10.0.0.1")
    assert rate_limit.client_ip(req) == "198.51.100.7"
    for _ in range(5):
        rate_limit.check_public_scan_limit(req)
