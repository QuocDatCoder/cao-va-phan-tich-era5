"""Small Responses transport using existing httpx; errors never expose remote bodies."""
import httpx

from backend.app.ai.config import AISettings


class AIConnectionError(Exception):
    def __init__(self, code, message, status_code=None, details=None):
        super().__init__(message)
        self.code, self.status_code = code, status_code
        self.details = details


class ResponsesClient:
    def __init__(self, settings: AISettings, *, transport=None):
        self.settings = settings
        self.transport = transport

    def create(self, *, input_items, tools, tool_choice, instructions):
        secret = self.settings.api_key
        if secret is None or not secret.get_secret_value().strip():
            raise AIConnectionError('missing_key', 'Set AI_API_KEY locally before live testing.')
        payload = dict(model=self.settings.model, reasoning={'effort': self.settings.reasoning_effort},
                       store=False, stream=False, max_output_tokens=self.settings.max_output_tokens,
                       include=['reasoning.encrypted_content'], instructions=instructions,
                       input=input_items, tools=tools, tool_choice=tool_choice, parallel_tool_calls=False)
        try:
            with httpx.Client(transport=self.transport, timeout=self.settings.timeout_seconds,
                              follow_redirects=False, trust_env=False) as client:
                response = client.post(self.settings.base_url+'/responses', json=payload,
                                       headers={'Authorization': 'Bearer '+secret.get_secret_value()})
        except httpx.TimeoutException:
            raise AIConnectionError('timeout', 'AI request timed out; no automatic retry.') from None
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise AIConnectionError('network_error', 'AI connection failed ('+type(exc).__name__+
                                    '); no automatic retry.') from None
        if not 200 <= response.status_code < 300:
            raise AIConnectionError('http_error', 'AI gateway rejected the request. Check credentials, '
                                    'model access, balance and Responses/tool compatibility.', response.status_code)
        try:
            body = response.json()
        except ValueError:
            raise AIConnectionError('invalid_response', 'AI gateway did not return JSON.') from None
        if not isinstance(body, dict) or body.get('status') != 'completed' or not isinstance(body.get('output'), list):
            raise AIConnectionError('incomplete_response', 'AI response is incomplete or incompatible; probe did not pass.')
        if not all(isinstance(item,dict) for item in body['output']):
            raise AIConnectionError('invalid_response', 'AI output items are incompatible.')
        return body
