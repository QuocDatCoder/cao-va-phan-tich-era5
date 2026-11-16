from abc import ABC, abstractmethod
from datetime import date, datetime

from backend.app.domain.schemas import DownloadArea, DownloadArtifact, Location, TimeSeriesResponse


class ClimateDataProvider(ABC):
    def download_point(
        self, variable: str, location: Location, start_date: date, end_date: date, frequency: str,
    ) -> DownloadArtifact:
        return self.download_area(variable, DownloadArea(
            north=location.latitude, south=location.latitude,
            west=location.longitude, east=location.longitude,
        ), start_date, end_date, frequency)

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
