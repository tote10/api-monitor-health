"""Shared request dependencies for authentication and roles."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.db.models import ADMIN_ROLE, User
from app.services import auth_service


def token_from_request(request: Request) -> str | None:
    """Read a bearer token or the session cookie."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    if not settings.AUTH_ENABLED:
        return None

    token = token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use the API.",
            headers={"WWW-Authenticate": "Session"},
        )

    user = auth_service.get_user_for_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is invalid or has expired. Sign in again.",
            headers={"WWW-Authenticate": "Session"},
        )

    return user


def require_admin(user: User | None = Depends(get_current_user)) -> User | None:
    if not settings.AUTH_ENABLED:
        return user

    if user is None or user.role != ADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required.",
        )

    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]
AdminUser = Annotated[User | None, Depends(require_admin)]
