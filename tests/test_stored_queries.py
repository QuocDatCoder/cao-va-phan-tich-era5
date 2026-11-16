"""No-network contract for explicit queries, including partial and absent caches."""
from datetime import datetime, timedelta
import csv
import io

import cdsapi
from fastapi.testclient import TestClient
import pytest

from backend.app.core.config import Settings
from backend.app.domain.schemas import ClimatePoint, DownloadArea, Location
from backend.app.domain.sources import source_dataset
from backend.app.main import create_app
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.era5_land_timeseries_provider import ERA5LandTimeseriesProvider
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.services.dependencies import get_stored_climate_service
from backend.app.services.stored_climate_service import StoredClimateService
from backend.app.storage.climate_repository import ClimateRepository

START = datetime(2025, 1, 1)
LOCATION = Location(latitude=10.8231, longitude=106.6297)
GRID = Location(latitude=10.75, longitude=106.75)
PARAMS = {"variable": "temperature", "latitude": LOCATION.latitude, "longitude": LOCATION.longitude,
          "start": "2025-01-01T00:00:00Z", "end": "2025-01-01T02:00:00Z"}
PREFIX = "/api/v1/stored/climate/"


@pytest.fixture
def stored(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("DB-only queries must not use providers, CDS clients or file import")
    monkeypatch.setattr(cdsapi, "Client", forbidden)
    for cls in (ERA5ClimateProvider, ERA5LandTimeseriesProvider, MockClimateProvider):
        monkeypatch.setattr(cls, "get_timeseries", forbidden)
        monkeypatch.setattr(cls, "download_area", forbidden)
        monkeypatch.setattr(cls, "download_point", forbidden)
    monkeypatch.setattr(ERA5ClimateProvider, "_retrieve", forbidden)
    monkeypatch.setattr(ERA5ClimateProvider, "_load_point_data", forbidden)
    monkeypatch.setattr(ERA5LandTimeseriesProvider, "_load_point_data", forbidden)
    settings = Settings(_env_file=None, climate_provider="mock", cds_api_key=None, cds_url=None,
                        data_dir=tmp_path / "raw", climate_data_db_path=tmp_path / "observations.sqlite3",
                        job_metadata_db_path=tmp_path / "jobs.sqlite3")
    writer = ClimateRepository(settings.climate_data_db_path)
    service = StoredClimateService(ClimateRepository(settings.climate_data_db_path, read_only=True), settings)
    app = create_app()
    app.dependency_overrides[get_stored_climate_service] = lambda: service
    with TestClient(app) as client:
        yield settings, writer, service, client


def save(writer, settings, source="land", variable="2m_temperature", hours=(0, 1, 2),
         values=None, unit="K", grid=GRID):
    values = values if values is not None else [273.15 + h for h in hours]
    writer.save(dataset=source_dataset(source, settings), variable=variable,
                requested_location=LOCATION, grid_location=grid, unit=unit,
                points=[ClimatePoint(time=START+timedelta(hours=h), value=v) for h, v in zip(hours, values)])


@pytest.mark.parametrize("source", ["land", "era5", "mock"])
@pytest.mark.parametrize("hours", [(), (0, 2), (0, 1, 2)], ids=["absent", "interior-gap", "complete"])
def test_all_stored_endpoints_never_call_providers_even_when_cache_is_missing(stored, source, hours):
    settings, writer, _, client = stored
    if hours:
        save(writer, settings, source, hours=hours)
    params = {**PARAMS, "source": source}
    for operation in ["timeseries", "statistics", "aggregate", "trend", "heatmap", "map"]:
        response = client.get(PREFIX+operation, params=params)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["query_mode"] == "db_only"
        assert result["source"] == source
        assert result["dataset"] == source_dataset(source, settings)
        assert result["available_count"] == len(hours)
        assert result["expected_count"] == 3
        assert result["coverage"] == round(len(hours)/3, 6)
        assert result["complete"] == (len(hours) == 3)
        assert sum(r["count"] for r in result["missing_ranges"]) == 3-len(hours)
        if operation == "trend" and len(hours) < 3:
            assert result["slope"] is None  # No regression over a gap disguised as equally spaced data.
        if operation == "statistics" and not hours:
            assert result["mean"] is None and result["count"] == 0
    csv_response = client.get(PREFIX+"timeseries.csv", params=params)
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert len(rows) == len(hours)
    assert csv_response.headers["x-data-source"] == source
    assert all(row["source"] == source and row["dataset"] == source_dataset(source, settings) for row in rows)


def test_three_sources_same_coordinate_variable_time_are_isolated_after_restart(stored):
    settings, writer, _, client = stored
    for i, source in enumerate(["land", "era5", "mock"]):
        save(writer, settings, source, values=[273.15 + 10*i + h for h in range(3)])
    restarted = StoredClimateService(ClimateRepository(settings.climate_data_db_path, read_only=True), settings)
    client.app.dependency_overrides[get_stored_climate_service] = lambda: restarted
    for i, source in enumerate(["land", "era5", "mock"]):
        r = client.get(PREFIX+"timeseries", params={**PARAMS, "source": source}).json()
        assert [p["value"] for p in r["data"]] == [10*i+h for h in range(3)]
        assert r["grid_location"] == GRID.model_dump()
        assert r["location"] == LOCATION.model_dump()
        assert r["data"][0]["time"] == "2025-01-01T07:00:00+07:00"
        assert r["unit"] == "°C"


def test_missing_range_and_partial_statistics_are_honest(stored):
    settings, writer, _, client = stored
    save(writer, settings, hours=(0, 2))
    r = client.get(PREFIX+"statistics", params={**PARAMS, "source": "land"}).json()
    assert r["status"] == "partial" and r["mean"] == 1
    assert r["count"] == 2
    assert r["missing_ranges"] == [{"variable": "2m_temperature", "start": "2025-01-01T08:00:00+07:00",
                                    "end": "2025-01-01T08:00:00+07:00", "count": 1,
                                    "time_basis": "observation_time"}]


def test_missing_database_not_created_and_raw_files_not_imported(tmp_path):
    settings = Settings(_env_file=None, climate_data_db_path=tmp_path/"does-not-exist.sqlite3")
    service = StoredClimateService(ClimateRepository(settings.climate_data_db_path, read_only=True), settings)
    from backend.app.domain.schemas import TimeSeriesRequest
    request = TimeSeriesRequest(variable="temperature", location=LOCATION, start=START, end=START)
    assert service.timeseries("land", request).status == "missing"
    assert not settings.climate_data_db_path.exists()


def test_db_only_does_not_import_an_existing_raw_file(stored):
    settings, _, _, client = stored
    settings.data_dir.mkdir()
    (settings.data_dir/"cached.nc").write_bytes(b"not a valid netcdf - must never be opened")
    assert client.get(PREFIX+"timeseries", params={**PARAMS, "source": "era5"}).json()["status"] == "missing"


def test_derived_wind_requires_both_components_and_keeps_calm_distinct_from_missing(stored):
    settings, writer, _, client = stored
    save(writer, settings, variable="10m_u_component_of_wind", values=[3, 0, 3], unit="m/s")
    save(writer, settings, variable="10m_v_component_of_wind", hours=(0, 1), values=[4, 0], unit="m/s")
    params = {**PARAMS, "source": "land", "variable": "wind_direction"}
    r = client.get(PREFIX+"timeseries", params=params).json()
    assert r["available_count"] == 2 and r["count"] == 1 and r["undefined_count"] == 1
    assert r["coverage"] == round(2/3, 6)
    assert r["missing_ranges"][0]["variable"] == "10m_v_component_of_wind"
    # Adding the missing source makes coverage complete but does not invent a calm direction.
    save(writer, settings, variable="10m_v_component_of_wind", values=[4, 0, 4], unit="m/s")
    r = client.get(PREFIX+"timeseries", params=params).json()
    assert r["complete"] and r["undefined_count"] == 1 and r["count"] == 2
    assert client.get(PREFIX+"trend", params=params).status_code == 422


def test_wind_components_from_different_grids_are_not_combined(stored):
    settings, writer, _, client = stored
    save(writer, settings, variable="10m_u_component_of_wind", unit="m/s")
    save(writer, settings, variable="10m_v_component_of_wind", unit="m/s",
         grid=Location(latitude=11, longitude=107))
    assert client.get(PREFIX+"timeseries", params={**PARAMS, "source": "land", "variable": "wind_speed"}).status_code == 422


def test_accumulated_aggregate_keeps_hour_start_alignment_and_reports_source_gaps(stored):
    settings, writer, _, client = stored
    save(writer, settings, source="era5", variable="total_precipitation", hours=(1, 3), values=[1, 2], unit="mm")
    r = client.get(PREFIX+"aggregate", params={**PARAMS, "source": "era5", "variable": "precipitation"}).json()
    assert r["data"][0]["value"] == 3 and r["data"][0]["count"] == 2
    assert r["time_basis"] == "hour_start" and not r["complete"]
    assert r["missing_ranges"][0]["start"] == "2025-01-01T09:00:00+07:00"


def test_area_grid_lookup_is_reused_only_inside_the_saved_area(stored):
    settings, writer, _, client = stored
    writer.save_area(dataset=source_dataset("era5", settings), variable="2m_temperature", unit="K",
                     area=DownloadArea(north=11, south=10, west=106, east=107),
                     latitudes=[10.75], longitudes=[106.75], records=[(10.75, 106.75, START, 300)])
    inside = client.get(PREFIX+"timeseries", params={**PARAMS, "source": "era5"}).json()
    outside = client.get(PREFIX+"timeseries", params={**PARAMS, "source": "era5", "latitude": 12}).json()
    assert inside["grid_location"] == GRID.model_dump() and inside["count"] == 1
    assert outside["grid_location"] is None and outside["count"] == 0


@pytest.mark.parametrize("change", [{"source": "sea"}, {"source": "era5_land_timeseries"},
                                    {"end": "2024-01-01T00:00:00Z"},
                                    {"variable": "not-a-weather-variable"}])
def test_invalid_stored_queries_are_rejected_without_a_download(stored, change):
    client = stored[3]
    assert client.get(PREFIX+"timeseries", params={**PARAMS, "source": "land", **change}).status_code in {400, 422}


def test_misconfigured_dataset_identity_cannot_leak_other_sources(stored):
    settings, writer, service, client = stored
    save(writer, settings, "land")
    service.settings = settings.model_copy(update={"era5_dataset": settings.era5_land_dataset})
    response = client.get(PREFIX+"timeseries", params={**PARAMS, "source": "era5"})
    assert response.status_code == 422
    assert "distinct" in response.json()["detail"]


def test_heatmap_preserves_partial_month_flag_even_when_requested_hours_are_complete(stored):
    settings, writer, _, client = stored
    save(writer, settings)
    response = client.get(PREFIX+"heatmap", params={**PARAMS, "source": "land"}).json()
    assert response["complete"]  # All three requested hours are available.
    assert response["data"][0]["partial_period"] and not response["data"][0]["complete"]
