import requests
import pytest

from frontend.api_client import DEFAULT_TIMEOUT_SECONDS, DashboardApiClient, DashboardApiError


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, json_error: bool = False) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response or FakeResponse({"status": "ok"})
        self.error = error
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params, timeout))
        if self.error:
            raise self.error
        return self.response

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json, timeout))
        if self.error:
            raise self.error
        return self.response


def test_client_uses_expected_endpoint_params_and_timeout() -> None:
    session = FakeSession(response=FakeResponse({"data": []}))
    client = DashboardApiClient(
        base_url="http://api.test/",
        timeout_seconds=3.5,
        session=session,
    )

    response = client.get_timeseries({"variable": "temperature"})

    assert response == {"data": []}
    assert session.calls == [
        ("GET", "http://api.test/api/v1/climate/timeseries", {"variable": "temperature"}, 3.5)
    ]


def test_client_posts_download_job_payload() -> None:
    session = FakeSession(response=FakeResponse({"job_id": "job-1", "status": "queued"}, 202))
    client = DashboardApiClient(base_url="http://api.test", timeout_seconds=2, session=session)
    payload = {"variable": "temperature"}

    response = client.create_download(payload)

    assert response["job_id"] == "job-1"
    assert session.calls == [("POST", "http://api.test/api/v1/downloads", payload, 2)]


def test_client_gets_job_status() -> None:
    session = FakeSession(response=FakeResponse({"job_id": "job-1", "status": "completed"}))
    client = DashboardApiClient(base_url="http://api.test", timeout_seconds=2, session=session)

    response = client.get_job("job-1")

    assert response["status"] == "completed"
    assert session.calls == [("GET", "http://api.test/api/v1/jobs/job-1", None, 2)]


def test_client_reads_sync_status_and_starts_sync():
    session = FakeSession()
    client = DashboardApiClient(base_url="http://api.test", session=session)
    client.get_sync_config()
    client.list_synchronizations()
    client.run_synchronization({"variable": "temperature"})
    assert [call[1] for call in session.calls] == [
        "http://api.test/api/v1/synchronization/config",
        "http://api.test/api/v1/synchronization",
        "http://api.test/api/v1/synchronization/run",
    ]


def test_client_raises_user_friendly_error_for_backend_4xx() -> None:
    session = FakeSession(response=FakeResponse({"detail": "invalid latitude"}, 422))
    client = DashboardApiClient(session=session)

    with pytest.raises(DashboardApiError, match="invalid latitude"):
        client.get_statistics({})


def test_client_raises_user_friendly_error_for_backend_5xx_without_detail() -> None:
    session = FakeSession(response=FakeResponse({}, 500))
    client = DashboardApiClient(session=session)

    with pytest.raises(DashboardApiError, match="mã 500"):
        client.get_statistics({})


def test_client_handles_connection_error() -> None:
    client = DashboardApiClient(session=FakeSession(error=requests.ConnectionError("no route")))

    with pytest.raises(DashboardApiError, match="Không kết nối"):
        client.health()


def test_client_handles_timeout() -> None:
    client = DashboardApiClient(session=FakeSession(error=requests.Timeout("slow")))

    with pytest.raises(DashboardApiError, match="quá thời gian chờ"):
        client.health()


def test_client_default_timeout_allows_slow_era5_requests() -> None:
    client = DashboardApiClient()

    assert client.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert client.timeout_seconds >= 180


def test_client_handles_malformed_json() -> None:
    client = DashboardApiClient(session=FakeSession(response=FakeResponse({}, json_error=True)))

    with pytest.raises(DashboardApiError, match="phản hồi không hợp lệ"):
        client.health()


def test_stored_client_explicit_source_routes_to_db_only_endpoints():
    session = FakeSession(response=FakeResponse({"data": []}))
    client = DashboardApiClient(base_url="http://api.test", session=session, source="land", stored_only=True)
    client.get_statistics({"variable": "temperature"})
    client.create_download({"variable": "temperature", "location": {"latitude": 10, "longitude": 106}})
    assert session.calls[0][1] == "http://api.test/api/v1/stored/climate/statistics"
    assert session.calls[0][2] == {"variable": "temperature", "source": "land"}
    assert session.calls[1][2]["source"] == "land"


def test_stored_client_requires_a_source_and_does_not_fall_back_to_legacy():
    session = FakeSession()
    with pytest.raises(DashboardApiError, match="nguồn dữ liệu"):
        DashboardApiClient(session=session, stored_only=True).get_timeseries({})
    assert session.calls == []
