from backend.app.domain.public_variables import public_variables


def test_six_variables_deduplicated_and_derived():
    assert public_variables(['2m_temperature', 'temperature', 'total_precipitation',
                             'dewpoint', 'wind_u', 'wind_v', 'solar_radiation']) == [
        'temperature', 'total_precipitation', 'dewpoint', 'wind_speed',
        'wind_direction', 'solar_radiation']


def test_missing_wind_component_does_not_invent_wind():
    assert public_variables(['wind_u', 'unknown']) == []


def test_canonical_components_support_derived_wind():
    assert public_variables(['10m_u_component_of_wind', '10m_v_component_of_wind']) == [
        'wind_speed', 'wind_direction']


def test_ui_action_rejects_internal_component_before_query():
    from backend.app.ai.view_service import resolve_action
    from backend.app.domain.ui_actions import UIAction
    action = UIAction(type='open_chart', collection_id='example', parameters=dict(
        variable='10m_u_component_of_wind', latitude=10., longitude=106.,
        start='2025-01-01T00:00:00+07:00', end='2025-01-01T23:00:00+07:00',
        frequency='hourly'))
    assert resolve_action(None, action)['status'] == 'invalid_arguments'
