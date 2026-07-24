from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import MeUpdateIn, UserOut

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("", response_model=UserOut)
def update_me(
    body: MeUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if body.feishu_webhook is not None:
        user.feishu_webhook = body.feishu_webhook.strip()
    if body.password:
        user.password_hash = hash_password(body.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
