import asyncio
import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from browser_agent.tools import parse_call, schemas, REGISTRY
from browser_agent.settings import Settings
from browser_agent.llm import (Decision, ProviderError, MalformedDecision, ZAIProvider,
    OpenAICompatibleProvider, create_provider)
from browser_agent.runtime import Runtime, Memory

@pytest.mark.parametrize('name,args', [
    ('shell', '{}'), ('click', '{'), ('click', '{"ref":"invented"}'),
    ('navigate', '{"url":"file:///etc/passwd"}'),
    ('navigate', '{"url":"https://secret:password@example.org"}'),
    ('scroll', '{"direction":"down","amount":true}'),
    ('wait', '{"seconds":100}'), ('go_back','{"extra":1}')])
def test_invalid_calls(name, args):
    with pytest.raises((ValueError, ValidationError)): parse_call(name, args)

def test_registry():
    assert len(schemas()) == len(REGISTRY)
    assert all(t['function']['parameters']['additionalProperties'] is False for t in schemas())
    assert parse_call('click', '{"ref":"s1e0"}').ref == 's1e0'

def test_config_and_missing_key(tmp_path, monkeypatch):
    for key in ['ZAI_API_KEY','HEADLESS','MAX_AGENT_STEPS','LLM_MODEL']:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / '.env'
    env.write_text('HEADLESS=true\nMAX_AGENT_STEPS=7\n')
    settings = Settings.load(env)
    assert settings.headless and settings.max_steps == 7
    with pytest.raises(ValueError): settings.require_key(lambda _: '')
    assert settings.require_key(lambda _: 'test-only-key') == 'test-only-key'
    assert 'test-only-key' not in repr(settings)
    assert 'test-only-key' not in env.read_text()
    monkeypatch.setenv('MAX_AGENT_STEPS','8')
    assert Settings.load(env).max_steps == 8

def test_generic_settings_and_local_http(tmp_path, monkeypatch):
    for key in ['LLM_PROVIDER','LLM_API_KEY','LLM_BASE_URL','LLM_API_KEY_REQUIRED','ZAI_API_KEY','ZAI_BASE_URL']:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / '.env'
    env.write_text('LLM_PROVIDER=openai_compatible\nLLM_MODEL=local-model\nLLM_BASE_URL=http://127.0.0.1:8000/v1/\nLLM_API_KEY_REQUIRED=false\n')
    settings = Settings.load(env)
    assert settings.provider == 'openai_compatible'
    assert settings.model == 'local-model'
    assert settings.base_url == 'http://127.0.0.1:8000/v1/'
    assert settings.require_key() == 'local-no-key-required'
    with pytest.raises(ValidationError):
        Settings(base_url='http://example.org/v1/')

def test_generic_env_precedence(tmp_path, monkeypatch):
    for key in ['LLM_PROVIDER','LLM_API_KEY','LLM_BASE_URL','ZAI_API_KEY','ZAI_BASE_URL']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ZAI_API_KEY', 'legacy-key')
    monkeypatch.setenv('ZAI_BASE_URL', 'https://legacy.example/v1/')
    monkeypatch.setenv('LLM_API_KEY', 'generic-key')
    monkeypatch.setenv('LLM_BASE_URL', 'https://generic.example/v1/')
    s = Settings.load(tmp_path / 'missing.env')
    assert s.base_url == 'https://generic.example/v1/'
    assert s.api_key.get_secret_value() == 'generic-key'

class FakeBrowser:
    page = SimpleNamespace(url='https://example.org')
    def __init__(self): self.executed=[]
    async def observe(self): return {'url':self.page.url,'text':'Hello','elements':[]}
    async def execute(self, name, args): self.executed.append(name); return {}

class ScriptedProvider:
    """Test double only. Never imported by the application."""
    def __init__(self, calls): self.calls=iter(calls); self.messages=[]
    async def decide(self, messages, tools):
        self.messages.append(messages)
        decision=next(self.calls)
        if isinstance(decision, Exception): raise decision
        return Decision(decision[0], json.dumps(decision[1]))

async def yes(_): return True
async def no(_): return False

def runtime(browser, provider, **kwargs):
    return Runtime(browser,provider,emit=lambda *_:None,**kwargs)

async def test_ask_finish_same_context():
    p=ScriptedProvider([('ask_user',{'question':'Email?'}),('finish',{'result':'Done','status':'complete'})])
    async def answer(_): return 'private@example.org'
    result=await runtime(FakeBrowser(),p,ask=answer).run('Fill form')
    assert result.status=='complete' and result.steps==2
    assert 'private@example.org' in p.messages[1][1]['content']

async def test_max_steps():
    p=ScriptedProvider([('read_page',{})]*3)
    result=await runtime(FakeBrowser(),p,max_steps=3).run('Read')
    assert result.status=='blocked' and 'MAX_AGENT_STEPS' in result.result

async def test_malformed_recovery():
    p=ScriptedProvider([('unknown',{}),MalformedDecision('bad'),('finish',{'result':'blocked','status':'blocked'})])
    r=runtime(FakeBrowser(),p)
    assert (await r.run('task')).steps==3
    assert len(r.memory.recent)==2

async def test_provider_error():
    p=ScriptedProvider([ProviderError('Unavailable')])
    assert (await runtime(FakeBrowser(),p).run('task')).result=='Unavailable'

async def test_confirmation_denial():
    b=FakeBrowser()
    p=ScriptedProvider([('navigate',{'url':'https://example.org'}),('finish',{'result':'denied','status':'blocked'})])
    assert (await runtime(b,p,confirm=no,confirmation_mode='all').run('task')).status=='blocked'
    assert not b.executed

async def test_loop_protection():
    p=ScriptedProvider([('read_page',{})]*6)
    assert 'Repeated action' in (await runtime(FakeBrowser(),p).run('task')).result

def test_memory_bounded():
    m=Memory()
    for _ in range(100): m.add('read_page',{'success':True})
    assert len(m.recent)==8 and sum(m.counts.values())==100

async def test_provider_request_contract():
    class Client:
        def __init__(self): self.chat=SimpleNamespace(completions=self)
        async def create(self, **kwargs):
            assert kwargs['model']=='glm-4.6'
            assert kwargs['tools']==schemas()
            assert kwargs['extra_body']['thinking']['type']=='disabled'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name='read_page',arguments='{}'))]))])
    provider=ZAIProvider(Settings(),client=Client())
    assert (await provider.decide([],schemas())).name=='read_page'

async def test_generic_provider_omits_zai_extension():
    class Client:
        def __init__(self): self.chat=SimpleNamespace(completions=self); self.kwargs=None
        async def create(self, **kwargs):
            self.kwargs=kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name='read_page',arguments='{}'))]))])
    client=Client()
    settings=Settings(provider='openai_compatible', model='other-model', base_url='https://example.org/v1/')
    provider=OpenAICompatibleProvider(settings,client=client)
    assert (await provider.decide([],schemas())).name=='read_page'
    assert 'extra_body' not in client.kwargs
    assert client.kwargs['model']=='other-model'

def test_provider_factory():
    client=SimpleNamespace()
    assert isinstance(create_provider(Settings(), client=client), ZAIProvider)
    assert isinstance(create_provider(Settings(provider='openai_compatible'), client=client), OpenAICompatibleProvider)

async def test_provider_timeout_sanitized():
    class Client:
        def __init__(self): self.chat=SimpleNamespace(completions=self)
        async def create(self, **kwargs): raise TimeoutError('sensitive response')
    with pytest.raises(ProviderError) as exc:
        await ZAIProvider(Settings(),client=Client()).decide([],schemas())
    assert 'sensitive' not in str(exc.value)
    assert 'timed out' in str(exc.value)

async def test_zai_1113_error_is_specific_and_safe():
    class QuotaError(Exception):
        status_code=429
        body={'code':'1113','message':'SECRET provider body'}
    class Client:
        def __init__(self): self.chat=SimpleNamespace(completions=self)
        async def create(self, **kwargs): raise QuotaError('SECRET raw error')
    with pytest.raises(ProviderError) as exc:
        await ZAIProvider(Settings(),client=Client()).decide([],schemas())
    text=str(exc.value)
    assert '1113' in text and 'no balance or resource package' in text
    assert 'SECRET' not in text

async def test_generic_401_error_is_safe():
    class AuthError(Exception):
        status_code=401
        body={'message':'SECRET body'}
    class Client:
        def __init__(self): self.chat=SimpleNamespace(completions=self)
        async def create(self, **kwargs): raise AuthError('SECRET raw')
    s=Settings(provider='openai_compatible',base_url='https://example.org/v1/')
    with pytest.raises(ProviderError) as exc:
        await OpenAICompatibleProvider(s,client=Client()).decide([],schemas())
    assert 'authentication rejected' in str(exc.value)
    assert 'SECRET' not in str(exc.value)

async def test_plan_and_fact_are_retained():
    p=ScriptedProvider([('update_plan',{'steps':['Find source','Read facts']}),
        ('remember',{'fact':'Observed evidence'}),
        ('finish',{'result':'Evidence','status':'complete'})])
    r=runtime(FakeBrowser(),p)
    assert (await r.run('task')).status=='complete'
    payload=json.loads(p.messages[-1][1]['content'])
    assert payload['plan']==['Find source','Read facts']
    assert payload['facts'][0]['fact']=='Observed evidence'

async def test_browser_crash_is_normal_result():
    class Broken(FakeBrowser):
        async def observe(self): raise RuntimeError('Browser closed')
    result=await runtime(Broken(),ScriptedProvider([])).run('task')
    assert result.status=='blocked'

def test_getpass_refuses_echo(monkeypatch):
    import getpass
    import warnings
    def unsafe(_):
        warnings.warn('No terminal',getpass.GetPassWarning)
        return 'should-never-be-read'
    monkeypatch.setattr(getpass,'getpass',unsafe)
    with pytest.raises(ValueError): Settings().require_key()

async def test_form_value_not_logged():
    class FormBrowser(FakeBrowser):
        async def resolve(self, ref): return None
    p=ScriptedProvider([('type_text',{'ref':'s1e0','text':'sensitive-value'}),
        ('finish',{'result':'Done','status':'complete'})])
    logs=[]
    await Runtime(FormBrowser(),p,confirm=yes,confirmation_mode='all',emit=lambda *parts:logs.append(str(parts))).run('Fill')
    assert 'sensitive-value' not in ''.join(logs)

async def test_browser_action_error_recovers_then_finishes():
    from browser_agent.errors import BrowserActionError

    class RecoveringBrowser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def execute(self, name, args):
            self.calls += 1
            if self.calls == 1:
                raise BrowserActionError('driver detail that should not escape')
            return {}

    p = ScriptedProvider([
        ('read_page', {}),
        ('read_page', {}),
        ('finish', {'result': 'Recovered', 'status': 'complete'}),
    ])
    result = await runtime(RecoveringBrowser(), p).run('recover')
    assert result.status == 'complete' and result.steps == 3


async def test_progress_marker_allows_repeated_scroll_when_scroll_position_changes():
    class ScrollingBrowser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.y = 0
        async def observe(self):
            return {
                'url': self.page.url,
                'title': 'Long page',
                'text': 'same body text',
                'elements': [],
                'scroll_y': self.y,
                'document_height': 10000,
            }
        async def execute(self, name, args):
            if name == 'scroll':
                self.y += args.amount
            return {}

    calls = [('scroll', {'direction': 'down', 'amount': 600})] * 5
    calls.append(('finish', {'result': 'bottom', 'status': 'complete'}))
    result = await runtime(ScrollingBrowser(), ScriptedProvider(calls)).run('scroll')
    assert result.status == 'complete' and result.steps == 6


async def test_action_log_has_semantic_target_but_redacts_typed_value():
    class FormBrowser(FakeBrowser):
        async def observe(self):
            return {
                'url': self.page.url,
                'title': 'Form',
                'text': '',
                'elements': [
                    {'ref': 's1e0', 'role': 'textbox', 'name': 'Email', 'href': None}
                ],
            }
        async def resolve(self, ref):
            return None

    p = ScriptedProvider([
        ('type_text', {'ref': 's1e0', 'text': 'private@example.org'}),
        ('finish', {'result': 'Done', 'status': 'complete'}),
    ])
    logs = []
    r = Runtime(FormBrowser(), p, emit=lambda tag, text: logs.append((tag, text)))
    await r.run('fill')
    rendered = '\n'.join(f'{a}:{b}' for a, b in logs)
    assert "ref=s1e0" in rendered and "name='Email'" in rendered
    assert 'private@example.org' not in rendered
    assert '<redacted:19 chars>' in rendered


async def test_denied_action_is_not_confirmed_twice():
    class ButtonBrowser(FakeBrowser):
        async def observe(self):
            return {
                'url': self.page.url,
                'title': 'Checkout',
                'text': '',
                'elements': [
                    {'ref': 's1e0', 'role': 'button', 'name': 'Place order', 'type': 'submit', 'href': None}
                ],
            }
        async def resolve(self, ref):
            return None

    confirmations = []
    async def deny(description):
        confirmations.append(description)
        return False

    p = ScriptedProvider([
        ('click', {'ref': 's1e0'}),
        ('click', {'ref': 's1e0'}),
        ('finish', {'result': 'blocked by user', 'status': 'blocked'}),
    ])
    result = await Runtime(ButtonBrowser(), p, confirm=deny, emit=lambda *_: None).run('order')
    assert result.status == 'blocked'
    assert len(confirmations) == 1


async def test_protocol_repair_uses_required_tool_choice():
    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)
            self.calls = []
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content='plain text'))])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name='read_page', arguments='{}'))
            ], content=None))])

    client = Client()
    s = Settings(provider='openai_compatible', base_url='https://example.org/v1/')
    provider = OpenAICompatibleProvider(s, client=client)
    decision = await provider.decide([], schemas())
    assert decision.name == 'read_page'
    assert client.calls[0]['tool_choice'] == 'auto'
    assert client.calls[1]['tool_choice'] == 'required'

async def test_runtime_recovers_from_stale_ref_with_fresh_observation():
    from browser_agent.errors import StaleRef

    class DynamicBrowser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.generation = 0
            self.failed_once = False
        async def observe(self):
            self.generation += 1
            return {
                'url': self.page.url,
                'title': 'Dynamic',
                'text': 'Continue',
                'elements': [{
                    'ref': f's{self.generation}e0',
                    'role': 'button',
                    'name': 'Continue',
                    'href': None,
                }],
            }
        async def execute(self, name, args):
            if name == 'click' and not self.failed_once:
                self.failed_once = True
                raise StaleRef('changed')
            self.executed.append(name)
            return {}

    p = ScriptedProvider([
        ('click', {'ref': 's1e0'}),
        ('click', {'ref': 's2e0'}),
        ('finish', {'result': 'Recovered', 'status': 'complete'}),
    ])
    r = runtime(DynamicBrowser(), p)
    result = await r.run('click dynamic button')
    assert result.status == 'complete' and result.steps == 3
    assert r.memory.counts['click:error'] == 1
    assert r.memory.counts['click:ok'] == 1

async def test_duplicate_plan_is_nudged_toward_action():
    p = ScriptedProvider([
        ('update_plan', {'steps': ['Open page', 'Read fact']}),
        ('update_plan', {'steps': ['Open page', 'Read fact']}),
        ('finish', {'result': 'Done', 'status': 'complete'}),
    ])
    r = runtime(FakeBrowser(), p)
    result = await r.run('task')
    assert result.status == 'complete'
    duplicate_result = r.memory.recent[-1]['result']
    assert duplicate_result['message'].startswith('Plan unchanged')
    assert duplicate_result['state_changed'] is False

async def test_confirmation_approval_is_retained_for_model():
    b = FakeBrowser()
    p = ScriptedProvider([
        ('navigate', {'url': 'https://example.org'}),
        ('finish', {'result': 'approved', 'status': 'complete'}),
    ])

    r = runtime(
        b,
        p,
        confirm=yes,
        confirmation_mode='all',
    )

    result = await r.run('task')

    assert result.status == 'complete'
    assert 'navigate' in b.executed

    approved = [
        item for item in r.memory.recent
        if item['tool'] == 'navigate'
    ]

    assert approved
    assert approved[-1]['result']['success'] is True
    assert approved[-1]['result']['human_confirmation'] == 'approved'
    assert 'explicit human approval' in approved[-1]['result']['message']

async def test_action_trace_is_grounded_in_executed_browser_actions():
    b = FakeBrowser()
    p = ScriptedProvider([
        ('navigate', {'url': 'https://example.org'}),
        ('go_back', {}),
        ('go_forward', {}),
        ('scroll', {'direction': 'down', 'amount': 600}),
        ('finish', {'result': 'Done', 'status': 'complete'}),
    ])

    r = runtime(b, p)
    result = await r.run('navigate, go back, go forward, scroll, then finish')

    assert result.status == 'complete'

    trace = list(r.memory.action_trace)
    assert [item['action'] for item in trace] == [
        'navigate',
        'go_back',
        'go_forward',
        'scroll',
    ]

    final_payload = json.loads(p.messages[-1][1]['content'])
    assert [item['action'] for item in final_payload['action_trace']] == [
        'navigate',
        'go_back',
        'go_forward',
        'scroll',
    ]

    assert all('text' not in item for item in trace)

async def test_action_trace_records_urls_only_after_success():
    from browser_agent.errors import BrowserActionError

    class TraceBrowser(FakeBrowser):
        async def execute(self, name, args):
            if name == 'scroll':
                raise BrowserActionError('simulated failure')

            self.executed.append(name)

            if name == 'navigate':
                self.page.url = args.url
            elif name == 'go_back':
                self.page.url = 'https://example.org/'
            elif name == 'go_forward':
                self.page.url = 'https://example.org/about/'

            return {}

    b = TraceBrowser()
    p = ScriptedProvider([
        ('navigate', {'url': 'https://example.org/about/'}),
        ('go_back', {}),
        ('go_forward', {}),
        ('scroll', {'direction': 'down', 'amount': 600}),
        ('finish', {'result': 'Recovered after failed scroll', 'status': 'complete'}),
    ])

    r = runtime(b, p)
    result = await r.run('test grounded action trace')

    assert result.status == 'complete'

    trace = list(r.memory.action_trace)

    assert [item['action'] for item in trace] == [
        'navigate',
        'go_back',
        'go_forward',
    ]

    assert trace[0]['from_url'] == 'https://example.org'
    assert trace[0]['resulting_url'] == 'https://example.org/about/'

    assert trace[1]['from_url'] == 'https://example.org/about/'
    assert trace[1]['resulting_url'] == 'https://example.org/'

    assert trace[2]['from_url'] == 'https://example.org/'
    assert trace[2]['resulting_url'] == 'https://example.org/about/'

    assert not any(item['action'] == 'scroll' for item in trace)


async def test_action_trace_context_preserves_execution_order_and_urls():
    b = FakeBrowser()
    p = ScriptedProvider([
        ('navigate', {'url': 'https://example.org'}),
        ('go_back', {}),
        ('go_forward', {}),
        ('finish', {'result': 'Done', 'status': 'complete'}),
    ])

    r = runtime(b, p)
    await r.run('test ordered trace')

    payload = json.loads(p.messages[-1][1]['content'])
    trace = payload['action_trace']

    assert [item['action'] for item in trace] == [
        'navigate',
        'go_back',
        'go_forward',
    ]
    assert all('from_url' in item for item in trace)
    assert all('resulting_url' in item for item in trace)

async def test_semantic_loop_guard_survives_dynamic_dom():
    """Repeated semantic actions must be caught even when refs/DOM change."""
    class DynamicSearchBrowser:
        page = SimpleNamespace(url='https://example.org/search')

        def __init__(self):
            self.observations = 0
            self.executed = []

        async def observe(self):
            self.observations += 1
            return {
                'url': self.page.url,
                'title': 'Search',
                'text': 'dynamic',
                'scroll_y': 0,
                'document_height': 1000 + self.observations,
                'elements': [{
                    'ref': f's{self.observations}e1',
                    'role': 'combobox',
                    'name': 'Search',
                    'href': None,
                    'focused': bool(self.observations % 2),
                }],
            }

        async def execute(self, name, args):
            self.executed.append(name)
            return {}

    b = DynamicSearchBrowser()
    p = ScriptedProvider([
        ('type_text', {'ref':'s1e1', 'text':'Eleanor'}),
        ('type_text', {'ref':'s2e1', 'text':'Eleanor'}),
        ('type_text', {'ref':'s3e1', 'text':'Eleanor'}),
        ('finish', {'result':'Recovered', 'status':'complete'}),
    ])

    r = await runtime(b, p, max_steps=4).run('Search for Eleanor')

    assert r.status == 'complete'
    assert r.result == 'Recovered'
    assert b.executed == ['type_text', 'type_text']
    assert any(
        item['result'].get('error') == 'RepeatedSemanticAction'
        for item in p.messages[3][1]['content'] and []
    ) is False
    assert any(
        item['result'].get('error') == 'RepeatedSemanticAction'
        for item in json.loads(p.messages[3][1]['content'])['recent_results']
    )

@pytest.mark.asyncio
async def test_repeated_planning_forces_action_or_question():
    """Three consecutive update_plan calls must trigger recovery instead of endless replanning."""
    b = FakeBrowser()
    p = ScriptedProvider([
        ('update_plan', {'steps': ['Plan version 1']}),
        ('update_plan', {'steps': ['Plan version 2']}),
        ('update_plan', {'steps': ['Plan version 3']}),
        ('ask_user', {'question': 'Which interpretation should I use?'}),
        ('finish', {'result': 'Recovered after ambiguity', 'status': 'complete'}),
    ])

    r = await runtime(
        b,
        p,
        max_steps=5,
        ask=lambda question: asyncio.sleep(0, result='Use the first interpretation'),
    ).run('Complete an ambiguous task')

    assert r.status == 'complete'
    assert r.result == 'Recovered after ambiguity'

    recovery_seen = any(
        'RepeatedPlanning' in message.get('content', '')
        for call_messages in p.messages
        for message in call_messages
        if message.get('role') == 'user'
    )
    assert recovery_seen

@pytest.mark.asyncio
async def test_semantic_cycle_guard_detects_alternating_actions():
    """A->B repeated three times must be detected even when DOM refs change."""
    class DynamicCycleBrowser:
        page = SimpleNamespace(url='https://example.org/products')

        def __init__(self):
            self.observations = 0
            self.executed = []

        async def observe(self):
            self.observations += 1
            return {
                'url': self.page.url,
                'title': 'Shop',
                'text': f'dynamic-{self.observations}',
                'scroll_y': 0,
                'document_height': 1000 + self.observations,
                'elements': [
                    {
                        'ref': f's{self.observations}e1',
                        'role': 'link',
                        'name': 'Products',
                        'href': '/products',
                        'focused': False,
                    },
                    {
                        'ref': f's{self.observations}e2',
                        'role': 'link',
                        'name': 'Cart',
                        'href': '/cart',
                        'focused': False,
                    },
                ],
            }

        async def execute(self, name, args):
            self.executed.append((name, args.ref))
            return {}

    b = DynamicCycleBrowser()
    p = ScriptedProvider([
        ('click', {'ref':'s1e1'}),
        ('click', {'ref':'s2e2'}),
        ('click', {'ref':'s3e1'}),
        ('click', {'ref':'s4e2'}),
        ('click', {'ref':'s5e1'}),
        ('click', {'ref':'s6e2'}),
        ('finish', {'result':'Recovered from cycle', 'status':'complete'}),
    ])

    r = await runtime(b, p, max_steps=7).run('Avoid navigation loops')

    assert r.status == 'complete'
    assert r.result == 'Recovered from cycle'

    # The sixth action is rejected before browser execution.
    assert len(b.executed) == 5

    recovery_seen = any(
        'RepeatedSemanticCycle' in message.get('content', '')
        for call_messages in p.messages
        for message in call_messages
        if message.get('role') == 'user'
    )
    assert recovery_seen


def test_find_and_fill_form_validation():
    found = parse_call('find_in_page', '{"text":"Apply","max_results":5}')
    assert found.text == 'Apply' and found.max_results == 5
    form = parse_call('fill_form', json.dumps({
        'fields': [
            {'ref':'s1e0','kind':'text','value':'Ada'},
            {'ref':'s1e1','kind':'select','value':'Remote'},
        ]
    }))
    assert len(form.fields) == 2
    with pytest.raises(ValidationError):
        parse_call('fill_form', json.dumps({'fields': [
            {'ref':'s1e0','kind':'text','value':'A'},
            {'ref':'s1e0','kind':'text','value':'B'},
        ]}))


async def test_find_in_page_reuses_snapshot_so_returned_ref_is_actionable():
    class FindBrowser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.observations = 0
        async def observe(self):
            self.observations += 1
            return {
                'url': self.page.url,
                'title': 'Jobs',
                'text': 'Many controls',
                'elements': [{'ref':'s1e0','role':'link','name':'Home','href':'https://example.org'}],
            }
        async def execute(self, name, args):
            self.executed.append(name)
            if name == 'find_in_page':
                return {'query': args.text, 'matches': [
                    {'ref':'s1e181','role':'button','name':'Open details','type':'button','href':None}
                ], 'total_matches': 1, 'text_snippets': ['Open details']}
            if name == 'click':
                assert args.ref == 's1e181'
            return {}

    b = FindBrowser()
    p = ScriptedProvider([
        ('find_in_page', {'text':'details'}),
        ('click', {'ref':'s1e181'}),
        ('finish', {'result':'Found and clicked','status':'complete'}),
    ])
    result = await runtime(b,p,confirmation_mode='none').run('Find details and click it')
    assert result.status == 'complete'
    # Step 2 must reuse the exact snapshot so the ephemeral search ref survives.
    assert b.observations == 2
    second_payload = json.loads(p.messages[1][1]['content'])
    assert any(e.get('ref') == 's1e181' for e in second_payload['observation']['elements'])




async def test_find_in_page_query_is_not_logged():
    class FindBrowser(FakeBrowser):
        async def observe(self):
            return {
                'url': self.page.url,
                'text': 'Many controls',
                'elements': [{'ref':'s1e0','role':'link','name':'Home','href':'https://example.org'}],
            }
        async def execute(self, name, args):
            self.executed.append(name)
            if name == 'find_in_page':
                return {'query': args.text, 'matches': [], 'total_matches': 0, 'text_snippets': []}
            return {}

    secret_query = 'candidate private phrase 92841'
    p = ScriptedProvider([
        ('find_in_page', {'text': secret_query}),
        ('finish', {'result':'Done','status':'complete'}),
    ])
    logs=[]
    result = await Runtime(FindBrowser(), p, emit=lambda tag,text:logs.append(f'{tag}:{text}')).run('Search page')
    rendered='\n'.join(logs)
    assert result.status == 'complete'
    assert secret_query not in rendered
    assert f'query=<redacted:{len(secret_query)} chars>' in rendered


async def test_fill_form_values_are_not_logged():
    class FormBrowser(FakeBrowser):
        async def observe(self):
            return {
                'url': self.page.url,
                'title': 'Form',
                'text': '',
                'elements': [
                    {'ref':'s1e0','role':'textbox','name':'First name','type':'text','href':None},
                    {'ref':'s1e1','role':'textbox','name':'City','type':'text','href':None},
                ],
            }

    b = FormBrowser()
    p = ScriptedProvider([
        ('fill_form', {'fields':[
            {'ref':'s1e0','kind':'text','value':'Ada'},
            {'ref':'s1e1','kind':'text','value':'London'},
        ]}),
        ('finish', {'result':'Done','status':'complete'}),
    ])
    logs=[]
    result = await Runtime(b,p,emit=lambda tag,text:logs.append(f'{tag}:{text}')).run('Fill the form')
    rendered='\n'.join(logs)
    assert result.status == 'complete' and b.executed == ['fill_form']
    assert 'Ada' not in rendered and 'London' not in rendered
    assert 'values=<redacted>' in rendered


def test_upload_file_requires_relative_safe_path(tmp_path):
    from browser_agent.upload import resolve_upload_path
    upload = tmp_path / 'uploads'
    upload.mkdir()
    file = upload / 'resume.pdf'
    file.write_bytes(b'pdf')
    args = parse_call('upload_file', '{"ref":"s1e0","relative_path":"resume.pdf"}')
    assert args.relative_path == 'resume.pdf'
    assert resolve_upload_path(str(upload), args.relative_path) == file.resolve()
    for bad in ('../secret.txt','/etc/passwd','C:\\secret.txt','a//b.txt'):
        with pytest.raises(ValidationError):
            parse_call('upload_file', json.dumps({'ref':'s1e0','relative_path':bad}))


def test_upload_root_setting(tmp_path, monkeypatch):
    monkeypatch.setenv('BROWSER_UPLOAD_ROOT', str(tmp_path))
    assert Settings.load(tmp_path / 'missing.env').browser_upload_root == str(tmp_path)


def test_last_action_error_is_explicit_and_clears_after_success():
    m=Memory()
    m.add('click',{'success':False,'error':'StaleRef','message':'Target changed'})
    assert m.context()['last_action_error']['error']=='StaleRef'
    m.add('read_page',{'success':True,'text':'ok'})
    assert m.context()['last_action_error'] is None


def test_extract_and_manage_tabs_validation():
    page = parse_call('extract_page', '{"query":"delivery","max_sections":4}')
    assert page.query == 'delivery' and page.max_sections == 4
    collection = parse_call('extract_collection', '{"query":"Python","max_items":12}')
    assert collection.query == 'Python' and collection.max_items == 12
    assert parse_call('manage_tabs', '{"action":"list"}').action == 'list'
    assert parse_call('manage_tabs', '{"action":"select","index":1}').index == 1
    assert parse_call('manage_tabs', '{"action":"new","url":"https://example.org/"}').url == 'https://example.org/'
    with pytest.raises(ValidationError):
        parse_call('manage_tabs', '{"action":"select"}')
    with pytest.raises(ValidationError):
        parse_call('manage_tabs', '{"action":"close"}')
    with pytest.raises(ValidationError):
        parse_call('manage_tabs', '{"action":"new","url":"file:///etc/passwd"}')


async def test_extract_collection_reuses_snapshot_so_control_ref_is_actionable():
    class CollectionBrowser(FakeBrowser):
        def __init__(self):
            super().__init__(); self.observations = 0
        async def observe(self):
            self.observations += 1
            return {
                'url': self.page.url, 'title':'Inbox', 'text':'Many messages',
                'elements':[{'ref':'s1e0','role':'link','name':'Inbox','href':'https://example.org'}],
                'tabs':[{'index':0,'url':self.page.url,'active':True}],
            }
        async def execute(self, name, args):
            self.executed.append(name)
            if name == 'extract_collection':
                control={'ref':'s1e181','role':'button','name':'Open message 7','type':'button','href':None}
                return {'query':args.query,'total_candidates':10,'items':[{'text':'Prize spam details','controls':[control]}],'matches':[control]}
            if name == 'click':
                assert args.ref == 's1e181'
            return {}

    b=CollectionBrowser()
    p=ScriptedProvider([
        ('extract_collection', {'query':'Prize'}),
        ('click', {'ref':'s1e181'}),
        ('finish', {'result':'Inspected and clicked','status':'complete'}),
    ])
    result=await runtime(b,p,confirmation_mode='none').run('Inspect the collection and click the matching control')
    assert result.status == 'complete'
    assert b.observations == 2
    second_payload=json.loads(p.messages[1][1]['content'])
    assert any(e.get('ref')=='s1e181' for e in second_payload['observation']['elements'])


async def test_extract_queries_are_redacted_from_action_log():
    class ExtractBrowser(FakeBrowser):
        async def execute(self,name,args):
            self.executed.append(name)
            if name == 'extract_page': return {'sections':[],'headings':[],'snippets':[]}
            if name == 'extract_collection': return {'items':[],'matches':[],'total_candidates':0}
            return {}
    secret='private mail subject 77191'
    p=ScriptedProvider([
        ('extract_collection', {'query':secret}),
        ('finish', {'result':'Done','status':'complete'}),
    ])
    logs=[]
    result=await Runtime(ExtractBrowser(),p,emit=lambda tag,text:logs.append(f'{tag}:{text}')).run('Inspect mail')
    rendered='\n'.join(logs)
    assert result.status == 'complete'
    assert secret not in rendered
    assert f'query=<redacted:{len(secret)} chars>' in rendered


async def test_close_tab_requires_confirmation_even_when_confirmation_mode_none():
    class TabsBrowser(FakeBrowser):
        async def observe(self):
            return {
                'url':'https://first.test/','title':'First','text':'Two tabs open',
                'elements':[],
                'tabs':[{'index':0,'url':'https://first.test/','active':True},{'index':1,'url':'https://draft.test/','active':False}],
            }
        async def execute(self,name,args):
            self.executed.append(name)
            return {'tabs':[{'index':0,'url':'https://first.test/','active':True}]}
    approvals=[]
    async def approve(message): approvals.append(message); return False
    p=ScriptedProvider([
        ('manage_tabs', {'action':'close','index':1,'expected_outcome':'Draft tab is closed'}),
        ('finish', {'result':'User denied closing the tab','status':'blocked'}),
    ])
    result=await Runtime(TabsBrowser(),p,confirm=approve,confirmation_mode='none',emit=lambda *_:None).run('Close the draft tab')
    assert result.status == 'blocked' and len(approvals)==1
    assert 'manage_tabs: close index=1' in approvals[0]


def test_structured_extraction_memory_keeps_multiple_items_within_budget():
    m=Memory()
    items=[{'text':f'Message {i} subject and preview '+('x'*120),'controls':[]} for i in range(10)]
    m.add('extract_collection',{
        'success':True,'message':'ok','current_url':'https://mail.test/','state_changed':False,
        'query':None,'total_candidates':10,'items':items,'matches':[],
    })
    stored=m.context()['recent_results'][-1]['result']
    assert len(stored.get('items',[])) >= 6


def test_recent_context_prioritizes_latest_results_when_budget_is_full():
    m=Memory()
    for i in range(8):
        m.add('read_page',{'success':True,'text':f'item-{i}-'+('x'*4500)})
    recent=m.context()['recent_results']
    rendered=json.dumps(recent)
    assert 'item-7-' in rendered


async def test_manage_tabs_close_is_verified_against_fresh_tab_state():
    class TabsBrowser(FakeBrowser):
        def __init__(self):
            super().__init__(); self.closed=False
        async def observe(self):
            return {
                'url':'https://first.test/','title':'First',
                'text':'Draft tab closed; 1 tab remains' if self.closed else 'Draft tab open; 2 tabs remain',
                'elements':[],
                'tabs':([{'index':0,'url':'https://first.test/','active':True}] if self.closed else [
                    {'index':0,'url':'https://first.test/','active':True},
                    {'index':1,'url':'https://draft.test/','active':False},
                ]),
            }
        async def execute(self,name,args):
            self.executed.append(name)
            if name=='manage_tabs' and args.action=='close': self.closed=True
            return {'tabs':(await self.observe())['tabs']}
    async def approve(_): return True
    b=TabsBrowser()
    p=ScriptedProvider([
        ('manage_tabs',{'action':'close','index':1,'expected_outcome':'Only the first tab remains'}),
        ('verify_action',{'outcome':'achieved','evidence':'Draft tab closed; 1 tab remains','explanation':'Fresh state shows the draft tab is gone'}),
        ('finish',{'result':'Closed and verified','status':'complete'}),
    ])
    result=await Runtime(b,p,confirm=approve,confirmation_mode='none',emit=lambda *_:None).run('Close draft tab')
    assert result.status=='complete' and b.closed
    assert not Runtime(b,p)._fingerprint({
        'url':'https://first.test/','title':'First','text':'same','elements':[],
        'tabs':[{'index':0,'url':'https://first.test/','active':True},{'index':1,'url':'https://draft.test/','active':False}],
    }) == Runtime(b,p)._fingerprint({
        'url':'https://first.test/','title':'First','text':'same','elements':[],
        'tabs':[{'index':0,'url':'https://first.test/','active':True}],
    })

async def test_find_in_page_result_directs_model_to_act_not_search_again():
    class FindBrowser(FakeBrowser):
        async def observe(self):
            return {
                'url': 'https://example.org/',
                'title': 'Example',
                'text': 'Search',
                'elements': [
                    {'ref':'s1e0','role':'textbox','name':'Search','type':'search'}
                ],
            }

        async def execute(self, name, args):
            self.executed.append(name)
            if name == 'find_in_page':
                return {
                    'query': args.text,
                    'matches': [
                        {'ref':'s1e0','role':'textbox','name':'Search','type':'search','href':None}
                    ],
                    'total_matches': 1,
                    'text_snippets': ['Search'],
                }
            return {}

    b = FindBrowser()
    p = ScriptedProvider([
        ('find_in_page', {'text':'Search'}),
        ('finish', {'result':'Done','status':'complete'}),
    ])

    result = await runtime(b, p, confirmation_mode='none').run('Find the search box')

    assert result.status == 'complete'

    second_payload = json.loads(p.messages[1][1]['content'])
    memory_text = json.dumps(second_payload, ensure_ascii=False)

    assert 'Use one of the returned refs in the NEXT action' in memory_text
    assert 'Do NOT call find_in_page again while the page is unchanged' in memory_text
