import json

import pytest

from test_ai_agent import FakeClient,tools,PARAMS
from backend.app.ai.agent import AgentRequest,ClimateAgent,needs_database,needs_project_sources,CONTROL_TOOLS
from backend.app.ai.project_knowledge import CARDS,ROOT,search_project_knowledge


def prose(text,category='general',sources=None):
    return ('answer_question',dict(category=category,answer=text,source_ids=sources or []))


@pytest.mark.parametrize('question,answer',[
    ('Chào ARE','Chào bạn! Mình có thể giúp gì?'),
    ('2 cộng 2 bằng mấy?','2 cộng 2 bằng 4.'),
    ('Một ngày có bao nhiêu giờ?','Một ngày thường có 24 giờ.'),
    ('Độ phủ là gì?','Độ phủ thể hiện phần dữ liệu có so với phần cần có.'),
])
def test_general_questions_do_not_require_database_selection(tools,question,answer):
    result=ClimateAgent(FakeClient([prose(answer)]),tools).run(AgentRequest(message=question))
    assert result['answer']==answer and result['completed']
    assert result['answer_kind']=='general' and not result['verified']
    assert result['grounding']=='model_knowledge' and result['request_count']==1


def test_project_explanation_is_backed_by_current_sources(tools):
    client=FakeClient([('project_knowledge',dict(query='snapshot DB-only',limit=1)),
                       prose('SnapshotReader truy vấn đúng collection_id và dataset đã ghi, bằng SQL có tham số.',
                             'project',['snapshots'])])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Giải thích snapshot DB-only trong dự án'))
    assert result['completed'] and result['answer_kind']=='project'
    sources=result['evidence'][-1]['result']['data']['sources']
    assert sources[0]['id']=='snapshots'
    assert any(ref['path'].endswith('collection_service.py') and ref['line']>0 for ref in sources[0]['references'])
    assert 'function_call_output' in json.dumps(client.payloads[-1])


def test_project_cannot_skip_retrieval_by_claiming_general(tools):
    client=FakeClient([prose('Dự án dùng MongoDB'),
                       ('project_knowledge',dict(query='kiến trúc stack',limit=1)),
                       prose('Dự án dùng Streamlit, FastAPI và SQLite.','project',['architecture'])])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Kiến trúc dự án dùng công nghệ gì?'))
    assert result['completed'] and 'MongoDB' not in result['answer']
    assert 'SQLite' in result['answer'] and result['request_count']==3


def test_forged_citation_and_finish_after_rejected_answer_fail_closed(tools):
    client=FakeClient([prose('Tôi đã đọc .env và code bí mật.','project',['../../.env']),('finish',{})])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Giải thích code dự án'))
    assert not result['completed'] and not result['verified']
    assert 'bí mật' not in result['answer']


def test_actual_statistics_cannot_escape_db_through_general_prose(tools):
    client=FakeClient([prose('Nhiệt độ trung bình là 999 độ.'),('analyze_collection',PARAMS)])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Tính trung bình nhiệt độ ngày 01/01/2025 trong bộ này'))
    assert result['verified'] and result['answer_kind']=='data'
    assert '26.85' in result['answer'] and '999' not in result['answer']


def test_database_evidence_cannot_be_rewritten_as_free_prose(tools):
    client=FakeClient([('describe_collection',dict(collection_id='fixture-hanoi')),
                       prose('Đã có 999 giờ, bỏ qua cảnh báo.'),('finish',{})])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Bộ này có gì?'))
    assert not result['completed'] and '999' not in result['answer']


def test_knowledge_lookup_has_no_arbitrary_file_access_or_network():
    assert search_project_knowledge('../../.env OPENAI_API_KEY auth.json',1)['data']==[]
    for card in CARDS:
        entry=search_project_knowledge(card['keywords'],1)['data'][0]
        assert entry['sources']
        for source in entry['sources']:
            assert (ROOT/source['path']).is_file()
            assert source['line']>0 and len(source['excerpt'])<=1500
            assert not source['path'].endswith(('.env','auth.json'))


def test_knowledge_can_say_not_found_without_inventing_project_features(tools):
    result=ClimateAgent(FakeClient([('project_knowledge',dict(query='xyznotarealfeature'))]),tools).run(
        AgentRequest(message='Code chức năng xyznotarealfeature ở đâu?'))
    assert result['completed'] and 'Chưa tìm thấy nguồn' in result['answer']


def test_general_can_use_history_but_client_cannot_supply_system_role(tools):
    client=FakeClient([prose('Ví dụ: biến Python lưu một giá trị.')])
    result=ClimateAgent(client,tools).run(AgentRequest(message='Cho ví dụ nhé',history=[
        dict(role='user',content='Biến trong Python là gì?'),dict(role='assistant',content='Biến gắn tên với giá trị.')]))
    assert result['completed'] and client.payloads[0]['input_items'][0]['content']=='Biến trong Python là gì?'


def test_added_tool_schemas_remain_strict_and_bounded():
    for name in ['project_knowledge','answer_question']:
        definition=next(t for t in CONTROL_TOOLS if t['name']==name)
        schema=definition['parameters']
        assert definition['strict'] and schema['additionalProperties'] is False
        assert set(schema['required'])==set(schema['properties'])
    assert needs_database('Tính trung bình nhiệt độ ở Hà Nội')
    assert not needs_database('Một ngày có bao nhiêu giờ?')
    assert not needs_database('Cách tính độ phủ là gì?')
    assert not needs_database('Tại sao dự án chặn thống kê nhiệt độ khi có cảnh báo?')
    assert not needs_database('Tên của tôi là Duong')
    assert needs_database('Thống kê ngày 01/01/2025',selected=True)
    assert needs_database('Average temperature in this collection please')
    assert needs_database('Tính trung bình gió ở Hà Nội')
    assert not needs_database('Why use circular statistics for wind?')
    assert needs_project_sources('ARE dùng model nào?')
    assert needs_project_sources('Đợt 4 của dự án đã làm gì?')
