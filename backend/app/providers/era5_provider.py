from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil, floor
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import (
    ClimatePoint,
    DownloadArea,
    DownloadArtifact,
    Location,
    TimeSeriesResponse,
)
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.errors import (
    ClimateConfigurationError,
    ClimateDataError,
    ClimateProviderError,
    UnsupportedVariableError,
)


@dataclass(frozen=True)
class Era5Variable:
    cds_name: str
    data_names: tuple[str, ...]
    unit: str
    scale: float = 1.0


ERA5_VARIABLES: dict[str, Era5Variable] = {
    "temperature": Era5Variable("2m_temperature", ("t2m", "2m_temperature"), "K"),
    "2m_temperature": Era5Variable("2m_temperature", ("t2m", "2m_temperature"), "K"),
    "dewpoint": Era5Variable(
        "2m_dewpoint_temperature",
        ("d2m", "2m_dewpoint_temperature"),
        "K",
    ),
    "2m_dewpoint_temperature": Era5Variable(
        "2m_dewpoint_temperature",
        ("d2m", "2m_dewpoint_temperature"),
        "K",
    ),
    "precipitation": Era5Variable(
        "total_precipitation",
        ("tp", "total_precipitation"),
        "mm",
        scale=1000.0,
    ),
    "total_precipitation": Era5Variable(
        "total_precipitation",
        ("tp", "total_precipitation"),
        "mm",
        scale=1000.0,
    ),
}

ERA5_REGULAR_GRID_DEGREES = 0.25


class ERA5ClimateProvider(ClimateDataProvider):
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._default_client_factory

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

        variable_spec = self._get_variable(variable)
        self._validate_time_range(start=start, end=end)
        target_path = self._target_path(
            variable=variable,
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
        )

        if not target_path.exists():
            self._download(
                variable_spec=variable_spec,
                latitude=latitude,
                longitude=longitude,
                start=start,
                end=end,
                target_path=target_path,
            )

        points = self._read_timeseries(
            file_path=target_path,
            variable_spec=variable_spec,
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
        )

        return TimeSeriesResponse(
            variable=variable,
            unit=variable_spec.unit,
            location=Location(latitude=latitude, longitude=longitude),
            data=points,
        )

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
            raise ClimateDataError("Only hourly ERA5 downloads are supported.")

        variable_spec = self._get_variable(variable)
        self._validate_time_range(
            start=datetime.combine(start_date, time.min),
            end=datetime.combine(end_date, time.max),
        )
        target_path = self._area_target_path(
            variable=variable_spec.cds_name,
            area=area,
            start_date=start_date,
            end_date=end_date,
        )

        if not target_path.exists():
            request = self._build_area_request(
                variable_spec=variable_spec,
                area=area,
                start_date=start_date,
                end_date=end_date,
            )
            self._retrieve(request=request, target_path=target_path)

        return DownloadArtifact(
            file_path=target_path,
            storage_path=target_path.relative_to(self._settings.data_dir).as_posix(),
            dataset=self._settings.era5_dataset,
            variable=variable_spec.cds_name,
        )

    def _get_variable(self, variable: str) -> Era5Variable:
        try:
            return ERA5_VARIABLES[variable]
        except KeyError as exc:
            supported = ", ".join(sorted(ERA5_VARIABLES))
            raise UnsupportedVariableError(
                f"Unsupported variable '{variable}'. Supported variables: {supported}."
            ) from exc

    def _validate_time_range(self, start: datetime, end: datetime) -> None:
        # A Vietnam day crosses two UTC dates; limit elapsed time, not date count.
        if end - start >= timedelta(days=self._settings.era5_max_days_per_request):
            raise ClimateDataError(
                "ERA5 requests are limited to "
                f"{self._settings.era5_max_days_per_request} days for Phase 4."
            )
        latest_available = self._latest_available_date()
        if end.date() > latest_available:
            raise ClimateDataError(
                "ERA5 data is not normally available for the requested end date "
                f"{end.date().isoformat()}. Latest configured available date is "
                f"{latest_available.isoformat()} based on "
                f"ERA5_SYNC_AVAILABILITY_LAG_DAYS={self._settings.era5_sync_availability_lag_days}. "
                "Choose an earlier end date or adjust the configured lag if needed."
            )

    def _download(
        self,
        variable_spec: Era5Variable,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
        target_path: Path,
    ) -> None:
        self._ensure_configured()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        request = self._build_request(
            variable_spec=variable_spec,
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
        )

        try:
            self._retrieve(request=request, target_path=target_path)
        except ClimateProviderError:
            raise

    def _ensure_configured(self) -> None:
        if not self._settings.cds_url or not self._settings.cds_api_key:
            raise ClimateConfigurationError(
                "ERA5 provider requires CDS_URL and CDS_API_KEY environment variables."
            )

    def _build_request(
        self,
        variable_spec: Era5Variable,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        dates = self._date_parts(start=start, end=end)
        return {
            "product_type": ["reanalysis"],
            "variable": [variable_spec.cds_name],
            "date": dates,
            "time": [f"{hour:02d}:00" for hour in range(24)],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": self._area_for_point(latitude=latitude, longitude=longitude),
        }

    def _build_area_request(
        self,
        variable_spec: Era5Variable,
        area: DownloadArea,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        return {
            "product_type": ["reanalysis"],
            "variable": [variable_spec.cds_name],
            "date": self._date_parts(
                start=datetime.combine(start_date, time.min),
                end=datetime.combine(end_date, time.min),
            ),
            "time": [f"{hour:02d}:00" for hour in range(24)],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": [
                round(area.north, 4),
                round(area.west, 4),
                round(area.south, 4),
                round(area.east, 4),
            ],
        }

    def _retrieve(self, request: dict[str, Any], target_path: Path) -> None:
        self._ensure_configured()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            client = self._client_factory(self._settings)
            client.retrieve(self._settings.era5_dataset, request, str(target_path))
        except ClimateProviderError:
            raise
        except Exception as exc:
            raise ClimateProviderError(
                f"ERA5 data request failed: {self._safe_provider_error(exc)}"
            ) from exc

        if not target_path.exists():
            raise ClimateDataError("ERA5 request completed but no NetCDF file was created.")

    def _latest_available_date(self) -> date:
        return datetime.now(UTC).date() - timedelta(
            days=self._settings.era5_sync_availability_lag_days
        )

    def _safe_provider_error(self, exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        if self._settings.cds_api_key:
            message = message.replace(self._settings.cds_api_key, "<redacted>")
        return message

    def _date_parts(self, start: datetime, end: datetime) -> list[str]:
        days = np.arange(
            np.datetime64(start.date()),
            np.datetime64(end.date()) + np.timedelta64(1, "D"),
            np.timedelta64(1, "D"),
        )
        return [str(day) for day in days]

    def _area_for_point(self, latitude: float, longitude: float) -> list[float]:
        buffer = max(self._settings.era5_point_buffer_degrees, ERA5_REGULAR_GRID_DEGREES)
        north = min(90.0, self._round_up_to_grid(latitude + buffer))
        south = max(-90.0, self._round_down_to_grid(latitude - buffer))
        west = max(-180.0, self._round_down_to_grid(longitude - buffer))
        east = min(180.0, self._round_up_to_grid(longitude + buffer))
        return [round(north, 4), round(west, 4), round(south, 4), round(east, 4)]

    def _round_down_to_grid(self, value: float) -> float:
        return floor(value / ERA5_REGULAR_GRID_DEGREES) * ERA5_REGULAR_GRID_DEGREES

    def _round_up_to_grid(self, value: float) -> float:
        return ceil(value / ERA5_REGULAR_GRID_DEGREES) * ERA5_REGULAR_GRID_DEGREES

    def _read_timeseries(
        self,
        file_path: Path,
        variable_spec: Era5Variable,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> list[ClimatePoint]:
        if not file_path.exists():
            raise ClimateDataError("NetCDF file does not exist.")

        try:
            with xr.open_dataset(file_path) as dataset:
                return self._extract_points(
                    dataset=dataset,
                    variable_spec=variable_spec,
                    latitude=latitude,
                    longitude=longitude,
                    start=start,
                    end=end,
                )
        except ClimateProviderError:
            raise
        except Exception as exc:
            raise ClimateDataError("Unable to read ERA5 NetCDF data.") from exc

    def _extract_points(
        self,
        dataset: xr.Dataset,
        variable_spec: Era5Variable,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> list[ClimatePoint]:
        data_var = self._find_data_variable(dataset=dataset, variable_spec=variable_spec)
        time_coord = self._find_coordinate(dataset=dataset, candidates=("valid_time", "time"))
        lat_coord = self._find_coordinate(dataset=dataset, candidates=("latitude", "lat"))
        lon_coord = self._find_coordinate(dataset=dataset, candidates=("longitude", "lon"))

        requested_lon = self._normalize_longitude(dataset[lon_coord].values, longitude)
        selected = dataset[data_var].sel(
            {lat_coord: latitude, lon_coord: requested_lon},
            method="nearest",
        )
        selected = selected.sel({time_coord: slice(np.datetime64(start), np.datetime64(end))})
        selected = selected.squeeze(drop=False)

        if time_coord not in selected.coords:
            raise ClimateDataError("ERA5 result is missing a time coordinate.")

        time_values = np.atleast_1d(selected[time_coord].values)
        data_values = np.atleast_1d(selected.values)
        if len(time_values) == 0 or len(data_values) == 0:
            raise ClimateDataError("ERA5 result is empty for the requested subset.")

        points: list[ClimatePoint] = []
        for time_value, raw_value in zip(time_values, data_values):
            value = float(raw_value)
            if np.isnan(value):
                continue
            points.append(
                ClimatePoint(
                    time=self._to_datetime(time_value),
                    value=round(value * variable_spec.scale, 6),
                )
            )

        if not points:
            raise ClimateDataError("ERA5 result contains no valid data values.")
        return points

    def _find_data_variable(self, dataset: xr.Dataset, variable_spec: Era5Variable) -> str:
        for name in variable_spec.data_names:
            if name in dataset.data_vars:
                return name
        raise ClimateDataError(
            "ERA5 NetCDF file is missing the expected variable "
            f"({', '.join(variable_spec.data_names)})."
        )

    def _find_coordinate(self, dataset: xr.Dataset, candidates: tuple[str, ...]) -> str:
        for name in candidates:
            if name in dataset.coords or name in dataset.dims:
                return name
        raise ClimateDataError(
            f"ERA5 NetCDF file is missing expected coordinate: {', '.join(candidates)}."
        )

    def _normalize_longitude(self, dataset_longitudes: np.ndarray, longitude: float) -> float:
        lon_min = float(np.nanmin(dataset_longitudes))
        lon_max = float(np.nanmax(dataset_longitudes))
        if lon_min >= 0 and longitude < 0:
            return longitude % 360
        if lon_max <= 180 and longitude > 180:
            return ((longitude + 180) % 360) - 180
        return longitude

    def _to_datetime(self, value: Any) -> datetime:
        return np.datetime64(value, "us").astype(datetime)

    def _target_path(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> Path:
        safe_variable = variable.replace("/", "_")
        filename = (
            f"{safe_variable}_{start:%Y%m%d%H}_{end:%Y%m%d%H}_"
            f"lat{latitude:.4f}_lon{longitude:.4f}.nc"
        )
        return self._settings.raw_era5_dir / safe_variable / filename

    def _area_target_path(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
    ) -> Path:
        safe_variable = variable.replace("/", "_")
        filename = (
            f"{start_date.isoformat()}_{end_date.isoformat()}_"
            f"{self._bbox_slug(area)}.nc"
        )
        return self._settings.raw_era5_dir / safe_variable / str(start_date.year) / filename

    def _bbox_slug(self, area: DownloadArea) -> str:
        return (
            f"bbox_s{self._safe_number(area.south)}_w{self._safe_number(area.west)}_"
            f"n{self._safe_number(area.north)}_e{self._safe_number(area.east)}"
        )

    def _safe_number(self, value: float) -> str:
        return f"{value:.4f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")

    def _default_client_factory(self, settings: Settings) -> Any:
        import cdsapi

        return cdsapi.Client(url=settings.cds_url, key=settings.cds_api_key)
