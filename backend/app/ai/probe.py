"""A single fixed local tool and exactly two model requests on the success path."""
import json
import re
from uuid import uuid4

from backend.app.ai.responses_client import AIConnectionError

TOOL = {
    'type': 'function', 'name': 'connection_probe',
    'description': 'Return a local verification code for this connection test. Not climate data.',
    'strict': True,
    'parameters': {'type': 'object', 'properties': {'nonce': {'type': 'string'}},
                   'required': ['nonce'], 'additionalProperties': False},
}
INSTRUCTIONS = ('This is an API connection test, not a weather question. Call connection_probe '
                'with the exact user nonce. After receiving its output, respond only with '
                'PROBE_OK: followed by the verification_code from that tool output.')


def usage_summary(response):
    usage = response.get('usage')
    usage = usage if isinstance(usage,dict) else {}
    return {key: value for key in ['input_tokens','output_tokens','total_tokens']
            if type(value := usage.get(key)) is int and value >= 0}


def run_probe(client):
    nonce, code = uuid4().hex, uuid4().hex
    items = [{'role': 'user', 'content': 'Run the connection test. nonce='+nonce}]
    first = client.create(input_items=items, tools=[TOOL], instructions=INSTRUCTIONS,
                          tool_choice={'type': 'function', 'name': TOOL['name']})
    calls = [item for item in first['output'] if isinstance(item,dict) and item.get('type') == 'function_call']
    if len(calls) != 1:
        raise AIConnectionError('tool_call_missing', 'Expected exactly one function call; gateway/model tool support not verified.')
    call = calls[0]
    if call.get('name') != TOOL['name'] or not isinstance(call.get('call_id'),str) or not call['call_id']:
        raise AIConnectionError('invalid_tool_call', 'AI returned an unexpected function or invalid call ID.')
    try:
        arguments = json.loads(call['arguments'])
    except (ValueError, TypeError, KeyError):
        raise AIConnectionError('invalid_tool_arguments', 'Function arguments are not valid JSON.') from None
    if arguments != {'nonce': nonce}:
        raise AIConnectionError('invalid_tool_arguments', 'Function arguments did not match the allowed nonce schema.')
    # The verification code is not in the first prompt: the final answer must use tool output.
    tool_output = {'verification_code': code, 'source': 'local_connection_fixture', 'not_climate_data': True}
    items = items + first['output'] + [{'type': 'function_call_output', 'call_id': call['call_id'],
                                      'output': json.dumps(tool_output)}]
    second = client.create(input_items=items, tools=[TOOL], instructions=INSTRUCTIONS, tool_choice='none')
    texts = []
    for item in second['output']:
        if not isinstance(item,dict):
            raise AIConnectionError('invalid_response', 'Invalid output item.')
        if item.get('type') == 'function_call':
            raise AIConnectionError('unexpected_tool_call', 'Model requested another tool after tools were disabled.')
        if item.get('type') == 'message':
            content = item.get('content')
            if not isinstance(content,list):
                raise AIConnectionError('invalid_response', 'AI message content is incompatible.')
            for part in content:
                if isinstance(part,dict) and part.get('type') == 'output_text' and isinstance(part.get('text'),str):
                    texts.append(part['text'])
    answer = ''.join(texts).strip()
    matched = re.fullmatch(r'PROBE_OK:[ \t\r\n]*([0-9a-f]{32})', answer)
    if matched is None or matched.group(1) != code:
        raise AIConnectionError('answer_mismatch', 'Final answer did not reproduce the tool verification code exactly.',
                                details={'request_count':2,'function_calls_executed':1,
                                         'answer_characters':len(answer),'expected_code_present':code in answer,
                                         'expected_prefix_present':'PROBE_OK:' in answer,
                                         'usage_by_request':[usage_summary(first),usage_summary(second)]})
    return {'passed': True, 'requested_model': client.settings.model,
            'reasoning_effort': client.settings.reasoning_effort, 'request_count': 2,
            'function_calls_executed': 1, 'tool_output_verified': True,
            'format_whitespace_normalized': answer != 'PROBE_OK:'+code,
            'returned_model_matches_request': [r.get('model') == client.settings.model for r in [first,second]],
            'usage_by_request': [usage_summary(first),usage_summary(second)]}
