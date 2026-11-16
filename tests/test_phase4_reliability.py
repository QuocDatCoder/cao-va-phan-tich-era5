from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
import requests

from backend.app.core.config import Settings
from backend.app.domain.schemas import DownloadRequest
from backend.app.providers.cds_retry import retrieve_with_retry
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.services.download_job_service import DownloadJobService
from test_source_downloads import collection, POINT, DATES


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError('fixture error', response=response)


@pytest.mark.parametrize('error', [http_error(429), http_error(503), requests.Timeout(), requests.ConnectionError()])
def test_transient_retries_are_bounded(error):
    class Client:
        calls = 0
        def retrieve(self, *args):
            self.calls += 1
            raise error
    client, delays = Client(), []
    with pytest.raises(type(error)):
        retrieve_with_retry(client, 'fixture', {}, Path('unused'), Settings(_env_file=None), sleep=delays.append)
    assert client.calls == 3 and delays == [2, 4]


@pytest.mark.parametrize('error', [http_error(400), http_error(401), http_error(403), http_error(404),
                                 http_error(422), requests.exceptions.SSLError(), ValueError('bad data')])
def test_permanent_errors_never_retry(error):
    class Client:
        calls = 0
        def retrieve(self, *args):
            self.calls += 1
            raise error
    client = Client()
    with pytest.raises(type(error)):
        retrieve_with_retry(client, 'fixture', {}, Path('unused'), Settings(_env_file=None),
                            sleep=lambda _: pytest.fail('No retry expected'))
    assert client.calls == 1


def test_retry_success_publishes_only_finished_file(tmp_path):
    settings = Settings(_env_file=None, cds_url='https://fixture.invalid', cds_api_key='fake',
                        cds_retry_base_seconds=0)
    target = tmp_path / 'data.nc'
    class Client:
        calls = 0
        def retrieve(self, dataset, request, destination):
            self.calls += 1
            assert not target.exists()
            Path(destination).write_bytes(b'partial' if self.calls == 1 else b'finished')
            if self.calls == 1:
                raise requests.Timeout()
    client = Client()
    ERA5ClimateProvider(settings, client_factory=lambda _: client)._retrieve({}, target)
    assert client.calls == 2 and target.read_bytes() == b'finished'


def test_failure_preserves_partial_without_publishing(tmp_path):
    settings = Settings(_env_file=None, cds_url='https://fixture.invalid', cds_api_key='fake')
    class Client:
        def retrieve(self, dataset, request, destination):
            Path(destination).write_bytes(b'partial')
            raise http_error(403)
    target = tmp_path / 'data.nc'
    with pytest.raises(Exception):
        ERA5ClimateProvider(settings, client_factory=lambda _: Client())._retrieve({}, target)
    assert not target.exists()
    assert next(tmp_path.glob('*.part')).read_bytes() == b'partial'


def test_jobs_wait_queued_and_execute_once_across_service_instances(collection, monkeypatch):
    settings, _, jobs, service, providers, _, _ = collection
    request = DownloadRequest(source='land', variable='temperature', location=POINT, **DATES)
    first = service.create_job(request)
    second = service.create_job(request.model_copy(update={'variable': 'total_precipitation'}))
    another = DownloadJobService(providers['mock'], jobs, provider_resolver=providers.__getitem__, settings=settings)
    started, release = threading.Event(), threading.Event()
    original = providers['land'].download_point
    calls = []
    def download(**kwargs):
        calls.append(kwargs['variable'])
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(providers['land'], 'download_point', download)
    with ThreadPoolExecutor(max_workers=3) as pool:
        one = pool.submit(service.run_job, first.job_id)
        assert started.wait(5)
        two = pool.submit(another.run_job, second.job_id)
        duplicate = pool.submit(another.run_job, first.job_id)
        try:
            assert jobs.get(second.job_id).status == 'queued'
        finally:
            release.set()
        for future in [one, two, duplicate]:
            future.result(timeout=10)
    assert len(calls) == 2
    assert all(jobs.get(j.job_id).status == 'completed' for j in [first, second])


@pytest.mark.parametrize('identity,interrupted', [('absent', True), (None, False)])
def test_queued_restart_recovery_is_conservative(collection, monkeypatch, identity, interrupted):
    import backend.app.services.worker_identity as workers
    settings, _, jobs, service, providers, cds, _ = collection
    job = service.create_job(DownloadRequest(source='land', variable='temperature', location=POINT, **DATES))
    monkeypatch.setattr(workers, 'process_identity', lambda _: identity)
    monkeypatch.setattr(cds, 'retrieve', lambda *a, **k: pytest.fail('Recovery must not call CDS'))
    restarted = DownloadJobService(providers['mock'], jobs, provider_resolver=providers.__getitem__, settings=settings)
    assert jobs.get(job.job_id).status == ('failed' if interrupted else 'queued')
    assert restarted.collections.get(job.job_id)['status'] == ('interrupted' if interrupted else 'queued')


def test_restart_handles_job_without_snapshot(collection, monkeypatch):
    import backend.app.services.worker_identity as workers
    settings, _, jobs, service, providers, _, _ = collection
    job = jobs.create('orphan', DownloadRequest(source='land', variable='temperature', location=POINT, **DATES))
    monkeypatch.setattr(workers, 'process_identity', lambda _: 'absent')
    DownloadJobService(providers['mock'], jobs, provider_resolver=providers.__getitem__, settings=settings)
    assert jobs.get(job.job_id).status == 'failed'


@pytest.mark.parametrize('failure', [requests.Timeout(), http_error(503)])
def test_sdk_session_preserves_transient_classification(monkeypatch, failure):
    from backend.app.providers.cds_retry import CDSSession, CDSTransientError
    def send(*args, **kwargs):
        if getattr(failure, 'response', None) is not None:
            from io import BytesIO
            failure.response.raw = BytesIO(b'')
            return failure.response
        raise failure
    monkeypatch.setattr(requests.Session, 'send', send)
    with pytest.raises(CDSTransientError):
        CDSSession().get('https://fixture.invalid')
