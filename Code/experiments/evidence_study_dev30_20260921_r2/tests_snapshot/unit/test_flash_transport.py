import json
from reachpatch.repair.deepseek_agent import DeepSeekHTTPTransport


def test_flash_disables_default_thinking_for_forced_tool_calls(monkeypatch):
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({'choices': [{'message': {'role': 'assistant'}, 'finish_reason': 'tool_calls'}]}).encode()
    def open_request(request, **kwargs):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr('urllib.request.urlopen', open_request)
    for model in ('deepseek-flash', 'deepseek-chat'):
        DeepSeekHTTPTransport('test-only', model=model).complete([], tools=(), max_tokens=16,
            timeout_seconds=1, tool_choice={'type': 'function', 'function': {'name': 'submit'}})
    assert requests[0]['thinking'] == {'type': 'disabled'}
    assert 'thinking' not in requests[1]
