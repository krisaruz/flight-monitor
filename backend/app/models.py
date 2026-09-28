from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    feishu_webhook: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tasks: Mapped[list[WatchTask]] = relationship(back_populates="user", cascade="all, delete-orphan")


class WatchTask(Base):
    __tablename__ = "watch_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(64))
    dest: Mapped[str] = mapped_column(String(64))
    # 逗号分隔 IATA；多机场 = 国家「不限机场」
    origin_codes: Mapped[str] = mapped_column(String(128), default="")
    dest_codes: Mapped[str] = mapped_column(String(128), default="")
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    stay_min: Mapped[int] = mapped_column(Integer)
    stay_max: Mapped[int] = mapped_column(Integer)
    adults: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    cabin: Mapped[str] = mapped_column(String(32), default="")
    top_n: Mapped[int] = mapped_column(Integer, default=10)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=6)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    best_price_seen: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_notified_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="tasks")
    runs: Mapped[list[ScanRun]] = relationship(back_populates="task", cascade="all, delete-orphan")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("watch_tasks.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | schedule
    # pending | running | done | partial | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # discovering | verifying | ""
    phase: Mapped[str] = mapped_column(String(32), default="")
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str] = mapped_column(String(512), default="")
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    notify_message: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped[WatchTask] = relationship(back_populates="runs")
    results: Mapped[list[FlightResult]] = relationship(back_populates="run", cascade="all, delete-orphan")


class FlightResult(Base):
    __tablename__ = "flight_results"
    __table_args__ = (UniqueConstraint("run_id", "rank", name="uq_run_rank"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    outbound_date: Mapped[str] = mapped_column(String(10))
    return_date: Mapped[str] = mapped_column(String(10))
    trip_days: Mapped[int] = mapped_column(Integer)
    # 展示/排序用：核验成功时为核验价
    total_price: Mapped[float] = mapped_column(Float)
    cache_price: Mapped[float] = mapped_column(Float, default=0.0)
    verified_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # pending | ok | failed
    verify_status: Mapped[str] = mapped_column(String(16), default="")
    currency: Mapped[str] = mapped_column(String(8))
    outbound_summary: Mapped[str] = mapped_column(String(255), default="")
    return_summary: Mapped[str] = mapped_column(String(255), default="")
    booking_class: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(32), default="")
    verify_url: Mapped[str] = mapped_column(String(1024), default="")
    verify_url_ctrip: Mapped[str] = mapped_column(String(1024), default="")
    verify_url_qunar: Mapped[str] = mapped_column(String(1024), default="")

    run: Mapped[ScanRun] = relationship(back_populates="results")
