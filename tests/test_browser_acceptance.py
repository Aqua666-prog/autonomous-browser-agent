"""Real browser acceptance fixtures. Scripted providers are NOT live LLM E2E.
No fixture selectors or scenario logic are imported by production code.
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path
import pytest
pytest.importorskip('playwright.async_api')
from browser_agent.browser import Browser
from browser_agent.runtime import Runtime
from browser_agent.llm import Decision
from browser_agent.tools import parse_call


@pytest.fixture
async def browser():
    async with Browser(headless=True, executable_path=os.getenv('BROWSER_EXECUTABLE_PATH')) as b:
        yield b


class Decisions:
    def __init__(self, actions): self.actions=iter(actions)
    async def decide(self,messages,tools):
        state=json.loads(messages[1]['content'])
        name,args=next(self.actions)
        args=dict(args)
        if 'label' in args:
            label=args.pop('label')
            args['ref']=next(e['ref'] for e in state['observation']['elements'] if e['name']==label)
        return Decision(name,json.dumps(args))


def verified(text):
    return ('verify_action',{'outcome':'achieved','evidence':text,'explanation':'Receipt matches the requested change'})


DONE=('finish',{'status':'complete','result':'Verified against fresh page evidence'})


async def test_cart_variant_quantity_and_budget(browser):
    await browser.page.set_content('''<label>Variant<select id="variant" aria-label="Variant"><option>Small</option><option>Large</option></select></label>
      <label>Quantity<input id="qty" type="number" value="1" min="1"></label>
      <button onclick="receipt.textContent=variant.value+' / '+qty.value+' items / '+(12*qty.value)+' EUR'">Add to cart</button>
      <p id="receipt">Empty</p>''')
    p=Decisions([('select_option',{'label':'Variant','value':'Large'}),
                 ('type_text',{'label':'Quantity','text':'2'}),
                 verified('"value": "2"'),
                 ('click',{'label':'Add to cart','expected_outcome':'Large, 2 items, total 24 EUR <= 30 EUR'}),
                 verified('Large / 2 items / 24 EUR'),DONE])
    result=await Runtime(browser,p,emit=lambda *_:None).run('Add two Large items within 30 EUR budget')
    assert result.status=='complete'
    assert await browser.page.locator('#receipt').inner_text()=='Large / 2 items / 24 EUR'


async def test_mail_read_only_analysis_then_confirmed_deletion(browser):
    await browser.page.set_content('''<main id="messages">10 messages; id 7: obvious unsolicited prize spam</main>
       <button onclick="messages.textContent='9 messages; id 7 moved to Trash'">Delete message 7</button>''')
    approvals=[]
    async def approve(description):
        assert '10 messages' in await browser.page.locator('main').inner_text()
        approvals.append(description)
        return True
    p=Decisions([('remember',{'key':'mail-7','fact':'Message 7 is unsolicited prize spam; propose moving to Trash'}),
                 ('click',{'label':'Delete message 7','expected_outcome':'Message 7 removed; nine remain'}),
                 verified('9 messages; id 7 moved to Trash'),DONE])
    result=await Runtime(browser,p,confirm=approve,emit=lambda *_:None).run('Inspect messages before deleting obvious spam')
    assert result.status=='complete' and len(approvals)==1


async def test_job_cover_letter_implicit_submit_confirmation(browser):
    await browser.page.set_content('''<p>Profile: Python developer. Vacancy: Python AI Engineer.</p>
        <form onsubmit="event.preventDefault();document.querySelector('#receipt').textContent='Application received: Python AI Engineer'">
        <label>Cover letter<input id="letter" required></label><button>Continue</button></form><p id="receipt"></p>''')
    approvals=[]
    async def approve(msg): approvals.append(msg); return True
    p=Decisions([('remember',{'key':'candidate','fact':'Profile Python matches AI Engineer Python requirement'}),
                 ('type_text',{'label':'Cover letter','text':'My Python development experience matches your AI Engineer role.'}),
                 ('press_key',{'key':'Enter','expected_outcome':'Application receipt for AI Engineer'}),
                 verified('Application received: Python AI Engineer'),DONE])
    result=await Runtime(browser,p,confirm=approve,emit=lambda *_:None).run('Prepare and submit a matching application after approval')
    assert result.status=='complete' and len(approvals)==1
    assert 'press_key: Enter' in approvals[0]


async def test_form_validation_error_observable_and_not_success(browser):
    await browser.page.set_content('<form><label>Email<input type="email" required></label><button>Continue</button></form>')
    async def approve(_): return True
    p=Decisions([('click',{'label':'Continue'}),('finish',{'status':'blocked','result':'Email required; not submitted'})])
    r=Runtime(browser,p,confirm=approve,emit=lambda *_:None)
    result=await r.run('Submit form')
    snap=await browser.observe()
    assert result.status=='blocked' and r.pending_verification
    assert snap['elements'][0]['invalid'] and snap['elements'][0]['validation_message']


async def test_closed_popup_recovery_and_explicit_tab_switch(browser):
    parent=browser.page
    popup=await browser.context.new_page()
    await popup.set_content('<p>Popup</p>')
    await browser.observe()
    await browser.execute('switch_tab',parse_call('switch_tab','{"index":0}'))
    assert browser.page==parent
    await browser.execute('switch_tab',parse_call('switch_tab','{"index":1}'))
    await popup.close()
    assert len((await browser.observe())['tabs'])==1
    assert browser.page==parent


async def test_cdp_disconnect_preserves_browser_and_existing_local_session(tmp_path):
    # Real Chromium process, real CDP, synthetic auth-like localStorage only.
    # This does not assert authentication against any third-party site.
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        executable=os.getenv('BROWSER_EXECUTABLE_PATH') or pw.chromium.executable_path
    proc=subprocess.Popen([executable,'--headless','--no-sandbox','--disable-gpu',
        '--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',
        f'--user-data-dir={tmp_path}', 'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        port_file=tmp_path/'DevToolsActivePort'
        for _ in range(100):
            if port_file.exists(): break
            if proc.poll() is not None: pytest.fail('Chromium exited before CDP startup')
            await asyncio.sleep(.1)
        assert port_file.exists()
        endpoint='http://127.0.0.1:'+port_file.read_text().splitlines()[0]
        async with Browser(cdp_url=endpoint) as b:
            await b.page.route('http://127.0.0.1:8765/**',lambda route:route.fulfill(body='<h1>Existing session</h1>',content_type='text/html'))
            try:
                await b.page.goto('http://127.0.0.1:8765/')
            except Exception as exc:
                if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc):
                    pytest.skip('Environment browser policy blocks even intercepted local navigation')
                raise
            await b.page.evaluate("localStorage.setItem('test-session', 'synthetic')")
            assert (await b.observe())['text']=='Existing session'
        assert proc.poll() is None
        async with Browser(cdp_url=endpoint) as b:
            assert await b.page.evaluate("localStorage.getItem('test-session')")=='synthetic'
            assert 'Existing session' in (await b.observe())['text']
        assert proc.poll() is None
    finally:
        proc.terminate()
        await asyncio.to_thread(proc.wait,10)
