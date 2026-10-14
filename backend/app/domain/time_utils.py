from datetime import UTC, datetime, timedelta, timezone


VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def to_utc_naive(value: datetime) -> datetime:
    """Providers use naive UTC; API datetimes without an offset also mean UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def to_vietnam_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(VIETNAM_TIMEZONE)
