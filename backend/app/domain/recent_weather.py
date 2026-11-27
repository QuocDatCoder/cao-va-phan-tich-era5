"""Date window for collection from the GFS historical forecast archive."""
from datetime import date, datetime, timedelta, timezone

VIETNAM_TIME = timezone(timedelta(hours=7))
GFS_FIRST_DATE = date(2021, 3, 23)


def latest_weather_hour() -> datetime:
    return datetime.now(VIETNAM_TIME).replace(minute=0, second=0, microsecond=0)


def is_same_day_collection(start: datetime | None, end: datetime | None, created: datetime) -> bool:
    return bool(start and end and start.astimezone(VIETNAM_TIME).date()
                == end.astimezone(VIETNAM_TIME).date() == created.astimezone(VIETNAM_TIME).date())


def historical_date_bounds() -> tuple[date, date]:
    today = datetime.now(VIETNAM_TIME).date()
    earliest = date(today.year - 5, today.month, min(today.day, 28)) if (
        today.month == 2 and today.day == 29
    ) else today.replace(year=today.year - 5)
    return max(earliest, GFS_FIRST_DATE + timedelta(days=1)), today - timedelta(days=1)
