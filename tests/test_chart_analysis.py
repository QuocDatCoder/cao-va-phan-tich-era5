from datetime import datetime,timedelta
import json
import sqlite3
import pytest
from backend.app.ai.chart_analysis import summarize_plot,render_chart_analysis
from backend.app.ai.agent import ClimateAgent,AgentRequest
from test_ai_agent import tools,FakeClient


PARAMS=dict(variable='temperature',latitude=21.,longitude=105.,start='2025-01-01T00:00:00+07:00',
            end='2025-01-01T23:00:00+07:00',frequency='hourly')


@pytest.mark.parametrize('message,calls', [
    ('nhận xét sơ về biểu đồ này', []),
    ('Phân tích chuyên gia biểu đồ đang xem. Tìm đặc điểm nổi bật và kiểm tra độ phủ.', []),
    ('Tháng nào có mưa nhiều nhất?', [('analyze_chart', {})]),
])
def test_monthly_climate_uses_both_plotted_series(tools, monkeypatch, message, calls):
    from frontend.chat_actions import applied_view
    from frontend.components.climate_views import CLIMATOLOGY, build_derived_chart

    cid = 'fixture-hanoi'
    params = {**PARAMS, 'variable': 'total_precipitation', 'frequency': 'monthly',
              'end': '2025-03-31T23:00:00+07:00', 'aggregation': 'auto'}
    variables = ['2m_temperature', 'total_precipitation']
    entry = tools.collections.get(cid)
    entry['variables'] = [dict(variable=variable) for variable in variables]

    def query(collection_id, operation, variable, latitude, longitude, start, end, frequency, aggregation):
        assert (collection_id, operation, latitude, longitude, start, end, frequency) == (
            cid, 'aggregate', 21., 105., params['start'], params['end'], 'monthly')
        assert (variable, aggregation) in zip(variables, ['mean', 'sum'])
        values = [19., 99., 25.] if variable == variables[0] else [90., 999., 150.]
        return dict(variable=variable, grid_location=dict(latitude=latitude, longitude=longitude),
                    coverage=0.9, data=[dict(time=f'2025-0{i}-01', value=value,
                    complete=(i != 2 or variable == variables[0])) for i, value in enumerate(values, 1)])

    monkeypatch.setattr(tools.collections, 'get', lambda _: entry)
    monkeypatch.setattr(tools.collections, 'query', query)
    chart = build_derived_chart(CLIMATOLOGY, lambda operation, args: query(cid, operation, **args), params, entry)
    assert list(chart['derived_figure'].data[0].y) == [90., None, 150.]
    assert list(chart['derived_figure'].data[1].y) == [19., None, 25.]
    context = applied_view(
        dict(workflow_section='Biểu đồ', chart_details_visible=True, chart_collection=cid,
             chart_result=(cid, CLIMATOLOGY, chart)),
        dict(chart_applied=json.dumps([cid, CLIMATOLOGY, params, 'aggregate'])),
    )
    assert context['view_kind'] == 'monthly_climate'
    result = ClimateAgent(FakeClient(calls), tools).run(AgentRequest(message=message, view_context=context))
    assert result['verified'] and result['request_count'] == len(calls)
    assert '150.00 mm' in result['answer'] and '25.00 °C' in result['answer']
    assert '2/3 tháng' in result['answer'] and '999' not in result['answer']


def series(values,**updates):
    start=datetime.fromisoformat(PARAMS['start'])
    return dict(variable='2m_temperature',unit='°C',complete=True,
                data=[dict(time=(start+timedelta(hours=i)).isoformat(),value=v) for i,v in enumerate(values)],**updates)


def test_elapsed_time_regression_extrema_and_changes():
    result=summarize_plot(series([1.,3.,5.,7.]),'hourly')
    assert result['mean']==4 and result['std']==pytest.approx(5**.5)
    assert result['trend']['slope_per_day']==pytest.approx(48.)
    assert result['trend']['r_squared']==pytest.approx(1.)
    assert result['max']['value']==7 and result['net_change']==6


def test_constant_series_has_zero_slope_and_undefined_r_squared():
    result=summarize_plot(series([26.85]*24),'hourly')
    assert result['trend']==dict(slope_per_day=0.,r_squared=None)


def test_null_gaps_and_partial_periods_do_not_invent_trends():
    payload=series([1.,None,5.])
    payload['complete']=False
    result=summarize_plot(payload,'hourly')
    assert result['point_count']==2 and result['mean']==3
    assert 'trend' not in result and 'largest_rise' not in result
    payload=series([1.,3.,5.])
    payload['data'][0]['partial_period']=True
    assert 'trend' not in summarize_plot(payload,'monthly')


def test_circular_direction_never_uses_linear_slope_or_minmax():
    payload=series([359.,1.,0.])
    payload.update(variable='wind_direction',unit='°')
    result=summarize_plot(payload,'hourly')
    assert result['mean']==pytest.approx(0.,abs=1e-5) or result['mean']==pytest.approx(360.,abs=1e-5)
    assert 'trend' not in result and 'min' not in result and 'net_change' not in result


def test_aggregated_diagnostics_use_plotted_values_not_hourly_extrema():
    payload=series([10.,20.,30.],aggregation='mean')
    for i,p in enumerate(payload['data']):
        p.update(min=-100.,max=100.,time=f'2025-01-0{i+1}T00:00:00+07:00')
    result=summarize_plot(payload,'daily')
    assert result['min']['value']==10 and result['max']['value']==30
    assert result['trend']['slope_per_day']==pytest.approx(10.)


def test_chart_tool_reports_real_snapshot_without_model_arithmetic(tools):
    result=tools.execute('analyze_chart',dict(collection_id='fixture-hanoi',**PARAMS))
    assert result['status']=='ok' and result['data']['mean']==pytest.approx(26.85)
    assert result['data']['point_count']==24
    assert 'giả lập' in render_chart_analysis(result)
    daily=tools.execute('analyze_chart',dict(collection_id='fixture-hanoi',**{**PARAMS,'frequency':'daily'}))
    assert daily['data']['point_count']==1 and 'trend' not in daily['data']
    assert 'không phải cực trị quan trắc' in render_chart_analysis(daily)


@pytest.mark.parametrize('cid,status',[('fixture-empty','no_data'),('fixture-warning','quality_review_required'),('deleted','not_found')])
def test_chart_tool_refuses_empty_flagged_or_deleted_snapshots(tools,cid,status):
    result=tools.execute('analyze_chart',dict(collection_id=cid,**PARAMS))
    assert result['status']==status and not result.get('data')


def test_agent_inherits_only_applied_view_and_does_not_write_prose_numbers(tools):
    client=FakeClient([('analyze_chart',dict(focus='overview'))])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Phân tích chuyên sâu biểu đồ đang xem',
        view_context=dict(page='chart',collection_id='fixture-hanoi',parameters=PARAMS)))
    assert result['verified'] and '26.85' in result['answer'] and 'R²' in result['answer']
    assert result['evidence'][0]['arguments']['frequency']=='hourly'
    assert 'nguyên nhân' in result['answer']


def test_explicit_other_collection_does_not_inherit_context(tools):
    result=ClimateAgent(FakeClient([('analyze_chart',dict(collection_id='fixture-empty'))]),tools).run(
        AgentRequest(message='Phân tích biểu đồ',view_context=dict(page='chart',collection_id='fixture-hanoi',parameters=PARAMS)))
    assert result['evidence'][0]['result']['status']=='needs_clarification'
    assert result['evidence'][0]['arguments']['variable'] is None


def test_chart_with_missing_hour_has_no_linear_trend(tools):
    with sqlite3.connect(tools.collections.repository._db_path) as db:
        db.execute('DELETE FROM collection_observations WHERE collection_id=? AND time_utc=(SELECT min(time_utc) FROM collection_observations WHERE collection_id=?)',('fixture-hanoi','fixture-hanoi'))
    result=tools.execute('analyze_chart',dict(collection_id='fixture-hanoi',**PARAMS))
    assert result['status']=='partial' and 'trend' not in result['data']


def test_map_is_not_silently_interpreted_as_a_time_series(tools):
    result=ClimateAgent(FakeClient([('analyze_chart',{})]),tools).run(AgentRequest(message='Phân tích biểu đồ',
        view_context=dict(page='chart',collection_id='fixture-hanoi',view_kind='map',parameters=PARAMS)))
    assert 'không gian' in result['answer'] and 'trung bình 26.85' not in result['answer']


def test_monthly_heatmap_context_uses_monthly_points(tools):
    result=ClimateAgent(FakeClient([('analyze_chart',{})]),tools).run(AgentRequest(message='Phân tích biểu đồ',
        view_context=dict(page='chart',collection_id='fixture-hanoi',view_kind='heatmap',parameters=PARAMS)))
    assert result['evidence'][0]['arguments']['frequency']=='monthly'
    assert 'kỳ lịch chưa trọn' in result['answer']
