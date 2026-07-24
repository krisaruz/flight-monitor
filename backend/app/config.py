from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → 项目根（含 backend/、data/）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

TOKEN_HINT = (
    "请在 https://www.travelpayouts.com/ 免费注册联盟账号，"
    "于 Programs → Aviasales → API 获取 TRAVELPAYOUTS_TOKEN 并写入 .env 后重启。"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jwt_secret: str = "change-me-in-production"
    jwt_expire_hours: int = 72
    database_url: str = f"sqlite:///{(DATA_DIR / 'flight_monitor.db').as_posix()}"

    admin_username: str = "admin"
    admin_password: str = "admin123"

    # 生产唯一管线：Travelpayouts 发现 + 携程核验。不再支持 links/demo 降级。
    travelpayouts_token: str = ""
    travelpayouts_market: str = "cn"
    travelpayouts_request_delay: float = 0.2

    verify_top_k: int = 20
    ctrip_verify_timeout_ms: int = 45000
    ctrip_verify_delay_sec: float = 1.5
    # 仅测试夹具可设 true；生产扫价一律拒绝
    use_demo: bool = False

    max_concurrent_scans: int = 2
    default_scan_interval_hours: int = 6

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()


def travelpayouts_configured() -> bool:
    return bool(settings.travelpayouts_token.strip())


def pipeline_ready() -> bool:
    """生产扫价是否可启动（fail-closed）。"""
    return travelpayouts_configured() and not settings.use_demo


def require_pipeline_ready() -> None:
    if settings.use_demo:
        raise RuntimeError(
            "USE_DEMO=true 已禁止生产扫价。请关闭 USE_DEMO，并配置 TRAVELPAYOUTS_TOKEN。"
        )
    if not travelpayouts_configured():
        raise RuntimeError(f"未配置 TRAVELPAYOUTS_TOKEN，无法扫价。{TOKEN_HINT}")


def resolve_pipeline() -> str:
    """返回生产管线名；未就绪则抛错，禁止静默降级。"""
    require_pipeline_ready()
    return "hybrid"


def health_hint() -> str:
    if settings.use_demo:
        return "USE_DEMO=true：生产扫价已禁用，请关闭后配置 TRAVELPAYOUTS_TOKEN。"
    if not travelpayouts_configured():
        return f"缺少 TRAVELPAYOUTS_TOKEN。{TOKEN_HINT}"
    return (
        "混合管线：Travelpayouts 缓存价扫窗排序 → Playwright 核验真价"
        "（优先携程，失败则 Google Flights）。推荐最低价以核验价为准。"
    )
