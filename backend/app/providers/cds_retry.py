"""Bounded transport retry; parsing and database writes must not be retried here."""
import logging
import time

import requests
from backend.app.domain.cancellation import check_cancelled, current_cancellation, cancellable_wait

logger = logging.getLogger(__name__)
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


class CDSTransientError(Exception):
    """Keep transport classification when SDKs otherwise erase it as 'Could not connect'."""
    def __init__(self, response=None):
        self.response = response
        super().__init__('Temporary CDS transport failure')


class CDSSession(requests.Session):
    def send(self, request, **kwargs):
        check_cancelled()
        try:
            response = super().send(request, **kwargs)
        except requests.exceptions.SSLError:
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise CDSTransientError() from exc
        if response.status_code in TRANSIENT_STATUSES:
            response.close()
            raise CDSTransientError(response)
        try:
            check_cancelled()
        except Exception:
            response.close()
            raise
        original_iter = response.iter_content
        def chunks(*args, **options):
            try:
                for chunk in original_iter(*args, **options):
                    check_cancelled()
                    yield chunk
            finally:
                response.close()
        response.iter_content = chunks
        return response


class CancellableCDSClient:
    """Use the installed modern SDK's public submit/results API for short waits."""
    def __init__(self, client):
        self.client = client

    def retrieve(self, dataset, request, target):
        check_cancelled()
        api = getattr(self.client, 'client', None)
        if current_cancellation.get() is None or not callable(getattr(api, 'submit', None)):
            return self.client.retrieve(dataset, request, target)
        remote = api.submit(dataset, request)
        # Stop waiting locally; do not claim that the remote CDS job was deleted.
        while True:
            check_cancelled()
            if remote.results_ready:
                break
            cancellable_wait(1)
        check_cancelled()
        return remote.download(target)


def status_code(error):
    response = getattr(error, 'response', None)
    return getattr(response, 'status_code', None)


def retryable(error):
    status = status_code(error)
    if status is not None:
        return status in TRANSIENT_STATUSES
    # Certificate errors and invalid URLs require configuration changes.
    if isinstance(error, requests.exceptions.SSLError):
        return False
    return isinstance(error, (CDSTransientError, requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError))


def retrieve_with_retry(client, dataset, request, target, settings, *, sleep=time.sleep):
    for attempt in range(1, settings.cds_max_attempts + 1):
        check_cancelled()
        try:
            client.retrieve(dataset, request, str(target))
            return
        except Exception as exc:
            if not retryable(exc) or attempt == settings.cds_max_attempts:
                raise
            delay = min(30, settings.cds_retry_base_seconds * 2 ** (attempt - 1))
            # Never log exception bodies/URLs; they may contain credentials.
            logger.warning('CDS transient failure: dataset=%s attempt=%s/%s status=%s type=%s delay=%ss',
                           dataset, attempt, settings.cds_max_attempts,
                           status_code(exc), type(exc).__name__, delay)
            if current_cancellation.get() is not None:
                cancellable_wait(delay)
            else:
                sleep(delay)
