from datetime import datetime

import pytest

from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.mock_provider import MockClimateProvider


def test_mock_provider_implements_climate_data_provider() -> None:
    provider = MockClimateProvider()

    assert isinstance(provider, ClimateDataProvider)


def test_mock_provider_returns_hourly_deterministic_timeseries() -> None:
    provider = MockClimateProvider()
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)

    first = provider.get_timeseries("temperature", 10.8231, 106.6297, start, end)
    second = provider.get_timeseries("temperature", 10.8231, 106.6297, start, end)

    assert first == second
    assert first.variable == "temperature"
    assert first.unit == "K"
    assert first.location.latitude == 10.8231
    assert first.location.longitude == 106.6297
    assert [point.time for point in first.data] == [
        datetime(2025, 1, 1, 0),
        datetime(2025, 1, 1, 1),
        datetime(2025, 1, 1, 2),
    ]
    assert [point.value for point in first.data] == [299.15, 300.444, 301.65]


def test_mock_provider_rejects_inverted_date_range() -> None:
    provider = MockClimateProvider()

    with pytest.raises(ValueError):
        provider.get_timeseries(
            variable="temperature",
            latitude=10.8231,
            longitude=106.6297,
            start=datetime(2025, 1, 2),
            end=datetime(2025, 1, 1),
        )
