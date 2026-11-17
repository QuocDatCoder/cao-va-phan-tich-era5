from datetime import datetime
import json
import sqlite3

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from test_ai_agent import tools,FakeClient
from test_source_downloads import collection
from test_phase3_workflow import ui
from backend.app.ai.agent import AgentRequest,ClimateAgent
from backend.app.ai.view_service import prepare_view,resolve_action
from backend.app.core.config import get_settings
from backend.app.domain.ui_actions import AppliedView,PrepareView,UIAction
from frontend.chat_actions import applied_view

PARAMS=dict(variable='temperature',latitude=21.,longitude=105.,start='2025-01-01T09:00:00+07:00',
            end='2025-01-01T15:00:00+07:00',frequency='daily',aggregation='auto')


def test_prepare_chart_inherits_only_same_collection_applied_context(tools):
    context=AppliedView(page='chart',collection_id='fixture-hanoi',parameters=PARAMS)
    result=prepare_view(tools,PrepareView(target='open_chart',frequency='monthly'),context=context)
    assert result['status']=='ok'
    assert result['action']['parameters']['frequency']=='monthly'
    assert result['action']['parameters']['start']==PARAMS['start']
    assert result['action']['parameters']['variable']=='2m_temperature'
    other=prepare_view(tools,PrepareView(target='open_chart',collection_id='fixture-empty'),context=context)
    assert other['status']=='needs_clarification' and 'start' in other['missing_fields']
    assert 'action' not in other


def test_partial_grid_is_not_silently_filled_from_context(tools):
    context=AppliedView(page='chart',collection_id='fixture-hanoi',parameters=PARAMS)
    result=prepare_view(tools,PrepareView(target='open_chart',latitude=21.),context=context)
    assert result['status']=='needs_clarification' and 'longitude' in result['missing_fields']


def test_full_plan_is_not_navigation_and_has_allowlisted_action(tools):
    args={k:v for k,v in PARAMS.items() if k!='aggregation'}
    client=FakeClient([('prepare_view',dict(target='open_chart',collection_id='fixture-hanoi',**args))])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Vẽ nhiệt độ theo ngày'))
    assert result['completed'] and len(result['actions'])==1
    assert result['actions'][0]['type']=='open_chart'
    assert 'Bấm nút' in result['answer'] and 'đã mở' not in result['answer'].lower()


@pytest.mark.parametrize('updates',[
    dict(start='2024-12-31T00:00:00+07:00'),dict(latitude=20.),
    dict(variable='DELETE FROM observations'),dict(start=PARAMS['end'],end=PARAMS['start']),
])
def test_action_resolution_rejects_invalid_scope(tools,updates):
    action=UIAction(type='open_chart',collection_id='fixture-hanoi',parameters={**PARAMS,**updates})
    assert resolve_action(tools,action)['status']=='invalid_arguments'


@pytest.mark.parametrize('cid,status',[('fixture-empty','no_data'),('fixture-warning','quality_review_required'),('missing','not_found')])
def test_empty_suspicious_deleted_do_not_produce_ready_chart(tools,cid,status):
    action=UIAction(type='open_chart',collection_id=cid,parameters=PARAMS)
    assert resolve_action(tools,action)['status']==status


def test_multiple_grids_require_choice(tools):
    with sqlite3.connect(tools.collections.repository._db_path) as db:
        db.execute('INSERT INTO collection_grids VALUES (?,?,?)',('fixture-hanoi',22,106))
    args=PrepareView(target='open_chart',collection_id='fixture-hanoi',variable='temperature',
                     start=PARAMS['start'],end=PARAMS['end'],frequency='daily')
    result=prepare_view(tools,args)
    assert result['status']=='needs_clarification'
    assert len(result['data']['choices']['grids'])==2


def test_contract_rejects_scripts_urls_sql_and_wrong_combinations():
    for payload in [dict(type='run_js',script='alert(1)'),dict(type='open_chart',collection_id='x',url='https://evil.invalid'),
                    dict(type='find_data',search='Hà Nội',collection_id='x')]:
        with pytest.raises(ValidationError): UIAction.model_validate(payload)
    with pytest.raises(ValidationError):
        UIAction(type='open_chart',collection_id='x',parameters={**PARAMS,'start':'2025-01-01T00:00:00+05:30'})


def test_daily_temperature_is_not_silently_converted_into_monthly_heatmap(tools):
    args=dict(target='open_chart',collection_id='fixture-hanoi',**{k:v for k,v in PARAMS.items() if k!='aggregation'})
    result=ClimateAgent(FakeClient([('prepare_view',{**args,'view_kind':'heatmap'}),
                                   ('prepare_view',{**args,'view_kind':'timeseries'})]),tools).run(
        AgentRequest(message='Vẽ nhiệt độ theo ngày'))
    assert result['request_count']==2 and result['actions'][0]['parameters']['frequency']=='daily'


def test_one_aggregate_period_has_a_readable_axis_label():
    from frontend.components.charts import timeseries_figure
    figure=timeseries_figure(dict(variable='temperature',unit='°C',frequency='daily',aggregation='mean',data=[
        dict(time='2025-01-01T00:00:00+07:00',period_end='2025-01-02T00:00:00+07:00',value=26.)]))
    assert tuple(figure.layout.xaxis.ticktext)==('01/01/2025',)


def test_view_context_uses_applied_filters_not_draft_widgets():
    params={**PARAMS,'variable':'2m_temperature'}
    state=dict(workflow_section='Biểu đồ',chart_details_visible=True,chart_collection='fixture-hanoi',
               chart_result=('fixture-hanoi','Chuỗi thời gian',{}),chart_variable_fixture='total_precipitation')
    query=dict(chart_applied=json.dumps(['fixture-hanoi','Chuỗi thời gian',params,'aggregate']))
    context=applied_view(state,query)
    assert context['parameters']==params
    state['chart_details_visible']=False
    assert applied_view(state,query) is None


def test_resolve_endpoint_is_db_only_and_does_not_require_ai_key(tools):
    from backend.app.main import create_app
    from backend.app.services.dependencies import get_stored_climate_service
    from backend.app.services.stored_climate_service import StoredClimateService
    app=create_app()
    app.dependency_overrides[get_stored_climate_service]=lambda:StoredClimateService(tools.collections.repository,tools.collections.settings)
    with TestClient(app) as client:
        payload=UIAction(type='open_chart',collection_id='fixture-hanoi',parameters=PARAMS).model_dump(mode='json')
        result=client.post('/api/v1/agent/resolve-action',json=payload)
        assert result.status_code==200 and result.json()['status']=='ok'
        assert client.post('/api/v1/agent/resolve-action',json=dict(type='execute_sql',sql='DELETE')).status_code==422


def _seed_ui_action(ui,action):
    app,bridge,collection=ui
    app.session_state['ai_chat_history_loaded']=True
    app.session_state['ai_chat_open']=True
    app.session_state['ai_chat_messages']=[dict(role='assistant',content='Bấm nút để mở',result=dict(
        completed=True,verified=True,evidence=[dict(tool='prepare_view',result=dict(status='ok',data={},
        provenance=dict(collection_id=action['collection_id'])))],actions=[action]))]
    app.run()
    return app,bridge,collection


def test_click_opens_exact_chart_and_does_not_requery_or_use_drafts(ui):
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    entry=collection[-1].get('/api/v1/collections/'+cid).json()
    a,b=(datetime.fromisoformat(entry['metadata'][k]) for k in ['requested_start','requested_end'])
    params=dict(variable=entry['metadata']['primary_variable'],**entry['grids'][0],frequency='daily',aggregation='auto',
                start=a.replace(hour=9).isoformat(),end=b.replace(hour=15).isoformat())
    action=UIAction(type='open_chart',collection_id=cid,parameters=params).model_dump(mode='json')
    app,bridge,_=_seed_ui_action(ui,action)
    before=len(bridge.calls)
    app.button(key='ai_action_0_0').click().run()
    assert not app.exception and not app.error
    assert app.session_state['workflow_section']=='Biểu đồ' and not app.session_state['ai_chat_open']
    saved=json.loads(app.query_params['chart_applied'])
    assert saved[2]['frequency']=='daily' and datetime.fromisoformat(saved[2]['start'])==datetime.fromisoformat(params['start'])
    assert app.session_state['chart_result'][2]['expected_count']==7
    assert app.time_input(key='chart_hour_start_'+cid).value.hour==9
    assert app.session_state['chart_filters_'+cid] is False
    queries=[c for c in bridge.calls[before:] if c[0]=='GET' and c[1].endswith('/aggregate')]
    assert len(queries)==1


def test_failed_action_leaves_view_and_applied_result_untouched(ui,monkeypatch):
    from frontend.api_client import DashboardApiClient,DashboardApiError
    app,bridge,collection=ui
    old_section=app.session_state['workflow_section']
    action=UIAction(type='open_chart',collection_id='no-such-collection',parameters=PARAMS).model_dump(mode='json')
    app,_,_=_seed_ui_action(ui,action)
    app.button(key='ai_action_0_0').click().run()
    assert app.session_state['workflow_section']==old_section and app.session_state['ai_chat_open']
    assert app.session_state['ai_chat_action_error'] and not app.exception


def test_query_failure_after_validation_preserves_existing_chart(ui,monkeypatch):
    from frontend.api_client import DashboardApiClient,DashboardApiError
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    app.pills(key='workflow_section').set_value('Biểu đồ').run()
    app.button(key='chart_draw_'+cid).click().run()
    old=app.session_state['chart_result']
    saved=app.query_params['chart_applied']
    params=json.loads(saved)[2]
    app,_,_=_seed_ui_action(ui,UIAction(type='open_chart',collection_id=cid,parameters=params).model_dump(mode='json'))
    def failed(*args,**kwargs): raise DashboardApiError('fixture read failed')
    monkeypatch.setattr(DashboardApiClient,'query_collection',failed)
    app.button(key='ai_action_0_0').click().run()
    assert app.session_state['chart_result']==old and app.query_params['chart_applied']==saved
    assert app.session_state['ai_chat_open'] and not app.exception


def test_opening_data_keeps_other_tabs_applied_result(ui):
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    app.pills(key='workflow_section').set_value('Biểu đồ').run()
    app.button(key='chart_draw_'+cid).click().run()
    old=app.session_state['chart_result']
    saved=app.query_params['chart_applied']
    action=UIAction(type='open_data',collection_id=cid,parameters=json.loads(saved)[2]).model_dump(mode='json')
    app,_,_=_seed_ui_action(ui,action)
    app.button(key='ai_action_0_0').click().run()
    assert app.session_state['workflow_section']=='Dữ liệu đã lưu'
    assert app.session_state['chart_result']==old and app.query_params['chart_applied']==saved
    assert app.session_state['stored_result'][0]==cid and not app.exception


def test_generic_data_action_opens_metadata_without_arbitrary_query(ui):
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    action=UIAction(type='open_data',collection_id=cid).model_dump(mode='json')
    app,bridge,_=_seed_ui_action(ui,action)
    before=len(bridge.calls)
    app.button(key='ai_action_0_0').click().run()
    assert app.session_state['workflow_section']=='Dữ liệu đã lưu'
    assert app.session_state['stored_defer_query']==cid
    assert not any(c[0]=='GET' and c[1].endswith('/timeseries') for c in bridge.calls[before:])
    assert not app.exception and not app.error


def test_find_action_prefills_search_and_expands_library(ui):
    app,bridge,_=ui
    action=UIAction(type='find_data',search='Hà Nội').model_dump(mode='json')
    app.session_state['ai_chat_history_loaded']=True
    app.session_state['ai_chat_open']=True
    app.session_state['ai_chat_messages']=[dict(role='assistant',content='Tìm trong kho',result=dict(evidence=[
        dict(tool='search_collections',result=dict(status='no_data',data=[],has_more=False))],actions=[action]))]
    app.run()
    app.button(key='ai_action_0_0').click().run()
    assert app.session_state['stored_search']=='Hà Nội'
    assert app.text_input(key='stored_library_text').value=='Hà Nội'
    assert app.session_state['stored_library_cards'] is True and not app.exception


def test_missing_parameters_can_be_filled_in_chat_without_calling_ai_again(ui):
    from datetime import time
    from backend.app.ai.db_tools import build_database_tools
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    plan=prepare_view(build_database_tools(collection[0]),PrepareView(target='open_chart',collection_id=cid))
    app.session_state['ai_chat_history_loaded']=True
    app.session_state['ai_chat_open']=True
    app.session_state['ai_chat_messages']=[dict(role='assistant',content='Chọn tham số',result=dict(evidence=[
        dict(tool='prepare_view',result=plan)],actions=[]))]
    app.run()
    choice=plan['data']['choices']
    day=datetime.fromisoformat(choice['requested_start']).date()
    app.selectbox(key='ai_view_variable_0').select(choice['primary_variable'])
    app.selectbox(key='ai_view_grid_0').select(0)
    app.date_input(key='ai_view_start_0').set_value(day)
    app.date_input(key='ai_view_end_0').set_value(day)
    app.time_input(key='ai_view_start_hour_0').set_value(time(9))
    app.time_input(key='ai_view_end_hour_0').set_value(time(15))
    app.selectbox(key='ai_view_frequency_0').select('daily')
    before=len(bridge.calls)
    app.button(key='ai_view_submit_0').click().run()
    assert not app.exception and not app.error
    assert app.session_state['chart_result'][2]['expected_count']==7
    assert not any(c[1]=='/api/v1/agent/chat' for c in bridge.calls[before:])
