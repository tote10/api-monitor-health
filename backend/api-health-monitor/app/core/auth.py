import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.db.models import LoginSession, User

COOKIE = "monitor_session"
SESSION_LIFETIME = timedelta(hours=12)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PASSWORD_CHECKS = BoundedSemaphore(2)
router = APIRouter(prefix="/auth", tags=["authentication"])


def derive_password(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt),
        n=2**17, r=8, p=1, maxmem=256 * 1024**2, dklen=32,
    ).hex()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"{salt}:{derive_password(password, salt)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_request_origin(request: Request):
    origin = request.headers.get("origin")
    site = request.headers.get("sec-fetch-site")
    if site in {"cross-site", "same-site"}:
        raise HTTPException(403, "Cross-origin request denied")
    if origin:
        try:
            parsed_origin = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, "Cross-origin request denied") from None
        expected_scheme = "https" if settings.AUTH_SECURE_COOKIE else request.url.scheme
        if (parsed_origin.scheme, parsed_origin.netloc.lower()) != (
            expected_scheme, request.url.netloc.lower()
        ):
            raise HTTPException(403, "Cross-origin request denied")


def session_user(request: Request, db: Session) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    return db.scalar(
        select(User)
        .join(LoginSession, LoginSession.user_id == User.id)
        .where(
            LoginSession.token_hash == token_hash(token),
            LoginSession.expires_at > datetime.now(timezone.utc),
        )
    )


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = session_user(request, db)
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user


def authorize_endpoint(request: Request, user: User = Depends(require_user)):
    if request.method not in SAFE_METHODS:
        check_request_origin(request)
        if user.role != "admin":
            raise HTTPException(403, "Administrator access required")


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login", dependencies=[Depends(check_request_origin)])
def login(
    credentials: Credentials, request: Request, response: Response,
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == credentials.username))
    stored = user.password_hash if user else f"{'0' * 32}:{'0' * 64}"
    salt, expected = stored.split(":")
    if not PASSWORD_CHECKS.acquire(blocking=False):
        raise HTTPException(429, "Too many sign-in requests")
    try:
        password_matches = hmac.compare_digest(derive_password(credentials.password, salt), expected)
    finally:
        PASSWORD_CHECKS.release()
    if not password_matches or user is None:
        raise HTTPException(401, "Invalid username or password")

    now = datetime.now(timezone.utc)
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
    old_token = request.cookies.get(COOKIE)
    if old_token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old_token)))
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=now + SESSION_LIFETIME))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE, token, max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True, samesite="strict", secure=settings.AUTH_SECURE_COOKIE,
    )
    return {"username": user.username, "role": user.role}


@router.post("/logout", status_code=204, dependencies=[Depends(check_request_origin)])
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=settings.AUTH_SECURE_COOKIE)
    return response


@router.get("/me")
def current_user(response: Response, user: User = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"username": user.username, "role": user.role}
