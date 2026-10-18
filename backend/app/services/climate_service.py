from datetime import timedelta
from backend.app.domain.schemas import AggregationRequest, AggregationResponse, ClimatePoint, TimeSeriesRequest, TimeSeriesResponse
from backend.app.domain.time_utils import to_utc_naive, to_vietnam_time
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.errors import ClimateDataError
from backend.app.services.aggregation import ACCUMULATED_VARIABLES, aggregate_series, resolve_aggregation


class ClimateService:
    def __init__(self, provider: ClimateDataProvider, max_query_days: int = 366) -> None:
        self._provider = provider
        self._max_query_days = max_query_days

    def get_timeseries(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
        if to_utc_naive(request.end) - to_utc_naive(request.start) >= timedelta(days=self._max_query_days):
            raise ClimateDataError(f"Climate queries are limited to {self._max_query_days} days.")
        series = self._provider.get_timeseries(
            variable=request.variable,
            latitude=request.location.latitude,
            longitude=request.location.longitude,
            start=to_utc_naive(request.start),
            end=to_utc_naive(request.end),
        )
        convert_temperature = series.unit == "K" and series.variable in {
            "temperature", "2m_temperature", "dewpoint", "2m_dewpoint_temperature"
        }
        return TimeSeriesResponse(
            variable=series.variable,
            unit="°C" if convert_temperature else series.unit,
            location=series.location,
            data=[
                ClimatePoint(
                    time=to_vietnam_time(point.time),
                    value=round(point.value - 273.15, 6) if convert_temperature else point.value,
                )
                for point in series.data
            ],
        )

    def get_aggregate(self, request: AggregationRequest) -> AggregationResponse:
        method = resolve_aggregation(request.variable, request.aggregation)
        source_request = TimeSeriesRequest(**request.model_dump(exclude={"frequency", "aggregation"}))
        accumulated = request.variable in ACCUMULATED_VARIABLES
        if accumulated:
            # ERA5 accumulations end at the validity timestamp. Group by hour start.
            source_request = source_request.model_copy(update={
                "start": request.start + timedelta(hours=1), "end": request.end + timedelta(hours=1),
            })
        series = self.get_timeseries(source_request)
        if accumulated:
            series = series.model_copy(update={"data": [
                point.model_copy(update={"time": point.time - timedelta(hours=1)}) for point in series.data
            ]})
        return aggregate_series(series, request, method)
