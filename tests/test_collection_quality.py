"""Quality fixtures never contact CDS; verify durable warnings and retained cells."""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import numpy as np
import pytest
import xarray as xr

from test_source_downloads import collection, POINT, AREA, DATES
from backend.app.storage.job_repository import JobRepository
from backend.app.domain.schemas import SynchronizationRequest
from backend.app.services.download_job_service import DownloadJobService
from backend.app.services.synchronization_service import SynchronizationService, LatestAvailableDateProvider
from backend.app.storage.synchronization_repository import SynchronizationRepository
from datetime import date


def collect(collection, source, content):
    settings, observations, jobs, service, providers, cds, client = collection
    def retrieve(dataset, request, target):
        if source == "land":
            Path(target).write_text(content, encoding="utf-8")
        else:
            if content.sizes["valid_time"] == 0:
                Path(target).write_bytes(b"")
            else:
                content.to_netcdf(target, engine="scipy")
    cds.retrieve = retrieve
    response = client.post("/api/v1/downloads", json=dict(source=source, variable="temperature",
        **DATES, **({"location": POINT} if source == "land" else {"area": AREA})))
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    return JobRepository(settings.job_metadata_db_path).get(job_id), client


def land_rows():
    return [[str(datetime(2025, 1, 1)+timedelta(hours=h)), "300", "295", "3", "4", ".001", "2000000", "10.8", "106.7"] for h in range(24)]


@pytest.mark.parametrize('suspect_hours', [(0,), tuple(range(24))], ids=['one-cell', 'whole-column'])
def test_land_suspect_temperature_units_keep_values_and_durable_warnings(collection, suspect_hours):
    rows = land_rows()
    for hour in suspect_hours:
        rows[hour][1:3] = ['25', '20']
    text = 'valid_time,t2m,d2m,u10,v10,tp,ssrd,latitude,longitude\n' + '\n'.join(','.join(row) for row in rows)
    job, client = collect(collection, 'land', text)
    assert job.status.value == 'completed'
    assert job.quality.status == 'warnings' and not job.quality.complete
    assert job.quality.issue_counts == {'suspicious_value': 2 * len(suspect_hours)}
    assert job.quality.valid_values == job.quality.imported_values == 144
    settings = collection[0]
    restarted = JobRepository(settings.job_metadata_db_path).get(job.job_id)
    assert restarted.quality == job.quality
    from backend.app.storage.collection_repository import CollectionRepository
    snapshots = CollectionRepository(settings.climate_data_db_path, read_only=True)
    entry = snapshots.get(job.job_id)
    assert entry['status'] == 'complete'  # Hourly coverage is distinct from quality.
    assert entry['summary']['quality']['status'] == 'warnings'
    issues = snapshots.issues(job.job_id)['issues']
    assert len(issues) == 2 * len(suspect_hours)
    assert {i['variable'] for i in issues} == {'2m_temperature', '2m_dewpoint_temperature'}
    for issue in issues:
        assert issue['code'] == 'suspicious_value'
        assert issue['source_file'] and issue['row_number'] >= 2 and issue['time_utc']
        assert 'Kelvin' in issue['message'] and 'Original value retained' in issue['message']
    with sqlite3.connect(settings.climate_data_db_path) as db:
        value = db.execute("SELECT value FROM collection_observations WHERE collection_id=? AND variable='2m_temperature' ORDER BY time_utc LIMIT 1", (job.job_id,)).fetchone()[0]
        assert value == 25  # Never guess Celsius or add 273.15 during import.
    payload = client.get(f'/api/v1/collections/{job.job_id}/timeseries').json()
    assert payload['data'][0]['value'] == pytest.approx(-248.15)
    assert payload['collection_quality']['status'] == 'warnings'
    details = client.get(f'/api/v1/jobs/{job.job_id}/quality').json()
    assert details['total'] == len(issues)


def test_land_kelvin_temperatures_do_not_trigger_unit_warning(collection):
    text = 'valid_time,t2m,d2m,u10,v10,tp,ssrd,latitude,longitude\n' + '\n'.join(','.join(row) for row in land_rows())
    job, _ = collect(collection, 'land', text)
    assert job.status.value == 'completed'
    assert job.quality.status == 'passed' and job.quality.issue_count == 0


@pytest.mark.parametrize("problem,code,valid", [
    ("empty_cell", "missing_value", 143), ("nan", "nonfinite_value", 143),
    ("bad_time", "invalid_time", 138), ("missing_column", "missing_column", 120),
    ("missing_hour", "missing_hour", 138), ("conflict", "duplicate_conflict", 144),
    ("empty_file", "empty_file", 0), ("all_nan", "nonfinite_value", 0),
    ("duplicate_good_after_nan", "nonfinite_value", 144),
])
def test_land_quality_survives_restart(collection, problem, code, valid):
    rows = land_rows()
    header = "valid_time,t2m,d2m,u10,v10,tp,ssrd,latitude,longitude"
    if problem == "empty_cell": rows[0][1] = ""
    if problem == "nan": rows[0][1] = "NaN"
    if problem == "bad_time": rows[0][0] = "wrong"
    if problem == "missing_column":
        header = header.replace("t2m,", "")
        rows = [r[:1]+r[2:] for r in rows]
    if problem == "missing_hour": rows.pop(0)
    if problem == "conflict":
        duplicate = rows[0].copy()
        duplicate[1] = "999"
        rows.append(duplicate)
    if problem == "all_nan":
        for row in rows: row[1:7] = ["NaN"]*6
    if problem == "duplicate_good_after_nan":
        rows.append(rows[0].copy())
        rows[0][1] = "NaN"
    text = "" if problem == "empty_file" else header+"\n"+"\n".join(",".join(r) for r in rows)
    job, client = collect(collection, "land", text)
    # Additional variables cannot make a temperature request succeed without t2m.
    assert job.status.value == ("completed" if valid and problem != "missing_column" else "failed")
    assert not job.quality.complete
    assert job.quality.issue_counts[code] > 0
    assert job.quality.imported_values == job.quality.valid_values == valid
    settings = collection[0]
    with sqlite3.connect(settings.climate_data_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == valid
        if problem == "conflict":
            assert db.execute("SELECT value FROM climate_points WHERE variable='2m_temperature' ORDER BY time_utc LIMIT 1").fetchone()[0] == 300
    details = client.get(f"/api/v1/jobs/{job.job_id}/quality?limit=500").json()
    assert details["total"] == job.quality.issue_count == sum(job.quality.issue_counts.values())
    assert len(details["issues"]) == details["total"]
    assert list(settings.data_dir.rglob("*.zip")) or list(settings.data_dir.rglob("*.part"))  # Failed/empty transfers stay as partial evidence.


@pytest.mark.parametrize("problem,code,valid", [
    ("nan", "nonfinite_value", 23), ("bad_time", "invalid_time", 23),
    ("missing_hour", "missing_hour", 23), ("conflict", "duplicate_conflict", 24),
    ("missing_column", "missing_column", 0), ("empty_file", "empty_file", 0),
    ("all_nan", "nonfinite_value", 0),
    ("duplicate_good_after_nan", "nonfinite_value", 24),
])
def test_era5_quality_retains_good_cells(collection, problem, code, valid):
    times = np.arange(np.datetime64("2025-01-01T00"), np.datetime64("2025-01-02T00"), np.timedelta64(1, "h"))
    values = np.full(24, 300.)
    if problem == "nan": values[0] = np.nan
    if problem == "bad_time": times[0] = np.datetime64("NaT", "h")
    if problem == "missing_hour": times, values = times[1:], values[1:]
    if problem == "conflict":
        times, values = np.append(times, times[0]), np.append(values, 999.)
    if problem == "empty_file": times, values = times[:0], values[:0]
    if problem == "all_nan": values[:] = np.nan
    if problem == "duplicate_good_after_nan":
        times, values = np.append(times, times[0]), np.append(values, 300.)
        values[0] = np.nan
    data = xr.Dataset({"other" if problem == "missing_column" else "t2m":
        (("valid_time", "latitude", "longitude"), values[:, None, None])},
        coords={"valid_time": times, "latitude": [10.75], "longitude": [106.75]})
    job, client = collect(collection, "era5", data)
    assert job.status.value == ("completed" if valid else "failed"), job.error
    assert job.quality.issue_counts[code] > 0
    assert not job.quality.complete
    assert job.quality.valid_values == job.quality.imported_values == valid
    with sqlite3.connect(collection[0].climate_data_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == valid
    assert JobRepository(collection[0].job_metadata_db_path).quality_issues(job.job_id)["total"] == job.quality.issue_count


@pytest.mark.parametrize("source", ["land", "era5"])
def test_good_collection_has_verified_quality(collection, source):
    settings, _, _, service, _, _, client = collection
    result = client.post("/api/v1/downloads", json=dict(source=source, variable="temperature",
        **DATES, **({"location": POINT} if source == "land" else {"area": AREA})))
    job = JobRepository(settings.job_metadata_db_path).get(result.json()["job_id"])
    assert job.quality.complete and job.quality.status == "passed"
    assert job.quality.imported_values == (144 if source == "land" else 24)


@pytest.mark.parametrize("source", ["land", "era5"])
@pytest.mark.parametrize("reconcile", [False, True])
def test_quality_warning_blocks_sync_and_restart_reconciliation(collection, source, reconcile):
    settings, _, jobs, _, providers, cds, _ = collection
    original = cds.retrieve
    def retrieve(dataset, request, target):
        original(dataset, request, target)
        if source == "land":
            p = Path(target)
            p.write_text(p.read_text().replace(",300,", ",NaN,", 1))
        else:
            with xr.open_dataset(target) as ds:
                damaged = ds.load()
            damaged["t2m"].values[0, 0, 0] = np.nan
            damaged.to_netcdf(target, engine="scipy")
    cds.retrieve = retrieve
    settings = settings.model_copy(update={"climate_provider": "era5_land_timeseries" if source == "land" else "era5"})
    downloads = DownloadJobService(providers[source], jobs,
        provider_resolver=providers.__getitem__, settings=settings)
    sync_db = SynchronizationRepository(settings.job_metadata_db_path)
    sync = SynchronizationService(downloads, sync_db, LatestAvailableDateProvider(5))
    request = SynchronizationRequest(variable="temperature", area=AREA,
        start_date=date(2025, 1, 1), latest_available_date=date(2025, 1, 1))
    queued = sync.synchronize(request)
    if reconcile:
        downloads.run_job(queued.job_id)
        sync = SynchronizationService(downloads, SynchronizationRepository(settings.job_metadata_db_path), LatestAvailableDateProvider(5))
        result = sync.synchronize(request)
    else:
        result = sync.run_synchronization(queued.sync_id)
    assert jobs.get(queued.job_id).status == "completed"
    assert result.status == "failed" and result.last_synced_date is None
    assert "quality" in result.error


def test_dashboard_renders_persisted_quality_warning_without_network():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import streamlit as st
from frontend.dashboard import _poll_collection
class Client:
    base_url = 'http://quality-fixture.test'
    source = 'land'
    stored_only = True
    def get_job(self, job_id):
        return {"status": "completed", "source": "land", "dataset": "fixture",
            "spatial_mode": "point", "quality": {"status": "warnings", "complete": False,
            "imported_values": 143, "issue_count": 1}}
    def get_job_quality(self, job_id):
        return {"total": 1, "issues": [{"code": "missing_value", "variable": "2m_temperature", "row_number": 2}]}
st.session_state["collection_jobs"] = {"fixture": "job"}
st.session_state["collection_completed"] = "job"
_poll_collection(Client(), "fixture")
''').run()
    assert not app.exception and not app.error
    assert len(app.warning) == 1 and len(app.dataframe) == 0
    app.session_state['job_quality_job'] = True
    app.run()
    assert not app.exception and not app.error
    assert len(app.warning) == 1 and len(app.dataframe) == 1
    assert any("143" in item.value for item in app.caption)
