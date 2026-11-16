from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.domain.schemas import (
    DownloadArea,
    DownloadArtifact,
    DownloadJobStatus,
    DownloadRequest,
    TimeSeriesResponse,
)
from backend.app.main import app
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.services.dependencies import get_download_job_service
from backend.app.services.download_job_service import (
    DownloadJobService,
    JobNotFoundError,
    JobPersistenceError,
)
from backend.app.storage.job_repository import JobRepository


class SuccessfulProvider(ClimateDataProvider):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
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
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("downloaded", encoding="utf-8")
        self.calls.append(
            DownloadRequest(
                variable=variable,
                area=area,
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
            )
        )
        return DownloadArtifact(
            file_path=self.file_path,
            storage_path="raw/era5/2m_temperature/file.nc",
            dataset="test-dataset",
            variable=variable,
        )


class FailingProvider(SuccessfulProvider):
    def download_area(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
        frequency: str,
    ) -> DownloadArtifact:
        raise RuntimeError("CDS rejected request for the selected range")


class ObservingProvider(SuccessfulProvider):
    def __init__(self, file_path: Path, on_download) -> None:
        super().__init__(file_path=file_path)
        self._on_download = on_download

    def download_area(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
        frequency: str,
    ) -> DownloadArtifact:
        self._on_download()
        return super().download_area(
            variable=variable,
            area=area,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
        )


class CompletionFailingRepository:
    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    def create(self, job_id: str, request: DownloadRequest):
        return self._repository.create(job_id=job_id, request=request)

    def get(self, job_id: str):
        return self._repository.get(job_id)

    def mark_running(self, job_id: str):
        return self._repository.mark_running(job_id)

    def mark_completed(self, job_id: str, artifact: DownloadArtifact):
        raise RuntimeError("database write failed during completion")

    def mark_failed(self, job_id: str, error: str):
        return self._repository.mark_failed(job_id=job_id, error=error)


class FailurePersistenceFailingRepository(CompletionFailingRepository):
    def mark_failed(self, job_id: str, error: str):
        raise RuntimeError("database write failed during failure")


def make_request() -> DownloadRequest:
    return DownloadRequest(
        variable="2m_temperature",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
        area=DownloadArea(north=11, west=106, south=10, east=107),
        frequency="hourly",
    )


def make_service(tmp_path: Path, provider: ClimateDataProvider | None = None) -> DownloadJobService:
    repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    return DownloadJobService(
        provider=provider or SuccessfulProvider(tmp_path / "raw" / "file.nc"),
        repository=repository,
    )


def test_download_request_rejects_invalid_date_range() -> None:
    with pytest.raises(ValueError):
        DownloadRequest(
            variable="2m_temperature",
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 1),
            area=DownloadArea(north=11, west=106, south=10, east=107),
        )


def test_download_area_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        DownloadArea(north=10, west=106, south=11, east=107)


def test_download_job_service_creates_queued_job(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    response = service.create_job(make_request())
    job = service.get_job(response.job_id)

    assert response.status == DownloadJobStatus.QUEUED
    assert job.status == DownloadJobStatus.QUEUED
    assert job.progress == 0
    assert job.file_path is None


def test_download_job_service_runs_successful_job_and_persists_metadata(tmp_path: Path) -> None:
    provider = SuccessfulProvider(tmp_path / "raw" / "file.nc")
    repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    service = DownloadJobService(provider=provider, repository=repository)

    response = service.create_job(make_request())
    service.run_job(response.job_id)

    job = service.get_job(response.job_id)
    reloaded = JobRepository(db_path=tmp_path / "jobs.sqlite3").get(response.job_id)
    assert job.status == DownloadJobStatus.COMPLETED
    assert job.progress == 100
    assert job.dataset == "test-dataset"
    assert job.file_path == "raw/era5/2m_temperature/file.nc"
    assert job.completed_at is not None
    assert reloaded == job
    assert provider.calls[0].area == make_request().area


def test_download_job_service_transitions_through_running(tmp_path: Path) -> None:
    repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    observed_statuses: list[DownloadJobStatus] = []

    def observe_running() -> None:
        job = repository.get(created.job_id)
        assert job is not None
        observed_statuses.append(job.status)

    provider = ObservingProvider(tmp_path / "raw" / "file.nc", on_download=observe_running)
    service = DownloadJobService(provider=provider, repository=repository)
    created = service.create_job(make_request())

    service.run_job(created.job_id)

    assert observed_statuses == [DownloadJobStatus.RUNNING]
    assert service.get_job(created.job_id).status == DownloadJobStatus.COMPLETED


def test_download_job_service_marks_failed_job_with_safe_error(tmp_path: Path) -> None:
    service = make_service(tmp_path, provider=FailingProvider(tmp_path / "raw" / "file.nc"))

    response = service.create_job(make_request())
    service.run_job(response.job_id)

    job = service.get_job(response.job_id)
    assert job.status == DownloadJobStatus.FAILED
    assert job.error == "CDS rejected request for the selected range"
    assert "Traceback" not in job.error


def test_download_job_service_marks_failed_when_completion_metadata_update_fails(
    tmp_path: Path,
) -> None:
    repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    service = DownloadJobService(
        provider=SuccessfulProvider(tmp_path / "raw" / "file.nc"),
        repository=CompletionFailingRepository(repository),
    )

    response = service.create_job(make_request())
    service.run_job(response.job_id)

    job = repository.get(response.job_id)
    assert job is not None
    assert job.status == DownloadJobStatus.FAILED
    assert job.error is not None
    assert "completion metadata" in job.error


def test_download_job_service_raises_when_failure_status_cannot_be_persisted(
    tmp_path: Path,
) -> None:
    repository = JobRepository(db_path=tmp_path / "jobs.sqlite3")
    service = DownloadJobService(
        provider=SuccessfulProvider(tmp_path / "raw" / "file.nc"),
        repository=FailurePersistenceFailingRepository(repository),
    )

    response = service.create_job(make_request())
    with pytest.raises(JobPersistenceError):
        service.run_job(response.job_id)

    job = repository.get(response.job_id)
    assert job is not None
    assert job.status == DownloadJobStatus.RUNNING


def test_download_job_service_does_not_rerun_failed_job(tmp_path: Path) -> None:
    service = make_service(tmp_path, provider=FailingProvider(tmp_path / "raw" / "file.nc"))
    response = service.create_job(make_request())
    service.run_job(response.job_id)

    service.run_job(response.job_id)

    assert service.get_job(response.job_id).status == DownloadJobStatus.FAILED


def test_download_job_service_raises_for_unknown_job(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(JobNotFoundError):
        service.get_job("missing")


def test_mock_provider_download_area_writes_deterministic_artifact(tmp_path: Path) -> None:
    provider = MockClimateProvider(data_dir=tmp_path)

    artifact = provider.download_area(
        variable="2m_temperature",
        area=DownloadArea(north=11, west=106, south=10, east=107),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2),
        frequency="hourly",
    )

    assert artifact.file_path.exists()
    assert artifact.storage_path == (
        "raw/mock/2m_temperature/2025/"
        "2025-01-01_2025-01-02_bbox_s10_w106_n11_e107.nc"
    )
    assert artifact.file_path.resolve().is_relative_to((tmp_path / "raw" / "mock").resolve())


@pytest.mark.parametrize(
    "variable",
    [
        "../escape",
        "..\\escape",
        "C:\\temp\\escape",
        "/tmp/escape",
    ],
)
def test_mock_provider_download_area_rejects_path_like_variables(
    tmp_path: Path,
    variable: str,
) -> None:
    provider = MockClimateProvider(data_dir=tmp_path)

    with pytest.raises(ValueError):
        provider.download_area(
            variable=variable,
            area=DownloadArea(north=11, west=106, south=10, east=107),
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 2),
            frequency="hourly",
        )

    assert not (tmp_path / "escape").exists()


def test_create_download_endpoint_returns_job_id_and_background_completes(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    client = TestClient(app)
    app.dependency_overrides[get_download_job_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/downloads",
            json={
                "variable": "2m_temperature",
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "area": {"north": 11, "west": 106, "south": 10, "east": 107},
                "frequency": "hourly",
            },
        )
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert service.get_job(payload["job_id"]).status == DownloadJobStatus.COMPLETED


def test_get_job_endpoint_returns_existing_job(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    created = service.create_job(make_request())
    service.run_job(created.job_id)
    client = TestClient(app)
    app.dependency_overrides[get_download_job_service] = lambda: service
    try:
        response = client.get(f"/api/v1/jobs/{created.job_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == created.job_id
    assert payload["status"] == "completed"
    assert payload["file_path"] == "raw/era5/2m_temperature/file.nc"
    assert "cds_api_key" not in payload


def test_get_job_endpoint_returns_404_for_unknown_job(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    client = TestClient(app)
    app.dependency_overrides[get_download_job_service] = lambda: service
    try:
        response = client.get("/api/v1/jobs/missing")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_create_download_endpoint_rejects_invalid_request(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    client = TestClient(app)
    app.dependency_overrides[get_download_job_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/downloads",
            json={
                "variable": "2m_temperature",
                "start_date": "2025-01-02",
                "end_date": "2025-01-01",
                "area": {"north": 11, "west": 106, "south": 10, "east": 107},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_existing_climate_endpoint_still_works(tmp_path, monkeypatch) -> None:
    from backend.app.services.dependencies import get_climate_service
    from backend.app.services.climate_service import ClimateService
    from backend.app.providers.mock_provider import MockClimateProvider
    monkeypatch.setitem(app.dependency_overrides, get_climate_service,
                        lambda: ClimateService(MockClimateProvider(tmp_path)))
    client = TestClient(app)
    response = client.get(
        "/api/v1/climate/timeseries",
        params={
            "variable": "temperature",
            "latitude": 10.8231,
            "longitude": 106.6297,
            "start": datetime(2025, 1, 1, 0).isoformat(),
            "end": datetime(2025, 1, 1, 1).isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json()["variable"] == "temperature"
