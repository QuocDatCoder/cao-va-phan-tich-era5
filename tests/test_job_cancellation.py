from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from test_source_downloads import collection, POINT, DATES
from backend.app.domain.schemas import DownloadRequest
from backend.app.domain.cancellation import JobCancelled, current_cancellation, cancellable_wait
from backend.app.providers.cds_retry import CancellableCDSClient


def request(variable='temperature'):
    return DownloadRequest(source='mock',variable=variable,location=POINT,**DATES)


def test_cancel_queued_is_idempotent_persistent_and_never_downloads(collection, monkeypatch):
    settings, _, jobs, service, providers, _, api = collection
    jid = service.create_job(request()).job_id
    monkeypatch.setattr(providers['mock'], 'download_point', lambda **kw: pytest.fail('Cancelled job ran'))
    for _ in range(2):
        reply = api.post(f'/api/v1/jobs/{jid}/cancel')
        assert reply.status_code == 200 and reply.json()['status'] == 'cancelled'
    service.run_job(jid)
    from backend.app.storage.job_repository import JobRepository
    assert JobRepository(settings.job_metadata_db_path).get(jid).status == 'cancelled'
    assert service.collections.get(jid)['status'] == 'cancelled'
    assert api.post('/api/v1/jobs/absent/cancel').status_code == 404
    assert service.create_job(request()).job_id != jid


def test_delayed_callback_after_cancel_and_delete_is_harmless(collection, monkeypatch):
    _, _, _, service, providers, _, api = collection
    jid=service.create_job(request()).job_id
    assert api.post(f'/api/v1/jobs/{jid}/cancel').json()['status']=='cancelled'
    assert api.delete(f'/api/v1/collections/{jid}').status_code==200
    monkeypatch.setattr(providers['mock'],'download_point',lambda **kw:pytest.fail('Deleted job downloaded'))
    service.run_job(jid)


def test_running_cancel_retains_data_releases_queue_and_cannot_be_overwritten(collection, monkeypatch):
    _, _, jobs, service, providers, _, api = collection
    first = service.create_job(request()).job_id
    second = service.create_job(request('dewpoint')).job_id
    started = Event()
    original = providers['mock'].download_point
    def download(**kwargs):
        artifact = original(**kwargs)
        if kwargs['variable'] == 'temperature':
            started.set()
            cancellable_wait(30)
        return artifact
    monkeypatch.setattr(providers['mock'], 'download_point', download)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_work = pool.submit(service.run_job,first)
        assert started.wait(5)
        next_work = pool.submit(service.run_job,second)
        response = api.post(f'/api/v1/jobs/{first}/cancel')
        assert response.json()['status'] in {'cancelling','cancelled'}
        first_work.result(timeout=5)
        next_work.result(timeout=5)
    assert jobs.get(first).status == 'cancelled'
    assert service.collections.get(first)['status'] == 'cancelled'
    assert service.collections.get(first)['summary']['imported_values'] == 24
    assert jobs.get(second).status == 'completed'
    jobs.mark_failed(first,'late failure')
    assert jobs.get(first).status == 'cancelled'
    jobs.mark_completed(first,original(variable='temperature',location=request().location,
                                     start_date=request().start_date,end_date=request().end_date,frequency='hourly'))
    assert jobs.get(first).status == 'cancelled'
    assert api.get(f'/api/v1/collections/{first}/timeseries').json()['count'] == 24
    assert api.post(f'/api/v1/jobs/{second}/cancel').json()['status'] == 'completed'


def test_modern_cds_poll_can_stop_without_download_or_extra_submit():
    cancel = Event()
    class Remote:
        @property
        def results_ready(self):
            cancel.set()
            return False
        def download(self,target):
            pytest.fail('Cancelled remote result was downloaded')
    calls=[]
    def submit(*args):
        calls.append(args)
        return Remote()
    client=CancellableCDSClient(SimpleNamespace(client=SimpleNamespace(submit=submit)))
    token=current_cancellation.set(cancel.is_set)
    try:
        with pytest.raises(JobCancelled):
            client.retrieve('fixture',{},'unused.part')
    finally:
        current_cancellation.reset(token)
    assert len(calls)==1


def test_restart_finishes_abandoned_cancellation(collection, monkeypatch):
    import backend.app.services.worker_identity as workers
    from backend.app.storage.collection_repository import recover_abandoned
    _, _, jobs, service, _, _, _ = collection
    jid=service.create_job(request()).job_id
    assert jobs.claim_running(jid)
    service.collections.running(jid)
    assert service.cancel_job(jid).status == 'cancelling'
    monkeypatch.setattr(workers,'process_identity',lambda pid:'dead-worker')
    recover_abandoned(service.collections,jobs)
    assert jobs.get(jid).status == 'cancelled'
    assert service.collections.get(jid)['status'] == 'cancelled'
