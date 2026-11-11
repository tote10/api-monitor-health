"""Account, role and login-session management."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.db.models import ADMIN_ROLE, VIEWER_ROLE, User, UserSession


class AuthError(Exception):
    """Base class for authentication and account errors."""


class EmailAlreadyRegistered(AuthError):
    """Raised when an account already uses the requested email address."""


class InvalidCredentials(AuthError):
    """Raised when an email and password pair does not match an account."""


class RegistrationClosed(AuthError):
    """Raised when self-registration is disabled."""


class LastAdminError(AuthError):
    """Raised when a change would leave the service without an admin."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


# Used to keep login timing similar when the email does not exist.
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def create_user(
    db: Session,
    *,
    email: str,
    name: str,
    password: str,
    role: str = VIEWER_ROLE,
) -> User:
    user = User(
        email=normalize_email(email),
        name=name.strip(),
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise EmailAlreadyRegistered(
            "An account with this email address already exists."
        ) from exc

    return user


def register_user(
    db: Session, *, email: str, name: str, password: str
) -> User:
    """Self-service sign-up. The first account becomes the administrator."""
    is_first_user = count_users(db) == 0

    if not is_first_user and not settings.ALLOW_REGISTRATION:
        raise RegistrationClosed(
            "Self-registration is disabled. Ask an administrator for an account."
        )

    return create_user(
        db,
        email=email,
        name=name,
        password=password,
        role=ADMIN_ROLE if is_first_user else VIEWER_ROLE,
    )


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)

    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise InvalidCredentials("Incorrect email or password.")

    if not verify_password(password, user.password_hash):
        raise InvalidCredentials("Incorrect email or password.")

    if not user.is_active:
        raise InvalidCredentials("This account has been deactivated.")

    return user


def create_session(db: Session, user: User) -> tuple[str, UserSession]:
    """Create a login session and return the raw token to set as a cookie."""
    now = _utcnow()
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))

    token = generate_session_token()
    session = UserSession(
        token_hash=hash_session_token(token),
        user_id=user.id,
        expires_at=now + timedelta(hours=settings.SESSION_TTL_HOURS),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return token, session


def get_user_for_token(db: Session, token: str) -> User | None:
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(token)
        )
    )
    if session is None:
        return None

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= _utcnow():
        db.delete(session)
        db.commit()
        return None

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return user


def revoke_session(db: Session, token: str) -> None:
    db.execute(
        delete(UserSession).where(
            UserSession.token_hash == hash_session_token(token)
        )
    )
    db.commit()


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)).all())


def _active_admin_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == ADMIN_ROLE, User.is_active.is_(True))
        )
        or 0
    )


def update_user(
    db: Session,
    user: User,
    *,
    role: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
) -> User:
    """Apply an admin change, keeping at least one active administrator."""
    loses_admin_access = (
        user.role == ADMIN_ROLE
        and user.is_active
        and (
            (role is not None and role != ADMIN_ROLE)
            or is_active is False
        )
    )
    if loses_admin_access and _active_admin_count(db) <= 1:
        raise LastAdminError(
            "At least one active administrator account is required."
        )

    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        user.password_hash = hash_password(password)
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))

    db.commit()
    db.refresh(user)
    return user
