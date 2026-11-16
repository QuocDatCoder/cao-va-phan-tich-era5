import sqlite3

import pytest

from backend.app.domain.schemas import DownloadRequest
from test_source_downloads import collection, POINT, DATES


def completed(service):
    job = service.create_job(DownloadRequest(source='land', variable='temperature', location=POINT, **DATES))
    service.run_job(job.job_id)
    return job.job_id


@pytest.mark.parametrize('status', ['complete', 'partial', 'failed', 'interrupted'])
def test_delete_terminal_collection_removes_only_its_snapshot_and_job(collection, monkeypatch, status):
    settings, _, jobs, service, _, cds, api = collection
    cid = completed(service)
    other = completed(service)
    artifact = service.collections.get(cid)['artifacts'][0]['path']
    with sqlite3.connect(settings.climate_data_db_path) as db:
        db.execute('UPDATE collections SET status=? WHERE id=?', (status, cid))
    monkeypatch.setattr(cds, 'retrieve', lambda *a, **k: pytest.fail('Deletion cannot call CDS'))
    response = api.delete('/api/v1/collections/' + cid)
    assert response.status_code == 200
    assert response.json()['deleted']['collection_observations'] > 0
    assert jobs.get(cid) is None
    assert api.get('/api/v1/collections/' + cid).status_code == 404
    assert api.get('/api/v1/collections/' + other + '/timeseries').json()['data']
    from pathlib import Path
    assert Path(artifact).is_file()
    with sqlite3.connect(settings.climate_data_db_path) as db:
        for table in ['collection_observations', 'collection_variables', 'collection_grids',
                      'collection_artifacts', 'collection_quality_issues']:
            assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE collection_id=?', (cid,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM climate_points').fetchone()[0] > 0
    assert api.delete('/api/v1/collections/' + cid).status_code == 404


@pytest.mark.parametrize('running', [False, True])
def test_delete_rejects_active_jobs(collection, running):
    _, _, jobs, service, _, _, api = collection
    job = service.create_job(DownloadRequest(source='land', variable='temperature', location=POINT, **DATES))
    if running:
        jobs.claim_running(job.job_id)
        service.collections.running(job.job_id)
    assert api.delete('/api/v1/collections/' + job.job_id).status_code == 409
    assert jobs.get(job.job_id) is not None
    assert service.collections.get(job.job_id) is not None


def test_delete_checks_job_status_even_if_snapshot_already_finalized(collection):
    _, _, jobs, service, _, _, api = collection
    cid = completed(service)
    jobs.mark_running(cid)
    assert api.delete('/api/v1/collections/' + cid).status_code == 409
    assert service.collections.get(cid)['variables']


def test_delete_rolls_back_job_and_snapshot_on_database_failure(collection):
    settings, _, jobs, service, _, _, api = collection
    cid = completed(service)
    with sqlite3.connect(settings.climate_data_db_path) as db:
        db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON collection_variables BEGIN SELECT RAISE(ABORT, 'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        api.delete('/api/v1/collections/' + cid)
    assert jobs.get(cid) is not None
    with sqlite3.connect(settings.climate_data_db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM collection_observations WHERE collection_id=?', (cid,)).fetchone()[0] > 0
