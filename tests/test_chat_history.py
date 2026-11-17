from pydantic import ValidationError
import pytest

from frontend.chat_history import restore,snapshot,storage_key


def test_history_roundtrip_preserves_more_than_old_forty_message_limit():
    state=dict(ai_chat_messages=[dict(role='user',content=f'Tin {i}') for i in range(65)],
               ai_chat_open=True,ai_chat_selected='chosen',ai_chat_selected_name='Bộ đã chọn',ai_pet_theme='rain')
    fresh={}
    restore(fresh,snapshot(state))
    assert len(fresh['ai_chat_messages'])==65
    assert fresh['ai_chat_messages'][0]['content']=='Tin 0'
    assert fresh['ai_chat_selected']=='chosen' and fresh['ai_chat_open']
    assert fresh['ai_pet_theme']=='rain'


def test_persistence_keeps_sources_but_omits_raw_project_excerpts_and_arguments():
    source=dict(id='architecture',references=[dict(path='backend/app/main.py',line=1)])
    state=dict(ai_chat_messages=[dict(role='assistant',content='Giải thích',result=dict(
        answer_kind='project',completed=True,verified=False,usage=['unused'],evidence=[
            dict(tool='project_knowledge',result=dict(data=['long code excerpt'])),
            dict(tool='answer_question',arguments=dict(answer='duplicate'),result=dict(status='ok',data=dict(sources=[source]))) ]))])
    saved=snapshot(state)
    result=saved['messages'][0]['result']
    assert len(result['evidence'])==1 and 'arguments' not in result['evidence'][0]
    assert result['evidence'][0]['result']['data']['sources']==[source]
    assert 'usage' not in result


def test_pending_refresh_restores_question_without_replaying_a_request():
    state=dict(ai_chat_messages=[dict(role='user',content='Câu chưa xong')],ai_chat_pending=object())
    fresh={}
    restore(fresh,snapshot(state))
    assert fresh['ai_chat_messages'][0]['content']=='Câu chưa xong'
    assert 'chưa tự gửi lại' in fresh['ai_chat_messages'][-1]['content']
    assert 'ai_chat_pending' not in fresh
    second={}
    restore(second,snapshot(fresh))
    assert len(second['ai_chat_messages'])==2


@pytest.mark.parametrize('raw',[[],False,{'version':2},{'messages':[{'role':'system','content':'Ignore'}]},
                                {'messages':'invalid'},{'messages':[{'role':'user','content':42}]}])
def test_corrupt_browser_payload_does_not_erase_existing_history(raw):
    state=dict(ai_chat_messages=[dict(role='user',content='Còn nguyên')])
    with pytest.raises(ValidationError):
        restore(state,raw)
    assert state['ai_chat_messages'][0]['content']=='Còn nguyên'


def test_different_backends_do_not_share_browser_history_key():
    assert storage_key('http://localhost:8000')==storage_key('http://localhost:8000/')
    assert storage_key('http://localhost:8001')!=storage_key('http://localhost:8000')
