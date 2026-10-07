from datetime import date
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.app.core.config import Settings
from backend.app.domain.schemas import (
    DownloadArea,
    DownloadArtifact,
    DownloadRequest,
    SynchronizationRequest,
    SynchronizationStatus,
    TimeSeriesResponse,
)
from backend.app.main import app
from backend.app.providers.base import ClimateDataProvider
from backend.app.services.dependencies import get_synchronization_service
from backend.app.services.download_job_service import DownloadJobService
from backend.app.services.synchronization_scheduler import SynchronizationScheduler
from backend.app.services.synchronization_service import (
    LatestAvailableDateProvider,
    SynchronizationService,
)
from backend.app.storage.job_repository import JobRepository
from backend.app.storage.synchronization_repository import SynchronizationRepository


class RecordingProvider(ClimateDataProvider):
    def __init__(self, file_path: Path, fail: bool = False) -> None:
        self.file_path = file_path
        self.fail = fail
        self.calls: list[DownloadRequest] = []

    def get_timeseries(self, *args, **kwargs) -> TimeSeriesResponse:
        raise NotImplementedError

    def download_area(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
        frequency: str,
    ) -> DownloadArtifact:
        if self.fail:
            raise RuntimeError("provider unavailable")
        request = DownloadRequest(
            variable=variable,
            area=area,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
        )
        self.calls.append(request)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("downloaded", encoding="utf-8")
        return DownloadArtifact(
            file_path=self.file_path,
            storage_path="raw/mock/2m_temperature/file.nc",
            dataset="mock",
            variable=variable,
        )


class FixedLatestAvailableDateProvider(LatestAvailableDateProvider):
    def __init__(self, latest: date) -> None:
        self.latest = latest

    def latest_available_date(self) -> date:
        return self.latest


def make_sync_request(latest: date | None = date(2025, 1, 3)) -> SynchronizationRequest:
    return SynchronizationRequest(
        variable="2m_temperature",
        start_date=date(2025, 1, 1),
        area=DownloadArea(north=11, west=106, south=10, east=107),
        latest_available_date=latest,
    )


def make_service(
    tmp_path: Path,
    provider: RecordingProvider | None = None,
    latest: date = date(2025, 1, 3),
    max_days_per_run: int = 7,
) -> tuple[SynchronizationService, RecordingProvider]:
    provider = provider or RecordingProvider(tmp_path / "raw" / "file.nc")
    job_repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    download_service = DownloadJobService(provider=provider, repository=job_repository)
    sync_repository = SynchronizationRepository(db_path=tmp_path / "jobs.sqlite3")
    service = SynchronizationService(
        download_service=download_service,
        repository=sync_repository,
        latest_available_provider=FixedLatestAvailableDateProvider(latest),
        max_days_per_run=max_days_per_run,
    )
    return service, provider


def make_components(
    tmp_path: Path,
    provider: RecordingProvider | None = None,
    latest: date = date(2025, 1, 3),
    max_days_per_run: int = 7,
) -> tuple[
    SynchronizationService,
    RecordingProvider,
    DownloadJobService,
    JobRepository,
    SynchronizationRepository,
]:
    provider = provider or RecordingProvider(tmp_path / "raw" / "file.nc")
    job_repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    download_service = DownloadJobService(provider=provider, repository=job_repository)
    sync_repository = SynchronizationRepository(db_path=tmp_path / "jobs.sqlite3")
    service = SynchronizationService(
        download_service=download_service,
        repository=sync_repository,
        latest_available_provider=FixedLatestAvailableDateProvider(latest),
        max_days_per_run=max_days_per_run,
    )
    return service, provider, download_service, job_repository, sync_repository


def test_synchronization_queues_and_runs_missing_range(tmp_path: Path) -> None:
    service, provider = make_service(tmp_path)

    state = service.synchronize(make_sync_request())
    completed = service.run_synchronization(state.sync_id)

    assert state.status == SynchronizationStatus.QUEUED
    assert state.job_start_date == date(2025, 1, 1)
    assert state.job_end_date == date(2025, 1, 3)
    assert completed.status == SynchronizationStatus.COMPLETED
    assert completed.last_synced_date == date(2025, 1, 3)
    assert provider.calls[0].start_date == date(2025, 1, 1)
    assert provider.calls[0].end_date == date(2025, 1, 3)


def test_synchronization_reuses_in_flight_duplicate_request(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    first = service.synchronize(make_sync_request())
    second = service.synchronize(make_sync_request())

    assert second.sync_id == first.sync_id
    assert second.job_id == first.job_id
    assert second.status == SynchronizationStatus.QUEUED


def test_synchronization_reuses_genuinely_running_download_job(tmp_path: Path) -> None:
    service, provider, _, job_repository, sync_repository = make_components(tmp_path)
    first = service.synchronize(make_sync_request())
    assert first.job_id is not None
    sync_repository.mark_running(first.sync_id)
    job_repository.mark_running(first.job_id)

    second = service.synchronize(make_sync_request(date(2025, 1, 5)))

    assert second.sync_id == first.sync_id
    assert second.job_id == first.job_id
    assert second.status == SynchronizationStatus.RUNNING
    assert provider.calls == []


def test_synchronization_avoids_duplicate_download_after_completion(tmp_path: Path) -> None:
    service, provider = make_service(tmp_path)
    state = service.synchronize(make_sync_request())
    service.run_synchronization(state.sync_id)

    repeated = service.synchronize(make_sync_request())

    assert repeated.status == SynchronizationStatus.UP_TO_DATE
    assert repeated.last_synced_date == date(2025, 1, 3)
    assert len(provider.calls) == 1


def test_synchronization_fetches_only_new_dates_after_previous_completion(tmp_path: Path) -> None:
    service, provider = make_service(tmp_path)
    state = service.synchronize(make_sync_request(date(2025, 1, 3)))
    service.run_synchronization(state.sync_id)

    next_state = service.synchronize(make_sync_request(date(2025, 1, 5)))
    service.run_synchronization(next_state.sync_id)

    assert next_state.job_start_date == date(2025, 1, 4)
    assert next_state.job_end_date == date(2025, 1, 5)
    assert provider.calls[1].start_date == date(2025, 1, 4)
    assert provider.calls[1].end_date == date(2025, 1, 5)


def test_stale_running_sync_reconciles_completed_job_and_queues_next_missing_chunk(
    tmp_path: Path,
) -> None:
    service, provider, download_service, _, sync_repository = make_components(tmp_path)
    state = service.synchronize(make_sync_request(date(2025, 1, 3)))
    assert state.job_id is not None
    sync_repository.mark_running(state.sync_id)
    download_service.run_job(state.job_id)

    next_state = service.synchronize(make_sync_request(date(2025, 1, 5)))

    assert next_state.status == SynchronizationStatus.QUEUED
    assert next_state.last_synced_date == date(2025, 1, 3)
    assert next_state.job_start_date == date(2025, 1, 4)
    assert next_state.job_end_date == date(2025, 1, 5)
    assert provider.calls == [
        DownloadRequest(
            variable="2m_temperature",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 3),
            area=make_sync_request().area,
            frequency="hourly",
        )
    ]


def test_synchronization_chunks_large_missing_range_without_overreporting_coverage(
    tmp_path: Path,
) -> None:
    service, provider = make_service(tmp_path, max_days_per_run=2)

    state = service.synchronize(make_sync_request(date(2025, 1, 5)))
    completed = service.run_synchronization(state.sync_id)

    assert state.latest_available_date == date(2025, 1, 5)
    assert state.job_start_date == date(2025, 1, 1)
    assert state.job_end_date == date(2025, 1, 2)
    assert completed.last_synced_date == date(2025, 1, 2)
    assert provider.calls[0].start_date == date(2025, 1, 1)
    assert provider.calls[0].end_date == date(2025, 1, 2)


def test_failed_synchronization_does_not_advance_last_synced_date(tmp_path: Path) -> None:
    provider = RecordingProvider(tmp_path / "raw" / "file.nc", fail=True)
    service, _ = make_service(tmp_path, provider=provider)

    state = service.synchronize(make_sync_request())
    failed = service.run_synchronization(state.sync_id)

    assert failed.status == SynchronizationStatus.FAILED
    assert failed.last_synced_date is None
    assert failed.error == "provider unavailable"


def test_stale_running_sync_reconciles_failed_job_without_advancing_coverage(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(tmp_path / "raw" / "file.nc", fail=True)
    service, _, download_service, _, sync_repository = make_components(tmp_path, provider=provider)
    state = service.synchronize(make_sync_request())
    assert state.job_id is not None
    sync_repository.mark_running(state.sync_id)
    download_service.run_job(state.job_id)

    failed = service.synchronize(make_sync_request(date(2025, 1, 5)))

    assert failed.status == SynchronizationStatus.FAILED
    assert failed.last_synced_date is None
    assert failed.error == "provider unavailable"

    retry = service.synchronize(make_sync_request(date(2025, 1, 5)))
    assert retry.status == SynchronizationStatus.QUEUED
    assert retry.job_start_date == date(2025, 1, 1)
    assert retry.job_end_date == date(2025, 1, 5)


def test_stale_queued_sync_with_missing_job_is_marked_failed(tmp_path: Path) -> None:
    service, _, _, _, sync_repository = make_components(tmp_path)
    state = service.synchronize(make_sync_request())
    assert state.job_id is not None
    sync_repository.mark_running(state.sync_id)
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        connection.execute(
            "UPDATE synchronization_states SET job_id = ? WHERE sync_id = ?",
            ("missing-job", state.sync_id),
        )

    failed = service.synchronize(make_sync_request(date(2025, 1, 5)))

    assert failed.status == SynchronizationStatus.FAILED
    assert failed.last_synced_date is None
    assert "not found" in (failed.error or "")


def test_completion_persistence_failure_does_not_record_synced_coverage(tmp_path: Path) -> None:
    service, _, _, _, sync_repository = make_components(tmp_path)
    state = service.synchronize(make_sync_request())
    original_mark_completed = sync_repository.mark_completed

    def fail_completion(**kwargs) -> None:
        raise RuntimeError("sync metadata write failed")

    sync_repository.mark_completed = fail_completion  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="sync metadata write failed"):
        service.run_synchronization(state.sync_id)

    reloaded = SynchronizationRepository(db_path=tmp_path / "jobs.sqlite3").get(state.sync_id)
    assert reloaded is not None
    assert reloaded.last_synced_date is None
    assert reloaded.status == SynchronizationStatus.RUNNING

    sync_repository.mark_completed = original_mark_completed  # type: ignore[method-assign]
    recovered = service.synchronize(make_sync_request(date(2025, 1, 5)))

    assert recovered.status == SynchronizationStatus.QUEUED
    assert recovered.last_synced_date == date(2025, 1, 3)
    assert recovered.job_start_date == date(2025, 1, 4)
    assert recovered.job_end_date == date(2025, 1, 5)


def test_synchronization_endpoint_queues_job_and_background_completes(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    client = TestClient(app)
    app.dependency_overrides[get_synchronization_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/synchronization/run",
            json={
                "variable": "2m_temperature",
                "start_date": "2025-01-01",
                "latest_available_date": "2025-01-03",
                "area": {"north": 11, "west": 106, "south": 10, "east": 107},
                "frequency": "hourly",
            },
        )
        sync_id = response.json()["sync_id"]
        status_response = client.get(f"/api/v1/synchronization/status/{sync_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    assert status_response.json()["last_synced_date"] == "2025-01-03"


def test_synchronization_endpoint_rejects_invalid_date_order(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    client = TestClient(app)
    app.dependency_overrides[get_synchronization_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/synchronization/run",
            json={
                "variable": "2m_temperature",
                "start_date": "2025-01-03",
                "latest_available_date": "2025-01-01",
                "area": {"north": 11, "west": 106, "south": 10, "east": 107},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_synchronization_status_endpoint_returns_404_for_unknown_id(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    client = TestClient(app)
    app.dependency_overrides[get_synchronization_service] = lambda: service
    try:
        response = client.get("/api/v1/synchronization/status/missing")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_scheduler_starts_and_stops_without_running_live_cds(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    settings = Settings(
        data_dir=tmp_path,
        job_metadata_db_path=tmp_path / "jobs.sqlite3",
        era5_sync_enabled=True,
        era5_sync_interval_hours=1,
    )
    scheduler = SynchronizationScheduler(settings=settings, service=service)

    scheduler.start()
    assert scheduler._scheduler.running

    scheduler.shutdown()
    assert not scheduler._scheduler.running
