import json
import sqlite3

import pytest

from scripts.check_demo_data import audit, main
from backend.app.domain.schemas import DownloadRequest
from test_source_downloads import collection, POINT, DATES


def test_audit_missing_database_never_creates_it(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    assert not audit(path)['ready']
    assert not path.exists()


def test_local_audit_verifies_snapshot_and_detects_tampered_source(collection, tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Local audit must not use network'))
    settings, _, _, service, _, _, _ = collection
    job = service.create_job(DownloadRequest(source='land', variable='temperature', location=POINT, **DATES))
    service.run_job(job.job_id)
    report = audit(settings.climate_data_db_path, [job.job_id], ['land'], tmp_path / 'csv')
    assert report['ready'], report
    assert (tmp_path / 'csv' / (job.job_id + '.csv')).is_file()
    source = report['collections'][0]['artifacts'][0]['path']
    from pathlib import Path
    Path(source).write_text('changed fixture', encoding='utf-8')
    assert not audit(settings.climate_data_db_path, [job.job_id])['ready']


def test_local_audit_flags_wrong_counts_and_missing_required_source(collection):
    settings, _, _, service, _, _, _ = collection
    job = service.create_job(DownloadRequest(source='land', variable='temperature', location=POINT, **DATES))
    service.run_job(job.job_id)
    assert not audit(settings.climate_data_db_path, [job.job_id], ['era5'])['ready']
    with sqlite3.connect(settings.climate_data_db_path) as db:
        db.execute('DELETE FROM collection_observations WHERE rowid=(SELECT MIN(rowid) FROM collection_observations)')
    assert not audit(settings.climate_data_db_path, [job.job_id])['ready']


@pytest.mark.parametrize('args', [['--allow-live'], ['--live-request', 'never-read.json']])
def test_live_mode_requires_both_explicit_flags(args, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('No network without both flags'))
    with pytest.raises(SystemExit) as result:
        main(args)
    assert result.value.code == 2


def test_cli_logs_real_failure_and_returns_nonzero(tmp_path):
    log = tmp_path / 'audit.json'
    assert main(['--db', str(tmp_path / 'missing'), '--log', str(log)]) == 1
    assert json.loads(log.read_text(encoding='utf-8'))['ready'] is False
