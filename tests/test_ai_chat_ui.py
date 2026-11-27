from concurrent.futures import Future
from pathlib import Path

from streamlit.testing.v1 import AppTest
import pytest

from test_source_downloads import collection
from test_phase3_workflow import ui
from frontend.api_client import DashboardApiClient


@pytest.fixture(autouse=True)
def browser_storage_stub(monkeypatch):
    # AppTest cannot execute browser JavaScript; real hydration is covered in Chromium.
    import frontend.ai_chat as chat
    def ready(api):
        import streamlit as st
        st.session_state.setdefault('ai_chat_history_loaded',True)
    monkeypatch.setattr(chat,'sync_history',ready)


def test_chat_is_lazy_and_keeps_history_when_closed_and_switching_tabs(ui,monkeypatch):
    app,bridge,_=ui
    assert 'ai_chat_input' not in [x.key for x in app.chat_input]
    assert not any('/agent/' in call[1] for call in bridge.calls)
    app.button(key='ai_chat_toggle').click().run()
    assert len(app.chat_input)==1 and not app.exception
    app.session_state['ai_chat_messages']=[dict(role='user',content='Câu đang xem')]
    app.session_state['ai_chat_selected']='fixture-selection'
    app.button(key='ai_chat_close').click().run()
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    app.button(key='ai_chat_toggle').click().run()
    assert app.session_state['ai_chat_selected']=='fixture-selection'
    assert any(x.value=='Câu đang xem' for x in app.text)
    assert not app.exception
    app.button(key='ai_chat_clear').click().run()
    assert app.session_state['ai_chat_messages']==[]


def test_chart_from_saved_data_is_ready_for_era_on_the_first_click(ui, monkeypatch):
    from backend.app.ai.agent import AgentRequest, ClimateAgent
    from backend.app.ai.db_tools import build_database_tools
    from test_ai_agent import FakeClient
    from test_phase3_workflow import open_latest_collection
    import frontend.ai_chat as chat

    app, bridge, collection = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    open_latest_collection(app)
    app.button(key='stored_selected_submit').click().run()
    cid, _, saved = app.session_state['stored_result']
    app.button(key='ai_chat_toggle').click().run()
    before = len([call for call in bridge.calls if call[1].endswith('/timeseries')])
    next(button for button in app.button if button.label == 'Vẽ biểu đồ từ dữ liệu này').click().run()
    assert not app.exception and not app.error
    assert app.session_state['workflow_section'] == 'Biểu đồ'
    assert app.session_state['chart_result'][2]['data'] == saved['data']
    assert app.get('plotly_chart')
    assert len([call for call in bridge.calls if call[1].endswith('/timeseries')]) == before
    assert app.button(key='era_analyze_current')

    requests = []
    agent = ClimateAgent(FakeClient([]), build_database_tools(collection[0]))

    class Workers:
        def submit(self, fn, payload):
            requests.append(AgentRequest.model_validate(payload))
            future = Future()
            future.set_result(agent.run(requests[-1]))
            return future

    monkeypatch.setattr(chat, '_workers', lambda: Workers())
    app.chat_input(key='ai_chat_input').set_value('nhận xét sơ về biểu đồ này').run()
    result = app.session_state['ai_chat_messages'][-1]['result']
    context = requests[0].view_context
    assert context.collection_id == cid and context.view_kind == 'timeseries'
    assert context.parameters.start == saved['requested_start']
    assert context.parameters.end == saved['requested_end']
    assert context.parameters.variable == saved['variable']
    assert (context.parameters.latitude, context.parameters.longitude) == (
        saved['grid_location']['latitude'], saved['grid_location']['longitude'])
    assert result['verified'] and result['request_count'] == 0
    assert result['evidence'][-1]['result']['data']['point_count'] == len(saved['data'])


def test_pending_chat_can_close_and_complete_without_duplicate_requests(monkeypatch):
    import frontend.ai_chat as chat
    future=Future()
    submitted=[]
    class Workers:
        def submit(self,fn,payload):
            submitted.append(payload)
            return future
    monkeypatch.setattr(chat,'_workers',lambda:Workers())
    app=AppTest.from_string('''
import streamlit as st
from frontend.ai_chat import render_ai_chat
from frontend.api_client import DashboardApiClient
render_ai_chat(DashboardApiClient())
''').run()
    app.button(key='ai_chat_toggle').click().run()
    app.chat_input(key='ai_chat_input').set_value('Kho có gì?').run()
    assert len(submitted)==1 and app.chat_input[0].disabled
    app.button(key='ai_chat_close').click().run()
    future.set_result(dict(answer='Kết quả đã kiểm tra',evidence=[],verified=True))
    app.run()
    assert app.session_state['ai_chat_unread'] is True
    app.button(key='ai_chat_toggle').click().run()
    assert app.session_state['ai_chat_unread'] is False
    assert any(x.value=='Kết quả đã kiểm tra' for x in app.text)
    assert len(submitted)==1 and not app.chat_input[0].disabled
    assert not app.exception


def test_chat_header_actions_without_model_calls(ui):
    app,bridge,_=ui
    app.button(key='ai_chat_toggle').click().run()
    app.button(key='ai_chat_close').click().run()
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    app.button(key='ai_chat_toggle').click().run()
    assert app.button(key='ai_chat_clear').label=='Làm mới'
    assert app.button(key='ai_chat_close').label=='Đóng chat'
    assert not any(p.key=='ai_pet_theme' for p in app.pills)
    assert not any('/agent/' in call[1] for call in bridge.calls)
    assert not app.exception


def test_pet_art_includes_day_and_night_variants():
    from frontend.components.weather_pet import pet_svg,themed_pet_art
    assert 'data-mode="sun"' in themed_pet_art()
    assert 'data-mode="moon"' in themed_pet_art()
    assert '<script>' not in pet_svg('<script>')


def test_project_explanation_has_readable_citations_and_no_debug_json():
    app=AppTest.from_string('''
import streamlit as st
from frontend.ai_chat import render_ai_chat
from frontend.api_client import DashboardApiClient
render_ai_chat(DashboardApiClient())
''').run()
    app.button(key='ai_chat_toggle').click().run()
    answer='Dự án dùng **SQLite**. ![ảnh](https://example.invalid/tracker)'
    result=dict(answer=answer,answer_kind='project',completed=True,verified=False,evidence=[
        dict(tool='answer_question',result=dict(status='ok',data=dict(answer=answer,sources=[
            dict(id='snapshots',references=[dict(path='backend/app/services/collection_service.py',line=1)])])))])
    app.session_state['ai_chat_messages']=[dict(role='assistant',content=answer,result=result)]
    app.run()
    assert any('**SQLite**' in m.value and '![' not in m.value for m in app.markdown)
    assert any('collection_service.py:1' in t.value for t in app.text)
    assert len(app.json)==0 and not app.exception


def test_long_history_is_available_without_deleting_old_messages():
    app=AppTest.from_string('''
import streamlit as st
from frontend.ai_chat import render_ai_chat
from frontend.api_client import DashboardApiClient
render_ai_chat(DashboardApiClient())
''').run()
    app.button(key='ai_chat_toggle').click().run()
    app.session_state['ai_chat_messages']=[dict(role='user',content=f'Tin cũ {i}') for i in range(45)]
    app.run()
    assert len(app.session_state['ai_chat_messages'])==45
    app.button(key='ai_chat_older').click().run()
    assert any(t.value=='Tin cũ 0' for t in app.text)
    app.button(key='ai_chat_clear').click().run()
    assert app.session_state['ai_chat_messages']==[] and not app.exception
    previous_reset=app.session_state['ai_chat_reset_token']
    app.button(key='ai_chat_clear').click().run()
    assert app.session_state['ai_chat_reset_token']!=previous_reset
