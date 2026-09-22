import json
from unittest.mock import AsyncMock

import httpx
import pytest

from browser_agent.browser_webdriver import WebDriverBrowser, WEBDRIVER_KEYS
from browser_agent.errors import BrowserActionError, StaleRef
from browser_agent.tools import parse_call


async def test_webdriver_error_payload_is_decoded_before_http_error():
    def handler(request):
        return httpx.Response(500, json={
            'value': {'error': 'unknown error', 'message': 'useful driver message\nstack trace'}
        })

    b = WebDriverBrowser()
    b.session_id = 'session'
    b.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BrowserActionError) as exc:
            await b._request('GET', 'title')
        assert 'unknown error' in str(exc.value)
        assert 'useful driver message' in str(exc.value)
        assert 'stack trace' not in str(exc.value)
    finally:
        await b.client.aclose()


async def test_webdriver_stale_error_maps_to_stale_ref():
    def handler(request):
        return httpx.Response(404, json={
            'value': {'error': 'stale element reference', 'message': 'stale'}
        })

    b = WebDriverBrowser()
    b.session_id = 'session'
    b.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(StaleRef):
            await b._request('GET', 'title')
    finally:
        await b.client.aclose()


async def test_webdriver_click_and_type_use_native_element_commands():
    b = WebDriverBrowser()
    b.resolve = AsyncMock(return_value=4)
    b._element_id = AsyncMock(return_value='element-1')
    b._sync_windows = AsyncMock(return_value=[])
    calls = []

    async def request(method, suffix, **kwargs):
        calls.append((method, suffix, kwargs.get('json')))
        return None

    b._request = request

    await b.execute('click', parse_call('click', '{"ref":"s1e0"}'))
    assert ('POST', 'element/element-1/click', {}) in calls

    calls.clear()
    args = parse_call('type_text', json.dumps({'ref': 's1e0', 'text': 'Ada'}))
    await b.execute('type_text', args)
    assert calls[0] == ('POST', 'element/element-1/clear', {})
    assert calls[1][0:2] == ('POST', 'element/element-1/value')
    assert calls[1][2]['text'] == 'Ada'
    assert calls[1][2]['value'] == ['A', 'd', 'a']


async def test_webdriver_press_key_uses_w3c_actions():
    b = WebDriverBrowser()
    b._sync_windows = AsyncMock(return_value=[])
    calls = []

    async def request(method, suffix, **kwargs):
        calls.append((method, suffix, kwargs.get('json')))
        return None

    b._request = request
    await b.execute('press_key', parse_call('press_key', '{"key":"Enter"}'))
    assert calls[0][0:2] == ('POST', 'actions')
    actions = calls[0][2]['actions'][0]['actions']
    assert actions == [
        {'type': 'keyDown', 'value': WEBDRIVER_KEYS['Enter']},
        {'type': 'keyUp', 'value': WEBDRIVER_KEYS['Enter']},
    ]
    assert calls[1][0:2] == ('DELETE', 'actions')


async def test_webdriver_read_page_reports_continuation():
    b = WebDriverBrowser()
    b._execute_script = AsyncMock(return_value='X' * 13001)
    result = await b.execute('read_page', parse_call('read_page', '{"offset":12000}'))
    assert result['has_more'] is False
    assert result['next_offset'] is None
    assert len(result['text']) == 1001

async def test_webdriver_follows_new_window_and_reports_tabs():
    b = WebDriverBrowser()
    b.known_handles = {'a'}
    state = {'current': 'a'}
    urls = {'a': 'https://example.test/a', 'b': 'https://example.test/b'}

    async def request(method, suffix, **kwargs):
        if method == 'GET' and suffix == 'window/handles':
            return ['a', 'b']
        if method == 'GET' and suffix == 'window':
            return state['current']
        if method == 'POST' and suffix == 'window':
            state['current'] = kwargs['json']['handle']
            return None
        if method == 'GET' and suffix == 'url':
            return urls[state['current']]
        raise AssertionError((method, suffix, kwargs))

    b._request = request
    tabs = await b._sync_windows(follow_new=True)
    assert state['current'] == 'b'
    assert b.current_url == urls['b']
    assert tabs == [
        {'index': 0, 'url': urls['a'], 'active': False},
        {'index': 1, 'url': urls['b'], 'active': True},
    ]

async def test_webdriver_select_scroll_history_and_wait_dispatch():
    b = WebDriverBrowser()
    b.resolve = AsyncMock(return_value=2)
    b._sync_windows = AsyncMock(return_value=[])
    b._execute_script = AsyncMock(return_value=True)
    calls = []

    async def request(method, suffix, **kwargs):
        calls.append((method, suffix, kwargs.get('json')))
        return None

    b._request = request

    await b.execute(
        'select_option',
        parse_call('select_option', json.dumps({'ref': 's1e0', 'value': 'Rome'})),
    )
    script_args = b._execute_script.await_args.args[1]
    assert script_args == [2, 'Rome']

    b._execute_script.reset_mock(return_value=True)
    await b.execute('scroll', parse_call('scroll', '{"direction":"down","amount":700}'))
    assert b._execute_script.await_args.args[1] == [700]

    calls.clear()
    await b.execute('go_back', parse_call('go_back', '{}'))
    assert calls[0] == ('POST', 'back', {})
    assert b._sync_windows.await_args.kwargs['follow_new'] is False

    calls.clear()
    await b.execute('go_forward', parse_call('go_forward', '{}'))
    assert calls[0] == ('POST', 'forward', {})

    await b.execute('wait', parse_call('wait', '{"seconds":0}'))


async def test_webdriver_switch_tab_and_missing_tab():
    b = WebDriverBrowser()
    calls = []
    async def request(method, suffix, **kwargs):
        calls.append((method, suffix, kwargs))
        if suffix == 'window/handles': return ['a', 'b']
    b._request = request
    b._sync_windows = AsyncMock(return_value=[])
    await b.execute('switch_tab', parse_call('switch_tab', '{"index":1}'))
    assert calls[-1] == ('POST', 'window', {'json': {'handle':'b'}})
    with pytest.raises(StaleRef):
        await b.execute('switch_tab', parse_call('switch_tab', '{"index":9}'))


async def test_webdriver_find_in_page_registers_actionable_refs():
    b = WebDriverBrowser()
    b.generation = 4
    b._execute_script = AsyncMock(return_value={
        'matches': [{
            'index': 181, 'role':'button', 'name':'Apply now', 'href':None,
            'type':'button', 'placeholder':None, 'disabled':False,
        }],
        'total_matches': 3,
        'text_snippets': ['... Apply now ...'],
    })
    result = await b.execute('find_in_page', parse_call('find_in_page', '{"text":"apply"}'))
    assert result['total_matches'] == 3
    assert result['matches'][0]['ref'] == 's4e181'
    assert b.refs['s4e181'] == 181
    assert b.ref_meta['s4e181']['name'] == 'Apply now'


async def test_webdriver_fill_form_text_and_select():
    b = WebDriverBrowser()
    b.resolve = AsyncMock(side_effect=[2, 3])
    b._element_id = AsyncMock(return_value='text-id')
    b._execute_script = AsyncMock(return_value=True)
    calls=[]
    async def request(method, suffix, **kwargs):
        calls.append((method, suffix, kwargs.get('json')))
        return None
    b._request = request
    args = parse_call('fill_form', json.dumps({'fields':[
        {'ref':'s1e0','kind':'text','value':'Ada'},
        {'ref':'s1e1','kind':'select','value':'Remote'},
    ]}))
    await b.execute('fill_form', args)
    assert calls[0] == ('POST','element/text-id/clear',{})
    assert calls[1][0:2] == ('POST','element/text-id/value')
    assert calls[1][2]['text'] == 'Ada'
    assert b._execute_script.await_args.args[1] == [3, 'Remote']


async def test_webdriver_upload_file_uses_only_configured_root(tmp_path):
    root=tmp_path/'uploads'
    root.mkdir()
    file=root/'resume.pdf'
    file.write_bytes(b'pdf')
    b=WebDriverBrowser(upload_root=str(root))
    b.resolve=AsyncMock(return_value=2)
    b._element_id=AsyncMock(return_value='file-id')
    calls=[]
    async def request(method,suffix,**kwargs):
        calls.append((method,suffix,kwargs.get('json')))
        return None
    b._request=request
    await b.execute('upload_file',parse_call('upload_file','{"ref":"s1e0","relative_path":"resume.pdf"}'))
    assert calls[0][0:2]==('POST','element/file-id/value')
    assert calls[0][2]['text']==str(file.resolve())


async def test_webdriver_extract_collection_registers_control_refs():
    b = WebDriverBrowser()
    b.generation = 7
    b._execute_script = AsyncMock(return_value={
        'query':'Python', 'total_candidates':2,
        'items':[{
            'tag':'article','role':None,'text':'Python AI Engineer · Remote',
            'text_truncated':False,'links':[],
            'controls':[{'index':190,'role':'button','name':'Apply Python AI Engineer','type':'button','href':None}],
        }],
    })
    data = await b.execute('extract_collection', parse_call('extract_collection', '{"query":"Python"}'))
    control = data['items'][0]['controls'][0]
    assert control['ref'] == 's7e190'
    assert b.refs['s7e190'] == 190
    assert data['matches'][0]['name'] == 'Apply Python AI Engineer'


async def test_webdriver_extract_page_uses_fixed_script():
    b = WebDriverBrowser()
    b._execute_script = AsyncMock(return_value={'query':'cart','headings':[],'sections':[],'snippets':[]})
    data = await b.execute('extract_page', parse_call('extract_page', '{"query":"cart","max_sections":4}'))
    assert data['query'] == 'cart'
    args = b._execute_script.await_args.args[1][0]
    assert args['query'] == 'cart' and args['max_sections'] == 4


async def test_webdriver_manage_tabs_new_select_close():
    b = WebDriverBrowser()
    state = {'handles':['a'], 'current':'a'}
    urls = {'a':'about:blank', 'b':'about:blank'}

    async def request(method, suffix, **kwargs):
        if method == 'GET' and suffix == 'window/handles':
            return list(state['handles'])
        if method == 'GET' and suffix == 'window':
            return state['current']
        if method == 'GET' and suffix == 'url':
            return urls[state['current']]
        if method == 'POST' and suffix == 'window/new':
            state['handles'].append('b'); state['current'] = 'b'
            return {'handle':'b','type':'tab'}
        if method == 'POST' and suffix == 'window':
            state['current'] = kwargs['json']['handle']; return None
        if method == 'POST' and suffix == 'url':
            urls[state['current']] = kwargs['json']['url']; return None
        if method == 'DELETE' and suffix == 'window':
            closing = state['current']; state['handles'].remove(closing)
            state['current'] = state['handles'][-1]
            return list(state['handles'])
        raise AssertionError((method, suffix, kwargs))

    b._request = request
    b._refresh_url = AsyncMock()
    created = await b.execute('manage_tabs', parse_call('manage_tabs', '{"action":"new","url":"https://example.test/"}'))
    assert state['current'] == 'b' and urls['b'] == 'https://example.test/' and len(created['tabs']) == 2
    await b.execute('manage_tabs', parse_call('manage_tabs', '{"action":"select","index":0}'))
    assert state['current'] == 'a'
    closed = await b.execute('manage_tabs', parse_call('manage_tabs', '{"action":"close","index":1}'))
    assert state['handles'] == ['a'] and len(closed['tabs']) == 1
    with pytest.raises(ValueError):
        await b.execute('manage_tabs', parse_call('manage_tabs', '{"action":"close","index":0}'))
