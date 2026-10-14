from collections import defaultdict
from math import isfinite, sqrt

from backend.app.domain.schemas import (
    AnomalyPoint,
    AnomalyResponse,
    ClimatePoint,
    ComparePeriodSummary,
    CompareResponse,
    HeatmapCell,
    HeatmapResponse,
    Location,
    MapPoint,
    MapResponse,
    StatisticsResponse,
    TimeSeriesRequest,
    TimeSeriesResponse,
    TrendResponse,
)
from backend.app.services.climate_service import ClimateService
from backend.app.domain.time_utils import to_vietnam_time


class ClimateAnalysisError(Exception):
    pass


class ClimateAnalysisService:
    def __init__(self, climate_service: ClimateService) -> None:
        self._climate_service = climate_service

    def statistics(self, request: TimeSeriesRequest) -> StatisticsResponse:
        series = self._get_non_empty_series(request)
        values = self._values(series.data)
        mean = self._mean(values)
        return StatisticsResponse(
            variable=series.variable,
            unit=series.unit,
            location=series.location,
            mean=self._round(mean),
            min=self._round(min(values)),
            max=self._round(max(values)),
            std=self._round(self._population_std(values, mean)),
            count=len(values),
        )

    def trend(self, request: TimeSeriesRequest) -> TrendResponse:
        series = self._get_non_empty_series(request)
        points = self._sorted_points(series.data)
        if len(points) < 2:
            raise ClimateAnalysisError("Trend analysis requires at least two observations.")

        values = [point.value for point in points]
        slope = self._linear_slope(values)
        return TrendResponse(
            variable=series.variable,
            unit=series.unit,
            location=series.location,
            slope=self._round(slope),
            direction=self._trend_direction(slope),
            period=f"{to_vietnam_time(request.start).date().isoformat()}-{to_vietnam_time(request.end).date().isoformat()}",
            count=len(values),
        )

    def anomaly(
        self,
        request: TimeSeriesRequest,
        baseline_request: TimeSeriesRequest,
    ) -> AnomalyResponse:
        series = self._get_non_empty_series(request)
        baseline = self._get_non_empty_series(baseline_request)
        self._ensure_compatible(series=series, other=baseline)

        baseline_mean = self._mean(self._values(baseline.data))
        data = [
            AnomalyPoint(
                time=point.time,
                value=self._round(point.value),
                anomaly=self._round(point.value - baseline_mean),
            )
            for point in self._sorted_points(series.data)
        ]
        return AnomalyResponse(
            variable=series.variable,
            unit=series.unit,
            location=series.location,
            baseline_start=to_vietnam_time(baseline_request.start),
            baseline_end=to_vietnam_time(baseline_request.end),
            baseline_mean=self._round(baseline_mean),
            data=data,
        )

    def compare(
        self,
        period_a_request: TimeSeriesRequest,
        period_b_request: TimeSeriesRequest,
    ) -> CompareResponse:
        period_a = self._get_non_empty_series(period_a_request)
        period_b = self._get_non_empty_series(period_b_request)
        self._ensure_compatible(series=period_a, other=period_b)

        mean_a = self._mean(self._values(period_a.data))
        mean_b = self._mean(self._values(period_b.data))
        return CompareResponse(
            variable=period_a.variable,
            unit=period_a.unit,
            location=period_a.location,
            period_a=ComparePeriodSummary(
                start=to_vietnam_time(period_a_request.start),
                end=to_vietnam_time(period_a_request.end),
                mean=self._round(mean_a),
                count=len(period_a.data),
            ),
            period_b=ComparePeriodSummary(
                start=to_vietnam_time(period_b_request.start),
                end=to_vietnam_time(period_b_request.end),
                mean=self._round(mean_b),
                count=len(period_b.data),
            ),
            difference=self._round(mean_a - mean_b),
        )

    def heatmap(self, request: TimeSeriesRequest) -> HeatmapResponse:
        series = self._get_non_empty_series(request)
        grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
        for point in self._sorted_points(series.data):
            grouped[(point.time.year, point.time.month)].append(point.value)

        cells = [
            HeatmapCell(
                year=year,
                month=month,
                value=self._round(self._mean(values)),
                count=len(values),
            )
            for (year, month), values in sorted(grouped.items())
        ]
        return HeatmapResponse(
            variable=series.variable,
            unit=series.unit,
            location=series.location,
            data=cells,
        )

    def map(self, request: TimeSeriesRequest) -> MapResponse:
        series = self._get_non_empty_series(request)
        values = self._values(series.data)
        return MapResponse(
            variable=series.variable,
            unit=series.unit,
            data=[
                MapPoint(
                    latitude=series.location.latitude,
                    longitude=series.location.longitude,
                    value=self._round(self._mean(values)),
                )
            ],
        )

    def _get_non_empty_series(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
        series = self._climate_service.get_timeseries(request)
        points = [point for point in self._sorted_points(series.data) if isfinite(point.value)]
        if not points:
            raise ClimateAnalysisError("Analysis requires at least one observation.")
        return TimeSeriesResponse(
            variable=series.variable,
            unit=series.unit,
            location=series.location,
            data=points,
        )

    def _ensure_compatible(self, series: TimeSeriesResponse, other: TimeSeriesResponse) -> None:
        if series.variable != other.variable:
            raise ClimateAnalysisError("Analysis inputs must use the same variable.")
        if series.unit != other.unit:
            raise ClimateAnalysisError("Analysis inputs must use the same unit.")
        if series.location != other.location:
            raise ClimateAnalysisError("Analysis inputs must use the same location.")

    def _values(self, points: list[ClimatePoint]) -> list[float]:
        return [point.value for point in self._sorted_points(points)]

    def _sorted_points(self, points: list[ClimatePoint]) -> list[ClimatePoint]:
        return sorted(points, key=lambda point: point.time)

    def _mean(self, values: list[float]) -> float:
        return sum(values) / len(values)

    def _population_std(self, values: list[float], mean: float) -> float:
        return sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    def _linear_slope(self, values: list[float]) -> float:
        count = len(values)
        x_mean = (count - 1) / 2
        y_mean = self._mean(values)
        numerator = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values))
        denominator = sum((index - x_mean) ** 2 for index in range(count))
        return numerator / denominator

    def _trend_direction(self, slope: float) -> str:
        if slope > 0:
            return "increasing"
        if slope < 0:
            return "decreasing"
        return "stable"

    def _round(self, value: float) -> float:
        return round(value, 6)
