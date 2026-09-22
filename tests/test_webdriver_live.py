"""Opt-in actual ChromeDriver parity; desktop protocol test, not Android certification."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import pytest
from browser_agent.browser_webdriver import WebDriverBrowser
from browser_agent.errors import StaleRef
from browser_agent.tools import parse_call


@pytest.fixture
async def driver_browser(tmp_path):
    driver=os.getenv('CHROMEDRIVER_EXECUTABLE_PATH')
    binary=os.getenv('BROWSER_EXECUTABLE_PATH')
    if not driver or not binary: pytest.skip('Set CHROMEDRIVER_EXECUTABLE_PATH and BROWSER_EXECUTABLE_PATH for real WebDriver parity')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    proc=subprocess.Popen([driver,f'--port={port}','--allowed-ips=127.0.0.1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        import httpx
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(100):
                if proc.poll() is not None: pytest.fail('ChromeDriver exited before startup')
                try:
                    if (await client.get(f'http://127.0.0.1:{port}/status')).status_code==200: break
                except httpx.HTTPError: pass
                await asyncio.sleep(.05)
            else: pytest.fail('ChromeDriver did not become ready')
        async with WebDriverBrowser(webdriver_url=f'http://127.0.0.1:{port}',binary=binary,upload_root=str(tmp_path)) as browser:
            yield browser,tmp_path
    finally:
        proc.terminate()
        await asyncio.to_thread(proc.wait,10)


def args(tool,**values):return parse_call(tool,json.dumps(values))
def target(snapshot,label):return next(e for e in snapshot['elements'] if e['name']==label)


async def test_real_webdriver_find_form_collection_upload_tabs(driver_browser):
    b,root=driver_browser
    # Fixture installation only. No production model tool exposes JavaScript.
    await b._execute_script("document.body.innerHTML=arguments[0]",['''<form role="search"><label>Search<input type="search"></label></form>
      <label>City<select><option>Paris</option><option>Rome</option></select></label>
      <label>Resume<input type="file"></label>
      <article><h2>AI Engineer</h2><dl><dt>Company</dt><dd>Example Labs</dd></dl><button type="button">Details</button></article>'''])
    snapshot=await b.observe()
    generation=b.generation
    found=await b.execute('find_in_page',args('find_in_page',text='Search'))
    control=next(e for e in found['matches'] if e['type']=='search')
    await b.execute('type_text',args('type_text',ref=control['ref'],text='visible query'))
    assert b.generation==generation
    snapshot=await b.observe()
    assert next(e for e in snapshot['elements'] if e.get('type')=='search')['value']=='visible query'
    await b.execute('fill_form',args('fill_form',fields=[{'ref':target(snapshot,'City')['ref'],'kind':'select','value':'Rome'}]))
    snapshot=await b.observe()
    assert target(snapshot,'City')['value']=='Rome'
    data=await b.execute('extract_collection',args('extract_collection'))
    job=next(row for row in data['items'] if row['fields'].get('title')=='AI Engineer')
    assert job['fields']['Company']=='Example Labs'
    await b.execute('click',args('click',ref=next(c['ref'] for c in job['controls'] if c['name']=='Details')))
    snapshot=await b.observe()
    (root/'resume.txt').write_text('Synthetic resume')
    await b.execute('upload_file',args('upload_file',ref=target(snapshot,'Resume')['ref'],relative_path='resume.txt'))
    assert target(await b.observe(),'Resume')['file_names']==['resume.txt']
    snapshot=await b.observe(); ref=target(snapshot,'Search')['ref']
    await b.execute('manage_tabs',args('manage_tabs',action='new'))
    with pytest.raises(StaleRef):await b.resolve(ref)
    snapshot=await b.observe(); assert len(snapshot['tabs'])==2
    await b.execute('manage_tabs',args('manage_tabs',action='close',index=1))
    assert len((await b.observe())['tabs'])==1


async def test_real_webdriver_stale_target_after_async_replacement(driver_browser):
    b,_=driver_browser
    await b._execute_script("document.body.innerHTML='<button>Open</button>'")
    ref=target(await b.observe(),'Open')['ref']
    await b._execute_script("document.querySelector('button').textContent='Delete Account'")
    with pytest.raises(StaleRef):await b.execute('click',args('click',ref=ref))


class AuditDecisions:
    """Deterministic decisions for security regressions, never a live LLM claim."""
    def __init__(self, actions): self.actions = iter(actions); self.states = []
    async def decide(self, messages, tools):
        from browser_agent.llm import Decision
        state = json.loads(messages[1]['content']); self.states.append(state)
        name, values = next(self.actions)
        values = dict(values)
        if 'label' in values:
            values['ref'] = target(state['observation'], values.pop('label'))['ref']
        if name == 'fill_form':
            values['fields'] = [{'ref':target(state['observation'], 'Attachment')['ref'],
                                 'kind':'text', 'value':values.pop('path')}]
        return Decision(name, json.dumps(values))


@pytest.mark.parametrize('tool', ['type_text', 'fill_form'])
@pytest.mark.parametrize('through_runtime', [False, True])
@pytest.mark.parametrize('root_enabled', [False, True])
async def test_file_input_rejects_text_tools(driver_browser, tool, through_runtime, root_enabled):
    from browser_agent.runtime import Runtime
    from browser_agent.errors import BrowserActionError
    b, root = driver_browser
    outside = root.parent / (root.name + '-outside.txt')
    outside.write_text('SYNTHETIC AUDIT MARKER, NOT A SECRET')
    b.upload_root = str(root) if root_enabled else None
    await b._execute_script("document.body.innerHTML='<label>Attachment<input type=file></label><p></p>';document.querySelector('input').onchange=async e=>{document.querySelector('p').textContent=await e.target.files[0].text()}")
    if through_runtime:
        approvals = []
        async def deny(description): approvals.append(description); return False
        values = {'label':'Attachment','text':str(outside)} if tool == 'type_text' else {'path':str(outside)}
        provider = AuditDecisions([(tool, values), ('finish', {'status':'blocked','result':'Use the approved upload tool'})])
        runtime = Runtime(b, provider, confirm=deny, emit=lambda *_:None)
        result = await runtime.run('Inspect attachment field without uploading')
        assert result.status == 'blocked' and not approvals
        assert runtime.memory.counts[f'{tool}:error'] == 1
        assert provider.states[1]['last_action_error']['error'] == 'PolicyError'
        assert not runtime.memory.action_trace and runtime.pending_verification is None
    else:
        ref = target(await b.observe(), 'Attachment')['ref']
        values = {'ref':ref,'text':str(outside)} if tool == 'type_text' else {
            'fields':[{'ref':ref,'kind':'text','value':str(outside)}]}
        with pytest.raises(BrowserActionError, match='require upload_file'):
            await b.execute(tool, args(tool, **values))
    assert await b._execute_script('return document.querySelector("input").files.length') == 0
    assert await b._execute_script('return document.querySelector("p").textContent') == ''


@pytest.mark.parametrize('attribute,value', [
    ('action', 'https://example.test/different'), ('method', 'post'),
])
async def test_webdriver_enter_rejects_form_change_during_approval(driver_browser, attribute, value):
    from browser_agent.runtime import Runtime
    b, _ = driver_browser
    await b._execute_script("document.body.innerHTML=arguments[0];document.querySelector('input').focus()", [
        '''<form action="https://example.test/approved" onsubmit="event.preventDefault();document.querySelector('p').textContent='SENT'">
        <label>Message<input></label><button>Continue</button></form><p></p>'''])
    approvals = []
    async def approve(description):
        approvals.append(description)
        await b._execute_script("document.querySelector('form').setAttribute(arguments[0],arguments[1])", [attribute,value])
        return True
    provider = AuditDecisions([('press_key', {'key':'Enter'}),
                              ('finish', {'status':'blocked','result':'Form changed'})])
    runtime = Runtime(b, provider, confirm=approve, emit=lambda *_:None)
    result = await runtime.run('Submit only the approved form')
    assert result.status == 'blocked' and len(approvals) == 1
    assert await b._execute_script('return document.querySelector("p").textContent') == ''
    assert runtime.memory.counts['press_key:error'] == 1
    assert not runtime.memory.action_trace and runtime.pending_verification is None


@pytest.mark.parametrize('approved', [False, True])
async def test_webdriver_shadow_enter_confirmation_and_verification(driver_browser, approved):
    from browser_agent.runtime import Runtime
    b, _ = driver_browser
    await b._execute_script('''
      document.body.innerHTML='<div id="host" tabindex="0"></div><p id="receipt"></p>';
      const outer=document.querySelector('#host').attachShadow({mode:'open'});
      outer.innerHTML='<div id="inner" tabindex="0"></div>';
      const root=outer.querySelector('#inner').attachShadow({mode:'open'});
      root.innerHTML='<form><label>Message<input></label><button>Continue</button></form>';
      root.querySelector('form').onsubmit=e=>{e.preventDefault();document.querySelector('#receipt').textContent='SENT'};
      root.querySelector('input').focus();
    ''')
    focused = [e for e in (await b.observe())['elements'] if e.get('focused')]
    assert len(focused) == 1 and focused[0]['name'] == 'Message' and focused[0]['form']
    found = await b.execute('find_in_page', args('find_in_page', text='Message'))
    assert next(e for e in found['matches'] if e['name'] == 'Message')['focused']
    approvals = []
    async def confirm(description): approvals.append(description); return approved
    actions = [('press_key', {'key':'Enter'})]
    if approved:
        actions += [('verify_action', {'outcome':'achieved','evidence':'SENT','explanation':'Fresh submit receipt'}),
                    ('finish', {'status':'complete','result':'Submission verified'})]
    else:
        actions += [('finish', {'status':'blocked','result':'User denied submission'})]
    provider = AuditDecisions(actions)
    runtime = Runtime(b, provider, confirm=confirm, emit=lambda *_:None)
    result = await runtime.run('Submit after approval and verify the receipt')
    assert len(approvals) == 1 and 'Message' in approvals[0]
    assert await b._execute_script('return document.querySelector("#receipt").textContent') == ('SENT' if approved else '')
    assert result.status == ('complete' if approved else 'blocked')
    if approved:
        assert provider.states[1]['pending_verification']['action'] == 'press_key'
        assert runtime.memory.verifications[-1]['outcome'] == 'achieved'
    else:
        assert not runtime.memory.action_trace and runtime.pending_verification is None
