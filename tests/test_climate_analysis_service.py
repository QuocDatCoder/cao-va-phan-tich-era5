from datetime import datetime
from math import nan

import pytest

from backend.app.domain.schemas import ClimatePoint, Location, TimeSeriesRequest, TimeSeriesResponse
from backend.app.services.climate_analysis_service import ClimateAnalysisError, ClimateAnalysisService


class StaticClimateService:
    def __init__(self, responses: dict[tuple[datetime, datetime], list[float]]) -> None:
        self._responses = responses

    def get_timeseries(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
        values = self._responses.get((request.start, request.end), [])
        return TimeSeriesResponse(
            variable=request.variable,
            unit="K",
            location=request.location,
            data=[
                ClimatePoint(time=request.start.replace(hour=index), value=value)
                for index, value in enumerate(values)
            ],
        )


class FailingClimateService:
    def get_timeseries(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
        raise RuntimeError("provider unavailable")


def make_request(start: datetime, end: datetime) -> TimeSeriesRequest:
    return TimeSeriesRequest(
        variable="temperature",
        location=Location(latitude=10.0, longitude=106.0),
        start=start,
        end=end,
    )


def test_statistics_uses_population_standard_deviation() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)
    service = ClimateAnalysisService(StaticClimateService({(start, end): [1.0, 3.0, 5.0]}))

    response = service.statistics(make_request(start, end))

    assert response.mean == 3.0
    assert response.min == 1.0
    assert response.max == 5.0
    assert response.std == 1.632993
    assert response.count == 3


def test_trend_uses_linear_regression_over_observation_index() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)
    service = ClimateAnalysisService(StaticClimateService({(start, end): [1.0, 3.0, 5.0]}))

    response = service.trend(make_request(start, end))

    assert response.slope == 2.0
    assert response.direction == "increasing"
    assert response.period == "2025-01-01-2025-01-01"
    assert response.count == 3


def test_trend_rejects_single_observation() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 0)
    service = ClimateAnalysisService(StaticClimateService({(start, end): [1.0]}))

    with pytest.raises(ClimateAnalysisError):
        service.trend(make_request(start, end))


def test_anomaly_uses_observation_minus_baseline_mean() -> None:
    observation_start = datetime(2025, 1, 2, 0)
    observation_end = datetime(2025, 1, 2, 1)
    baseline_start = datetime(2025, 1, 1, 0)
    baseline_end = datetime(2025, 1, 1, 1)
    service = ClimateAnalysisService(
        StaticClimateService(
            {
                (observation_start, observation_end): [4.0, 6.0],
                (baseline_start, baseline_end): [2.0, 4.0],
            }
        )
    )

    response = service.anomaly(
        request=make_request(observation_start, observation_end),
        baseline_request=make_request(baseline_start, baseline_end),
    )

    assert response.baseline_mean == 3.0
    assert [point.value for point in response.data] == [4.0, 6.0]
    assert [point.anomaly for point in response.data] == [1.0, 3.0]


def test_compare_returns_period_mean_difference() -> None:
    period_a_start = datetime(2025, 1, 2, 0)
    period_a_end = datetime(2025, 1, 2, 1)
    period_b_start = datetime(2025, 1, 1, 0)
    period_b_end = datetime(2025, 1, 1, 1)
    service = ClimateAnalysisService(
        StaticClimateService(
            {
                (period_a_start, period_a_end): [4.0, 6.0],
                (period_b_start, period_b_end): [1.0, 3.0],
            }
        )
    )

    response = service.compare(
        period_a_request=make_request(period_a_start, period_a_end),
        period_b_request=make_request(period_b_start, period_b_end),
    )

    assert response.period_a.mean == 5.0
    assert response.period_b.mean == 2.0
    assert response.difference == 3.0


def test_heatmap_groups_monthly_means() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 2, 1, 2)

    class HeatmapClimateService:
        def get_timeseries(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
            return TimeSeriesResponse(
                variable=request.variable,
                unit="K",
                location=request.location,
                data=[
                    ClimatePoint(time=datetime(2025, 1, 1, 0), value=1.0),
                    ClimatePoint(time=datetime(2025, 1, 2, 0), value=3.0),
                    ClimatePoint(time=datetime(2025, 2, 1, 0), value=5.0),
                ],
            )

    service = ClimateAnalysisService(HeatmapClimateService())

    response = service.heatmap(make_request(start, end))

    assert [cell.model_dump() for cell in response.data] == [
        {"year": 2025, "month": 1, "value": 2.0, "count": 2},
        {"year": 2025, "month": 2, "value": 5.0, "count": 1},
    ]


def test_map_returns_period_mean_for_requested_point() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)
    service = ClimateAnalysisService(StaticClimateService({(start, end): [1.0, 3.0, 5.0]}))

    response = service.map(make_request(start, end))

    assert response.data[0].latitude == 10.0
    assert response.data[0].longitude == 106.0
    assert response.data[0].value == 3.0


def test_analysis_rejects_empty_timeseries() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)
    service = ClimateAnalysisService(StaticClimateService({(start, end): []}))

    with pytest.raises(ClimateAnalysisError):
        service.statistics(make_request(start, end))


def test_analysis_ignores_nan_observations() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)
    service = ClimateAnalysisService(StaticClimateService({(start, end): [1.0, nan, 5.0]}))

    response = service.statistics(make_request(start, end))

    assert response.mean == 3.0
    assert response.count == 2


def test_provider_failure_propagates_to_api_error_handling() -> None:
    start = datetime(2025, 1, 1, 0)
    end = datetime(2025, 1, 1, 2)
    service = ClimateAnalysisService(FailingClimateService())

    with pytest.raises(RuntimeError):
        service.statistics(make_request(start, end))
