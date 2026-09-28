from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import hash_password
from app.config import DATA_DIR, health_hint, pipeline_ready, settings, travelpayouts_configured
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers import admin, auth, me, places, tasks
from app.services.ctrip_verify import playwright_available
from app.services.flight_search import load_env
from app.services.scanner import ensure_schema
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger(__name__)


def bootstrap_admin() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin_user = User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
                is_active=True,
            )
            db.add(admin_user)
            db.commit()
            _log.info("已创建初始管理员账号: %s", settings.admin_username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_env()
    bootstrap_admin()
    ensure_schema()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Flight Monitor", version="2.0.0", lifespan=lifespan)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not settings.public_mode:
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(admin.router)
app.include_router(places.router)
app.include_router(tasks.router)


@app.get("/api/health")
def health() -> dict:
    tp_ok = travelpayouts_configured()
    pw_ok = playwright_available()
    ready = pipeline_ready() and pw_ok
    return {
        "ok": ready,
        "provider": "hybrid" if ready else "unconfigured",
        "pipeline": "travelpayouts+ctrip",
        "demo": settings.use_demo,
        "public_mode": settings.public_mode,
        "travelpayouts_configured": tp_ok,
        "travelpayouts_ok": tp_ok and not settings.use_demo,
        "playwright_ok": pw_ok,
        "hint": health_hint()
        if ready
        else (
            health_hint()
            if not tp_ok or settings.use_demo
            else "已配置 token，但未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        ),
    }


# 生产构建的前端静态文件（可选）
_STATIC = Path(__file__).resolve().parents[1] / "static"
if _STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        candidate = _STATIC / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC / "index.html")
