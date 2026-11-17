"""Offline protocol fixture by default. --live explicitly sends up to two billable requests."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from pydantic import ValidationError
from backend.app.ai.config import AISettings
from backend.app.ai.probe import run_probe
from backend.app.ai.responses_client import AIConnectionError, ResponsesClient


def fixture_transport():
    def respond(request):
        body = json.loads(request.content)
        if isinstance(body['tool_choice'],dict):
            nonce = body['input'][0]['content'].split('nonce=')[1]
            output = [{'type':'function_call','name':'connection_probe','call_id':'fixture-call',
                       'arguments':json.dumps({'nonce':nonce})}]
        else:
            code = json.loads(body['input'][-1]['output'])['verification_code']
            output = [{'type':'message','content':[{'type':'output_text','text':'PROBE_OK:'+code}]}]
        return httpx.Response(200,json={'status':'completed','model':'offline-fixture','output':output})
    return httpx.MockTransport(respond)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Allow real AI requests (may incur charges).')
    parser.add_argument('--log',type=Path,help='Save a sanitized JSON report, without raw responses or keys.')
    args = parser.parse_args(argv)
    log_path = None
    if args.log:
        log_path = args.log.resolve()
        if (ROOT/'output').resolve() not in log_path.parents:
            parser.error('--log must be inside the repository output/ directory.')
    mode = 'live' if args.live else 'offline_fixture'
    try:
        # Offline mode ignores both env files and AI_* environment settings.
        settings = AISettings() if args.live else AISettings(_env_file=None, _env_prefix='PROBE_OFFLINE_UNUSED_',
                                                           api_key='offline-fixture-only')
        client = ResponsesClient(settings,transport=None if args.live else fixture_transport())
        report = {'mode':mode, **run_probe(client), 'live_connection_verified':args.live}
        exit_code = 0
    except AIConnectionError as exc:
        report = {'mode':mode,'passed':False,'live_connection_verified':False,
                  'error_code':exc.code,'status_code':exc.status_code,'message':str(exc)}
        if exc.details is not None:
            report['details'] = exc.details
        exit_code = 1
    except ValidationError:
        # Pydantic error text can include input secrets: never print it.
        report = {'mode':mode,'passed':False,'live_connection_verified':False,
                  'error_code':'invalid_config','message':'Check AI_* configuration; no request was sent.'}
        exit_code = 2
    if log_path:
        log_path.parent.mkdir(parents=True,exist_ok=True)
        log_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
