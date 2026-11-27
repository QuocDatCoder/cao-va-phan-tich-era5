from collections.abc import AsyncIterator
from contextvars import Token
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import Settings, get_settings
from backend.app.core.user_context import current_user_id
from backend.app.services.auth_service import (
    AuthService,
    InvalidSessionError,
)
from backend.app.services.dependencies import get_auth_service


_bearer_scheme = HTTPBearer(auto_error=False)
_PASSIVE_GET_ROUTES = {
    "/api/v1/collections/{collection_id}/live",
    "/api/v1/jobs",
    "/api/v1/jobs/{job_id}",
    "/api/v1/jobs/{job_id}/quality",
    "/api/v1/synchronization",
    "/api/v1/synchronization/status/{sync_id}",
}


async def require_current_user(
    request: Request,
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
        path = getattr(request.scope.get("route"), "path", None)
        passive = (
            request.method == "GET"
            and (path in _PASSIVE_GET_ROUTES or request.headers.get("X-Weather-Activity") == "passive")
            or path == "/api/v1/collections/{collection_id}/refresh"
            and request.query_params.get("automatic", "").lower() in {"true", "1", "yes", "on", "t", "y"}
        )
        if not passive:
            service._repository.record_activity(int(user["id"]), int(time.time()))
        yield user
    finally:
        current_user_id.reset(context_token)
