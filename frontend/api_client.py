from dataclasses import dataclass
from typing import Any

import requests


class DashboardApiError(Exception):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class DashboardApiClient:
    base_url: str = "http://localhost:8000"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    session: requests.Session | None = None
    source: str | None = None
    stored_only: bool = False
    access_token: str | None = None

    def register(self, username: str, password: str) -> dict[str, Any]:
        return self._post(
            "/api/v1/auth/register",
            {"username": username, "password": password},
            authenticated=False,
        )

    def login(self, username: str, password: str) -> dict[str, Any]:
        return self._post(
            "/api/v1/auth/login",
            {"username": username, "password": password},
            authenticated=False,
        )

    def logout(self) -> dict[str, Any]:
        return self._post("/api/v1/auth/logout", {})

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def agent_status(self):
        return self._get('/api/v1/agent/status')

    def agent_chat(self, payload):
        return self._post('/api/v1/agent/chat', payload)

    def resolve_agent_action(self,payload):
        return self._post('/api/v1/agent/resolve-action',payload)

    def get_timeseries(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/timeseries", params=params)

    def get_statistics(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/statistics", params=params)

    def get_aggregate(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/aggregate", params=params)

    def get_trend(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/trend", params=params)

    def get_anomaly(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/anomaly", params=params)

    def get_compare(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/compare", params=params)

    def get_heatmap(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/heatmap", params=params)

    def get_map(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/map", params=params)

    def create_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/downloads", payload)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/jobs/{job_id}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._post(f'/api/v1/jobs/{job_id}/cancel', {})

    def get_job_quality(self, job_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return self._get(f"/api/v1/jobs/{job_id}/quality", params={"limit": limit, "offset": offset})

    def list_jobs(self, limit=100, offset=0):
        return self._get("/api/v1/jobs", params={"limit": limit, "offset": offset})["jobs"]

    def get_all_job_quality(self, job_id):
        first = self.get_job_quality(job_id, limit=500)
        issues = list(first['issues'])
        while len(issues) < first['total']:
            page = self.get_job_quality(job_id, offset=len(issues), limit=500)
            if not page['issues'] or page['total'] != first['total']:
                raise DashboardApiError("Báo cáo đang thay đổi; hãy đợi tác vụ kết thúc rồi tải lại.")
            issues.extend(page['issues'])
        return {**first, "issues": issues, "limit": len(issues), "offset": 0}

    def get_catalog(self, limit=100, offset=0):
        if self.source not in {"land", "era5", "mock"}:
            raise DashboardApiError("Hãy chọn nguồn dữ liệu.")
        return self._get("/api/v1/stored/climate/catalog", params={"source": self.source, "limit": limit, "offset": offset})

    def list_collections(self, search='', limit=100, offset=0):
        return self._get('/api/v1/collections', params=dict(search=search, limit=limit, offset=offset))

    def get_collection(self, collection_id):
        return self._get(f'/api/v1/collections/{collection_id}')

    def delete_collection(self, collection_id):
        try:
            headers = self._auth_headers()
            request_options = {"timeout": self.timeout_seconds}
            if headers is not None:
                request_options["headers"] = headers
            response = self._session().delete(
                self._url(f'/api/v1/collections/{collection_id}'), **request_options)
        except requests.Timeout as exc:
            raise DashboardApiError(self._timeout_message()) from exc
        except requests.RequestException as exc:
            raise DashboardApiError('Không kết nối được tới máy chủ API.') from exc
        return self._decode_response(response)

    def query_collection(self, collection_id, operation, params):
        return self._get(f'/api/v1/collections/{collection_id}/{operation}', params=params)

    def collection_quality(self, collection_id):
        path = f'/api/v1/collections/{collection_id}/quality'
        result = self._get(path, params=dict(limit=500, offset=0))
        while len(result['issues']) < result['total']:
            page = self._get(path, params=dict(limit=500, offset=len(result['issues'])))
            if not page['issues'] or page['total'] != result['total']:
                raise DashboardApiError('Báo cáo đang cập nhật. Hãy thử lại khi tác vụ kết thúc.')
            result['issues'].extend(page['issues'])
        return result

    def get_sync_config(self) -> dict[str, Any]:
        return self._get("/api/v1/synchronization/config")

    def list_synchronizations(self) -> dict[str, Any]:
        return self._get("/api/v1/synchronization")

    def run_synchronization(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/synchronization/run", payload)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.stored_only and path.startswith("/api/v1/climate/"):
            if self.source not in {"land", "era5", "mock"}:
                raise DashboardApiError("Hãy chọn nguồn dữ liệu cho truy vấn database.")
            path = path.replace("/api/v1/climate/", "/api/v1/stored/climate/", 1)
            params = {**(params or {}), "source": self.source}
        try:
            request_options = {"params": params, "timeout": self.timeout_seconds}
            headers = self._auth_headers()
            if headers is not None:
                request_options["headers"] = headers
            response = self._session().get(self._url(path), **request_options)
        except requests.Timeout as exc:
            raise DashboardApiError(self._timeout_message()) from exc
        except requests.RequestException as exc:
            raise DashboardApiError("Không kết nối được tới máy chủ API.") from exc
        return self._decode_response(response)

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if self.stored_only and path == "/api/v1/downloads":
            if self.source not in {"land", "era5", "mock"}:
                raise DashboardApiError("Hãy chọn nguồn dữ liệu trước khi thu thập.")
            payload = {**payload, "source": self.source}
        try:
            request_options = {"json": payload, "timeout": self.timeout_seconds}
            headers = self._auth_headers() if authenticated else None
            if headers is not None:
                request_options["headers"] = headers
            response = self._session().post(self._url(path), **request_options)
        except requests.Timeout as exc:
            raise DashboardApiError(self._timeout_message()) from exc
        except requests.RequestException as exc:
            raise DashboardApiError("Không kết nối được tới máy chủ API.") from exc
        return self._decode_response(response)

    def _decode_response(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DashboardApiError("Máy chủ API trả về phản hồi không hợp lệ.") from exc

        if not response.ok:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, list):
                detail = "Yêu cầu không hợp lệ. Vui lòng kiểm tra thông tin đã nhập."
            if not isinstance(detail, str) or not detail:
                detail = f"Yêu cầu tới máy chủ API thất bại với mã {response.status_code}."
            raise DashboardApiError(detail, status_code=response.status_code)

        if not isinstance(payload, dict):
            raise DashboardApiError("Máy chủ API trả về phản hồi ngoài dự kiến.")
        return payload

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _session(self) -> requests.Session:
        return self.session or requests.Session()

    def _auth_headers(self) -> dict[str, str] | None:
        if not self.access_token:
            return None
        return {"Authorization": f"Bearer {self.access_token}"}

    def _timeout_message(self) -> str:
        return (
            f"Yêu cầu tới máy chủ API đã quá thời gian chờ sau {self.timeout_seconds:g} giây. "
            "Yêu cầu ERA5 lần đầu có thể lâu hơn; hãy tăng API_TIMEOUT_SECONDS "
            "hoặc tạo tác vụ tải dữ liệu trước."
        )
