"""Six user-facing variables; wind components remain internal source data."""

VARIABLE_ALIASES = (
    ('temperature', '2m_temperature'),
    ('precipitation', 'total_precipitation'),
    ('dewpoint', '2m_dewpoint_temperature'),
    ('wind_speed',),
    ('wind_direction',),
    ('solar_radiation', 'surface_solar_radiation_downwards', 'ssrd'),
)
PUBLIC_VARIABLES = frozenset(v for group in VARIABLE_ALIASES for v in group)


def public_variables(available):
    """Keep actual stored names, deduplicate aliases, derive wind only with both components."""
    available = set(available)
    if (available & {'wind_u', 'u10', '10m_u_component_of_wind'} and
            available & {'wind_v', 'v10', '10m_v_component_of_wind'}):
        available.update(('wind_speed', 'wind_direction'))
    return [next(v for v in group if v in available)
            for group in VARIABLE_ALIASES if any(v in available for v in group)]
