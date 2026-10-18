from datetime import UTC, date, datetime

from frontend.components.controls import local_date_range
from frontend.dashboard import _download_payload


def test_local_date_filter_covers_exactly_one_vietnam_day():
    start, end = local_date_range(date(2025, 2, 1), date(2025, 2, 1))
    assert start == "2025-02-01T00:00:00+07:00"
    assert end == "2025-02-01T23:00:00+07:00"
    assert datetime.fromisoformat(start).astimezone(UTC).isoformat() == "2025-01-31T17:00:00+00:00"


def test_download_job_covers_both_utc_dates_for_one_vietnam_day():
    start, end = local_date_range(date(2025, 2, 1), date(2025, 2, 1))
    payload = _download_payload({
        "variable": "temperature", "latitude": 10.8231, "longitude": 106.6297,
        "start": start, "end": end,
    })
    assert payload["start_date"] == "2025-01-31"
    assert payload["end_date"] == "2025-02-01"
