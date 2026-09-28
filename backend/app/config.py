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

    # —— 日历扫价主逻辑 ——
    # 每方向 Google 单程补洞上限（先均匀，再低价邻域 ±1）
    calendar_google_day_max: int = 16
    # 匹配后最多送核验的候选数（且 ≥ top_n）
    calendar_candidate_k: int = 20
    verify_top_k: int = 20
    ctrip_verify_timeout_ms: int = 45000
    ctrip_verify_delay_sec: float = 1.5
    # OTA 核验是否无头；false 时弹出本机 Chrome/Chromium，部分环境可降低 WhaleGuard 命中
    ota_verify_headless: bool = True
    # 健康检查提示用；扫价主路径：日历匹配 → Google full Top-N（可选 ctrip_h5 精修）
    ota_verify_providers: str = "google,ctrip_h5"
    # Playwright 出站代理，例：socks5://127.0.0.1:40000
    ota_proxy: str = ""
    # 携程 H5 真人登录 Cookie（cticket）。勿提交 git。
    ota_ctrip_cticket: str = ""
    # 兼容旧旋钮：主路径不再密核池化，默认与 candidate 对齐
    google_verify_max: int = 20
    google_verify_target_ok: int = 10
    google_verify_delay_sec: float = 0.2
    # Google 单源阶段并行浏览器数（1=串行；2C/2G 机器建议 ≤2；每路独立 Chrome）
    google_verify_workers: int = 2
    # Top-N 核验默认 full（价+班次）
    google_verify_mode: str = "full"
    # 旧字段保留：日历模型下 detail/neighbor 默认关闭
    google_detail_top: int = 0
    google_neighbor_extra: int = 0
    # 可选：对核验头部用携程 H5 回填班次；默认 0 不烧 Cookie
    ctrip_h5_refine_top: int = 0
    ctrip_h5_verify_delay_sec: float = 2.5
    # 整次核验墙钟预算（秒），超时则收工进入下一阶段/结束
    verify_budget_sec: int = 720
    # 仅测试夹具可设 true；生产扫价一律拒绝
    use_demo: bool = False

    max_concurrent_scans: int = 2
    default_scan_interval_hours: int = 6

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 公开模式：免登录（guest Cookie），关闭 admin/登录墙，适合博客外链
    public_mode: bool = False
    guest_cookie_name: str = "gatefare_guest"
    guest_cookie_max_age_days: int = 30
    # 公开模式下每 IP 每小时允许的扫价次数（创建任务 + 立即扫描合计）
    public_scan_limit_per_hour: int = 5
    # 公开扫价 IP 白名单（逗号分隔）；命中后不受每小时次数限制。本机 loopback 始终放行。
    public_scan_ip_whitelist: str = ""


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
    day_max = int(getattr(settings, "calendar_google_day_max", 16) or 16)
    cand_k = int(getattr(settings, "calendar_candidate_k", 20) or 20)
    return (
        "混合管线：去/回程按天最低价日历（TP+Google单程补洞"
        f"≤{day_max}/向）→ 双边有价匹配（国内空池均匀骨架直核验）→ 核验 Top≤{cand_k}"
        "（国内 Google→携程H5 回落；国际 Google）"
        f"；预算 {settings.verify_budget_sec}s。"
    )
