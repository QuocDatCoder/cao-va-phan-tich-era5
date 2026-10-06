from datetime import datetime, timedelta
from math import pi, sin

from backend.app.domain.schemas import ClimatePoint, Location, TimeSeriesResponse
from backend.app.providers.base import ClimateDataProvider


class MockClimateProvider(ClimateDataProvider):
    _UNITS = {
        "temperature": "K",
        "2m_temperature": "K",
        "precipitation": "mm",
        "total_precipitation": "mm",
    }

    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> TimeSeriesResponse:
        if end < start:
            raise ValueError("end must be greater than or equal to start")

        points: list[ClimatePoint] = []
        current = start
        while current <= end:
            points.append(
                ClimatePoint(
                    time=current,
                    value=self._value_for(variable=variable, timestamp=current),
                )
            )
            current += timedelta(hours=1)

        return TimeSeriesResponse(
            variable=variable,
            unit=self._UNITS.get(variable, "unknown"),
            location=Location(latitude=latitude, longitude=longitude),
            data=points,
        )

    def _value_for(self, variable: str, timestamp: datetime) -> float:
        hour_angle = 2 * pi * (timestamp.hour / 24)

        if variable in {"temperature", "2m_temperature"}:
            return round(299.15 + 5 * sin(hour_angle), 3)

        if variable in {"precipitation", "total_precipitation"}:
            return round(max(0.0, 2 * sin(hour_angle)), 3)

        return round(sin(hour_angle), 3)
