"""SQLite-only queries: no provider factory, CDS client, raw-file import or synthesis."""
from datetime import datetime, timedelta

from backend.app.core.config import Settings
from backend.app.domain.schemas import AggregationRequest, ClimatePoint, DataSource, TimeSeriesRequest, TimeSeriesResponse
from backend.app.domain.stored import MissingRange, StoredMetadata, StoredTimeSeriesResponse
from backend.app.domain.sources import source_dataset
from backend.app.domain.time_utils import hourly_timestamps, missing_hour_ranges, to_utc_naive, to_vietnam_time
from backend.app.domain.wind import DERIVED_WIND_UNITS, WIND_COMPONENTS, derive_wind
from backend.app.providers.era5_provider import ERA5_VARIABLES
from backend.app.providers.errors import UnsupportedVariableError
from backend.app.services.aggregation import ACCUMULATED_VARIABLES, aggregate_series, resolve_aggregation
from backend.app.services.climate_analysis_service import ClimateAnalysisService, ClimateAnalysisError
from backend.app.storage.climate_repository import ClimateRepository


class _SnapshotService:
    """Allow existing analysis formulas to consume only this query's immutable series."""
    def __init__(self, series):
        self.series = series

    def get_timeseries(self, request):
        return self.series


class StoredClimateService:
    def __init__(self, repository: ClimateRepository, settings: Settings, *, recorded_dataset=None):
        self.repository = repository
        self.settings = settings
        self.recorded_dataset = recorded_dataset

    def catalog(self, source, limit=100, offset=0):
        dataset = self.recorded_dataset or source_dataset(source, self.settings)
        entries = self.repository.catalog(dataset, limit, offset)
        for entry in entries:
            entry['start'] = to_vietnam_time(datetime.fromisoformat(entry['start'])).isoformat()
            entry['end'] = to_vietnam_time(datetime.fromisoformat(entry['end'])).isoformat()
        return {"source": source, "dataset": dataset, "timezone": "UTC+7", "entries": entries,
                "limit": limit, "offset": offset, "coverage_scope": "stored_min_max"}

    def timeseries(self, source: DataSource, request: TimeSeriesRequest) -> StoredTimeSeriesResponse:
        start, end = to_utc_naive(request.start), to_utc_naive(request.end)
        if end - start >= timedelta(days=self.settings.era5_max_query_days):
            raise ValueError(f"Queries are limited to {self.settings.era5_max_query_days} days.")
        expected = hourly_timestamps(start, end)
        if not expected:
            raise ValueError("The requested interval contains no hourly observations.")
        expected_set = set(expected)
        if request.variable not in ERA5_VARIABLES:
            raise UnsupportedVariableError(f"Unsupported variable '{request.variable}'.")
        spec = ERA5_VARIABLES[request.variable]
        dataset = self.recorded_dataset or source_dataset(source, self.settings)
        variables = WIND_COMPONENTS if request.variable in DERIVED_WIND_UNITS else (spec.cds_name,)
        grids, components, missing = [], [], []
        available = set(expected)
        for name in variables:
            unit = ERA5_VARIABLES[name].unit
            grid, points = self.repository.read_available(
                dataset=dataset, variable=name, location=request.location, unit=unit, start=start, end=end)
            points = [p for p in points if p.time in expected_set]
            times = {p.time for p in points}
            available &= times
            grids.append(grid)
            components.append(TimeSeriesResponse(variable=name, unit=unit,
                                                  location=request.location, data=points))
            for a, b in missing_hour_ranges(start, end, times):
                missing.append(MissingRange(variable=name, start=to_vietnam_time(a), end=to_vietnam_time(b),
                                            count=int((b-a).total_seconds() // 3600) + 1))
        if len(variables) == 2:
            if all(grids) and grids[0] != grids[1]:
                raise ValueError("Stored wind components use different grid points; cannot combine them.")
            aligned = [s.model_copy(update={"data": [p for p in s.data if p.time in available]})
                       for s in components]
            points = derive_wind(request.variable, *aligned).data if available else []
        else:
            points = components[0].data
        temperature = spec.unit == "K"
        data = [ClimatePoint(time=to_vietnam_time(p.time),
                             value=round(p.value - 273.15, 6) if temperature else p.value) for p in points]
        complete = len(available) == len(expected)
        return StoredTimeSeriesResponse(
            variable=request.variable, unit="°C" if temperature else spec.unit,
            location=request.location, data=data, source=source, dataset=dataset,
            grid_location=grids[0] if grids and all(g == grids[0] for g in grids) else None,
            expected_count=len(expected), available_count=len(available), count=len(data),
            coverage=round(len(available)/len(expected), 6), complete=complete,
            status="complete" if complete else "partial" if available else "missing",
            missing_ranges=missing, undefined_count=len(available)-len(data),
        )

    @staticmethod
    def metadata(series):
        return StoredMetadata.model_validate(series.model_dump()).model_dump(mode="json")

    def query(self, operation: str, source: DataSource, request: TimeSeriesRequest,
              frequency: str = "daily", aggregation: str = "auto") -> dict:
        # Accumulated observations end at their validity timestamp, as in the legacy service.
        shifted = operation in {"aggregate", "heatmap"} and request.variable in ACCUMULATED_VARIABLES
        read_request = request.model_copy(update={
            "start": request.start + timedelta(hours=1), "end": request.end + timedelta(hours=1),
        }) if shifted else request
        series = self.timeseries(source, read_request)
        metadata = self.metadata(series)
        if operation == "timeseries":
            return series.model_dump(mode="json")
        base = {"variable": series.variable, "unit": series.unit,
                "location": request.location.model_dump(), "data": []}
        analysis = ClimateAnalysisService(_SnapshotService(series))
        if operation in {"aggregate", "heatmap"}:
            frequency = "monthly" if operation == "heatmap" else frequency
            aggregate_request = AggregationRequest(**request.model_dump(), frequency=frequency,
                                                   aggregation="auto" if operation == "heatmap" else aggregation)
            grouped = series.model_copy(update={"data": [
                p.model_copy(update={"time": p.time-timedelta(hours=1)}) for p in series.data
            ]}) if shifted else series
            result = aggregate_series(grouped, aggregate_request,
                                      resolve_aggregation(request.variable, aggregate_request.aggregation))
            base = result.model_dump(mode="json")
            if operation == "heatmap":
                base["data"] = [{"year": p.time.year, "month": p.time.month, "value": p.value,
                                 "count": p.count, "expected_count": p.expected_count,
                                 "coverage": p.coverage, "partial_period": p.partial_period,
                                 "complete": p.complete} for p in result.data if p.value is not None]
        elif operation == "statistics":
            base = (analysis.statistics(request).model_dump(mode="json") if series.data else
                    {**base, "mean": None, "min": None, "max": None, "std": None})
        elif operation == "trend":
            if request.variable == "wind_direction":
                raise ClimateAnalysisError("Wind direction is circular; linear trend is not supported.")
            # Never fit a slope over holes as though observations were equally spaced.
            base = (analysis.trend(request).model_dump(mode="json") if series.complete and len(series.data) >= 2
                    else {**base, "slope": None, "direction": None,
                          "message": "Trend requires complete coverage and at least two observations."})
        elif operation == "map":
            base = analysis.map(request).model_dump(mode="json") if series.data else base
        else:
            raise ValueError("Unsupported stored-data operation")
        return {**base, "location": request.location.model_dump(), **metadata}
