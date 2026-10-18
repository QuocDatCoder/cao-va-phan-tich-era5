from math import atan2, cos, degrees, hypot, log, radians, sin, sqrt

from backend.app.domain.schemas import ClimatePoint, TimeSeriesResponse


WIND_COMPONENTS = ("10m_u_component_of_wind", "10m_v_component_of_wind")
DERIVED_WIND_UNITS = {"wind_speed": "m/s", "wind_direction": "°"}


def derive_wind(variable: str, u: TimeSeriesResponse, v: TimeSeriesResponse) -> TimeSeriesResponse:
    if u.unit != "m/s" or v.unit != "m/s" or u.location != v.location:
        raise ValueError("Wind components must have matching locations and units of m/s.")
    u_points = {point.time: point.value for point in u.data}
    v_points = {point.time: point.value for point in v.data}
    if not u_points or u_points.keys() != v_points.keys():
        raise ValueError("Wind components must have matching hourly timestamps.")
    points = []
    for timestamp in sorted(u_points):
        east, north = u_points[timestamp], v_points[timestamp]
        speed = hypot(east, north)
        if variable == "wind_speed":
            value = round(speed, 6)
        elif speed == 0:
            continue  # A calm wind has no meteorological direction.
        else:
            value = round((180 + degrees(atan2(east, north))) % 360, 6) % 360
        points.append(ClimatePoint(time=timestamp, value=value))
    return TimeSeriesResponse(variable=variable, unit=DERIVED_WIND_UNITS[variable],
                              location=u.location, data=points)


def circular_statistics(values: list[float]) -> tuple[float | None, float | None]:
    """Unweighted circular mean/std in degrees; opposing directions have no mean."""
    east = sum(sin(radians(value)) for value in values) / len(values)
    north = sum(cos(radians(value)) for value in values) / len(values)
    resultant = min(1.0, hypot(east, north))
    if resultant < 1e-12:
        return None, None
    mean = round(degrees(atan2(east, north)) % 360, 6) % 360
    return mean, round(degrees(sqrt(-2 * log(resultant))), 6)
