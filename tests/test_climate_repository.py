from datetime import UTC, datetime, timedelta, timezone
import sqlite3

import pytest

from backend.app.domain.schemas import ClimatePoint, Location
from backend.app.storage.climate_repository import ClimateRepository


LOCATION = Location(latitude=10.8231, longitude=106.6297)
GRID = Location(latitude=10.75, longitude=106.75)


def save(repository, points, **overrides):
    params = dict(dataset="era5", variable="2m_temperature", requested_location=LOCATION,
                  grid_location=GRID, unit="K", points=points)
    params.update(overrides)
    repository.save(**params)


def query(repository, **overrides):
    params = dict(dataset="era5", variable="2m_temperature", location=LOCATION, unit="K",
                  start=datetime(2025, 2, 1), end=datetime(2025, 2, 1, 2))
    params.update(overrides)
    return repository.get_complete_series(**params)


def points():
    return [ClimatePoint(time=datetime(2025, 2, 1, hour), value=290 + hour) for hour in range(3)]


def test_restart_reads_complete_series_and_subsets_in_order(tmp_path):
    path = tmp_path / "climate.sqlite3"
    save(ClimateRepository(path), list(reversed(points())))
    restarted = ClimateRepository(path)
    assert query(restarted) == points()
    assert query(restarted, start=datetime(2025, 2, 1, 1)) == points()[1:]


def test_upsert_uses_actual_grid_and_does_not_duplicate_observations(tmp_path):
    path = tmp_path / "climate.sqlite3"
    repo = ClimateRepository(path)
    save(repo, points())
    revised = [ClimatePoint(time=p.time, value=p.value + 1) for p in points()]
    save(repo, revised)
    other_location = Location(latitude=10.8, longitude=106.7)
    save(repo, revised, requested_location=other_location)
    assert query(repo) == revised
    assert query(repo, location=other_location) == revised
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == 3
        assert connection.execute("SELECT grid_latitude,grid_longitude FROM climate_points LIMIT 1").fetchone() == (10.75, 106.75)


def test_missing_interior_hour_is_not_treated_as_complete(tmp_path):
    repo = ClimateRepository(tmp_path / "climate.sqlite3")
    save(repo, [points()[0], points()[2]])
    assert query(repo) is None
    save(repo, [points()[1]])
    assert query(repo) == points()


@pytest.mark.parametrize("overrides", [
    {"dataset": "era5-land"}, {"variable": "total_precipitation"}, {"unit": "°C"},
    {"location": Location(latitude=11, longitude=107)},
])
def test_cache_does_not_mix_dataset_variable_unit_or_location(tmp_path, overrides):
    repo = ClimateRepository(tmp_path / "climate.sqlite3")
    save(repo, points())
    assert query(repo, **overrides) is None


def test_storage_is_utc_and_non_hour_aligned_query_selects_only_observations(tmp_path):
    repo = ClimateRepository(tmp_path / "climate.sqlite3")
    vn = timezone(timedelta(hours=7))
    local_points = [ClimatePoint(time=p.time.replace(tzinfo=UTC).astimezone(vn), value=p.value)
                    for p in points()]
    save(repo, local_points)
    assert query(repo, start=datetime(2025, 2, 1, 0, 30)) == points()[1:]


def test_nonfinite_values_do_not_make_a_complete_series(tmp_path):
    repo = ClimateRepository(tmp_path / "climate.sqlite3")
    save(repo, [points()[0], ClimatePoint(time=points()[1].time, value=float("inf")), points()[2]])
    assert query(repo) is None
