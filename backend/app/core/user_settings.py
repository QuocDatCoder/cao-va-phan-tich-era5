from hashlib import sha256
import json

from backend.app.core.config import Settings, get_settings


def get_settings_for_user(
    user_id: int | None, settings: Settings | None = None
) -> Settings:
    settings = settings or get_settings()
    if not settings.auth_enabled:
        return settings
    if user_id is None:
        raise RuntimeError("Account-scoped storage requires an authenticated user.")

    user_data_dir = settings.data_dir / "users" / str(user_id)
    return settings.model_copy(
        update={
            "data_dir": user_data_dir,
            "climate_data_db_path": user_data_dir / "cache" / "climate.sqlite3",
            "job_metadata_db_path": user_data_dir / "cache" / "jobs.sqlite3",
        }
    )


def settings_cache_key(
    user_id: int | None, settings: Settings | None = None
) -> tuple[int | None, str]:
    settings = settings or get_settings()
    values = settings.model_dump(mode="json", exclude={"cds_api_key"})
    serialized = json.dumps(values, sort_keys=True, separators=(",", ":"))
    secret = settings.cds_api_key or ""
    fingerprint = sha256(f"{serialized}\0{secret}".encode()).hexdigest()
    return user_id, fingerprint
