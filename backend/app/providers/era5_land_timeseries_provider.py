"""Provider cho dataset ``reanalysis-era5-land-timeseries`` (dataset trong slide môn học).

Khác với provider ERA5 single-levels:
* Mỗi request chỉ trả MỘT điểm (``location``), xuất CSV (thường nằm trong file zip).
* Một request lấy được khoảng dài và nhiều biến cùng lúc, nên mỗi lần tải ta xin cả 6 biến
  và lưu hết vào SQLite; các biến còn lại được truy vấn sau đó không cần gọi CDS nữa.
* Mưa/bức xạ đã được ECMWF tách tích lũy về từng giờ, nên không cần cộng dồn thêm.

Kế thừa ERA5ClimateProvider để dùng lại: alias biến, tính gió từ u/v, đổi đơn vị,
phát hiện giờ còn thiếu, SQLite, khóa CDS tuần tự và giới hạn khoảng truy vấn.
"""
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from math import isfinite
from pathlib import Path
import csv
import json
import logging
import zipfile
from typing import Any

from backend.app.core.config import Settings
from backend.app.domain.schemas import (
    ClimatePoint,
    DownloadArea,
    DownloadArtifact,
    Location,
)
from backend.app.domain.time_utils import to_utc_naive
from backend.app.domain.wind import DERIVED_WIND_UNITS
from backend.app.providers.era5_provider import (
    _CDS_LOCK,
    ERA5_VARIABLES,
    ERA5ClimateProvider,
    Era5Variable,
)
from backend.app.providers.errors import ClimateDataError
from backend.app.storage.climate_repository import ClimateRepository

logger = logging.getLogger(__name__)

# Các biến gốc (không gồm tốc độ/hướng gió, vì chúng được tính từ u/v).
BASE_VARIABLES: dict[str, Era5Variable] = {
    spec.cds_name: spec
    for name, spec in ERA5_VARIABLES.items()
    if name not in DERIVED_WIND_UNITS
}

TIME_COLUMNS = ("valid_time", "time", "datetime", "date")
LAT_COLUMNS = ("latitude", "lat")
LON_COLUMNS = ("longitude", "lon")
TEMPERATURE_VARIABLES = ("2m_temperature", "2m_dewpoint_temperature")
ALL_VARIABLES_NAME = "all_variables"


class _BrokenDownload(ClimateDataError):
    """File tải về hỏng/không đúng cấu trúc (ví dụ tải dở), nên xóa để tải lại."""


class ERA5LandTimeseriesProvider(ERA5ClimateProvider):
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[Settings], Any] | None = None,
        repository: ClimateRepository | None = None,
    ) -> None:
        # Bản sao cấu hình: logic của lớp cha tự dùng dataset và giới hạn ngày của dataset này.
        land_settings = settings.model_copy(update={
            "era5_dataset": settings.era5_land_dataset,
            "era5_max_days_per_request": settings.era5_land_max_days_per_request,
        })
        super().__init__(land_settings, client_factory, repository)

    # ------------------------------------------------------------------ tải điểm
    def _load_point_data(
        self, variable: str, variable_spec: Era5Variable, location: Location,
        start: datetime, end: datetime,
    ) -> list[ClimatePoint]:
        # Dataset trả nguyên ngày UTC; chuẩn hóa để một file dùng lại cho mọi biến.
        day_start = datetime.combine(start.date(), time.min)
        day_end = datetime.combine(end.date(), time(hour=23))
        target_path = self._target_path(
            ALL_VARIABLES_NAME, location.latitude, location.longitude, day_start, day_end,
        )
        if not target_path.exists():
            self._validate_time_range(day_start, day_end)
            self._ensure_configured()
            request = self._build_request(
                variable_spec, location.latitude, location.longitude, day_start, day_end,
            )
            self._retrieve(request=request, target_path=target_path)

        try:
            grid_location, series, columns = self._parse_download(target_path, location)
        except _BrokenDownload:
            target_path.unlink(missing_ok=True)
            raise

        derived = variable_spec.cds_name in DERIVED_WIND_UNITS
        if not derived and variable_spec.cds_name not in series:
            raise ClimateDataError(
                f"ERA5-Land time-series file has no column for '{variable_spec.cds_name}'. "
                f"Columns found: {', '.join(sorted(columns)) or '(none)'}."
            )
        if self._repository is not None:
            for cds_name, points in series.items():
                if not points:
                    continue
                self._repository.save(
                    dataset=self._settings.era5_dataset, variable=cds_name,
                    requested_location=location, grid_location=grid_location,
                    unit=BASE_VARIABLES[cds_name].unit, points=points,
                )
                logger.info("ERA5-Land SQLite saved: variable=%s observations=%s", cds_name, len(points))
        if derived:
            return []
        start_naive, end_naive = to_utc_naive(start), to_utc_naive(end)
        return [p for p in series[variable_spec.cds_name] if start_naive <= p.time <= end_naive]

    def _build_request(
        self, variable_spec: Era5Variable, latitude: float, longitude: float,
        start: datetime, end: datetime,
    ) -> dict[str, Any]:
        return {
            "variable": list(BASE_VARIABLES),
            "location": {"longitude": round(longitude, 4), "latitude": round(latitude, 4)},
            "date": [f"{start.date().isoformat()}/{end.date().isoformat()}"],
            "data_format": "csv",
        }

    # ------------------------------------------------------------------ tải "vùng"
    def download_area(
        self, variable: str, area: DownloadArea, start_date: date, end_date: date, frequency: str,
    ) -> DownloadArtifact:
        """Dataset chỉ có điểm: lấy tâm của vùng làm điểm cần tải trước."""
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        if frequency != "hourly":
            raise ClimateDataError("Only hourly ERA5 downloads are supported.")
        variable_spec = self._get_variable(variable)
        location = Location(
            latitude=round((area.north + area.south) / 2, 4),
            longitude=round((area.west + area.east) / 2, 4),
        )
        self._validate_query_range(
            datetime.combine(start_date, time.min), datetime.combine(end_date, time(hour=23)),
        )
        max_days = self._settings.era5_max_days_per_request
        files: list[Path] = []
        current = start_date
        while current <= end_date:
            chunk_end = min(end_date, current + timedelta(days=max_days - 1))
            chunk_start_dt = datetime.combine(current, time.min)
            chunk_end_dt = datetime.combine(chunk_end, time(hour=23))
            with _CDS_LOCK:
                self._load_point_data(variable, variable_spec, location, chunk_start_dt, chunk_end_dt)
            files.append(self._target_path(
                ALL_VARIABLES_NAME, location.latitude, location.longitude, chunk_start_dt, chunk_end_dt,
            ))
            current = chunk_end + timedelta(days=1)

        data_dir = self._settings.data_dir
        if len(files) == 1:
            artifact_path = files[0]
        else:
            artifact_path = self._dataset_raw_dir() / "manifests" / (
                f"{start_date.isoformat()}_{end_date.isoformat()}_"
                f"lat{location.latitude:.4f}_lon{location.longitude:.4f}.manifest.json"
            )
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(json.dumps({
                "dataset": self._settings.era5_dataset, "variable": variable_spec.cds_name,
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                "location": location.model_dump(),
                "files": [f.relative_to(data_dir).as_posix() for f in files],
            }, indent=2), encoding="utf-8")
        return DownloadArtifact(
            file_path=artifact_path,
            storage_path=artifact_path.relative_to(data_dir).as_posix(),
            dataset=self._settings.era5_dataset,
            variable=variable_spec.cds_name,
        )

    # ------------------------------------------------------------------ đường dẫn
    def _target_path(
        self, variable: str, latitude: float, longitude: float, start: datetime, end: datetime,
    ) -> Path:
        # Tên biến không còn nằm trong tên file: một request chứa cả 6 biến.
        filename = (
            f"{start:%Y%m%d%H}_{end:%Y%m%d%H}_lat{latitude:.4f}_lon{longitude:.4f}.zip"
        )
        return self._dataset_raw_dir() / ALL_VARIABLES_NAME / filename

    # ------------------------------------------------------------------ đọc CSV
    def _parse_download(
        self, file_path: Path, location: Location,
    ) -> tuple[Location, dict[str, list[ClimatePoint]], set[str]]:
        series: dict[str, dict[datetime, ClimatePoint]] = {}
        columns: set[str] = set()
        grid: list[Location] = []
        try:
            for name, text in self._csv_texts(file_path):
                self._parse_csv_text(name, text, series, columns, grid)
        except (zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
            raise _BrokenDownload("Unable to read the ERA5-Land time-series download.") from exc

        for cds_name in TEMPERATURE_VARIABLES:
            values = [p.value for p in series.get(cds_name, {}).values()]
            if values and max(values) < 100:
                raise ClimateDataError(
                    f"'{cds_name}' values look like °C, but Kelvin was expected. "
                    "Check the units of the downloaded CSV before using it."
                )
        grid_location = grid[0] if grid else Location(
            latitude=round(location.latitude, 1), longitude=round(location.longitude, 1),
        )
        ordered = {
            name: sorted(points.values(), key=lambda p: p.time) for name, points in series.items()
        }
        return grid_location, ordered, columns

    def _csv_texts(self, file_path: Path):
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path) as archive:
                names = sorted(n for n in archive.namelist() if n.lower().endswith(".csv"))
                if not names:
                    raise _BrokenDownload("The ERA5-Land download zip contains no CSV file.")
                for name in names:
                    yield name, archive.read(name).decode("utf-8-sig")
        else:
            yield file_path.name, file_path.read_text(encoding="utf-8-sig")

    def _parse_csv_text(
        self, name: str, text: str, series: dict[str, dict[datetime, ClimatePoint]],
        columns: set[str], grid: list[Location],
    ) -> None:
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        if not lines:
            raise _BrokenDownload(f"{name} is empty.")
        reader = csv.DictReader(lines)
        fields = {self._normalize_column(f): f for f in (reader.fieldnames or [])}
        columns.update(fields)
        time_col = next((fields[c] for c in TIME_COLUMNS if c in fields), None)
        if time_col is None:
            raise _BrokenDownload(
                f"{name} has no time column. Columns found: {', '.join(sorted(fields))}."
            )
        lat_col = next((fields[c] for c in LAT_COLUMNS if c in fields), None)
        lon_col = next((fields[c] for c in LON_COLUMNS if c in fields), None)
        value_cols: dict[str, str] = {}
        for cds_name, spec in BASE_VARIABLES.items():
            for data_name in spec.data_names:
                if data_name in fields:
                    value_cols[cds_name] = fields[data_name]
                    break

        for row in reader:
            try:
                stamp = to_utc_naive(datetime.fromisoformat((row.get(time_col) or "").strip()))
            except ValueError:
                continue
            if not grid and lat_col and lon_col:
                try:
                    grid.append(Location(
                        latitude=round(float(row[lat_col]), 4),
                        longitude=round(((float(row[lon_col]) + 180) % 360) - 180, 4),
                    ))
                except (TypeError, ValueError):
                    pass
            for cds_name, column in value_cols.items():
                try:
                    raw = float(row[column])
                except (TypeError, ValueError):
                    continue
                if not isfinite(raw):
                    continue
                spec = BASE_VARIABLES[cds_name]
                series.setdefault(cds_name, {})[stamp] = ClimatePoint(
                    time=stamp, value=round(raw * spec.scale, 6),
                )

    @staticmethod
    def _normalize_column(name: str) -> str:
        cleaned = (name or "").strip().lower()
        for separator in ("[", "("):
            cleaned = cleaned.split(separator)[0]
        return cleaned.strip().replace(" ", "_")