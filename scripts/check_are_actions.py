"""Small explicit live routing check on synthetic data; no CDS or navigation."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from backend.app.ai.agent import AgentRequest,ClimateAgent
from backend.app.ai.config import AISettings
from backend.app.ai.db_tools import build_database_tools
from backend.app.ai.responses_client import ResponsesClient
from scripts.agent_fixtures import create_fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    out=args.output_dir.resolve()
    if not args.live or out.exists() or (ROOT/'output').resolve() not in out.parents:
        parser.error('Use --live and a new output/ directory.')
    settings=AISettings()
    if not settings.api_key or not settings.api_key.get_secret_value().strip():
        parser.error('Missing local AI_API_KEY; no requests sent.')
    out.mkdir(parents=True)
    agent=ClimateAgent(ResponsesClient(settings),build_database_tools(create_fixture(out/'fixtures')))
    context=dict(page='chart',collection_id='fixture-hanoi',parameters=dict(variable='2m_temperature',latitude=21.,longitude=105.,
        start='2025-01-01T00:00:00+07:00',end='2025-01-01T23:00:00+07:00',frequency='hourly',aggregation='auto'))
    cases=[
        ('applied_filters',AgentRequest(message='Mở biểu đồ nhiệt độ theo ngày với bộ và khoảng đang xem.',view_context=context)),
        ('search',AgentRequest(message='Tìm bộ Hà Nội fixture đã lưu để tôi xem dữ liệu.')),
        ('missing_dates',AgentRequest(message='Mở biểu đồ nhiệt độ cho bộ này.',selected_collection_id='fixture-hanoi')),
    ]
    report=dict(mode='live_synthetic_ui_actions',rows=[])
    tokens=0
    for name,request in cases:
        if tokens>=30000: break
        result=agent.run(request)
        tokens+=sum(u.get('total_tokens',0) for u in result['usage'])
        if name=='applied_filters':
            passed=bool(result['actions'] and result['actions'][0]['type']=='open_chart'
                and result['actions'][0]['parameters']['frequency']=='daily'
                and result['actions'][0]['parameters']['start']==context['parameters']['start'])
        elif name=='search':
            passed=any(e['tool']=='search_collections' and e['result']['data'] for e in result['evidence'])
        else:
            passed=bool(result['evidence'] and result['evidence'][-1]['result']['status']=='needs_clarification' and not result['actions'])
        report['rows'].append(dict(case=name,passed=passed,**result))
        report['reported_total_tokens']=tokens
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(case=name,passed=passed,requests=result['request_count'],stop=result['stop_reason'],answer=result['answer']),ensure_ascii=False),flush=True)
        if result['stop_reason'] in {'http_error','network_error','missing_key'}: break


if __name__=='__main__':
    main()
