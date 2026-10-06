from abc import ABC, abstractmethod
from datetime import datetime

from backend.app.domain.schemas import TimeSeriesResponse


class ClimateDataProvider(ABC):
    @abstractmethod
    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
    ) -> TimeSeriesResponse:
        raise NotImplementedError
