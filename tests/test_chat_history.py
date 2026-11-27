import json

from pydantic import ValidationError
import pytest

from frontend.chat_history import MAX_HISTORY_BYTES, restore, snapshot, storage_key


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


def test_large_chat_snapshot_keeps_newest_messages_within_utf8_budget():
    messages = [
        dict(role='assistant' if index % 2 else 'user', content=f'{index}: ' + '\U0001F327' * 7500)
        for index in range(12)
    ]
    state = dict(ai_chat_messages=messages, ai_chat_open=True, ai_chat_selected='chosen')
    saved = snapshot(state)
    encoded = json.dumps(saved, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    assert len(encoded) <= MAX_HISTORY_BYTES
    assert saved['messages'] == messages[-4:]
    assert len(state['ai_chat_messages']) == 12
    restored = {}
    restore(restored, json.loads(encoded))
    assert [message['content'] for message in restored['ai_chat_messages']] == [
        message['content'] for message in messages[-4:]
    ]
    assert restored['ai_chat_open'] and restored['ai_chat_selected'] == 'chosen'


def test_oversized_latest_tool_result_preserves_recent_message_text():
    messages = [
        dict(role='user', content='Question'),
        dict(role='assistant', content='Answer', result=dict(
            evidence=[dict(tool='answer_question', result='x' * MAX_HISTORY_BYTES)],
        )),
    ]
    saved = snapshot(dict(ai_chat_messages=messages))
    assert saved['messages'] == [messages[0], dict(role='assistant', content='Answer')]
    assert 'result' in messages[-1]


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
