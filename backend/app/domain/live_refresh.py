from dataclasses import dataclass
from datetime import datetime


LIVE_INTERVAL_HOURS = (1, 3, 6, 24)
DEFAULT_LIVE_INTERVAL_HOURS = 1
MAX_ACTIVE_LIVE_SCHEDULES = 5
INACTIVE_AFTER_SECONDS = 7 * 86400


@dataclass(frozen=True)
class LiveRefreshPolicy:
    effective_enabled: bool
    effective_interval_hours: int | None
    backed_off: bool
    disabled_reason: str | None


def adaptive_backoff(
    requested_interval_hours: int,
    idle_seconds: float,
    enabled: bool,
    disabled_reason: str | None = None,
) -> LiveRefreshPolicy:
    if requested_interval_hours not in LIVE_INTERVAL_HOURS:
        raise ValueError("Unsupported live refresh interval.")
    if not enabled:
        return LiveRefreshPolicy(False, None, False, disabled_reason)
    if idle_seconds >= INACTIVE_AFTER_SECONDS:
        return LiveRefreshPolicy(False, None, False, "inactive_7_days")
    minimum = 24 if idle_seconds >= 86400 else 6 if idle_seconds >= 6 * 3600 else 1
    interval = max(requested_interval_hours, minimum)
    return LiveRefreshPolicy(True, interval, interval > requested_interval_hours, None)


def refresh_due(schedule, now):
    if not schedule["enabled"]:
        return False
    last = schedule["last_refreshed_at"]
    return last is None or now - datetime.fromisoformat(last).timestamp() >= schedule["effective_interval_hours"] * 3600
