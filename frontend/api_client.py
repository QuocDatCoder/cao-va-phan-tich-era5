from dataclasses import dataclass
from typing import Any

import requests


class DashboardApiError(Exception):
    pass


DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class DashboardApiClient:
    base_url: str = "http://localhost:8000"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    session: requests.Session | None = None

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def get_timeseries(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/timeseries", params=params)

    def get_statistics(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._get("/api/v1/climate/statistics", params=params)

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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._session().get(
                self._url(path),
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise DashboardApiError(self._timeout_message()) from exc
        except requests.RequestException as exc:
            raise DashboardApiError("Không kết nối được tới máy chủ API.") from exc
        return self._decode_response(response)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._session().post(
                self._url(path),
                json=payload,
                timeout=self.timeout_seconds,
            )
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
                detail = "Yêu cầu không hợp lệ. Vui lòng kiểm tra bộ lọc đã chọn."
            if not isinstance(detail, str) or not detail:
                detail = f"Yêu cầu tới máy chủ API thất bại với mã {response.status_code}."
            raise DashboardApiError(detail)

        if not isinstance(payload, dict):
            raise DashboardApiError("Máy chủ API trả về phản hồi ngoài dự kiến.")
        return payload

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _session(self) -> requests.Session:
        return self.session or requests.Session()

    def _timeout_message(self) -> str:
        return (
            f"Yêu cầu tới máy chủ API đã quá thời gian chờ sau {self.timeout_seconds:g} giây. "
            "Yêu cầu ERA5 lần đầu có thể lâu hơn; hãy tăng API_TIMEOUT_SECONDS "
            "hoặc tạo tác vụ tải dữ liệu trước."
        )
