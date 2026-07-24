"""Fail-closed 配置与健康检查（无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import (
    pipeline_ready,
    require_pipeline_ready,
    resolve_pipeline,
    settings,
)


def test_require_pipeline_ready_without_token() -> None:
    with patch.object(settings, "travelpayouts_token", ""), patch.object(settings, "use_demo", False):
        assert pipeline_ready() is False
        with pytest.raises(RuntimeError, match="TRAVELPAYOUTS_TOKEN"):
            require_pipeline_ready()


def test_use_demo_blocks_production_scan() -> None:
    with patch.object(settings, "travelpayouts_token", "tok"), patch.object(settings, "use_demo", True):
        assert pipeline_ready() is False
        with pytest.raises(RuntimeError, match="USE_DEMO"):
            require_pipeline_ready()


def test_resolve_pipeline_hybrid_when_ready() -> None:
    with patch.object(settings, "travelpayouts_token", "tok"), patch.object(settings, "use_demo", False):
        assert resolve_pipeline() == "hybrid"


def test_no_links_silent_fallback() -> None:
    """无 token 时不得伪装成可扫价的 links/demo。"""
    with patch.object(settings, "travelpayouts_token", ""), patch.object(settings, "use_demo", False):
        with pytest.raises(RuntimeError):
            resolve_pipeline()
