from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil, floor
from pathlib import Path
import logging
import json
import sqlite3
import threading
from uuid import uuid4
from backend.app.providers.cds_retry import CDSSession, CancellableCDSClient, retrieve_with_retry
from backend.app.domain.cancellation import check_cancelled
from typing import Any

import numpy as np
import xarray as xr

from backend.app.domain.quality import current_quality
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
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.domain.time_utils import (
    batch_missing_ranges, hourly_timestamps, missing_hour_ranges, request_chunks, to_utc_naive,
)
from backend.app.domain.wind import DERIVED_WIND_UNITS, WIND_COMPONENTS, derive_wind

logger = logging.getLogger(__name__)

# CDS giới hạn số request đang xếp hàng cho mỗi dataset (lỗi 400 "Maximum number queued
# requests"), nên mọi lần gọi CDS trong tiến trình này phải đi tuần tự, từng cái một.
_CDS_LOCK = threading.RLock()


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

ERA5_VARIABLES.update({
    "10m_u_component_of_wind": Era5Variable("10m_u_component_of_wind", ("u10", "10m_u_component_of_wind"), "m/s"),
    "10m_v_component_of_wind": Era5Variable("10m_v_component_of_wind", ("v10", "10m_v_component_of_wind"), "m/s"),
    "surface_solar_radiation_downwards": Era5Variable(
        "surface_solar_radiation_downwards", ("ssrd", "surface_solar_radiation_downwards"), "MJ/m²", scale=1e-6,
    ),
})
for alias, name in {"wind_u": WIND_COMPONENTS[0], "u10": WIND_COMPONENTS[0],
                    "wind_v": WIND_COMPONENTS[1], "v10": WIND_COMPONENTS[1],
                    "solar_radiation": "surface_solar_radiation_downwards",
                    "ssrd": "surface_solar_radiation_downwards"}.items():
    ERA5_VARIABLES[alias] = ERA5_VARIABLES[name]
for name, unit in DERIVED_WIND_UNITS.items():
    ERA5_VARIABLES[name] = Era5Variable(name, (), unit)

ERA5_REGULAR_GRID_DEGREES = 0.25


class ERA5ClimateProvider(ClimateDataProvider):
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[Settings], Any] | None = None,
        repository: ClimateRepository | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._default_client_factory
        self._repository = repository

    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> TimeSeriesResponse:
        start, end = to_utc_naive(start), to_utc_naive(end)
        if end < start:
            raise ValueError("end must be greater than or equal to start")

        variable_spec = self._get_variable(variable)
        self._validate_query_range(start, end)
        if not hourly_timestamps(start, end):
            raise ClimateDataError("The requested interval contains no hourly ERA5 observations.")
        if variable in DERIVED_WIND_UNITS:
            components = [self.get_timeseries(name, latitude, longitude, start, end) for name in WIND_COMPONENTS]
            try:
                return derive_wind(variable, *components)
            except ValueError as exc:
                raise ClimateDataError(str(exc)) from exc
        location = Location(latitude=latitude, longitude=longitude)
        if self._repository is None:
            points = []
            for chunk_start, chunk_end in request_chunks(start, end, self._settings.era5_max_days_per_request):
                points.extend(self._load_point_data(variable, variable_spec, location, chunk_start, chunk_end))
        else:
            try:
                points = self._get_stored_or_download(variable, variable_spec, location, start, end)
            except sqlite3.Error as exc:
                raise ClimateDataError("Unable to read or persist ERA5 climate observations.") from exc
        return TimeSeriesResponse(variable=variable, unit=variable_spec.unit, location=location, data=points)

    def _get_stored_or_download(
        self, variable: str, spec: Era5Variable, location: Location,
        start: datetime, end: datetime,
    ) -> list[ClimatePoint]:
        assert self._repository is not None
        query = dict(dataset=self._settings.era5_dataset, variable=spec.cds_name,
                     location=location, unit=spec.unit, start=start, end=end)
        points = self._repository.get_series(**query)
        gaps = missing_hour_ranges(start, end, {point.time for point in points})
        if not gaps:
            logger.info("ERA5 SQLite hit: variable=%s observations=%s", spec.cds_name, len(points))
            return points

        with _CDS_LOCK:
            # Request khác có thể đã tải xong các giờ này trong lúc mình chờ khóa.
            points = self._repository.get_series(**query)
            gaps = missing_hour_ranges(start, end, {point.time for point in points})
            if not gaps:
                return points
            # Import an existing exact-query file before planning new CDS requests.
            existing_path = self._target_path(variable, location.latitude, location.longitude, start, end)
            if existing_path.exists():
                self._load_point_data(variable, spec, location, start, end)
                points = self._repository.get_series(**query)
                gaps = missing_hour_ranges(start, end, {point.time for point in points})

            for gap_start, gap_end in batch_missing_ranges(gaps, self._settings.era5_max_days_per_request):
                logger.info("ERA5 missing range: variable=%s UTC=%s..%s", spec.cds_name, gap_start, gap_end)
                self._load_point_data(variable, spec, location, gap_start, gap_end)

            points = self._repository.get_series(**query)
            remaining = missing_hour_ranges(start, end, {point.time for point in points})
            if remaining:
                raise ClimateDataError(
                    "ERA5 data is still incomplete after downloading missing ranges. "
                    f"First missing UTC hour: {remaining[0][0].isoformat()}."
                )
            return points

    def _load_point_data(
        self, variable: str, variable_spec: Era5Variable, location: Location,
        start: datetime, end: datetime,
    ) -> list[ClimatePoint]:
        latitude, longitude = location.latitude, location.longitude
        target_path = self._target_path(
            variable=variable,
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
        )

        if not target_path.exists():
            self._validate_time_range(start, end)
            self._download(
                variable_spec=variable_spec,
                latitude=latitude,
                longitude=longitude,
                start=start,
                end=end,
                target_path=target_path,
            )

        points, grid_location = self._read_timeseries(
            file_path=target_path,
            variable_spec=variable_spec,
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
        )
        if self._repository is not None:
            self._repository.save(
                dataset=self._settings.era5_dataset, variable=variable_spec.cds_name,
                requested_location=location, grid_location=grid_location,
                unit=variable_spec.unit, points=points,
            )
            logger.info("ERA5 SQLite saved: variable=%s observations=%s", variable_spec.cds_name, len(points))

        return points

    def download_area(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
        frequency: str,
    ) -> DownloadArtifact:
        if (end_date - start_date).days + 1 > self._settings.era5_max_days_per_request:
            # Khoảng dài: các đợt con gọi lại download_area và tự khóa từng đợt.
            return self._download_area(variable, area, start_date, end_date, frequency)
        with _CDS_LOCK:
            return self._download_area(variable, area, start_date, end_date, frequency)

    def _download_area(
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
        self._validate_query_range(datetime.combine(start_date, time.min), datetime.combine(end_date, time(hour=23)))
        if (end_date - start_date).days + 1 > self._settings.era5_max_days_per_request:
            files = []
            current = start_date
            while current <= end_date:
                chunk_end = min(end_date, current + timedelta(days=self._settings.era5_max_days_per_request - 1))
                artifact = self.download_area(variable, area, current, chunk_end, frequency)
                files.append(artifact.storage_path)
                current = chunk_end + timedelta(days=1)
            manifest = self._area_target_path(variable_spec.cds_name, area, start_date, end_date).with_suffix(".manifest.json")
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({
                "dataset": self._settings.era5_dataset, "variable": variable_spec.cds_name,
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                "area": area.model_dump(), "files": files,
            }, indent=2), encoding="utf-8")
            return DownloadArtifact(file_path=manifest, storage_path=manifest.relative_to(self._settings.data_dir).as_posix(),
                                    dataset=self._settings.era5_dataset, variable=variable_spec.cds_name)
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

        if self._repository is not None:
            specs = [self._get_variable(name) for name in WIND_COMPONENTS] if variable in DERIVED_WIND_UNITS else [variable_spec]
            for spec in specs:
                self._import_area(target_path, spec, area, start_date, end_date)

        return DownloadArtifact(
            file_path=target_path,
            storage_path=target_path.relative_to(self._settings.data_dir).as_posix(),
            dataset=self._settings.era5_dataset,
            variable=variable_spec.cds_name,
        )

    def _import_area(
        self, file_path: Path, spec: Era5Variable, area: DownloadArea,
        start_date: date, end_date: date,
    ) -> None:
        assert self._repository is not None
        from backend.app.storage.collection_repository import record_artifact
        record_artifact(file_path, self._settings.climate_data_db_path)
        if current_quality.get() is not None:
            from backend.app.providers.quality_import import import_area
            return import_area(self, file_path, spec, area, start_date, end_date)
        try:
            with xr.open_dataset(file_path) as dataset:
                time_coord = self._find_coordinate(dataset, ("valid_time", "time"))
                lat_coord = self._find_coordinate(dataset, ("latitude", "lat"))
                lon_coord = self._find_coordinate(dataset, ("longitude", "lon"))
                dimensions = (time_coord, lat_coord, lon_coord)
                data = dataset[self._find_data_variable(dataset, spec)]
                for dimension in tuple(data.dims):
                    if dimension not in dimensions:
                        if data.sizes[dimension] != 1:
                            raise ClimateDataError("ERA5 area has an unsupported extra dimension.")
                        data = data.isel({dimension: 0}, drop=True)
                data = data.transpose(*dimensions).sortby(time_coord)
                start = datetime.combine(start_date, time.min)
                end = datetime.combine(end_date, time(hour=23))
                data = data.sel({time_coord: slice(np.datetime64(start), np.datetime64(end))})
                timestamps = [self._to_datetime(value) for value in data[time_coord].values]
                if timestamps != hourly_timestamps(start, end):
                    raise ClimateDataError("ERA5 area is missing requested hourly timestamps.")
                latitudes = [float(value) for value in data[lat_coord].values]
                longitudes = [float(value) for value in data[lon_coord].values]
                if not latitudes or not longitudes or not all(
                    np.isfinite(value) for value in latitudes + longitudes
                ) or any(abs(value) > 90 for value in latitudes):
                    raise ClimateDataError("ERA5 area contains invalid grid coordinates.")

                def records():
                    # Read one time slice at a time; do not flatten the full area into RAM.
                    for index, timestamp in enumerate(timestamps):
                        values = data.isel({time_coord: index}).values
                        for (lat_index, lon_index), raw_value in np.ndenumerate(values):
                            yield (latitudes[lat_index], (longitudes[lon_index] + 180) % 360 - 180,
                                   timestamp, round(float(raw_value) * spec.scale, 6))

                count = self._repository.save_area(
                    dataset=self._settings.era5_dataset, variable=spec.cds_name, unit=spec.unit,
                    area=area, latitudes=latitudes, longitudes=longitudes, records=records(),
                )
                logger.info("ERA5 area imported to SQLite: variable=%s observations=%s", spec.cds_name, count)
        except ClimateProviderError:
            raise
        except Exception as exc:
            raise ClimateDataError("Unable to import ERA5 area observations into SQLite.") from exc

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
        self._validate_query_range(start, end)

    def _validate_query_range(self, start: datetime, end: datetime) -> None:
        if end - start >= timedelta(days=self._settings.era5_max_query_days):
            raise ClimateDataError(f"Climate queries are limited to {self._settings.era5_max_query_days} days.")
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
        hours = range(24)
        if start.date() == end.date():
            hours = [stamp.hour for stamp in hourly_timestamps(start, end)]
        return {
            "product_type": ["reanalysis"],
            "variable": list(WIND_COMPONENTS) if variable_spec.cds_name in DERIVED_WIND_UNITS else [variable_spec.cds_name],
            "date": dates,
            "time": [f"{hour:02d}:00" for hour in hours],
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
            "variable": list(WIND_COMPONENTS) if variable_spec.cds_name in DERIVED_WIND_UNITS else [variable_spec.cds_name],
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
        check_cancelled()
        self._ensure_configured()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Preserve partial downloads separately; only publish a successful file.
            partial = target_path.with_name(uuid4().hex + '.part')
            with _CDS_LOCK:
                client = self._client_factory(self._settings)
                retrieve_with_retry(client, self._settings.era5_dataset, request, partial, self._settings)
            if not partial.is_file() or partial.stat().st_size == 0:
                quality = current_quality.get()
                if quality is not None:
                    quality.issue('empty_file', 'CDS returned no non-empty data file.', source_file=str(partial))
                raise ClimateDataError('CDS returned no non-empty data file.')
            check_cancelled()
            partial.replace(target_path)
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
    ) -> tuple[list[ClimatePoint], Location]:
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
    ) -> tuple[list[ClimatePoint], Location]:
        data_var = self._find_data_variable(dataset=dataset, variable_spec=variable_spec)
        time_coord = self._find_coordinate(dataset=dataset, candidates=("valid_time", "time"))
        lat_coord = self._find_coordinate(dataset=dataset, candidates=("latitude", "lat"))
        lon_coord = self._find_coordinate(dataset=dataset, candidates=("longitude", "lon"))

        requested_lon = self._normalize_longitude(dataset[lon_coord].values, longitude)
        selected = dataset[data_var].sel(
            {lat_coord: latitude, lon_coord: requested_lon},
            method="nearest",
        )
        grid_location = Location(
            latitude=float(selected[lat_coord].values),
            longitude=((float(selected[lon_coord].values) + 180) % 360) - 180,
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
            if not np.isfinite(value):
                continue
            points.append(
                ClimatePoint(
                    time=self._to_datetime(time_value),
                    value=round(value * variable_spec.scale, 6),
                )
            )

        if not points:
            raise ClimateDataError("ERA5 result contains no valid data values.")
        return points, grid_location

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
        return self._dataset_raw_dir() / safe_variable / filename

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
        return self._dataset_raw_dir() / safe_variable / str(start_date.year) / filename

    def _dataset_raw_dir(self) -> Path:
        # Preserve existing files for the original dataset; isolate other datasets.
        if self._settings.era5_dataset == "reanalysis-era5-single-levels":
            return self._settings.raw_era5_dir
        safe_dataset = self._settings.era5_dataset.replace("/", "_").replace("\\", "_")
        if safe_dataset in {"", ".", ".."}:
            raise ClimateConfigurationError("Invalid ERA5 dataset name.")
        return self._settings.raw_era5_dir / safe_dataset

    def _bbox_slug(self, area: DownloadArea) -> str:
        return (
            f"bbox_s{self._safe_number(area.south)}_w{self._safe_number(area.west)}_"
            f"n{self._safe_number(area.north)}_e{self._safe_number(area.east)}"
        )

    def _safe_number(self, value: float) -> str:
        return f"{value:.4f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")

    def _default_client_factory(self, settings: Settings) -> Any:
        import cdsapi

        return CancellableCDSClient(cdsapi.Client(url=settings.cds_url, key=settings.cds_api_key,
                             retry_max=1, timeout=settings.cds_request_timeout_seconds,
                             sleep_max=30, quiet=True, debug=False, session=CDSSession()))
