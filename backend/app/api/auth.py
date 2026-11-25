from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.api.auth_dependencies import require_current_user
from backend.app.domain.auth import AuthResponse, CredentialsRequest, UserResponse
from backend.app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
)
from backend.app.services.dependencies import get_auth_service
from backend.app.storage.auth_repository import UsernameAlreadyExistsError


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_bearer_scheme = HTTPBearer(auto_error=False)


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    credentials: CredentialsRequest,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    try:
        return service.register(credentials.username, credentials.password)
    except UsernameAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tên đăng nhập này đã được sử dụng.",
        ) from exc


@router.post("/login", response_model=AuthResponse)
def login(
    credentials: CredentialsRequest,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    try:
        return service.login(credentials.username, credentials.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập hoặc mật khẩu không đúng.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.get("/me", response_model=UserResponse)
def current_user(user: dict = Depends(require_current_user)) -> dict:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Đăng nhập đang bị tắt.",
        )
    return user


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    _: dict | None = Depends(require_current_user),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cần đăng nhập để đăng xuất.",
        )
    service.logout(credentials.credentials)
    return {"status": "ok"}
