"""Cooperative cancellation of a collection; never terminate Python threads."""
from contextvars import ContextVar
from time import monotonic, sleep
from backend.app.providers.errors import ClimateProviderError


class JobCancelled(ClimateProviderError):
    pass


current_cancellation = ContextVar('collection_cancellation', default=None)


def check_cancelled():
    callback = current_cancellation.get()
    if callback is not None and callback():
        raise JobCancelled('Collection cancelled by user; previously imported data retained.')


def cancellable_wait(seconds):
    deadline = monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleep(min(.25, remaining))
