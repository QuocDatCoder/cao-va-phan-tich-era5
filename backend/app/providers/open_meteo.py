"""Hourly weather model data from Open-Meteo Historical Forecast API."""
from datetime import date, datetime, timedelta, timezone
import json
from math import cos, isfinite, radians, sin
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from backend.app.domain.cancellation import check_cancelled
from backend.app.domain.quality import current_quality
from backend.app.domain.recent_weather import GFS_FIRST_DATE, historical_date_bounds
from backend.app.domain.schemas import ClimatePoint, DownloadArtifact, Location, TimeSeriesResponse
from backend.app.domain.sources import OPEN_METEO_BEST_MATCH_DATASET, source_dataset
from backend.app.domain.wind import WIND_COMPONENTS
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.era5_provider import ERA5_VARIABLES
from backend.app.providers.errors import ClimateDataError, ClimateProviderError, UnsupportedVariableError

FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
LIVE_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = {
    "2m_temperature": ("temperature_2m", "°C", 1),
    "total_precipitation": ("precipitation", "mm", 1),
    "2m_dewpoint_temperature": ("dew_point_2m", "°C", 1),
    "surface_solar_radiation_downwards": ("shortwave_radiation", "W/m²", .0036),
    "wind_speed": ("wind_speed_10m", "m/s", 1),
    "wind_direction": ("wind_direction_10m", "°", 1),
}
OUTPUT_UNITS = {**{name: unit for name, (_, unit, _) in FIELDS.items()},
                "2m_temperature": "K", "2m_dewpoint_temperature": "K",
                "surface_solar_radiation_downwards": "MJ/m²",
                **dict.fromkeys(WIND_COMPONENTS, "m/s")}


class OpenMeteoProvider(ClimateDataProvider):
    def __init__(self, settings, repository):
        self._settings = settings
        self._repository = repository
        self._dataset = source_dataset("open_meteo", settings)

    def download_area(self, variable, area, start_date, end_date, frequency):
        raise ClimateDataError("Open-Meteo thu thập theo một tọa độ.")

    def _fetch(self, location, start_date, end_date, *, live_interval=None, refresh_archive=False, model="gfs_global"):
        today = datetime.now(timezone.utc).date()
        earliest, latest = historical_date_bounds()
        first = GFS_FIRST_DATE if refresh_archive else earliest - timedelta(days=1)
        if live_interval is None and not first <= start_date <= end_date <= min(today, latest):
            raise ClimateDataError("Chọn khoảng thời gian trong 5 năm đã qua.")
        url = LIVE_FORECAST_URL if live_interval else FORECAST_URL
        params = dict(latitude=location.latitude, longitude=location.longitude,
                      start_date=start_date.isoformat(), end_date=end_date.isoformat(),
                      hourly=",".join(field for field, _, _ in FIELDS.values()),
                      timezone="GMT", wind_speed_unit="ms")
        if model is not None:
            params["models"] = model
        check_cancelled()
        try:
            with urlopen(f"{url}?{urlencode(params)}", timeout=30) as response:
                payload = json.load(response)
        except (OSError, URLError, ValueError) as exc:
            raise ClimateProviderError("Không lấy được dữ liệu từ Open-Meteo. Vui lòng thử lại.") from exc
        check_cancelled()
        try:
            grid = Location(latitude=payload["latitude"], longitude=payload["longitude"])
            hourly, units = payload["hourly"], payload["hourly_units"]
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
            lower = datetime.combine(start_date, datetime.min.time())
            if live_interval:
                lower, cutoff = (stamp.astimezone(timezone.utc).replace(tzinfo=None) for stamp in live_interval)
            times = [datetime.fromisoformat(value) for value in hourly["time"]]
            series = {}
            for name, (field, unit, scale) in FIELDS.items():
                values = hourly[field]
                if len(values) != len(times) or units[field] != unit:
                    raise ValueError(field)
                offset = 273.15 if unit == "°C" else 0
                series[name] = [ClimatePoint(time=stamp, value=value * scale + offset)
                                for stamp, value in zip(times, values)
                                if lower <= stamp <= cutoff and value is not None and isfinite(value)]
            directions = {point.time: point.value for point in series["wind_direction"]}
            for name, projection in zip(WIND_COMPONENTS, (sin, cos)):
                series[name] = [
                    ClimatePoint(time=point.time, value=-point.value * projection(radians(directions[point.time])))
                    for point in series["wind_speed"] if point.time in directions
                ]
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise ClimateDataError("Open-Meteo trả về dữ liệu không hợp lệ hoặc thiếu biến.") from exc
        if any(not values for values in series.values()):
            raise ClimateDataError("Open-Meteo chưa có đủ biến trong khoảng ngày đã chọn.")
        return payload, grid, series

    def fetch_refresh(self, location, first_hour, last_hour, latest, *, dataset):
        """Read the archive up to the UTC day containing today's Vietnam midnight."""
        recent_date = latest.replace(hour=0).astimezone(timezone.utc).date()
        first_date, last_date = first_hour.date(), last_hour.date()
        model = None if dataset == OPEN_METEO_BEST_MATCH_DATASET else "gfs_global"
        if first_date < recent_date:
            _, grid, series = self._fetch(
                location, first_date, min(last_date, recent_date - timedelta(days=1)),
                refresh_archive=True, model=model,
            )
            yield grid, series
        if last_date >= recent_date:
            start_date = max(first_date, recent_date)
            start = max(first_hour, datetime.combine(start_date, datetime.min.time()))
            _, grid, series = self._fetch(
                location, start_date, last_date,
                live_interval=(start.replace(tzinfo=timezone.utc), last_hour.replace(tzinfo=timezone.utc)),
                model=model,
            )
            yield grid, series

    def download_point(self, variable, location, start_date: date, end_date: date, frequency, *, live_interval=None):
        payload, grid, series = self._fetch(location, start_date, end_date, live_interval=live_interval)
        path = self._settings.data_dir / "raw" / "open_meteo" / f"{uuid4().hex}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        quality = current_quality.get()
        if quality is not None:
            quality.report.rows_read = len(payload["hourly"]["time"])
            quality.report.valid_values = sum(len(series[name]) for name in FIELDS)
            for name, (field, _, _) in FIELDS.items():
                for timestamp, value in zip(payload["hourly"]["time"], payload["hourly"][field]):
                    if live_interval:
                        stamp = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)
                        if not live_interval[0] <= stamp <= live_interval[1]:
                            continue
                    if value is None or not isfinite(value):
                        quality.issue("missing_hour", "Open-Meteo không có dữ liệu cho giờ này.",
                                      source_file=str(path), variable=name, time_utc=datetime.fromisoformat(timestamp))
        for name, points in series.items():
            check_cancelled()
            self._repository.save(dataset=self._dataset, variable=name, requested_location=location,
                                  grid_location=grid, unit=OUTPUT_UNITS[name], points=points)
            if quality is not None:
                quality.report.imported_values += len(points)
        return DownloadArtifact(file_path=path, storage_path=path.relative_to(self._settings.data_dir).as_posix(),
                                dataset=self._dataset, variable=variable)

    def get_timeseries(self, variable, latitude, longitude, start, end):
        spec = ERA5_VARIABLES.get(variable)
        if spec is None or spec.cds_name not in OUTPUT_UNITS:
            raise UnsupportedVariableError(f"Unsupported variable '{variable}'.")
        name = spec.cds_name
        location = Location(latitude=latitude, longitude=longitude)
        unit = OUTPUT_UNITS[name]
        points = self._repository.get_series(dataset=self._dataset, variable=name, location=location,
                                             unit=unit, start=start, end=end)
        return TimeSeriesResponse(variable=name, unit=unit, location=location, data=points)
