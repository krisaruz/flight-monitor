from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    feishu_webhook: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    is_admin: bool = False


class MeUpdateIn(BaseModel):
    feishu_webhook: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)


class TaskCreateIn(BaseModel):
    origin: str
    dest: str
    origin_codes: str = ""
    dest_codes: str = ""
    start_date: str
    end_date: str
    stay_min: int = Field(ge=1, le=60)
    stay_max: int = Field(ge=1, le=60)
    adults: int = Field(default=1, ge=1, le=9)
    currency: str = "CNY"
    cabin: str = ""
    top_n: int = Field(default=10, ge=10, le=50)
    target_price: Optional[float] = Field(default=None, gt=0)
    interval_hours: int = Field(default=6, ge=1, le=168)
    enabled: bool = True

    @field_validator("start_date", "end_date")
    @classmethod
    def _date_fmt(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")
        return v


class TaskUpdateIn(BaseModel):
    origin: Optional[str] = None
    dest: Optional[str] = None
    origin_codes: Optional[str] = None
    dest_codes: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    stay_min: Optional[int] = Field(default=None, ge=1, le=60)
    stay_max: Optional[int] = Field(default=None, ge=1, le=60)
    adults: Optional[int] = Field(default=None, ge=1, le=9)
    currency: Optional[str] = None
    cabin: Optional[str] = None
    top_n: Optional[int] = Field(default=None, ge=10, le=50)
    target_price: Optional[float] = Field(default=None, gt=0)
    clear_target_price: bool = False
    interval_hours: Optional[int] = Field(default=None, ge=1, le=168)
    enabled: Optional[bool] = None


class PlaceSuggestionOut(BaseModel):
    kind: str
    id: str
    label: str
    subtitle: str
    display: str
    codes: list[str]


class TaskOut(BaseModel):
    id: int
    origin: str
    dest: str
    origin_codes: str = ""
    dest_codes: str = ""
    start_date: str
    end_date: str
    stay_min: int
    stay_max: int
    adults: int
    currency: str
    cabin: str
    top_n: int
    target_price: Optional[float]
    interval_hours: int
    enabled: bool
    best_price_seen: Optional[float]
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    created_at: datetime
    estimated_combinations: int = 0

    model_config = {"from_attributes": True}


class FlightResultOut(BaseModel):
    rank: int
    outbound_date: str
    return_date: str
    trip_days: int
    total_price: float
    cache_price: float = 0.0
    verified_price: Optional[float] = None
    verify_status: str = ""
    currency: str
    outbound_summary: str
    return_summary: str
    booking_class: str
    source: str
    verify_url: str = ""
    verify_url_ctrip: str = ""
    verify_url_qunar: str = ""

    model_config = {"from_attributes": True}


class ScanRunOut(BaseModel):
    id: int
    task_id: int
    trigger: str
    status: str
    phase: str = ""
    progress_done: int
    progress_total: int
    progress_message: str = ""
    min_price: Optional[float]
    error: str
    notify_message: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    results: list[FlightResultOut] = []

    model_config = {"from_attributes": True}


class RefreshOut(BaseModel):
    run_id: int
    status: str
