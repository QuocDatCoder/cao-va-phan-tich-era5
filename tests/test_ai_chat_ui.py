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
    app.pills(key='workflow_section').set_value('Biểu đồ').run()
    app.button(key='ai_chat_toggle').click().run()
    assert app.session_state['ai_chat_selected']=='fixture-selection'
    assert any(x.value=='Câu đang xem' for x in app.text)
    assert not app.exception
    app.button(key='ai_chat_clear').click().run()
    assert app.session_state['ai_chat_messages']==[]


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


def test_pet_theme_persists_without_model_calls(ui):
    app,bridge,_=ui
    app.button(key='ai_chat_toggle').click().run()
    app.pills(key='ai_pet_theme').set_value('rain').run()
    app.button(key='ai_chat_close').click().run()
    app.pills(key='workflow_section').set_value('Biểu đồ').run()
    app.button(key='ai_chat_toggle').click().run()
    assert app.pills(key='ai_pet_theme').value=='rain'
    assert not any('/agent/' in call[1] for call in bridge.calls)
    assert not app.exception


def test_pet_automatic_states_and_untrusted_theme():
    from frontend.components.weather_pet import pet_mode,pet_svg
    assert pet_mode('auto')=='cloud'
    assert pet_mode('auto',pending=True)=='rain'
    assert pet_mode('auto',last_result={'verified':True})=='sun'
    assert pet_mode('auto',last_result={'verified':False})=='rain'
    assert pet_mode('auto',last_result={'verified':False,'completed':True})=='sun'
    assert pet_mode('sun',pending=True)=='sun'
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
