from datetime import datetime

import pandas as pd
from streamlit.testing.v1 import AppTest

from frontend.components.stored_table import summarize


def test_year_defaults_to_months_and_hourly_table_is_paginated():
    app = AppTest.from_string("""
import pandas as pd
from frontend.components.stored_table import render_stored_table
times = pd.Series(pd.date_range('2025-01-01', periods=8760, freq='h', tz='Asia/Ho_Chi_Minh'))
frame = pd.DataFrame({'Thời gian (UTC+7)': times.dt.strftime('%d/%m/%Y %H:%M'), 'Nhiệt độ (°C)': 27.0})
render_stored_table(frame, times, [{'variable': 'temperature', 'unit': '°C'}], 'year',
                    times.iloc[0].isoformat(), times.iloc[-1].isoformat())
""").run()
    assert not app.exception
    mode = app.get('segmented_control')[0]
    assert mode.value == 'Tháng'
    assert len(app.dataframe[0].value) == 12
    mode.set_value('Giờ').run()
    assert len(app.dataframe[0].value) == 25
    next(button for button in app.button if button.label == 'Trang sau').click().run()
    assert app.dataframe[0].value.iloc[0, 0] == '02/01/2025 01:00'


def test_summary_uses_local_day_totals_and_circular_wind_mean():
    times = pd.Series(pd.to_datetime(['2025-01-01T17:00:00Z', '2025-01-01T18:00:00Z']))
    times = times.dt.tz_convert('Asia/Ho_Chi_Minh')
    variables = [{'variable': 'precipitation', 'unit': 'mm'}, {'variable': 'wind_direction', 'unit': '°'}]
    frame = pd.DataFrame({'Lượng mưa (mm)': [1.2, 0.3], 'Hướng gió (°)': [359., 1.]})
    result = summarize(frame, times, variables, 'Ngày', times.iloc[0],
                       datetime.fromisoformat('2025-01-03T23:00:00+07:00'))
    assert result.iloc[0].tolist() == [1.5, 0.]
    assert result.iloc[1].isna().all()
