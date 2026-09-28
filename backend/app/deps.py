from __future__ import annotations

import secrets
import uuid

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.auth import ALGORITHM, AuthError, hash_password, username_from_token
from app.config import settings
from app.database import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)


def _guest_username() -> str:
    return f"guest_{uuid.uuid4().hex[:16]}"


def _encode_guest_token(username: str) -> str:
    from datetime import datetime, timedelta

    payload = {
        "sub": username,
        "guest": True,
        "exp": datetime.utcnow() + timedelta(days=settings.guest_cookie_max_age_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def _username_from_guest_cookie(token: str) -> str | None:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        if not data.get("guest"):
            return None
        sub = data.get("sub")
        return str(sub) if sub else None
    except JWTError:
        return None


def get_or_create_guest(request: Request, response: Response, db: Session) -> User:
    cookie_name = settings.guest_cookie_name
    raw = request.cookies.get(cookie_name)
    if raw:
        username = _username_from_guest_cookie(raw)
        if username:
            user = db.query(User).filter(User.username == username).first()
            if user and user.is_active:
                return user

    username = _guest_username()
    user = User(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = _encode_guest_token(username)
    response.set_cookie(
        key=cookie_name,
        value=token,
        max_age=settings.guest_cookie_max_age_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        # 公网 HTTPS 时由反代终止 TLS；本地/IP 访问用 false
        secure=request.url.scheme == "https",
        path="/",
    )
    return user


def get_current_user(
    request: Request,
    response: Response,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if settings.public_mode:
        return get_or_create_guest(request, response, db)

    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        username = username_from_token(creds.credentials)
    except AuthError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from None
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不可用")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if settings.public_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="公开模式已禁用管理端")
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
