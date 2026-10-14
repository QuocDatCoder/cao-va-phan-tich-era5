from backend.app.domain.schemas import ClimatePoint, TimeSeriesRequest, TimeSeriesResponse
from backend.app.domain.time_utils import to_utc_naive, to_vietnam_time
from backend.app.providers.base import ClimateDataProvider


class ClimateService:
    def __init__(self, provider: ClimateDataProvider) -> None:
        self._provider = provider

    def get_timeseries(self, request: TimeSeriesRequest) -> TimeSeriesResponse:
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
