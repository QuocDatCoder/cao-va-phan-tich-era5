from datetime import date
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain.schemas import DownloadRequest, Location
from backend.app.main import create_app
from backend.app.services.dependencies import _get_auth_repository
from backend.app.storage.collection_repository import CollectionRepository
from backend.app.storage.job_repository import JobRepository


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    database_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_DB_PATH", str(database_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLIMATE_DATA_DB_PATH", str(tmp_path / "legacy" / "climate.sqlite3"))
    monkeypatch.setenv("JOB_METADATA_DB_PATH", str(tmp_path / "legacy" / "jobs.sqlite3"))
    monkeypatch.setenv("CLIMATE_PROVIDER", "mock")
    get_settings.cache_clear()
    _get_auth_repository.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client, database_path
    finally:
        _get_auth_repository.cache_clear()
        get_settings.cache_clear()


def test_registration_login_and_protected_api(api_client):
    client, database_path = api_client
    registration = client.post(
        "/api/v1/auth/register",
        json={"username": "Alice_1", "password": "correct horse battery"},
    )

    assert registration.status_code == 201
    account = registration.json()
    assert account["username"] == "alice_1"
    token = account["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/synchronization/config").status_code == 401
    assert client.get("/api/v1/auth/me", headers=headers).json() == {
        "id": 1,
        "username": "alice_1",
    }
    assert client.get("/api/v1/synchronization/config", headers=headers).status_code == 200

    with sqlite3.connect(database_path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            ("alice_1",),
        ).fetchone()[0]
        stored_token_hash = connection.execute(
            "SELECT token_hash FROM auth_sessions"
        ).fetchone()[0]
    assert password_hash != "correct horse battery"
    assert password_hash.startswith("$argon2id$")
    assert stored_token_hash != token

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": "ALICE_1", "password": "another secure password"},
    )
    assert duplicate.status_code == 409

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "ALICE_1", "password": "correct horse battery"},
    )
    assert login.status_code == 200
    assert login.json()["username"] == "alice_1"
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "alice_1", "password": "incorrect password"},
    ).status_code == 401

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_accounts_only_list_their_collections_and_legacy_data_stays_archived(api_client):
    client, _ = api_client

    def account(username):
        registration = client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "correct horse battery"},
        )
        assert registration.status_code == 201
        token = registration.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        user = client.get("/api/v1/auth/me", headers=headers).json()
        return user["id"], headers

    first_id, first_headers = account("first_user")
    _, second_headers = account("second_user")
    settings = get_settings()

    def add_collection(path, collection_id):
        repository = CollectionRepository(path)
        with repository._connect() as connection:
            connection.execute(
                """INSERT INTO collections (id, job_id, metadata, status, created_at)
                   VALUES (?, ?, '{}', 'complete', '2025-01-01T00:00:00')""",
                (collection_id, f"job-{collection_id}"),
            )

    def add_job(path, job_id):
        JobRepository(path).create(
            job_id,
            DownloadRequest(
                source="mock",
                variable="temperature",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 1),
                location=Location(latitude=10.0, longitude=106.0),
            ),
        )

    add_collection(settings.climate_data_db_path, "legacy-collection")
    add_job(settings.job_metadata_db_path, "legacy-job")
    add_collection(
        get_settings_for_user(first_id).climate_data_db_path,
        "first-private-collection",
    )
    add_job(get_settings_for_user(first_id).job_metadata_db_path, "first-private-job")

    first_collections = client.get(
        "/api/v1/collections", headers=first_headers
    ).json()["collections"]
    second_collections = client.get(
        "/api/v1/collections", headers=second_headers
    ).json()["collections"]
    first_jobs = client.get("/api/v1/jobs", headers=first_headers).json()["jobs"]
    second_jobs = client.get("/api/v1/jobs", headers=second_headers).json()["jobs"]

    assert [item["id"] for item in first_collections] == ["first-private-collection"]
    assert second_collections == []
    assert [job["job_id"] for job in first_jobs] == ["first-private-job"]
    assert second_jobs == []
    assert [
        item["id"]
        for item in CollectionRepository(
            settings.climate_data_db_path, read_only=True
        ).list()
    ] == ["legacy-collection"]
    assert [job.job_id for job in JobRepository(settings.job_metadata_db_path).list_jobs()] == [
        "legacy-job"
    ]


@pytest.mark.parametrize(
    "username,password",
    [
        ("ab", "long enough password"),
        ("bad name", "long enough password"),
        ("valid_name", "short"),
    ],
)
def test_registration_rejects_invalid_credentials(api_client, username, password):
    client, _ = api_client
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 422
