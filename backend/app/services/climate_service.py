from backend.app.domain.schemas import TimeSeriesRequest, TimeSeriesResponse
from backend.app.providers.base import ClimateDataProvider


class ClimateService:
    def __init__(self, provider: ClimateDataProvider) -> None:
        self._provider = provider

    def get_timeseries(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
        return self._provider.get_timeseries(
            variable=request.variable,
            latitude=request.location.latitude,
            longitude=request.location.longitude,
            start=request.start,
            end=request.end,
        )
