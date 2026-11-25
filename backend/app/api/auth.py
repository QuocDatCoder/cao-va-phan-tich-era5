from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import sqlite3

from backend.app.api.auth_dependencies import require_current_user
from backend.app.domain.auth import AuthResponse, CredentialsRequest, UserResponse, AdminUserResponse, UserAccessRequest, AdminHandoffRequest
from backend.app.core.config import get_settings
from backend.app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    InvalidSessionError,
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
    settings = get_settings()
    if credentials.username.casefold() == settings.admin_username.casefold():
        raise HTTPException(status_code=409, detail="Tên tài khoản này đã được dành riêng cho quản trị viên.")
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
    return {**user, "is_admin": bool(user.get("is_admin", False))}


def require_admin(user: dict = Depends(require_current_user)) -> dict:
    if user is None or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản trị.")
    return user


@router.get("/users")
def list_users(_: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    return {"users": service._repository.list_users()}


@router.get("/admin/overview")
def admin_overview(_: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    return service._repository.get_admin_overview()


@router.get("/admin/collection-history")
def admin_collection_history(user_id: int | None = None, _: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    from backend.app.core.config import get_settings
    from backend.app.core.user_settings import get_settings_for_user

    users = service._repository.list_users()
    if user_id is not None:
        users = [account for account in users if account["id"] == user_id]
    history = []
    for account in users:
        db_path = get_settings_for_user(account["id"], get_settings()).job_metadata_db_path
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(db_path, timeout=5) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(download_jobs)")}
                if not columns:
                    continue
                wanted = ("job_id", "source", "variable", "start_date", "end_date", "frequency", "status", "progress", "created_at", "completed_at")
                select = ", ".join(name if name in columns else f"NULL AS {name}" for name in wanted)
                rows = connection.execute(f"SELECT {select} FROM download_jobs ORDER BY created_at DESC LIMIT 200").fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            history.append({"user_id": account["id"], "username": account["username"], **dict(zip(wanted, row))})
    history.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"history": history[:1000]}


@router.post("/admin/handoff")
def create_admin_handoff(admin: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    return {"ticket": service.create_admin_handoff(admin["id"])}


@router.post("/admin/handoff/redeem", response_model=AuthResponse)
def redeem_admin_handoff(request: AdminHandoffRequest, service: AuthService = Depends(get_auth_service)):
    try:
        return service.redeem_admin_handoff(request.ticket)
    except InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail="Mã chuyển trang đã hết hạn hoặc đã được sử dụng.") from exc


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    target = next((u for u in service._repository.list_users() if u["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    if target["username"] == get_settings().admin_username.casefold() or target["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="Không thể xóa tài khoản admin cố định hoặc tài khoản đang đăng nhập.")
    from backend.app.core.user_settings import get_settings_for_user
    user_settings = get_settings_for_user(user_id)
    user_data_dir = user_settings.data_dir
    if user_data_dir.exists():
        import shutil
        shutil.rmtree(user_data_dir)
    deleted = service._repository.delete_user(user_id)
    return {"status": "deleted", "username": deleted["username"]}


@router.patch("/users/{user_id}")
def update_user(user_id: int, access: UserAccessRequest, admin: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    target = next((u for u in service._repository.list_users() if u["id"] == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    if target["username"] == get_settings().admin_username.casefold() and (access.is_admin is False or access.is_active is False):
        raise HTTPException(status_code=400, detail="Không thể thu hồi quyền hoặc khóa tài khoản admin cố định.")
    if target["id"] == admin["id"] and access.is_admin is False:
        raise HTTPException(status_code=400, detail="Không thể tự thu hồi quyền quản trị.")
    service._repository.set_user_access(user_id, is_admin=access.is_admin, is_active=access.is_active)
    return next(u for u in service._repository.list_users() if u["id"] == user_id)


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
