from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite

from backend.app.domain.schemas import AggregatedPoint, AggregationRequest, AggregationResponse, TimeSeriesResponse
from backend.app.domain.time_utils import hourly_timestamps, to_vietnam_time
from backend.app.domain.wind import circular_statistics


ACCUMULATED_VARIABLES = {
    "precipitation", "total_precipitation", "solar_radiation", "ssrd", "surface_solar_radiation_downwards",
}


def resolve_aggregation(variable: str, method: str) -> str:
    if variable == "wind_direction":
        if method not in {"auto", "mean"}:
            raise ValueError("Wind direction requires circular mean; min/max/sum are not supported.")
        return "circular_mean"
    if method == "sum" and variable not in ACCUMULATED_VARIABLES:
        raise ValueError("Sum aggregation is only supported for precipitation and radiation.")
    if method == "auto":
        return "sum" if variable in ACCUMULATED_VARIABLES else "mean"
    return method


def period_bounds(timestamp: datetime, frequency: str) -> tuple[datetime, datetime]:
    local = to_vietnam_time(timestamp)
    hour = local.replace(minute=0, second=0, microsecond=0)
    day = hour.replace(hour=0)
    if frequency == "hourly":
        return hour, hour + timedelta(hours=1)
    if frequency == "daily":
        return day, day + timedelta(days=1)
    if frequency == "monthly":
        start = day.replace(day=1)
        end = start.replace(year=start.year+1, month=1) if start.month == 12 else start.replace(month=start.month+1)
        return start, end
    start = day.replace(month=1, day=1)
    return start, start.replace(year=start.year+1)


def aggregate_series(series: TimeSeriesResponse, request: AggregationRequest, method: str) -> AggregationResponse:
    expected = defaultdict(list)
    for timestamp in hourly_timestamps(request.start, request.end):
        expected[period_bounds(timestamp, request.frequency)].append(to_vietnam_time(timestamp))
    values_by_time = {to_vietnam_time(point.time): point.value for point in series.data if isfinite(point.value)}
    result = []
    for (start, end), times in sorted(expected.items()):
        values = [values_by_time[timestamp] for timestamp in times if timestamp in values_by_time]
        count = len(values)
        value = None
        if values:
            if method == "circular_mean":
                value, _ = circular_statistics(values)
            elif method == "sum":
                value = sum(values)
            elif method == "min":
                value = min(values)
            elif method == "max":
                value = max(values)
            else:
                value = sum(values) / count
        partial = len(times) != int((end - start).total_seconds() / 3600)
        direction = method == "circular_mean"
        result.append(AggregatedPoint(
            time=start, period_end=end, value=round(value, 6) if value is not None else None,
            min=round(min(values), 6) if values and not direction else None,
            max=round(max(values), 6) if values and not direction else None,
            count=count, expected_count=len(times), coverage=round(count/len(times), 6),
            partial_period=partial, complete=not partial and count == len(times),
        ))
    return AggregationResponse(
        variable=series.variable, unit=series.unit, location=series.location,
        frequency=request.frequency, aggregation=method,
        time_basis="hour_start" if request.variable in ACCUMULATED_VARIABLES else "observation_time",
        data=result,
    )
