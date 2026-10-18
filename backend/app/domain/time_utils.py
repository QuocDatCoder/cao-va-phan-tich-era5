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


def hourly_timestamps(start: datetime, end: datetime) -> list[datetime]:
    """UTC observation hours inside the inclusive interval."""
    start, end = to_utc_naive(start), to_utc_naive(end)
    current = start.replace(minute=0, second=0, microsecond=0)
    if current < start:
        current += timedelta(hours=1)
    hours = []
    while current <= end:
        hours.append(current)
        current += timedelta(hours=1)
    return hours


def missing_hour_ranges(
    start: datetime, end: datetime, available: set[datetime],
) -> list[tuple[datetime, datetime]]:
    """Group missing consecutive hours within each UTC day.

    CDS combines dates and hours as a Cartesian product. Splitting at midnight
    prevents requesting already cached hours on the boundary days.
    """
    available = {to_utc_naive(value) for value in available}
    ranges: list[tuple[datetime, datetime]] = []
    for hour in hourly_timestamps(start, end):
        if hour in available:
            continue
        if ranges and ranges[-1][1] + timedelta(hours=1) == hour and ranges[-1][0].date() == hour.date():
            ranges[-1] = (ranges[-1][0], hour)
        else:
            ranges.append((hour, hour))
    return ranges


def batch_missing_ranges(ranges: list[tuple[datetime, datetime]], max_days: int) -> list[tuple[datetime, datetime]]:
    """Combine consecutive complete UTC days, never partial boundary days."""
    batches: list[tuple[datetime, datetime]] = []
    for start, end in ranges:
        if (batches and start.hour == 0 and end.hour == 23
            and batches[-1][0].hour == 0 and batches[-1][1].hour == 23
            and batches[-1][1] + timedelta(hours=1) == start
            and (end.date() - batches[-1][0].date()).days < max_days):
            batches[-1] = (batches[-1][0], end)
        else:
            batches.append((start, end))
    return batches


def request_chunks(start: datetime, end: datetime, max_days: int):
    """Disjoint inclusive hourly request windows bounded by elapsed duration."""
    yield from batch_missing_ranges(missing_hour_ranges(start, end, set()), max_days)
