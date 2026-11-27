import json

import httpx
import pytest
from pydantic import ValidationError

from backend.app.ai.config import AISettings
from backend.app.ai.probe import run_probe
from backend.app.ai.responses_client import AIConnectionError, ResponsesClient
from scripts.check_ai_connection import fixture_transport, main


def settings(**updates):
    return AISettings(_env_file=None, _env_prefix='TEST_AI_UNUSED_',api_key='fixture-secret',**updates)


def test_responses_roundtrip_is_bounded_low_and_stateless():
    requests=[]
    fixture=fixture_transport()
    reasoning={'type':'reasoning','id':'reasoning-fixture','summary':[], 'encrypted_content':'fixture-ciphertext'}
    def handle(request):
        body=json.loads(request.content)
        requests.append(body)
        assert str(request.url)=='https://modelapi.vn/v1/responses'
        assert request.headers['Authorization']=='Bearer fixture-secret'
        assert body['model']=='gpt-6-luna'
        assert body['reasoning']=={'effort':'low'}
        assert body['max_output_tokens']==512
        assert body['store'] is False and body['stream'] is False
        assert body['parallel_tool_calls'] is False
        result=fixture.handle_request(request)
        if len(requests)==1:
            payload=result.json()
            payload['output'].insert(0,reasoning)
            return httpx.Response(200,json=payload)
        assert reasoning in body['input']
        assert body['input'][-1]['type']=='function_call_output'
        assert body['tool_choice']=='none'
        return result
    report=run_probe(ResponsesClient(settings(),transport=httpx.MockTransport(handle)))
    assert report['passed'] and report['tool_output_verified']
    assert report['request_count']==len(requests)==2
    assert report['function_calls_executed']==1
    assert 'fixture-secret' not in json.dumps(report)


@pytest.mark.parametrize('status',[301,400,401,402,403,404,429,500])
def test_http_errors_never_echo_body_or_retry(status):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(status,json={'error':'fixture-secret reflected by upstream'})
    with pytest.raises(AIConnectionError) as caught:
        run_probe(ResponsesClient(settings(),transport=httpx.MockTransport(handle)))
    assert caught.value.status_code==status
    assert 'fixture-secret' not in str(caught.value)
    assert len(calls)==1


def test_timeout_is_sanitized_and_not_retried():
    def handle(request):
        raise httpx.ReadTimeout('fixture-secret')
    with pytest.raises(AIConnectionError,match='timed out') as caught:
        run_probe(ResponsesClient(settings(),transport=httpx.MockTransport(handle)))
    assert caught.value.code=='timeout'
    assert 'fixture-secret' not in str(caught.value)


@pytest.mark.parametrize('variant',['missing','unknown','extra_args','bad_json','wrong_nonce','two_calls'])
def test_invalid_tool_requests_era_never_executed(variant):
    calls=[]
    fixture=fixture_transport()
    def handle(request):
        calls.append(request)
        body=fixture.handle_request(request).json()
        call=body['output'][0]
        if variant=='missing': body['output']=[]
        if variant=='unknown': call['name']='run_sql'
        if variant=='extra_args': call['arguments']=json.dumps({'nonce':'bad','sql':'DROP TABLE'})
        if variant=='bad_json': call['arguments']='not-json'
        if variant=='wrong_nonce': call['arguments']='{"nonce":"wrong"}'
        if variant=='two_calls': body['output'].append(dict(call))
        return httpx.Response(200,json=body)
    with pytest.raises(AIConnectionError):
        run_probe(ResponsesClient(settings(),transport=httpx.MockTransport(handle)))
    assert len(calls)==1


@pytest.mark.parametrize('body',[{'status':'incomplete','output':[]},{'status':'completed','output':[None]},[]])
def test_incompatible_response_is_not_success(body):
    with pytest.raises(AIConnectionError):
        run_probe(ResponsesClient(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))))


def test_final_answer_must_use_tool_output():
    fixture=fixture_transport()
    def handle(request):
        body=json.loads(request.content)
        if body['tool_choice']=='none':
            return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[
                {'type':'output_text','text':'Invented weather statistics'}]}]})
        return fixture.handle_request(request)
    with pytest.raises(AIConnectionError) as caught:
        run_probe(ResponsesClient(settings(),transport=httpx.MockTransport(handle)))
    assert caught.value.code=='answer_mismatch'


@pytest.mark.parametrize('prefix,allowed',[('PROBE_OK: ',True),('PROBE_OK:\n',True),
                                        ('Extra text PROBE_OK:',False)])
def test_only_formatting_whitespace_is_normalized(prefix,allowed):
    fixture=fixture_transport()
    def handle(request):
        result=fixture.handle_request(request).json()
        if json.loads(request.content)['tool_choice']=='none':
            text=result['output'][0]['content'][0]['text']
            result['output'][0]['content'][0]['text']=prefix+text.removeprefix('PROBE_OK:')
        return httpx.Response(200,json=result)
    client=ResponsesClient(settings(),transport=httpx.MockTransport(handle))
    if allowed:
        result=run_probe(client)
        assert result['passed'] and result['format_whitespace_normalized']
    else:
        with pytest.raises(AIConnectionError) as caught:
            run_probe(client)
        assert caught.value.code=='answer_mismatch'


def test_missing_key_prevents_any_request():
    config=AISettings(_env_file=None,_env_prefix='TEST_AI_UNUSED_',api_key=None)
    def forbidden(request): pytest.fail('Missing key reached network')
    with pytest.raises(AIConnectionError) as caught:
        run_probe(ResponsesClient(config,transport=httpx.MockTransport(forbidden)))
    assert caught.value.code=='missing_key'


@pytest.mark.parametrize('url',['http://api.modelapi.vn/v1','https://user:secret@example.test/v1',
                               'https://example.test/v1?key=secret','https://example.test:bad/v1'])
def test_invalid_endpoint_is_rejected(url):
    with pytest.raises(ValidationError): settings(base_url=url)


def test_default_cli_is_offline_even_with_live_env(monkeypatch,capsys):
    monkeypatch.setenv('AI_API_KEY','live-secret-must-not-be-used')
    monkeypatch.setenv('AI_BASE_URL','http://invalid-live-url')
    assert main([])==0
    report=json.loads(capsys.readouterr().out)
    assert report['mode']=='offline_fixture'
    assert report['live_connection_verified'] is False
    assert report['passed']


def test_bad_log_target_fails_before_live_configuration(monkeypatch):
    def forbidden(*a,**kw): pytest.fail('Invalid CLI args loaded credentials')
    monkeypatch.setattr('scripts.check_ai_connection.AISettings',forbidden)
    with pytest.raises(SystemExit) as caught:
        main(['--live','--log','.env'])
    assert caught.value.code==2
