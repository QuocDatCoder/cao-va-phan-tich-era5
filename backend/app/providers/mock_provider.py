from datetime import date, datetime, timedelta
from math import pi, sin
from pathlib import Path

from backend.app.domain.schemas import (
    ClimatePoint,
    DownloadArea,
    DownloadArtifact,
    Location,
    TimeSeriesResponse,
)
from backend.app.providers.base import ClimateDataProvider


class MockClimateProvider(ClimateDataProvider):
    _UNITS = {
        "temperature": "K",
        "2m_temperature": "K",
        "precipitation": "mm",
        "total_precipitation": "mm",
    }
    _STORAGE_NAMES = {
        "temperature": "2m_temperature",
        "2m_temperature": "2m_temperature",
        "precipitation": "total_precipitation",
        "total_precipitation": "total_precipitation",
    }

    def __init__(self, data_dir: Path = Path("data")) -> None:
        self._data_dir = data_dir.resolve()

    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> TimeSeriesResponse:
        if end < start:
            raise ValueError("end must be greater than or equal to start")

        points: list[ClimatePoint] = []
        current = start
        while current <= end:
            points.append(
                ClimatePoint(
                    time=current,
                    value=self._value_for(variable=variable, timestamp=current),
                )
            )
            current += timedelta(hours=1)

        return TimeSeriesResponse(
            variable=variable,
            unit=self._UNITS.get(variable, "unknown"),
            location=Location(latitude=latitude, longitude=longitude),
            data=points,
        )

    def _value_for(self, variable: str, timestamp: datetime) -> float:
        hour_angle = 2 * pi * (timestamp.hour / 24)

        if variable in {"temperature", "2m_temperature"}:
            return round(299.15 + 5 * sin(hour_angle), 3)

        if variable in {"precipitation", "total_precipitation"}:
            return round(max(0.0, 2 * sin(hour_angle)), 3)

        return round(sin(hour_angle), 3)

    def download_area(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
        frequency: str,
    ) -> DownloadArtifact:
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        if frequency != "hourly":
            raise ValueError("Only hourly downloads are supported.")

        target_path = self._target_path(
            variable=variable,
            area=area,
            start_date=start_date,
            end_date=end_date,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            "\n".join(
                [
                    "mock climate download",
                    f"variable={variable}",
                    f"start_date={start_date.isoformat()}",
                    f"end_date={end_date.isoformat()}",
                    f"area={area.model_dump_json()}",
                    f"frequency={frequency}",
                ]
            ),
            encoding="utf-8",
        )
        return DownloadArtifact(
            file_path=target_path,
            storage_path=target_path.relative_to(self._data_dir).as_posix(),
            dataset="mock",
            variable=variable,
        )

    def _target_path(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
    ) -> Path:
        safe_variable = self._storage_name_for(variable)
        filename = (
            f"{start_date.isoformat()}_{end_date.isoformat()}_"
            f"{self._bbox_slug(area)}.nc"
        )
        raw_dir = (self._data_dir / "raw" / "mock").resolve()
        target_path = (raw_dir / safe_variable / str(start_date.year) / filename).resolve()
        if not target_path.is_relative_to(raw_dir):
            raise ValueError("Generated mock download path is outside the data directory.")
        return target_path

    def _storage_name_for(self, variable: str) -> str:
        try:
            return self._STORAGE_NAMES[variable]
        except KeyError as exc:
            supported = ", ".join(sorted(self._STORAGE_NAMES))
            raise ValueError(f"Unsupported variable '{variable}'. Supported variables: {supported}.") from exc

    def _bbox_slug(self, area: DownloadArea) -> str:
        return (
            f"bbox_s{self._safe_number(area.south)}_w{self._safe_number(area.west)}_"
            f"n{self._safe_number(area.north)}_e{self._safe_number(area.east)}"
        )

    def _safe_number(self, value: float) -> str:
        return f"{value:.4f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")
