"""Sign-up, login, logout and account administration."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, token_from_request
from app.api.schemas import (
    AuthSessionResponse,
    LoginRequest,
    RegisterRequest,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.core.config import settings
from app.db.database import get_db
from app.db.models import User
from app.services import auth_service
from app.services.auth_service import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    LastAdminError,
    RegistrationClosed,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
    )


def _require_auth_enabled() -> None:
    if not settings.AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication is disabled for this deployment.",
        )


@router.get(
    "/session",
    response_model=AuthSessionResponse,
    summary="Report authentication state and the signed-in user",
)
def read_session(
    request: Request, db: Session = Depends(get_db)
) -> AuthSessionResponse:
    if not settings.AUTH_ENABLED:
        return AuthSessionResponse(
            auth_enabled=False,
            authenticated=False,
            registration_open=False,
            has_users=False,
        )

    user = None
    token = token_from_request(request)
    if token:
        user = auth_service.get_user_for_token(db, token)

    has_users = auth_service.count_users(db) > 0
    return AuthSessionResponse(
        auth_enabled=True,
        authenticated=user is not None,
        registration_open=settings.ALLOW_REGISTRATION or not has_users,
        has_users=has_users,
        user=UserResponse.model_validate(user) if user else None,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
)
def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    _require_auth_enabled()

    try:
        user = auth_service.register_user(
            db,
            email=payload.email,
            name=payload.name,
            password=payload.password,
        )
    except EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except RegistrationClosed as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    token, _ = auth_service.create_session(db, user)
    _set_session_cookie(response, token)
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=UserResponse,
    summary="Sign in with email and password",
)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    _require_auth_enabled()

    try:
        user = auth_service.authenticate(db, payload.email, payload.password)
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    token, _ = auth_service.create_session(db, user)
    _set_session_cookie(response, token)
    return UserResponse.model_validate(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out and revoke the current session",
)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> None:
    token = token_from_request(request)
    if token:
        auth_service.revoke_session(db, token)
    _clear_session_cookie(response)


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List accounts",
)
def list_users(_: AdminUser, db: Session = Depends(get_db)):
    return auth_service.list_users(db)


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account with a specific role",
)
def create_user(
    payload: UserCreate,
    _: AdminUser,
    db: Session = Depends(get_db),
):
    try:
        return auth_service.create_user(
            db,
            email=payload.email,
            name=payload.name,
            password=payload.password,
            role=payload.role,
        )
    except EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Change a role, reset a password or deactivate an account",
)
def update_user(
    user_id: int,
    payload: UserUpdate,
    _: AdminUser,
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found",
        )

    try:
        return auth_service.update_user(
            db,
            user,
            role=payload.role,
            is_active=payload.is_active,
            password=payload.password,
        )
    except LastAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
