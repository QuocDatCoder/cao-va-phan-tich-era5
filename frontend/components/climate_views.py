"""Derived views fed exclusively by saved collection observations."""
from collections import Counter
from math import isfinite

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from frontend.components.charts import map_figure

WIND_ROSE = 'Hoa gió'
ANOMALY = 'Heatmap dị thường nhiệt độ'
CLIMATOLOGY = 'Khí hậu tháng'
REGIONAL_MAP = 'Bản đồ thời tiết'
DERIVED_OPERATIONS = {
    WIND_ROSE: 'timeseries', ANOMALY: 'aggregate',
    CLIMATOLOGY: 'aggregate', REGIONAL_MAP: 'map',
}


def complete_months(payload):
    return {
        pd.Timestamp(row['time']).to_period('M'): row['value']
        for row in payload['data']
        if row.get('complete') and row['value'] is not None
    }


def build_derived_chart(kind, read, params, entry):
    """Read named variables at the exact selected grid and retain coverage metadata."""
    variables = {item['variable'] for item in entry['variables']} | {entry['metadata']['primary_variable']}
    query = {key: value for key, value in params.items() if key not in {'baseline_start', 'baseline_end'}}

    def find(*names):
        for name in names:
            if name in variables:
                return name
        raise ValueError('Bộ dữ liệu thiếu biến cần thiết để vẽ biểu đồ này.')

    def series(variable, operation='timeseries', **overrides):
        return read(operation, {**query, 'variable': variable, **overrides})

    if kind == WIND_ROSE:
        speed = series('wind_speed')
        direction = series('wind_direction')
        angles = {row['time']: row['value'] for row in direction['data']}
        valid = [row for row in speed['data'] if row['value'] is not None and isfinite(row['value']) and row['value'] >= 0]
        samples = [(row['value'], angles.get(row['time'])) for row in valid]
        paired = [(value, angle) for value, angle in samples if value > 0 and angle is not None and isfinite(angle)]
        calm = sum(value == 0 for value, _ in samples)
        total = len(paired) + calm
        if not total:
            raise ValueError('Không có quan sát gió hợp lệ trong khoảng đã chọn.')
        sectors = ['B', 'ĐB', 'Đ', 'ĐN', 'N', 'TN', 'T', 'TB']
        thresholds = (2, 5, 10)
        counts = Counter((int((angle % 360 + 22.5) // 45) % 8,
                          sum(value >= bound for bound in thresholds)) for value, angle in paired)
        fig = go.Figure()
        for band, (label, color) in enumerate(zip(
                ['< 2 m/s', '2–<5 m/s', '5–<10 m/s', '≥ 10 m/s'],
                ['#8ed5d3', '#439fb9', '#2563a6', '#172d75'])):
            values = [counts[i, band] for i in range(8)]
            fig.add_barpolar(theta=list(range(0, 360, 45)), r=[n / total * 100 for n in values],
                             width=[41] * 8, name=label, marker_color=color, customdata=sectors,
                             hovertemplate='%{customdata} · %{r:.1f}%<extra>%{fullData.name}</extra>')
        fig.update_layout(barmode='stack', polar=dict(angularaxis=dict(
            rotation=90, direction='clockwise', tickvals=list(range(0, 360, 45)), ticktext=sectors),
            radialaxis=dict(ticksuffix='%')))
        caption = f'Hướng gió thổi từ đâu · {total}/{speed["expected_count"]} giờ hợp lệ · Gió lặng {calm / total:.1%}.'
        return {**speed, 'derived_figure': fig, 'derived_caption': caption}

    if kind in {ANOMALY, CLIMATOLOGY}:
        variable = find('2m_temperature', 'temperature')
        temperature = series(variable, 'aggregate', frequency='monthly', aggregation='mean')
        months = complete_months(temperature)
        if kind == ANOMALY:
            baseline = series(variable, 'aggregate', frequency='monthly', aggregation='mean',
                              start=params['baseline_start'], end=params['baseline_end'])
            reference = complete_months(baseline)
            normals = pd.Series(reference, dtype=float)
            normals = normals.groupby(normals.index.month).mean() if len(normals) else normals
            differences = {period: value - normals[period.month] for period, value in months.items()
                           if period.month in normals}
            if not differences:
                raise ValueError('Cần tháng đủ dữ liệu ở cả khoảng xem và cùng tháng trong giai đoạn nền.')
            years = sorted({period.year for period in months})
            limit = max(0.1, max(abs(value) for value in differences.values()))
            fig = go.Figure(go.Heatmap(
                x=list(range(1, 13)), y=years,
                z=[[differences.get(pd.Period(year=year, month=month, freq='M'))
                    for month in range(1, 13)] for year in years],
                colorscale='RdBu_r', zmin=-limit, zmax=limit, colorbar=dict(title='Δ °C'),
                hoverongaps=False, hovertemplate='Tháng %{x}/%{y}<br>Dị thường %{z:+.2f} °C<extra></extra>',
            ))
            fig.update_xaxes(dtick=1, title='Tháng')
            fig.update_yaxes(dtick=1, title='Năm')
            caption = (f'Nền {params["baseline_start"][:10]} đến {params["baseline_end"][:10]} · '
                       f'{len(reference)} tháng đủ dữ liệu trong nền · Ô trống thiếu tháng đối chiếu.')
        else:
            rainfall = complete_months(series(find('total_precipitation', 'precipitation'),
                                             'aggregate', frequency='monthly', aggregation='sum'))
            common = sorted(months.keys() & rainfall.keys())
            if not common:
                raise ValueError('Cần ít nhất một tháng đủ dữ liệu nhiệt độ và lượng mưa.')
            periods = pd.period_range(common[0], common[-1], freq='M')
            labels = [period.strftime('%m/%Y') for period in periods]
            valid_months = set(common)
            fig = make_subplots(specs=[[{'secondary_y': True}]])
            fig.add_trace(go.Bar(x=labels, y=[rainfall[p] if p in valid_months else None for p in periods], name='Tổng mưa',
                                 marker_color='#438ac5'), secondary_y=False)
            fig.add_trace(go.Scatter(x=labels, y=[months[p] if p in valid_months else None for p in periods], name='Nhiệt độ trung bình',
                                     mode='lines+markers', line_color='#cf4934'), secondary_y=True)
            fig.update_yaxes(title_text='Tổng mưa (mm)', rangemode='tozero', secondary_y=False)
            fig.update_yaxes(title_text='Nhiệt độ (°C)', secondary_y=True, showgrid=False)
            caption = f'{len(common)} tháng đủ dữ liệu ở cả hai biến · Tháng thiếu được để trống.'
        return {**temperature, 'derived_figure': fig, 'derived_caption': caption}

    if params['variable'] == 'wind_direction':
        raise ValueError('Chọn Hoa gió để xem hướng gió, hoặc chọn một biến vô hướng cho bản đồ.')
    grids = entry['grids']
    # Bound HTTP fan-out and show the exact sampled coordinates.
    step = (len(grids) + 63) // 64
    sampled = grids[::step]
    payloads = [series(params['variable'], 'map', **grid) for grid in sampled]
    points = [point for payload in payloads if payload['complete'] for point in payload['data']]
    if not points:
        raise ValueError('Không có điểm lưới đủ dữ liệu trong khoảng đã chọn.')
    caption = (f'Trung bình theo thời gian · {len(points)}/{len(sampled)} điểm đủ dữ liệu'
               f' · Bộ dữ liệu có {len(grids)} điểm lưới.')
    if len(grids) == 1:
        caption = 'Giá trị trung bình theo thời gian tại điểm lưới đã lưu.'
    return {**payloads[0], 'derived_figure': map_figure({**payloads[0], 'data': points}),
            'derived_caption': caption}
