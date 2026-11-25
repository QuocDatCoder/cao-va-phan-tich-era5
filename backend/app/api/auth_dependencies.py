from collections.abc import AsyncIterator
from contextvars import Token

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import Settings, get_settings
from backend.app.core.user_context import current_user_id
from backend.app.services.auth_service import (
    AuthService,
    InvalidSessionError,
)
from backend.app.services.dependencies import get_auth_service


_bearer_scheme = HTTPBearer(auto_error=False)


async def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> AsyncIterator[dict | None]:
    if not settings.auth_enabled:
        yield None
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cần đăng nhập để sử dụng API này.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    service: AuthService = get_auth_service()
    try:
        user = service.authenticate(credentials.credentials)
    except InvalidSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    context_token: Token[int | None] = current_user_id.set(int(user["id"]))
    try:
        yield user
    finally:
        current_user_id.reset(context_token)
