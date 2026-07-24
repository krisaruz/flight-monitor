from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal, get_db
from app.deps import get_current_user
from app.models import ScanRun, User, WatchTask
from app.schemas import RefreshOut, ScanRunOut, TaskCreateIn, TaskOut, TaskUpdateIn
from app.services.places import normalize_place
from app.services.scanner import enqueue_scan, estimate_combinations

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_out(task: WatchTask) -> TaskOut:
    data = TaskOut.model_validate(task)
    data.estimated_combinations = estimate_combinations(task)
    return data


def _get_user_task(db: Session, user: User, task_id: int) -> WatchTask:
    task = db.query(WatchTask).filter(WatchTask.id == task_id, WatchTask.user_id == user.id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("", response_model=list[TaskOut])
def list_tasks(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TaskOut]:
    tasks = db.query(WatchTask).filter(WatchTask.user_id == user.id).order_by(WatchTask.id.desc()).all()
    return [_task_out(t) for t in tasks]


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskOut:
    if body.stay_max < body.stay_min:
        raise HTTPException(status_code=400, detail="stay_max 不能小于 stay_min")
    start = datetime.strptime(body.start_date, "%Y-%m-%d")
    end = datetime.strptime(body.end_date, "%Y-%m-%d")
    if end < start:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")

    try:
        origin_label, origin_codes = normalize_place(body.origin, body.origin_codes)
        dest_label, dest_codes = normalize_place(body.dest, body.dest_codes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    task = WatchTask(
        user_id=user.id,
        origin=origin_label[:64],
        dest=dest_label[:64],
        origin_codes=origin_codes[:128],
        dest_codes=dest_codes[:128],
        start_date=body.start_date,
        end_date=body.end_date,
        stay_min=body.stay_min,
        stay_max=body.stay_max,
        adults=body.adults,
        currency=body.currency.upper(),
        cabin=body.cabin or "",
        top_n=body.top_n,
        target_price=body.target_price,
        interval_hours=body.interval_hours,
        enabled=body.enabled,
        next_run_at=datetime.utcnow() if body.enabled else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskOut:
    return _task_out(_get_user_task(db, user, task_id))


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    body: TaskUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskOut:
    task = _get_user_task(db, user, task_id)
    data = body.model_dump(exclude_unset=True)
    clear_target = data.pop("clear_target_price", False)

    if "origin" in data or "origin_codes" in data:
        try:
            label, codes = normalize_place(
                data.get("origin") or task.origin,
                data.get("origin_codes", task.origin_codes),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        data["origin"] = label[:64]
        data["origin_codes"] = codes[:128]
    if "dest" in data or "dest_codes" in data:
        try:
            label, codes = normalize_place(
                data.get("dest") or task.dest,
                data.get("dest_codes", task.dest_codes),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        data["dest"] = label[:64]
        data["dest_codes"] = codes[:128]
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()

    for k, v in data.items():
        setattr(task, k, v)

    if clear_target:
        task.target_price = None

    stay_min = task.stay_min
    stay_max = task.stay_max
    if stay_max < stay_min:
        raise HTTPException(status_code=400, detail="stay_max 不能小于 stay_min")

    if body.enabled is True and task.next_run_at is None:
        task.next_run_at = datetime.utcnow()
    if body.enabled is False:
        task.next_run_at = None

    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    task = _get_user_task(db, user, task_id)
    db.delete(task)
    db.commit()


@router.post("/{task_id}/refresh", response_model=RefreshOut)
def refresh_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RefreshOut:
    _get_user_task(db, user, task_id)
    try:
        run_id = enqueue_scan(task_id, trigger="manual")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return RefreshOut(run_id=run_id, status="pending")


@router.get("/{task_id}/runs/latest", response_model=ScanRunOut | None)
def latest_run(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanRunOut | None:
    _get_user_task(db, user, task_id)
    run = (
        db.query(ScanRun)
        .options(joinedload(ScanRun.results))
        .filter(ScanRun.task_id == task_id)
        .order_by(ScanRun.id.desc())
        .first()
    )
    if not run:
        return None
    run.results.sort(key=lambda r: r.rank)
    return ScanRunOut.model_validate(run)


@router.get("/{task_id}/runs/{run_id}", response_model=ScanRunOut)
def get_run(
    task_id: int,
    run_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanRunOut:
    _get_user_task(db, user, task_id)
    run = (
        db.query(ScanRun)
        .options(joinedload(ScanRun.results))
        .filter(ScanRun.id == run_id, ScanRun.task_id == task_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="扫描记录不存在")
    run.results.sort(key=lambda r: r.rank)
    return ScanRunOut.model_validate(run)


@router.get("/{task_id}/runs/{run_id}/events")
async def run_events(
    task_id: int,
    run_id: int,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    # 校验归属
    db = SessionLocal()
    try:
        task = db.query(WatchTask).filter(WatchTask.id == task_id, WatchTask.user_id == user.id).first()
        run = db.query(ScanRun).filter(ScanRun.id == run_id, ScanRun.task_id == task_id).first()
        if not task or not run:
            raise HTTPException(status_code=404, detail="不存在")
    finally:
        db.close()

    async def event_gen():
        last_payload = ""
        while True:
            db2 = SessionLocal()
            try:
                r = (
                    db2.query(ScanRun)
                    .options(joinedload(ScanRun.results))
                    .filter(ScanRun.id == run_id)
                    .first()
                )
                if not r:
                    yield f"event: error\ndata: {json.dumps({'error': 'gone'})}\n\n"
                    return
                r.results.sort(key=lambda x: x.rank)
                payload = ScanRunOut.model_validate(r).model_dump(mode="json")
                text = json.dumps(payload, ensure_ascii=False)
                if text != last_payload:
                    last_payload = text
                    yield f"event: progress\ndata: {text}\n\n"
                if r.status in ("done", "partial", "failed"):
                    yield f"event: done\ndata: {text}\n\n"
                    return
            finally:
                db2.close()
            await asyncio.sleep(0.8)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
