from collections import Counter
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.api.auth_dependencies import require_current_user
from backend.app.core.config import Settings, get_settings
from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain.auth import (
    AdminCreateUserRequest,
    AdminEnableLiveRequest,
    AdminHandoffRequest,
    AdminResetPasswordRequest,
    AuthResponse,
    CredentialsRequest,
    UserAccessRequest,
    UserResponse,
)
from backend.app.domain.live_refresh import INACTIVE_AFTER_SECONDS
from backend.app.domain.schemas import DownloadJob
from backend.app.domain.time_utils import to_utc_naive
from backend.app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    InvalidSessionError,
)
from backend.app.services.dependencies import get_auth_service, get_download_job_service_for_user
from backend.app.services.live_refresh_service import (
    collection_lock,
    get_scheduler_health,
    refresh_collection,
    require_open_meteo,
)
from backend.app.storage.auth_repository import LiveScheduleLimitError, UsernameAlreadyExistsError
from backend.app.storage.collection_repository import CollectionRepository


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


def _audit(service, admin, action, target_type, target_id, **detail):
    service._repository.record_audit_log(
        admin["id"], admin["username"], action, target_type, str(target_id),
        json.dumps(detail, ensure_ascii=False) if detail else "",
    )


def _activity_at(user):
    for field in ("last_active_at", "last_login_at", "created_at"):
        if user[field] is not None:
            return user[field]


def _read_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _user_storage_stats(user_id: int, base_settings: Settings) -> dict[str, int]:
    settings = get_settings_for_user(user_id, base_settings)
    stats = dict(storage_bytes=0, collections_count=0, observations_count=0)
    for directory, _, files in os.walk(settings.data_dir):
        for name in files:
            path = Path(directory) / name
            try:
                if not path.is_symlink() and path.is_file():
                    stats["storage_bytes"] += path.stat().st_size
            except OSError:
                continue
    try:
        with closing(_read_database(settings.climate_data_db_path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "collections" in tables:
                stats["collections_count"] = connection.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
            if "collection_observations" in tables:
                stats["observations_count"] = connection.execute(
                    "SELECT COUNT(*) FROM collection_observations"
                ).fetchone()[0]
    except (OSError, sqlite3.Error):
        pass
    return stats


@router.get("/users")
def list_users(_: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    repository = service._repository
    now = int(time.time())
    settings = get_settings()
    sessions = repository.active_sessions_by_user(now)
    schedules = repository.list_live_schedules(now)
    live_counts = Counter(row["user_id"] for row in schedules if row["enabled"])
    schedule_counts = Counter(row["user_id"] for row in schedules)
    return {"users": [
        {
            **user,
            "idle_seconds": max(0, now - _activity_at(user)),
            "active_sessions": sessions.get(user["id"], 0),
            "live_active_count": live_counts[user["id"]],
            "live_schedules_count": schedule_counts[user["id"]],
            "is_fixed_admin": user["username"] == settings.admin_username.casefold(),
            **_user_storage_stats(user["id"], settings),
        }
        for user in repository.list_users()
    ]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def admin_create_user(body: AdminCreateUserRequest, admin: dict = Depends(require_admin),
                      service: AuthService = Depends(get_auth_service)):
    if body.username.casefold() == get_settings().admin_username.casefold():
        raise HTTPException(409, "Tên tài khoản này đã được dành riêng cho quản trị viên.")
    try:
        user = service.admin_create_user(body.username, body.password, is_admin=body.is_admin)
    except UsernameAlreadyExistsError as exc:
        raise HTTPException(409, "Tên đăng nhập này đã được sử dụng.") from exc
    _audit(service, admin, "create_user", "user", user["id"], username=user["username"], is_admin=body.is_admin)
    return user


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, body: AdminResetPasswordRequest, admin: dict = Depends(require_admin),
                         service: AuthService = Depends(get_auth_service)):
    revoked = service.admin_reset_password(user_id, body.password, revoke_sessions=body.revoke_sessions)
    if revoked is None:
        raise HTTPException(404, "Không tìm thấy tài khoản.")
    _audit(service, admin, "reset_password", "user", user_id, revoke_sessions=body.revoke_sessions, revoked=revoked)
    return {"status": "ok", "revoked": revoked}


@router.post("/users/{user_id}/revoke-sessions")
def admin_revoke_sessions(user_id: int, admin: dict = Depends(require_admin),
                          service: AuthService = Depends(get_auth_service)):
    if user_id not in service._repository.list_user_ids():
        raise HTTPException(404, "Không tìm thấy tài khoản.")
    revoked = service._repository.revoke_user_sessions(user_id, int(time.time()))
    _audit(service, admin, "revoke_sessions", "user", user_id, revoked=revoked)
    return {"revoked": revoked}


@router.get("/admin/overview")
def admin_overview(_: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    repository = service._repository
    now = int(time.time())
    settings = get_settings()
    users = repository.list_users()
    totals = Counter()
    for user in users:
        totals.update(_user_storage_stats(user["id"], settings))
    return {
        **repository.get_admin_overview(),
        "active_7d_users": sum(_activity_at(user) > now - INACTIVE_AFTER_SECONDS for user in users),
        "active_sessions": repository.count_active_sessions(now),
        "total_collections": totals["collections_count"],
        "total_observations": totals["observations_count"],
        "total_storage_bytes": totals["storage_bytes"],
        "scheduler_status": get_scheduler_health(),
        "recent_audit_logs": repository.list_audit_logs(15),
    }


@router.get("/admin/live-schedules")
def admin_live_schedules(_: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    return {"schedules": service._repository.list_live_schedules(int(time.time()))}


@router.post("/admin/live-schedules/{user_id}/{collection_id}/disable")
def admin_disable_live_schedule(
    user_id: int, collection_id: str,
    admin: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service),
):
    if not service._repository.disable_live_schedule(user_id, collection_id, int(time.time())):
        raise HTTPException(404, "Không tìm thấy lịch tự động cập nhật.")
    _audit(service, admin, "disable_live_schedule", "live_schedule", collection_id, user_id=user_id)
    return service._repository.get_live_schedule(user_id, collection_id, int(time.time()))


@router.post("/admin/jobs/{user_id}/{job_id}/cancel", response_model=DownloadJob)
def admin_cancel_job(user_id: int, job_id: str, admin: dict = Depends(require_admin),
                     service: AuthService = Depends(get_auth_service)):
    if not get_settings_for_user(user_id).job_metadata_db_path.is_file():
        raise HTTPException(404, "Không tìm thấy tác vụ.")
    job = get_download_job_service_for_user(user_id).cancel_job(job_id)
    _audit(service, admin, "cancel_job", "job", job_id, user_id=user_id, status=job.status)
    return job


@router.delete("/admin/collections/{user_id}/{collection_id}")
def admin_delete_collection(user_id: int, collection_id: str, admin: dict = Depends(require_admin),
                             service: AuthService = Depends(get_auth_service)):
    settings = get_settings_for_user(user_id)
    try:
        with collection_lock(user_id, collection_id):
            if not settings.climate_data_db_path.is_file():
                raise LookupError("Không tìm thấy bộ dữ liệu.")
            result = CollectionRepository(settings.climate_data_db_path).delete(collection_id, settings.job_metadata_db_path)
            service._repository.delete_live_schedule(user_id, collection_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    _audit(service, admin, "delete_collection", "collection", collection_id, user_id=user_id)
    return result


@router.post("/admin/live-schedules/disable-all")
def admin_disable_all_live_schedules(user_id: int | None = None, admin: dict = Depends(require_admin),
                                    service: AuthService = Depends(get_auth_service)):
    disabled = service._repository.disable_all_live_schedules(int(time.time()), user_id)
    _audit(service, admin, "disable_all_live_schedules", "live_schedule",
           user_id if user_id is not None else "all", disabled=disabled)
    return {"disabled": disabled}


@router.delete("/admin/live-schedules/cleanup")
def admin_cleanup_live_schedules(admin: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    deleted = service._repository.cleanup_disabled_live_schedules()
    _audit(service, admin, "cleanup_live_schedules", "live_schedule", "all", deleted=deleted)
    return {"deleted": deleted}


@router.post("/admin/live-schedules/{user_id}/{collection_id}/refresh")
def admin_refresh_live_schedule(user_id: int, collection_id: str, admin: dict = Depends(require_admin),
                               service: AuthService = Depends(get_auth_service)):
    try:
        result = refresh_collection(service._repository, user_id, collection_id, automatic=False)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _audit(service, admin, "refresh_live_schedule", "live_schedule", collection_id,
           user_id=user_id, status=result["status"])
    return result


@router.post("/admin/live-schedules/{user_id}/{collection_id}/enable")
def admin_enable_live_schedule(user_id: int, collection_id: str, body: AdminEnableLiveRequest,
                               admin: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    settings = get_settings_for_user(user_id)
    collections = CollectionRepository(settings.climate_data_db_path, read_only=True)
    try:
        with collection_lock(user_id, collection_id):
            entry = require_open_meteo(collections, collection_id)
            schedule = service._repository.enable_live_schedule(
                user_id, collection_id, entry["metadata"]["place_name"], body.interval_hours, int(time.time()),
            )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LiveScheduleLimitError as exc:
        raise HTTPException(409, "Tài khoản đã đạt giới hạn lịch tự động cập nhật.") from exc
    _audit(service, admin, "enable_live_schedule", "live_schedule", collection_id,
           user_id=user_id, interval_hours=body.interval_hours)
    return schedule


@router.get("/admin/audit-logs")
def admin_audit_logs(limit: int = Query(default=50, ge=0), admin: dict = Depends(require_admin),
                     service: AuthService = Depends(get_auth_service)):
    logs = service._repository.list_audit_logs(limit)
    _audit(service, admin, "list_audit_logs", "audit_log", "all", limit=limit)
    return {"logs": logs}


def _history_collections(path, job_ids):
    if not job_ids:
        return {}
    try:
        with closing(_read_database(path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "collections" not in tables:
                return {}
            variables = "'[]'"
            if "collection_variables" in tables:
                variables = """(SELECT json_group_array(variable) FROM (
                    SELECT variable FROM collection_variables WHERE collection_id = collections.id ORDER BY variable
                ))"""
            issues = "0"
            if "collection_quality_issues" in tables:
                issues = "(SELECT COUNT(*) FROM collection_quality_issues WHERE collection_id = collections.id)"
            placeholders = ", ".join("?" for _ in job_ids)
            rows = connection.execute(
                f"""SELECT collections.*, {variables} AS variables, {issues} AS issues_count
                    FROM collections WHERE job_id IN ({placeholders})""", job_ids,
            )
            return {row["job_id"]: dict(row) for row in rows}
    except (OSError, sqlite3.Error):
        return {}


def _history_object(collection, field):
    try:
        value = json.loads(collection.get(field) or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _history_details(job, collection, live_ids):
    metadata = _history_object(collection, "metadata")
    summary = _history_object(collection, "summary")
    variables = json.loads(collection.get("variables") or "[]")
    location = metadata.get("location") or {}
    duration = None
    if job["created_at"] and job["completed_at"]:
        try:
            start = to_utc_naive(datetime.fromisoformat(job["created_at"]))
            end = to_utc_naive(datetime.fromisoformat(job["completed_at"]))
            duration = (end - start).total_seconds()
        except (TypeError, ValueError):
            pass
    return {
        "place_name": metadata.get("place_name"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "dataset": metadata.get("dataset"),
        "variables": variables,
        "variables_count": len(variables),
        "collection_status": collection.get("status"),
        "expected_count": summary.get("expected_count"),
        "available_count": summary.get("available_count"),
        "coverage": summary.get("coverage"),
        "imported_values": summary.get("imported_values", 0),
        "quality_issues_count": collection.get("issues_count", 0),
        "duration_seconds": duration,
        "has_live_schedule": collection.get("id") in live_ids,
    }


@router.get("/admin/collection-history")
def admin_collection_history(user_id: int | None = None, _: dict = Depends(require_admin), service: AuthService = Depends(get_auth_service)):
    repository = service._repository
    users = repository.list_users()
    if user_id is not None:
        users = [account for account in users if account["id"] == user_id]
    live_ids = {}
    for schedule in repository.list_live_schedules(int(time.time())):
        live_ids.setdefault(schedule["user_id"], set()).add(schedule["collection_id"])
    settings = get_settings()
    history = []
    for account in users:
        user_settings = get_settings_for_user(account["id"], settings)
        try:
            with closing(_read_database(user_settings.job_metadata_db_path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(download_jobs)")}
                if not columns:
                    continue
                wanted = ("job_id", "source", "variable", "start_date", "end_date", "frequency", "status",
                          "progress", "created_at", "updated_at", "completed_at", "error")
                select = ", ".join(name if name in columns else f"NULL AS {name}" for name in wanted)
                rows = connection.execute(f"SELECT {select} FROM download_jobs ORDER BY created_at DESC LIMIT 200").fetchall()
        except (OSError, sqlite3.Error):
            continue
        collections = _history_collections(user_settings.climate_data_db_path, [row["job_id"] for row in rows])
        scheduled = live_ids.get(account["id"], set())
        for row in rows:
            history.append({
                "user_id": account["id"], "username": account["username"], **dict(row),
                **_history_details(row, collections.get(row["job_id"], {}), scheduled),
            })
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
    user_settings = get_settings_for_user(user_id)
    user_data_dir = user_settings.data_dir
    if user_data_dir.exists():
        import shutil
        shutil.rmtree(user_data_dir)
    deleted = service._repository.delete_user(user_id)
    if deleted is None:
        raise HTTPException(404, "Không tìm thấy tài khoản.")
    _audit(service, admin, "delete_user", "user", user_id, username=deleted["username"])
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
    _audit(service, admin, "update_user", "user", user_id, **access.model_dump(exclude_none=True))
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
