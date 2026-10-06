from abc import ABC, abstractmethod
from datetime import date, datetime

from backend.app.domain.schemas import DownloadArea, DownloadArtifact, TimeSeriesResponse


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

    @abstractmethod
    def download_area(
        self,
        variable: str,
        area: DownloadArea,
        start_date: date,
        end_date: date,
        frequency: str,
    ) -> DownloadArtifact:
        raise NotImplementedError
