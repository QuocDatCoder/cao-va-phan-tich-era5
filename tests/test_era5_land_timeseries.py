"""Kiểm thử offline cho ERA5LandTimeseriesProvider (không gọi CDS thật).

Định dạng CSV giả lập dựa trên kết quả chạy thử thực tế (cột valid_time, giá trị biến,
latitude, longitude). Tên cột/đơn vị của các biến khác cần đối chiếu với file thật.
"""
from datetime import date, datetime, timedelta
import io
import zipfile

import pytest

from backend.app.core.config import Settings
from backend.app.domain.schemas import DownloadArea
from backend.app.providers.era5_land_timeseries_provider import ERA5LandTimeseriesProvider
from backend.app.providers.errors import ClimateDataError
from backend.app.storage.climate_repository import ClimateRepository

LAT, LON = "10.800000000000081", "106.69999999999999"


def _rows(first_day: date, days: int):
    start = datetime.combine(first_day, datetime.min.time())
    return [start + timedelta(hours=h) for h in range(days * 24)]


def build_zip(first_day: date, days: int, *, kelvin=True, with_precip=True) -> bytes:
    stamps = _rows(first_day, days)
    temp = lambda h: (297.0 if kelvin else 24.0) + (h % 24) * 0.1
    files = {}
    head = "valid_time,t2m,d2m,latitude,longitude\n"
    files["t2m_d2m.csv"] = head + "".join(
        f"{t:%Y-%m-%d %H:%M:%S},{temp(i)},{temp(i) - 3},{LAT},{LON}\n" for i, t in enumerate(stamps))
    files["wind.csv"] = "valid_time,u10,v10,latitude,longitude\n" + "".join(
        f"{t:%Y-%m-%d %H:%M:%S},3.0,4.0,{LAT},{LON}\n" for t in stamps)
    if with_precip:
        files["precip_rad.csv"] = "valid_time,tp,ssrd,latitude,longitude\n" + "".join(
            f"{t:%Y-%m-%d %H:%M:%S},0.001,2000000.0,{LAT},{LON}\n" for t in stamps)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


class FakeClient:
    def __init__(self, payload_factory):
        self.calls = []
        self._factory = payload_factory

    def retrieve(self, dataset, request, target):
        self.calls.append((dataset, request))
        first, last = request["date"][0].split("/")
        days = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
        with open(target, "wb") as handle:
            handle.write(self._factory(date.fromisoformat(first), days))


@pytest.fixture
def make_provider(tmp_path):
    def _make(payload_factory=build_zip):
        settings = Settings(
            cds_url="https://example.invalid/api", cds_api_key="secret-key",
            data_dir=tmp_path / "data",
            climate_data_db_path=tmp_path / "climate.sqlite3",
            era5_sync_availability_lag_days=0,
            climate_provider="era5_land_timeseries",
        )
        client = FakeClient(payload_factory)
        provider = ERA5LandTimeseriesProvider(
            settings=settings, client_factory=lambda _s: client,
            repository=ClimateRepository(settings.climate_data_db_path),
        )
        return provider, client, settings
    return _make


START = datetime(2025, 1, 1, 0)
END = datetime(2025, 1, 1, 23)


def test_request_matches_slide_and_requests_all_variables(make_provider):
    provider, client, settings = make_provider()
    provider.get_timeseries("temperature", 10.8231, 106.6297, START, END)
    dataset, request = client.calls[0]
    assert dataset == "reanalysis-era5-land-timeseries"
    assert request["data_format"] == "csv"
    assert request["date"] == ["2025-01-01/2025-01-01"]
    assert request["location"] == {"longitude": 106.6297, "latitude": 10.8231}
    assert set(request["variable"]) == {
        "2m_temperature", "2m_dewpoint_temperature", "10m_u_component_of_wind",
        "10m_v_component_of_wind", "total_precipitation", "surface_solar_radiation_downwards",
    }


def test_one_request_serves_every_variable(make_provider):
    provider, client, _ = make_provider()
    temp = provider.get_timeseries("temperature", 10.8231, 106.6297, START, END)
    rain = provider.get_timeseries("precipitation", 10.8231, 106.6297, START, END)
    solar = provider.get_timeseries("solar_radiation", 10.8231, 106.6297, START, END)
    speed = provider.get_timeseries("wind_speed", 10.8231, 106.6297, START, END)
    assert len(client.calls) == 1
    assert temp.unit == "K" and len(temp.data) == 24
    assert temp.data[0].value == pytest.approx(297.0)
    assert rain.unit == "mm" and rain.data[0].value == pytest.approx(1.0)          # 0.001 m -> mm
    assert solar.unit == "MJ/m²" and solar.data[0].value == pytest.approx(2.0)      # 2e6 J -> MJ
    assert speed.unit == "m/s" and speed.data[0].value == pytest.approx(5.0)        # sqrt(3²+4²)


def test_partial_day_gap_downloads_whole_days_once(make_provider):
    provider, client, _ = make_provider()
    result = provider.get_timeseries("temperature", 10.8231, 106.6297,
                                     datetime(2025, 1, 1, 17), datetime(2025, 1, 2, 16))
    assert len(result.data) == 24
    # Backend có thể tách ngày lẻ ở nửa đêm UTC; miễn là tải đủ nguyên ngày 01 và 02/01.
    days = set()
    for _dataset, request in client.calls:
        first, last = request["date"][0].split("/")
        current = date.fromisoformat(first)
        while current <= date.fromisoformat(last):
            days.add(current)
            current += timedelta(days=1)
    assert days == {date(2025, 1, 1), date(2025, 1, 2)}
    calls_before = len(client.calls)
    provider.get_timeseries("temperature", 10.8231, 106.6297, datetime(2025, 1, 1), datetime(2025, 1, 2, 23))
    assert len(client.calls) == calls_before  # cả hai ngày đã nằm trong SQLite


def test_download_area_uses_center_point_and_feeds_queries(make_provider):
    provider, client, settings = make_provider()
    area = DownloadArea(north=11.0731, west=106.3797, south=10.5731, east=106.8797)
    artifact = provider.download_area("temperature", area, date(2025, 1, 1), date(2025, 1, 2), "hourly")
    assert client.calls[0][1]["location"] == {"longitude": 106.6297, "latitude": 10.8231}
    assert artifact.dataset == "reanalysis-era5-land-timeseries"
    assert (settings.data_dir / artifact.storage_path).exists()
    provider.get_timeseries("dewpoint", 10.8231, 106.6297, datetime(2025, 1, 1), datetime(2025, 1, 2, 23))
    assert len(client.calls) == 1


def test_long_download_area_writes_manifest(make_provider, monkeypatch):
    provider, client, settings = make_provider()
    provider._settings = provider._settings.model_copy(update={"era5_max_days_per_request": 2})
    area = DownloadArea(north=11.0731, west=106.3797, south=10.5731, east=106.8797)
    artifact = provider.download_area("temperature", area, date(2025, 1, 1), date(2025, 1, 5), "hourly")
    assert len(client.calls) == 3  # 2 + 2 + 1 ngày
    assert artifact.storage_path.endswith(".manifest.json")


def test_missing_variable_column_reports_columns(make_provider):
    provider, _, _ = make_provider(lambda d, n: build_zip(d, n, with_precip=False))
    with pytest.raises(ClimateDataError, match="Columns found"):
        provider.get_timeseries("precipitation", 10.8231, 106.6297, START, END)


def test_celsius_values_are_rejected(make_provider):
    provider, _, _ = make_provider(lambda d, n: build_zip(d, n, kelvin=False))
    with pytest.raises(ClimateDataError, match="°C"):
        provider.get_timeseries("temperature", 10.8231, 106.6297, START, END)


def test_broken_download_is_preserved_for_inspection(make_provider):
    provider, client, settings = make_provider(lambda d, n: b"PK\x03\x04 not a real zip")
    with pytest.raises(ClimateDataError):
        provider.get_timeseries("temperature", 10.8231, 106.6297, START, END)
    leftovers = list((settings.data_dir / "raw").rglob("*.zip"))
    assert len(leftovers) == 1
    assert leftovers[0].read_bytes() == b"PK\x03\x04 not a real zip"


def test_plain_csv_without_zip_is_supported(make_provider):
    def plain(first_day, days):
        stamps = _rows(first_day, days)
        return ("valid_time,t2m,latitude,longitude\n" + "".join(
            f"{t:%Y-%m-%d %H:%M:%S},300.0,{LAT},{LON}\n" for t in stamps)).encode()
    provider, _, _ = make_provider(plain)
    result = provider.get_timeseries("temperature", 10.8231, 106.6297, START, END)
    assert len(result.data) == 24


def test_real_cds_csv_layout(make_provider):
    """Bốn file CSV đúng như CDS trả về (đối chiếu từ file mẫu thật, 01/01/2025)."""
    grid = "10.800000000000814,106.69999999999823"
    stamps = _rows(date(2025, 1, 1), 1)

    def csv_file(header, row_values):
        body = "".join(f"{t:%Y-%m-%d %H:%M:%S},{row_values(i)},{grid}\n" for i, t in enumerate(stamps))
        return header + "\n" + body

    first = {
        "temperature": (292.76483, 296.70642), "tp": 7.1525574e-07,
        "ssrd": 47440.0, "wind": (-0.047348022, -1.6568289),
    }
    files = {
        "reanalysis-era5-land-timeseries-sfc-2m-temperaturexx.csv": csv_file(
            "valid_time,d2m,t2m,latitude,longitude", lambda i: f"{first['temperature'][0]},{first['temperature'][1]}"),
        "reanalysis-era5-land-timeseries-sfc-pressure-precipitationxx.csv": csv_file(
            "valid_time,tp,latitude,longitude", lambda i: f"{first['tp']}"),
        "reanalysis-era5-land-timeseries-sfc-radiation-heatxx.csv": csv_file(
            "valid_time,ssrd,latitude,longitude", lambda i: f"{first['ssrd']}"),
        "reanalysis-era5-land-timeseries-sfc-windxx.csv": csv_file(
            "valid_time,u10,v10,latitude,longitude", lambda i: f"{first['wind'][0]},{first['wind'][1]}"),
    }

    def real_zip(first_day, days):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return buffer.getvalue()

    provider, client, _ = make_provider(real_zip)
    temp = provider.get_timeseries("temperature", 10.8231, 106.6297, START, END)
    dew = provider.get_timeseries("dewpoint", 10.8231, 106.6297, START, END)
    rain = provider.get_timeseries("precipitation", 10.8231, 106.6297, START, END)
    solar = provider.get_timeseries("solar_radiation", 10.8231, 106.6297, START, END)
    speed = provider.get_timeseries("wind_speed", 10.8231, 106.6297, START, END)
    assert len(client.calls) == 1 and len(temp.data) == 24
    assert temp.data[0].value == pytest.approx(296.70642)
    assert dew.data[0].value == pytest.approx(292.76483)
    assert rain.data[0].value == pytest.approx(7.1525574e-07 * 1000, abs=1e-6)
    assert solar.data[0].value == pytest.approx(0.04744)
    assert speed.data[0].value == pytest.approx(((-0.047348022) ** 2 + 1.6568289 ** 2) ** 0.5, rel=1e-5)