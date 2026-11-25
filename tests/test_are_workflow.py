from datetime import datetime, timedelta, timezone

from backend.app.ai.agent import AgentRequest, ClimateAgent
from backend.app.ai.chart_analysis import daily_pattern
from test_ai_agent import FakeClient, tools


def test_collection_draft_only_prepares_request(tools):
    draft=dict(place='HCM', start='2026-11-10', end='2026-11-17', variable=None)
    result=ClimateAgent(FakeClient([('prepare_collection',draft)]),tools).run(
        AgentRequest(message='Cào dữ liệu HCM từ 10/11/2026 đến 17/11'))
    assert result['completed'] and result['actions']==[]
    assert result['evidence'][-1]['result']['data']==draft
    assert result['answer_kind']=='workflow'


def test_pattern_compares_same_local_hours_and_excludes_partial_day():
    start=datetime(2026,11,10,tzinfo=timezone(timedelta(hours=7)))
    rows=[]
    for day, offset in enumerate([0,1,5,100]):
        for hour in range(10 if day==3 else 24):
            rows.append(dict(time=(start+timedelta(days=day,hours=hour)).isoformat(),
                             value=hour+offset))
    report=daily_pattern(rows)
    assert report['peak_hour']==23
    assert report['hourly_range']==23
    assert report['excluded_days']==1
    assert report['notable_day']=='2026-11-12'
    assert report['notable_delta']==4

def test_collection_preview_requires_explicit_confirmation():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string("""
import streamlit as st
from frontend.are_collection import render_collection_draft

class API:
    base_url = "http://fixture"
    def list_collections(self, *args, **kwargs):
        return {'collections':[]}
    def get_sync_config(self):
        return {'latest_available_date':'2026-11-18'}
    def create_download(self, payload):
        st.session_state['submitted_payload']=payload
        return {'job_id':'fixture-1','status':'completed','collection_id':'fixture-1'}

@st.fragment
def card():
    render_collection_draft(API(), dict(place='HCM',start='2025-11-10',
        end='2025-11-17',variable='temperature'),0)

st.session_state.setdefault('ai_chat_messages',[{'result':{}}])
card()
""").run()
    app.button(key='are_collect_0_geocode').click().run()
    next(b for b in app.button if b.label=='Kiểm tra kho').click().run()
    assert 'submitted_payload' not in app.session_state
    app.button(key='are_collect_0_confirm').click().run()
    assert not app.exception
    payload=app.session_state['submitted_payload']
    assert payload['start_date']=='2025-11-09'
    assert payload['requested_end']=='2025-11-17T23:00:00+07:00'
    assert app.session_state['ai_chat_messages'][0]['result']['collection_job']=='fixture-1'
    assert app.session_state['workflow_selected_job']=='fixture-1'
    assert app.session_state['are_open_collection_jobs']
    assert app.session_state['job_filters']['fixture-1']['source']=='land'
