from contextlib import closing
from datetime import UTC, datetime, timedelta
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain.live_refresh import MAX_ACTIVE_LIVE_SCHEDULES
from backend.app.domain.schemas import DownloadRequest, Location
from backend.app.main import create_app
from backend.app.services import live_refresh_service
from backend.app.services.dependencies import _get_auth_repository, get_auth_service
from backend.app.storage.collection_repository import CollectionRepository
from backend.app.storage.job_repository import JobRepository


AUTH = "/api/v1/auth"
ADMIN = f"{AUTH}/admin"
LIVE = f"{ADMIN}/live-schedules"
PASSWORD = "fixture secure password"
START = datetime(2025, 1, 1, tzinfo=UTC)
POINT = Location(latitude=21.0, longitude=105.8)
VARIABLE = "2m_temperature"


def credentials(username, password=PASSWORD):
    return {"username": username, "password": password}


def bearer(response):
    assert response.status_code in {200, 201}, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def register(client, username):
    headers = bearer(client.post(f"{AUTH}/register", json=credentials(username)))
    return client.get(f"{AUTH}/me", headers=headers).json()["id"], headers


@pytest.fixture
def admin_api(tmp_path, monkeypatch):
    for name, value in {
        "AUTH_ENABLED": "true",
        "ADMIN_USERNAME": "fixture_admin",
        "ADMIN_PASSWORD": PASSWORD,
        "AUTH_DB_PATH": str(tmp_path / "auth.sqlite3"),
        "DATA_DIR": str(tmp_path / "data"),
        "CLIMATE_PROVIDER": "mock",
        "ERA5_SYNC_ENABLED": "false",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            admin = bearer(client.post(f"{AUTH}/login", json=credentials("fixture_admin")))
            yield client, admin, get_auth_service()._repository
    finally:
        _get_auth_repository.cache_clear()
        get_settings.cache_clear()


def seed_job(user_id, job_id, *, status, collection_id=None, source="open_meteo"):
    settings = get_settings_for_user(user_id)
    request = DownloadRequest(
        source=source, variable=VARIABLE, location=POINT, selected_place=POINT, place_name="Hà Nội", frequency="hourly",
        start_date=START.date(), end_date=START.date(), requested_start=START,
        requested_end=START + timedelta(hours=1),
    )
    jobs = JobRepository(settings.job_metadata_db_path)
    variables = [VARIABLE, "total_precipitation"]
    dataset = "running-dataset" if status == "running" else "test-dataset"
    job = jobs.create(job_id, request, dataset=dataset, retrieval_variables=variables)
    assert job.job_id == job_id
    completed_at = START + timedelta(seconds=30.5) if status in {"completed", "failed"} else None
    with closing(sqlite3.connect(settings.job_metadata_db_path)) as connection, connection:
        connection.execute(
            "UPDATE download_jobs SET created_at=?, updated_at=?, completed_at=?, status=?, error=? WHERE job_id=?",
            (START.isoformat(), (completed_at or START).isoformat(), completed_at.isoformat() if completed_at else None,
             status, "provider error" if status == "failed" else None, job_id),
        )
    if collection_id is None:
        return
    collections = CollectionRepository(settings.climate_data_db_path)
    collections.create(job, request, VARIABLE)
    with collections._connect() as connection:
        connection.execute("UPDATE collections SET id=?, status=?, summary=? WHERE job_id=?", (
            collection_id, "complete" if status == "completed" else status,
            '{"expected_count": 2, "available_count": 1, "coverage": 0.5, "imported_values": 1}', job_id,
        ))
        connection.execute("UPDATE collection_variables SET collection_id=? WHERE collection_id=?", (collection_id, job_id))
        connection.execute("INSERT INTO collection_quality_issues VALUES (?, 0, '{}')", (collection_id,))
        connection.execute("INSERT INTO collection_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            collection_id, job.dataset, VARIABLE, POINT.latitude, POINT.longitude, START.isoformat(), "K", 299.0,
        ))


def test_admin_overview_users_and_live_schedule_fields(admin_api):
    client, admin, repository = admin_api
    user_id, _ = register(client, "weather_user")
    idle_id, _ = register(client, "idle_user")
    now = int(time.time())
    seed_job(user_id, "weather_job", collection_id="weather_collection", status="completed")
    settings = get_settings_for_user(user_id)
    (settings.data_dir / "extra.bin").write_bytes(b"abc")
    with repository._connect() as connection:
        connection.execute("UPDATE users SET last_active_at=? WHERE id=?", (now - 8 * 86400, idle_id))
        connection.execute("UPDATE auth_sessions SET expires_at=? WHERE user_id=?", (now, idle_id))
    repository.enable_live_schedule(user_id, "weather_collection", "Hà Nội", 3, now)
    repository.enable_live_schedule(user_id, "not_refreshed", "Hà Nội", 1, now)
    repository.record_live_refresh(user_id, "weather_collection", "Hà Nội", now, automatic=True)

    schedules = {item["collection_id"]: item for item in client.get(LIVE, headers=admin).json()["schedules"]}
    current = schedules["weather_collection"]
    assert current["enabled"] and current["user_is_active"]
    assert current["idle_seconds"] >= 0 and current["last_active_at"] <= now
    assert current["user_active_live_count"] == 2
    assert datetime.fromisoformat(current["next_refresh_at"]) == datetime.fromtimestamp(now + 3 * 3600, UTC)
    immediately = datetime.fromisoformat(schedules["not_refreshed"]["next_refresh_at"])
    assert now <= immediately.timestamp() <= time.time()

    users = {item["id"]: item for item in client.get(f"{AUTH}/users", headers=admin).json()["users"]}
    assert users[user_id]["live_active_count"] == 2
    assert users[user_id]["live_schedules_count"] == 2
    assert {user["username"] for user in users.values() if user["is_fixed_admin"]} == {"fixture_admin"}
    assert users[user_id]["active_sessions"] == 1
    assert users[user_id]["collections_count"] == users[user_id]["observations_count"] == 1
    assert users[user_id]["storage_bytes"] >= 3 + settings.climate_data_db_path.stat().st_size
    assert users[idle_id]["active_sessions"] == users[idle_id]["collections_count"] == users[idle_id]["storage_bytes"] == 0
    assert users[idle_id]["idle_seconds"] >= 8 * 86400
    assert users[idle_id]["last_active_at"] == now - 8 * 86400
    assert not get_settings_for_user(idle_id).data_dir.exists()

    overview = client.get(f"{ADMIN}/overview", headers=admin).json()
    assert overview["total_users"] == overview["active_users"] == overview["new_today"] == 3
    assert overview["disabled_users"] == 0 and overview["admin_users"] == 1
    assert overview["active_7d_users"] == overview["active_sessions"] == 2
    assert overview["total_collections"] == overview["total_observations"] == 1
    assert overview["total_storage_bytes"] >= users[user_id]["storage_bytes"]
    assert overview["recent_audit_logs"] == []
    scheduler = overview["scheduler_status"]
    assert scheduler["running"] and scheduler["interval_seconds"] == 60
    assert datetime.fromisoformat(scheduler["started_at"]) < datetime.fromisoformat(scheduler["next_run_at"])


def test_admin_collection_history_joins_scoped_snapshots_and_preserves_orphan_jobs(admin_api):
    client, admin, repository = admin_api
    user_id, _ = register(client, "history_user")
    other_id, _ = register(client, "other_user")
    seed_job(user_id, "shared_job", collection_id="distinct_collection", status="failed")
    seed_job(user_id, "job_only", status="queued")
    seed_job(other_id, "shared_job", status="completed")
    repository.enable_live_schedule(user_id, "distinct_collection", "Hà Nội", 1, int(time.time()))
    rows = client.get(f"{ADMIN}/collection-history", params={"user_id": user_id}, headers=admin).json()["history"]
    assert len(rows) == 2
    by_job = {row["job_id"]: row for row in rows}
    assert by_job["shared_job"] == {
        "user_id": user_id, "username": "history_user", "job_id": "shared_job", "source": "open_meteo",
        "variable": VARIABLE, "start_date": "2025-01-01", "end_date": "2025-01-01", "frequency": "hourly",
        "status": "failed", "progress": 0, "created_at": START.isoformat(),
        "completed_at": (START + timedelta(seconds=30.5)).isoformat(),
        "updated_at": (START + timedelta(seconds=30.5)).isoformat(), "error": "provider error",
        "place_name": "Hà Nội", "latitude": POINT.latitude, "longitude": POINT.longitude,
        "dataset": "test-dataset", "variables": [VARIABLE, "total_precipitation"], "variables_count": 2,
        "collection_status": "failed", "expected_count": 2, "available_count": 1, "coverage": 0.5,
        "imported_values": 1, "quality_issues_count": 1, "duration_seconds": 30.5, "has_live_schedule": True,
    }
    orphan = by_job["job_only"]
    assert orphan["place_name"] is orphan["coverage"] is orphan["duration_seconds"] is None
    assert orphan["variables"] == [] and not orphan["has_live_schedule"]
    assert orphan["variables_count"] == orphan["imported_values"] == orphan["quality_issues_count"] == 0
    all_rows = client.get(f"{ADMIN}/collection-history", headers=admin).json()["history"]
    assert len(all_rows) == 3
    assert next(row for row in all_rows if row["user_id"] == other_id)["has_live_schedule"] is False


def test_admin_collection_history_totals_count_jobs_beyond_both_history_limits(admin_api):
    client, admin, _ = admin_api
    user_ids = []
    for index in range(6):
        user_id, _ = register(client, f"totals_user_{index}")
        user_ids.append(user_id)
        db_path = get_settings_for_user(user_id).job_metadata_db_path
        JobRepository(db_path)
        statuses = ["running", "failed", "cancelled", "queued"] + ["completed"] * 197
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.executemany(
                """INSERT INTO download_jobs (
                       job_id, status, created_at, updated_at, variable, start_date, end_date,
                       north, west, south, east, frequency, progress
                   ) VALUES (?, ?, ?, ?, ?, '2025-01-01', '2025-01-01', 0, 0, 0, 0, 'hourly', 0)""",
                [
                    (f"job_{job_index}", status, (START + timedelta(minutes=job_index)).isoformat(),
                     START.isoformat(), VARIABLE)
                    for job_index, status in enumerate(statuses)
                ],
            )

    response = client.get(f"{ADMIN}/collection-history", headers=admin)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["history"]) == 1000
    assert payload["totals"] == {
        "total_jobs": 1206, "completed_jobs": 1182, "running_jobs": 6,
        "failed_jobs": 6, "cancelled_jobs": 6,
    }

    scoped = client.get(f"{ADMIN}/collection-history", params={"user_id": user_ids[0]}, headers=admin).json()
    assert len(scoped["history"]) == 200
    assert all(row["user_id"] == user_ids[0] for row in scoped["history"])
    assert scoped["totals"] == {
        "total_jobs": 201, "completed_jobs": 197, "running_jobs": 1,
        "failed_jobs": 1, "cancelled_jobs": 1,
    }


@pytest.mark.parametrize("action", ["revoke-sessions", "reset-password", "disable", "update-password"])
def test_admin_handoff_is_invalidated_by_account_security_changes(admin_api, action):
    client, admin, _ = admin_api
    username = "handoff_admin"
    created = client.post(f"{AUTH}/users", json={**credentials(username), "is_admin": True}, headers=admin)
    assert created.status_code == 201
    user_path = f"{AUTH}/users/{created.json()['id']}"
    target = bearer(client.post(f"{AUTH}/login", json=credentials(username)))
    issued = client.post(f"{ADMIN}/handoff", headers=target)
    assert issued.status_code == 200
    ticket = issued.json()["ticket"]

    if action == "revoke-sessions":
        assert client.post(f"{user_path}/revoke-sessions", headers=admin).json() == {"revoked": 1}
    elif action == "reset-password":
        reset = client.post(
            f"{user_path}/reset-password",
            json={"password": "a different secure password", "revoke_sessions": False},
            headers=admin,
        )
        assert reset.status_code == 200
        assert reset.json()["revoked"] == 0
        assert client.get(f"{AUTH}/me", headers=target).status_code == 200
    elif action == "disable":
        assert client.patch(user_path, json={"is_active": False}, headers=admin).status_code == 200
        assert client.patch(user_path, json={"is_active": True}, headers=admin).status_code == 200
    else:
        get_auth_service().set_admin_password(username, "a different secure password")

    assert client.post(f"{ADMIN}/handoff/redeem", json={"ticket": ticket}).status_code == 401


def test_admin_account_actions_and_audit(admin_api):
    client, admin, repository = admin_api
    _, user_headers = register(client, "regular_user")
    payload = {**credentials("Another_Admin"), "is_admin": True}
    assert client.post(f"{AUTH}/users", json=payload, headers=user_headers).status_code == 403
    for username in ("FIXTURE_ADMIN", "REGULAR_USER"):
        assert client.post(f"{AUTH}/users", json=credentials(username), headers=admin).status_code == 409
    created = client.post(f"{AUTH}/users", json=payload, headers=admin)
    assert created.status_code == 201
    target = created.json()
    user_id = target["id"]
    assert target["username"] == "another_admin" and target["is_admin"] is True
    assert repository.count_active_sessions(int(time.time()), user_id) == 0
    login = client.post(f"{AUTH}/login", json=credentials(target["username"]))
    assert login.json()["is_admin"] is True
    original_session = bearer(login)
    reset = f"{AUTH}/users/{user_id}/reset-password"
    new_password = "a different secure password"
    assert client.post(reset, json={"password": "short"}, headers=admin).status_code == 422
    assert client.post(reset, json={"password": new_password, "revoke_sessions": False}, headers=admin).json()["revoked"] == 0
    assert client.get(f"{AUTH}/me", headers=original_session).status_code == 200
    assert client.post(f"{AUTH}/login", json=credentials(target["username"])).status_code == 401
    next_session = bearer(client.post(f"{AUTH}/login", json=credentials(target["username"], new_password)))
    old_hash = repository.get_user_by_username(target["username"])["password_hash"]
    assert client.post(reset, json={"password": PASSWORD}, headers=admin).json()["revoked"] == 2
    now = int(time.time())
    assert not repository.create_session("stale-login", user_id, now, now + 60, verified_password_hash=old_hash)
    for session in (original_session, next_session):
        assert client.get(f"{AUTH}/me", headers=session).status_code == 401
    renewed = bearer(client.post(f"{AUTH}/login", json=credentials(target["username"])))
    revoke = f"{AUTH}/users/{user_id}/revoke-sessions"
    assert client.post(revoke, headers=admin).json() == {"revoked": 1}
    assert client.post(revoke, headers=admin).json() == {"revoked": 0}
    assert client.get(f"{AUTH}/me", headers=renewed).status_code == 401
    assert client.patch(f"{AUTH}/users/{user_id}", json={"is_admin": False}, headers=admin).status_code == 200
    assert client.delete(f"{AUTH}/users/{user_id}", headers=admin).status_code == 200
    assert client.post(revoke, headers=admin).status_code == 404
    assert client.post(reset, json={"password": PASSWORD}, headers=admin).status_code == 404

    assert client.get(f"{ADMIN}/audit-logs", headers=user_headers).status_code == 403
    logs = client.get(f"{ADMIN}/audit-logs", headers=admin).json()["logs"]
    assert [row["action"] for row in logs] == [
        "delete_user", "update_user", "revoke_sessions", "revoke_sessions", "reset_password", "reset_password", "create_user",
    ]
    assert all(row["actor_username"] == "fixture_admin" and row["target_id"] == str(user_id) for row in logs)
    assert all(PASSWORD not in row["detail"] and new_password not in row["detail"] for row in logs)
    limited = client.get(f"{ADMIN}/audit-logs", params={"limit": 1}, headers=admin).json()["logs"]
    assert [row["action"] for row in limited] == ["list_audit_logs"]
    for index in range(16):
        repository.record_audit_log(None, "system", "test_event", "test", str(index), now=int(time.time()) + 60)
    overview = client.get(f"{ADMIN}/overview", headers=admin).json()
    assert [row["target_id"] for row in overview["recent_audit_logs"]] == [str(index) for index in range(15, 0, -1)]


def test_admin_cancels_jobs_and_deletes_collection_in_target_account(admin_api):
    client, admin, repository = admin_api
    user_id, user_headers = register(client, "target_user")
    other_id, _ = register(client, "other_user")
    seed_job(user_id, "queued_job", collection_id="queued_job", status="queued")
    seed_job(user_id, "running_job", collection_id="running_job", status="running")
    seed_job(other_id, "queued_job", status="queued")
    cancel = f"{ADMIN}/jobs/{user_id}/queued_job/cancel"
    delete = f"{ADMIN}/collections/{user_id}/queued_job"
    assert client.post(cancel, headers=user_headers).status_code == 403
    assert client.delete(delete, headers=admin).status_code == 409
    assert client.post(f"{ADMIN}/jobs/{user_id}/missing/cancel", headers=admin).status_code == 404
    assert client.post(cancel, headers=admin).json()["status"] == "cancelled"
    assert client.post(f"{ADMIN}/jobs/{user_id}/running_job/cancel", headers=admin).json()["status"] == "cancelling"
    repository.enable_live_schedule(user_id, "queued_job", "Hà Nội", 1, int(time.time()))
    deleted = client.delete(delete, headers=admin)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["job_id"] == "queued_job"
    assert client.delete(delete, headers=admin).status_code == 404
    settings = get_settings_for_user(user_id)
    assert CollectionRepository(settings.climate_data_db_path, read_only=True).get("queued_job") is None
    assert JobRepository(settings.job_metadata_db_path).get("queued_job") is None
    assert JobRepository(get_settings_for_user(other_id).job_metadata_db_path).get("queued_job").status == "queued"
    assert repository.list_live_schedules(int(time.time()), user_id=user_id) == []
    assert [row["action"] for row in repository.list_audit_logs()] == ["delete_collection", "cancel_job", "cancel_job"]


def test_admin_live_actions_and_cleanup(admin_api, monkeypatch):
    client, admin, repository = admin_api
    user_id, user_headers = register(client, "live_user")
    other_id, _ = register(client, "other_user")
    seed_job(user_id, "weather_job", collection_id="weather_collection", status="completed")
    seed_job(user_id, "unsupported_job", collection_id="unsupported", source="mock", status="completed")
    enable = f"{LIVE}/{user_id}/weather_collection/enable"
    refresh = f"{LIVE}/{user_id}/weather_collection/refresh"
    assert client.post(enable, json={}, headers=user_headers).status_code == 403
    assert client.post(f"{LIVE}/{user_id}/unsupported/enable", json={}, headers=admin).status_code == 422
    assert client.post(f"{LIVE}/{user_id}/missing/enable", json={}, headers=admin).status_code == 404
    assert client.post(enable, json={"interval_hours": 2}, headers=admin).status_code == 422
    for index in range(MAX_ACTIVE_LIVE_SCHEDULES):
        repository.enable_live_schedule(user_id, f"other-{index}", "Hà Nội", 1, int(time.time()))
    assert client.post(enable, json={}, headers=admin).status_code == 409
    assert client.post(f"{LIVE}/{user_id}/other-0/disable", headers=admin).status_code == 200
    assert client.post(enable, json={"interval_hours": 6}, headers=admin).json()["effective_interval_hours"] == 6
    monkeypatch.setattr(live_refresh_service, "latest_weather_hour", lambda: START + timedelta(hours=1))
    activity_before = next(row["last_active_at"] for row in repository.list_users() if row["id"] == user_id)
    assert client.post(refresh, headers=admin).json()["status"] == "up_to_date"
    assert next(row["last_active_at"] for row in repository.list_users() if row["id"] == user_id) == activity_before

    now = int(time.time())
    repository.enable_live_schedule(other_id, "other_schedule", "Hà Nội", 1, now)
    repository.update_live_schedule(user_id, "manual", "Hà Nội", False, 1, now)
    disabled = client.post(f"{LIVE}/disable-all", params={"user_id": user_id}, headers=admin)
    assert disabled.json() == {"disabled": MAX_ACTIVE_LIVE_SCHEDULES}
    assert repository.get_live_schedule(other_id, "other_schedule", now)["enabled"]
    assert client.post(f"{LIVE}/disable-all", headers=admin).json() == {"disabled": 1}
    assert client.delete(f"{LIVE}/cleanup", headers=admin).json() == {"deleted": 1}
    schedules = client.get(LIVE, headers=admin).json()["schedules"]
    assert len(schedules) == MAX_ACTIVE_LIVE_SCHEDULES + 2
    assert all(row["disabled_reason"] == "disabled_by_admin" and row["next_refresh_at"] is None for row in schedules)
    assert [row["action"] for row in repository.list_audit_logs()] == [
        "cleanup_live_schedules", "disable_all_live_schedules", "disable_all_live_schedules",
        "refresh_live_schedule", "enable_live_schedule", "disable_live_schedule",
    ]


def test_scheduler_health_records_last_tick_and_recovers_after_error(admin_api, monkeypatch):
    client, admin, repository = admin_api
    user_id, _ = register(client, "scheduler_user")
    now = int(time.time())
    for collection in ("failed", "healthy"):
        repository.enable_live_schedule(user_id, collection, "Hà Nội", 1, now)

    def refresh(_repository, _user_id, collection, *, automatic, now):
        assert automatic
        if collection == "failed":
            raise RuntimeError("weather service unavailable")
        return {"status": "updated"}

    monkeypatch.setattr(live_refresh_service, "refresh_collection", refresh)
    live_refresh_service.run_live_schedules(repository, now=now)
    health = client.get(f"{ADMIN}/overview", headers=admin).json()["scheduler_status"]
    assert health["last_tick_at"] == datetime.fromtimestamp(now, UTC).isoformat()
    assert health["last_refreshed_count"] == 1
    assert health["last_error"] == "weather service unavailable"
    repository.disable_all_live_schedules(now)
    live_refresh_service.run_live_schedules(repository, now=now + 1)
    health = live_refresh_service.get_scheduler_health()
    assert health["last_tick_at"] == datetime.fromtimestamp(now + 1, UTC).isoformat()
    assert health["last_refreshed_count"] == 0 and health["last_error"] is None
