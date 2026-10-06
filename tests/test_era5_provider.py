from datetime import datetime
from pathlib import Path
from shutil import copyfile

import numpy as np
import pytest
import xarray as xr

from backend.app.core.config import Settings, get_settings
from backend.app.providers.dependencies import get_climate_provider
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.errors import (
    ClimateConfigurationError,
    ClimateDataError,
    ClimateProviderError,
    UnsupportedVariableError,
)
from backend.app.providers.mock_provider import MockClimateProvider


class CopyingClient:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.calls: list[tuple[str, dict, str]] = []

    def retrieve(self, dataset: str, request: dict, target: str) -> None:
        self.calls.append((dataset, request, target))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        copyfile(self.source, target)


class FailingClient:
    def retrieve(self, dataset: str, request: dict, target: str) -> None:
        raise RuntimeError("network unavailable")


class NoFileClient:
    def retrieve(self, dataset: str, request: dict, target: str) -> None:
        Path(target).parent.mkdir(parents=True, exist_ok=True)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path,
        "cds_url": "https://example.test/api",
        "cds_api_key": "test-token",
        "era5_max_days_per_request": 7,
    }
    values.update(overrides)
    return Settings(**values)


def write_dataset(path: Path, variable_name: str = "t2m", engine: str = "scipy") -> None:
    times = np.array(
        [
            np.datetime64("2025-01-01T00:00:00"),
            np.datetime64("2025-01-01T01:00:00"),
            np.datetime64("2025-01-01T02:00:00"),
        ]
    )
    latitudes = np.array([11.0, 10.0])
    longitudes = np.array([106.5, 107.0])
    values = np.array(
        [
            [[290.0, 291.0], [300.0, 301.0]],
            [[292.0, 293.0], [302.0, 303.0]],
            [[294.0, 295.0], [304.0, 305.0]],
        ]
    )
    dataset = xr.Dataset(
        {
            variable_name: (
                ("time", "latitude", "longitude"),
                values,
            )
        },
        coords={
            "time": times,
            "latitude": latitudes,
            "longitude": longitudes,
        },
    )
    dataset.to_netcdf(path, engine=engine)


def test_era5_provider_requires_configuration(tmp_path: Path) -> None:
    provider = ERA5ClimateProvider(settings=make_settings(tmp_path, cds_url=None, cds_api_key=None))

    with pytest.raises(ClimateConfigurationError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 1),
        )


def test_era5_provider_rejects_unsupported_variable(tmp_path: Path) -> None:
    provider = ERA5ClimateProvider(settings=make_settings(tmp_path))

    with pytest.raises(UnsupportedVariableError):
        provider.get_timeseries(
            variable="humidity",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 1),
        )


def test_era5_provider_reads_netcdf_and_selects_nearest_point_and_time(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    write_dataset(source)
    client = CopyingClient(source)
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: client,
    )

    response = provider.get_timeseries(
        variable="temperature",
        latitude=10.2,
        longitude=106.6,
        start=datetime(2025, 1, 1, 1),
        end=datetime(2025, 1, 1, 2),
    )

    assert response.variable == "temperature"
    assert response.unit == "K"
    assert response.location.latitude == 10.2
    assert [point.time for point in response.data] == [
        datetime(2025, 1, 1, 1),
        datetime(2025, 1, 1, 2),
    ]
    assert [point.value for point in response.data] == [302.0, 304.0]
    assert client.calls[0][0] == "reanalysis-era5-single-levels"
    assert client.calls[0][1]["data_format"] == "netcdf"
    assert client.calls[0][1]["variable"] == ["2m_temperature"]
    assert client.calls[0][1]["date"] == ["2025-01-01"]


def test_era5_provider_reads_netcdf4_backend_file(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    write_dataset(source, engine="netcdf4")
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: CopyingClient(source),
    )

    response = provider.get_timeseries(
        variable="temperature",
        latitude=10.2,
        longitude=106.6,
        start=datetime(2025, 1, 1, 0),
        end=datetime(2025, 1, 1, 1),
    )

    assert [point.value for point in response.data] == [300.0, 302.0]


@pytest.mark.parametrize(
    ("start", "end", "expected_dates"),
    [
        (
            datetime(2025, 1, 2, 0),
            datetime(2025, 1, 4, 23),
            ["2025-01-02", "2025-01-03", "2025-01-04"],
        ),
        (
            datetime(2025, 1, 31, 0),
            datetime(2025, 2, 1, 23),
            ["2025-01-31", "2025-02-01"],
        ),
        (
            datetime(2025, 12, 31, 0),
            datetime(2026, 1, 1, 23),
            ["2025-12-31", "2026-01-01"],
        ),
        (
            datetime(2025, 6, 15, 6),
            datetime(2025, 6, 15, 18),
            ["2025-06-15"],
        ),
    ],
)
def test_era5_request_uses_exact_dates(
    tmp_path: Path,
    start: datetime,
    end: datetime,
    expected_dates: list[str],
) -> None:
    provider = ERA5ClimateProvider(settings=make_settings(tmp_path))

    request = provider._build_request(
        variable_spec=provider._get_variable("temperature"),
        latitude=10.2,
        longitude=106.6,
        start=start,
        end=end,
    )

    assert request["date"] == expected_dates
    assert "year" not in request
    assert "month" not in request
    assert "day" not in request


def test_era5_provider_converts_precipitation_to_mm(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    write_dataset(source, variable_name="tp")
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: CopyingClient(source),
    )

    response = provider.get_timeseries(
        variable="precipitation",
        latitude=10.2,
        longitude=106.6,
        start=datetime(2025, 1, 1, 0),
        end=datetime(2025, 1, 1, 0),
    )

    assert response.unit == "mm"
    assert response.data[0].value == 300000.0


def test_era5_provider_handles_missing_variable(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    write_dataset(source, variable_name="not_t2m")
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: CopyingClient(source),
    )

    with pytest.raises(ClimateDataError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 1),
        )


def test_era5_provider_handles_missing_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    dataset = xr.Dataset(
        {"t2m": (("time",), np.array([300.0]))},
        coords={"time": [np.datetime64("2025-01-01T00:00:00")]},
    )
    dataset.to_netcdf(source, engine="scipy")
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: CopyingClient(source),
    )

    with pytest.raises(ClimateDataError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 0),
        )


def test_era5_provider_handles_empty_time_selection(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    write_dataset(source)
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path, era5_max_days_per_request=31),
        client_factory=lambda _: CopyingClient(source),
    )

    with pytest.raises(ClimateDataError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 5, 0),
            end=datetime(2025, 1, 5, 1),
        )


def test_era5_provider_handles_malformed_netcdf(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    source.write_text("not a netcdf", encoding="utf-8")
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: CopyingClient(source),
    )

    with pytest.raises(ClimateDataError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 1),
        )


def test_era5_provider_handles_cds_failure(tmp_path: Path) -> None:
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: FailingClient(),
    )

    with pytest.raises(ClimateProviderError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 1),
        )


def test_era5_provider_handles_missing_downloaded_file(tmp_path: Path) -> None:
    provider = ERA5ClimateProvider(
        settings=make_settings(tmp_path),
        client_factory=lambda _: NoFileClient(),
    )

    with pytest.raises(ClimateDataError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.2,
            longitude=106.6,
            start=datetime(2025, 1, 1, 0),
            end=datetime(2025, 1, 1, 1),
        )


def test_provider_dependency_defaults_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIMATE_PROVIDER", raising=False)
    get_settings.cache_clear()
    get_climate_provider.cache_clear()

    provider = get_climate_provider()

    assert isinstance(provider, MockClimateProvider)
    get_climate_provider.cache_clear()
    get_settings.cache_clear()


def test_provider_dependency_can_select_era5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIMATE_PROVIDER", "era5")
    monkeypatch.setenv("CDS_URL", "https://example.test/api")
    monkeypatch.setenv("CDS_API_KEY", "test-token")
    get_settings.cache_clear()
    get_climate_provider.cache_clear()

    provider = get_climate_provider()

    assert isinstance(provider, ERA5ClimateProvider)
    get_climate_provider.cache_clear()
    get_settings.cache_clear()
