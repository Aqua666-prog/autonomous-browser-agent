import json
import time

import pytest

from browser_agent.llm import GigaChatProvider, ProviderError, _gigachat_functions, create_provider
from browser_agent.settings import DEFAULT_GIGACHAT_BASE_URL, Settings
from browser_agent.tools import schemas


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError('Unexpected HTTP call')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def giga_settings(**overrides):
    values = dict(
        provider='gigachat',
        model='GigaChat-3-Ultra',
        base_url=DEFAULT_GIGACHAT_BASE_URL,
        gigachat_auth_key='test-auth-key',
        gigachat_verify_ssl=False,
        llm_max_retries=0,
        llm_retry_backoff=0,
    )
    values.update(overrides)
    return Settings(**values)


def token_response(token='test-access-token'):
    return FakeResponse(200, {
        'access_token': token,
        'expires_at': int(time.time()) + 1800,
    })


def call_response(name, arguments):
    return FakeResponse(200, {
        'choices': [{
            'message': {
                'role': 'assistant',
                'content': '',
                'function_call': {'name': name, 'arguments': arguments},
                'functions_state_id': 'test-state-id',
            },
            'finish_reason': 'function_call',
        }]
    })


def test_gigachat_tool_aliases_are_letters_only_and_unique():
    functions, reverse = _gigachat_functions(schemas())
    names = [item['name'] for item in functions]
    assert len(names) == len(set(names)) == len(schemas())
    assert all(name.isascii() and name.isalpha() for name in names)
    assert reverse['readpage'] == 'read_page'
    assert reverse['updateplan'] == 'update_plan'


def test_gigachat_settings_defaults_from_env(tmp_path, monkeypatch):
    for key in [
        'LLM_PROVIDER', 'LLM_MODEL', 'LLM_BASE_URL', 'LLM_API_KEY',
        'ZAI_API_KEY', 'ZAI_BASE_URL', 'GIGACHAT_AUTH_KEY',
        'GIGACHAT_SCOPE', 'GIGACHAT_OAUTH_URL', 'GIGACHAT_VERIFY_SSL',
        'GIGACHAT_CA_BUNDLE',
    ]:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / '.env'
    env.write_text('LLM_PROVIDER=gigachat\nGIGACHAT_AUTH_KEY=test-auth-key\n')
    settings = Settings.load(env)
    assert settings.provider == 'gigachat'
    assert settings.provider_name == 'GigaChat'
    assert settings.model == 'GigaChat-3-Ultra'
    assert settings.base_url == DEFAULT_GIGACHAT_BASE_URL
    assert settings.require_key() == 'test-auth-key'
    assert 'test-auth-key' not in repr(settings)


def test_gigachat_provider_factory():
    client = FakeClient([])
    provider = create_provider(giga_settings(), client=client)
    assert isinstance(provider, GigaChatProvider)


@pytest.mark.asyncio
async def test_gigachat_oauth_and_function_call_roundtrip_with_cached_token():
    client = FakeClient([
        token_response(),
        call_response('readpage', {}),
        call_response('finish', {'status': 'complete', 'result': 'Done'}),
    ])
    provider = GigaChatProvider(giga_settings(), client=client)

    first = await provider.decide([
        {'role': 'system', 'content': 'Use one function.'},
        {'role': 'user', 'content': 'Read the page.'},
    ], schemas())
    second = await provider.decide([
        {'role': 'system', 'content': 'Use one function.'},
        {'role': 'user', 'content': 'Finish.'},
    ], schemas())

    assert first.name == 'read_page' and json.loads(first.arguments) == {}
    assert second.name == 'finish'
    assert len(client.calls) == 3  # OAuth once, chat twice.

    oauth_url, oauth = client.calls[0]
    assert oauth_url.endswith('/api/v2/oauth')
    assert oauth['headers']['Authorization'] == 'Basic test-auth-key'
    assert oauth['data']['scope'] == 'GIGACHAT_API_PERS'

    chat_url, chat = client.calls[1]
    assert chat_url == 'https://api.giga.chat/v1/chat/completions'
    assert chat['headers']['Authorization'] == 'Bearer test-access-token'
    assert chat['json']['model'] == 'GigaChat-3-Ultra'
    assert chat['json']['function_call'] == 'auto'
    assert all(item['name'].isalpha() for item in chat['json']['functions'])


@pytest.mark.asyncio
async def test_gigachat_refreshes_token_once_after_chat_401():
    client = FakeClient([
        token_response('first-access'),
        FakeResponse(401, {'message': 'do not expose'}),
        token_response('second-access'),
        call_response('readpage', '{}'),
    ])
    provider = GigaChatProvider(giga_settings(), client=client)
    decision = await provider.decide([], schemas())
    assert decision.name == 'read_page'
    assert len(client.calls) == 4
    assert client.calls[-1][1]['headers']['Authorization'] == 'Bearer second-access'


@pytest.mark.asyncio
async def test_gigachat_retries_transient_chat_status():
    client = FakeClient([
        token_response(),
        FakeResponse(503, {'message': 'temporary'}),
        call_response('readpage', '{}'),
    ])
    provider = GigaChatProvider(giga_settings(llm_max_retries=1), client=client)
    decision = await provider.decide([], schemas())
    assert decision.name == 'read_page'
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_gigachat_protocol_repair_uses_second_auto_request():
    client = FakeClient([
        token_response(),
        FakeResponse(200, {
            'choices': [{'message': {'role': 'assistant', 'content': 'plain text'}, 'finish_reason': 'stop'}]
        }),
        call_response('readpage', {}),
    ])
    provider = GigaChatProvider(giga_settings(), client=client)
    decision = await provider.decide([
        {'role': 'system', 'content': 'Use tools.'},
        {'role': 'user', 'content': 'Read.'},
    ], schemas())
    assert decision.name == 'read_page'
    assert client.calls[-1][1]['json']['function_call'] == 'auto'
    assert 'PROTOCOL CORRECTION' in client.calls[-1][1]['json']['messages'][-1]['content']


@pytest.mark.asyncio
async def test_gigachat_http_error_is_sanitized():
    client = FakeClient([
        token_response(),
        FakeResponse(422, {'message': 'SECRET provider body'}),
    ])
    provider = GigaChatProvider(giga_settings(), client=client)
    with pytest.raises(ProviderError) as exc:
        await provider.decide([], schemas())
    assert 'SECRET' not in str(exc.value)
    assert '422' in str(exc.value)
