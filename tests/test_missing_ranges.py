from datetime import datetime

from backend.app.domain.time_utils import hourly_timestamps, missing_hour_ranges


def test_gaps_at_start_middle_and_end_era_separate():
    hours = hourly_timestamps(datetime(2025, 2, 1), datetime(2025, 2, 1, 8))
    available = {hours[2], hours[3], hours[6]}
    assert missing_hour_ranges(hours[0], hours[-1], available) == [
        (hours[0], hours[1]), (hours[4], hours[5]), (hours[7], hours[8])
    ]


def test_vietnam_day_splits_at_utc_midnight_without_extra_hours():
    start = datetime.fromisoformat("2025-02-01T00:00:00+07:00")
    end = datetime.fromisoformat("2025-02-01T23:00:00+07:00")
    assert missing_hour_ranges(start, end, set()) == [
        (datetime(2025, 1, 31, 17), datetime(2025, 1, 31, 23)),
        (datetime(2025, 2, 1), datetime(2025, 2, 1, 16)),
    ]


def test_subhour_range_excludes_hours_outside_interval():
    assert missing_hour_ranges(datetime(2025, 2, 1, 0, 30), datetime(2025, 2, 1, 2, 30), set()) == [
        (datetime(2025, 2, 1, 1), datetime(2025, 2, 1, 2)),
    ]


def test_complete_range_has_no_downloads():
    hours = hourly_timestamps(datetime(2025, 2, 1), datetime(2025, 2, 1, 23))
    assert missing_hour_ranges(hours[0], hours[-1], set(hours)) == []
