from datetime import datetime

import pytest
from pydantic import ValidationError

from backend.app.domain.schemas import Location, TimeSeriesRequest


def test_location_accepts_valid_coordinates() -> None:
    location = Location(latitude=10.8231, longitude=106.6297)

    assert location.latitude == 10.8231
    assert location.longitude == 106.6297


def test_location_rejects_invalid_latitude() -> None:
    with pytest.raises(ValidationError):
        Location(latitude=91, longitude=106.6297)


def test_timeseries_request_rejects_inverted_date_range() -> None:
    with pytest.raises(ValidationError):
        TimeSeriesRequest(
            variable="temperature",
            location=Location(latitude=10.8231, longitude=106.6297),
            start=datetime(2025, 1, 2),
            end=datetime(2025, 1, 1),
        )
