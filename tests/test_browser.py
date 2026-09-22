"""Real Chromium integration tests; deterministic providers are NOT live LLM E2E."""
import json
import os
import pytest

pytest.importorskip(
    "playwright.async_api",
    reason="Playwright integration tests require the optional Playwright backend",
)

from browser_agent.browser import Browser, StaleRef
from browser_agent.tools import parse_call
from browser_agent.runtime import Runtime
from browser_agent.llm import Decision

@pytest.fixture
async def browser():
    async with Browser(headless=True, executable_path=os.getenv('BROWSER_EXECUTABLE_PATH') or None) as b: yield b

async def test_semantic_mapping_and_fill(browser):
    await browser.page.set_content('<label for="x">Name</label><input id="x"><button disabled>Save</button><button style="display:none">Hidden</button>')
    s=await browser.observe()
    assert [e['name'] for e in s['elements']]==['Name','Save']
    assert s['elements'][1]['disabled']
    ref=s['elements'][0]['ref']
    await browser.execute('type_text',parse_call('type_text',json.dumps({'ref':ref,'text':'Ada'})))
    assert await browser.page.locator('input').input_value()=='Ada'

async def test_invalid_stale_and_detached(browser):
    await browser.page.set_content('<button>Next</button>')
    s=await browser.observe(); ref=s['elements'][0]['ref']
    with pytest.raises(StaleRef): await browser.resolve('s999e0')
    # A target whose own identity changes is stale.
    await browser.page.evaluate('document.querySelector("button").textContent="Changed"')
    with pytest.raises(StaleRef): await browser.resolve(ref)
    s=await browser.observe(); ref=s['elements'][0]['ref']
    await browser.observe()
    with pytest.raises(StaleRef): await browser.resolve(ref)

async def test_new_document_invalidates_refs(browser):
    await browser.page.set_content('<button>Old</button>')
    s=await browser.observe()
    await browser.page.goto('about:blank')
    with pytest.raises(StaleRef): await browser.resolve(s['elements'][0]['ref'])

async def test_read_chunks(browser):
    await browser.page.set_content('<main>'+('A'*13000)+'Z</main>')
    assert (await browser.execute('read_page',parse_call('read_page','{"offset":12000}')))['text'].endswith('Z')

async def test_shadow_dom(browser):
    await browser.page.set_content('<div id="host"></div>')
    await browser.page.evaluate('document.querySelector("#host").attachShadow({mode:"open"}).innerHTML="<button>Shadow</button>"')
    s=await browser.observe()
    assert s['elements'][0]['name']=='Shadow'

async def test_runtime_real_browser_action(browser):
    await browser.page.set_content('<button onclick="document.body.innerHTML=\'<p>Success marker</p>\'">Continue</button>')
    class Provider:
        async def decide(self,messages,tools):
            state=json.loads(messages[-1]['content'])['observation']
            if 'Success marker' in state['text']:
                return Decision('finish',json.dumps({'result':'Success marker','status':'complete'}))
            return Decision('click',json.dumps({'ref':state['elements'][0]['ref']}))
    async def confirm(_): return True
    result=await Runtime(browser,Provider(),confirm=confirm,emit=lambda *_:None).run('Click Continue')
    assert result.status=='complete' and result.steps==2

async def test_popup_followed(browser):
    await browser.page.set_content('<button onclick="window.open(\'about:blank\')">Open</button>')
    s=await browser.observe()
    async with browser.context.expect_page() as pending:
        await browser.execute('click',parse_call('click',json.dumps({'ref':s['elements'][0]['ref']})))
    new=await pending.value
    assert browser.page==new
    assert len((await browser.observe())['tabs'])==2

async def test_unrelated_dom_churn_does_not_stale_target(browser):
    await browser.page.set_content('<button>Stable</button><span id="ticker">0</span>')
    snap = await browser.observe(); ref = snap['elements'][0]['ref']
    await browser.page.evaluate('document.querySelector("#ticker").textContent="1"')
    assert await browser.resolve(ref) is not None

async def test_select_option(browser):
    await browser.page.set_content('<label for="s">City</label><select id="s"><option>Paris</option><option>Rome</option></select>')
    snap = await browser.observe(); ref = snap['elements'][0]['ref']
    await browser.execute('select_option', parse_call('select_option', json.dumps({'ref':ref,'value':'Rome'})))
    assert await browser.page.locator('select').input_value() == 'Rome'

async def test_observation_exposes_select_options_focus_and_scroll_metadata(browser):
    await browser.page.set_content('''
        <input id="q" aria-label="Query">
        <select aria-label="Language">
          <option value="en">English</option>
          <option value="fr" selected>French</option>
        </select>
        <div style="height:3000px"></div>
    ''')
    await browser.page.locator('#q').focus()
    snap = await browser.observe()
    textbox = next(e for e in snap['elements'] if e['role'] == 'textbox')
    select = next(e for e in snap['elements'] if e['role'] == 'combobox')
    assert textbox['focused'] is True
    assert [o['label'] for o in select['options']] == ['English', 'French']
    assert select['options'][1]['selected'] is True
    assert snap['scroll_y'] == 0
    assert snap['viewport_height'] > 0
    assert snap['document_height'] > snap['viewport_height']


async def test_contenteditable_is_exposed_as_textbox_and_fillable(browser):
    await browser.page.set_content('<div contenteditable="true" aria-label="Notes"></div>')
    snap = await browser.observe()
    target = snap['elements'][0]
    assert target['role'] == 'textbox' and target['name'] == 'Notes'
    await browser.execute(
        'type_text',
        parse_call('type_text', json.dumps({'ref': target['ref'], 'text': 'Hello'})),
    )
    assert await browser.page.locator('[contenteditable=true]').inner_text() == 'Hello'


async def test_press_key_scroll_history_and_wait(browser):
    await browser.page.set_content('''
        <input id="q"><p id="state">idle</p><div style="height:3000px"></div>
        <script>
          q.addEventListener('keydown', e => { if (e.key === 'Enter') state.textContent = 'entered'; });
        </script>
    ''')
    await browser.page.locator('#q').focus()
    await browser.execute('press_key', parse_call('press_key', '{"key":"Enter"}'))
    assert await browser.page.locator('#state').inner_text() == 'entered'

    before = await browser.page.evaluate('scrollY')
    await browser.execute('scroll', parse_call('scroll', '{"direction":"down","amount":700}'))
    after = await browser.page.evaluate('scrollY')
    assert after > before

    await browser.page.evaluate("history.pushState({}, '', '#one')")
    await browser.page.evaluate("history.pushState({}, '', '#two')")
    await browser.execute('go_back', parse_call('go_back', '{}'))
    assert browser.page.url.endswith('#one')
    await browser.execute('go_forward', parse_call('go_forward', '{}'))
    assert browser.page.url.endswith('#two')
    await browser.execute('wait', parse_call('wait', '{"seconds":0}'))


async def test_read_chunks_report_continuation(browser):
    await browser.page.set_content('<main>'+('A'*13000)+'Z</main>')
    first = await browser.execute('read_page', parse_call('read_page', '{"offset":0}'))
    assert first['has_more'] is True and first['next_offset'] == 12000
    second = await browser.execute('read_page', parse_call('read_page', '{"offset":12000}'))
    assert second['has_more'] is False and second['next_offset'] is None


async def test_find_in_page_reaches_interactive_element_beyond_default_snapshot(browser):
    buttons=''.join(f'<button onclick="marker.textContent=this.textContent">Button {i}</button>' for i in range(220))
    await browser.page.set_content(buttons+'<p id="marker"></p>')
    snap=await browser.observe()
    assert len(snap['elements']) == 180
    assert not any(e['name']=='Button 219' for e in snap['elements'])
    found=await browser.execute('find_in_page',parse_call('find_in_page','{"text":"Button 219","max_results":3}'))
    assert found['total_matches'] == 1 and found['matches'][0]['name']=='Button 219'
    await browser.execute('click',parse_call('click',json.dumps({'ref':found['matches'][0]['ref']})))
    assert await browser.page.locator('#marker').inner_text()=='Button 219'


async def test_fill_form_batches_stable_text_and_select_fields(browser):
    await browser.page.set_content('''
        <label>Name<input id="name"></label>
        <label>Mode<select id="mode"><option>Office</option><option>Remote</option></select></label>
    ''')
    snap=await browser.observe()
    text_target=next(e for e in snap['elements'] if e['role']=='textbox')
    select_target=next(e for e in snap['elements'] if e['role']=='combobox')
    args=parse_call('fill_form',json.dumps({'fields':[
        {'ref':text_target['ref'],'kind':'text','value':'Ada'},
        {'ref':select_target['ref'],'kind':'select','value':'Remote'},
    ]}))
    await browser.execute('fill_form',args)
    assert await browser.page.locator('#name').input_value()=='Ada'
    assert await browser.page.locator('#mode').input_value()=='Remote'


async def test_upload_file_is_root_scoped_and_observable(tmp_path):
    root=tmp_path/'uploads'; root.mkdir()
    file=root/'resume.txt'; file.write_text('test resume')
    async with Browser(
        headless=True,
        executable_path=os.getenv('BROWSER_EXECUTABLE_PATH') or None,
        upload_root=str(root),
    ) as b:
        await b.page.set_content('''
            <label>Resume<input id="file" type="file" onchange="receipt.textContent=this.files[0]?.name||''"></label>
            <p id="receipt"></p>
        ''')
        snap=await b.observe(); target=next(e for e in snap['elements'] if e['type']=='file')
        await b.execute('upload_file',parse_call('upload_file',json.dumps({'ref':target['ref'],'relative_path':'resume.txt'})))
        fresh=await b.observe(); file_input=next(e for e in fresh['elements'] if e['type']=='file')
        assert file_input['file_names']==['resume.txt']
        assert await b.page.locator('#receipt').inner_text()=='resume.txt'


async def test_extract_page_returns_semantic_sections(browser):
    await browser.page.set_content('''
      <main aria-label="Catalog">
        <h1>Tea market</h1>
        <section aria-label="Delivery"><h2>Delivery</h2><p>Courier delivery tomorrow after 18:00.</p></section>
        <section aria-label="Payment"><h2>Payment</h2><p>Card or cash on receipt.</p></section>
      </main>
    ''')
    await browser.observe()
    data = await browser.execute('extract_page', parse_call('extract_page', json.dumps({
        'query':'delivery', 'max_sections':5, 'max_chars_per_section':500,
    })))
    assert any('Delivery' in h['text'] for h in data['headings'])
    assert any('Courier delivery' in s['text'] for s in data['sections'])
    assert data['query'] == 'delivery'


async def test_extract_collection_structures_mail_shop_and_job_rows_and_refs(browser):
    await browser.page.set_content('''
      <main>
        <ul aria-label="Inbox">
          <li><a href="#m1">Alice — Project update</a><button>Archive Alice</button></li>
          <li><a href="#m2">Prize Robot — YOU WON 1000000</a><button>Delete Prize Robot</button></li>
        </ul>
        <section aria-label="Products">
          <article><h2>Green tea 250 g</h2><p>499 RUB · in stock</p><button>Add Green tea</button></article>
          <article><h2>Black tea 500 g</h2><p>799 RUB · in stock</p><button>Add Black tea</button></article>
        </section>
        <section aria-label="Jobs">
          <article><h2>Python AI Engineer</h2><p>Remote · Python · LLM</p><button>Apply Python AI Engineer</button></article>
          <article><h2>Java Developer</h2><p>Office · Java</p><button>Apply Java Developer</button></article>
        </section>
      </main>
    ''')
    await browser.observe()
    mail = await browser.execute('extract_collection', parse_call('extract_collection', json.dumps({
        'query':'Prize Robot', 'max_items':10, 'max_chars_per_item':500,
    })))
    assert mail['items'] and 'YOU WON' in mail['items'][0]['text']
    delete = next(c for c in mail['items'][0]['controls'] if c['name'] == 'Delete Prize Robot')
    assert delete['ref'].startswith('s')
    await browser.execute('click', parse_call('click', json.dumps({'ref':delete['ref']})))

    shop = await browser.execute('extract_collection', parse_call('extract_collection', json.dumps({
        'query':'Green tea', 'max_items':10,
    })))
    assert any('499 RUB' in item['text'] for item in shop['items'])

    jobs = await browser.execute('extract_collection', parse_call('extract_collection', json.dumps({
        'query':'Python AI', 'max_items':10,
    })))
    assert any('Remote' in item['text'] and 'LLM' in item['text'] for item in jobs['items'])


async def test_manage_tabs_list_new_select_and_close(browser):
    await browser.page.set_content('<h1>First tab</h1>')
    first = browser.page
    listed = await browser.execute('manage_tabs', parse_call('manage_tabs', '{"action":"list"}'))
    assert len(listed['tabs']) == 1 and listed['tabs'][0]['active']

    created = await browser.execute('manage_tabs', parse_call('manage_tabs', '{"action":"new"}'))
    assert len(created['tabs']) == 2 and created['tabs'][1]['active']
    second = browser.page
    await second.set_content('<h1>Second tab</h1>')

    selected = await browser.execute('manage_tabs', parse_call('manage_tabs', '{"action":"select","index":0}'))
    assert browser.page == first and selected['tabs'][0]['active']

    closed = await browser.execute('manage_tabs', parse_call('manage_tabs', '{"action":"close","index":1}'))
    assert len(closed['tabs']) == 1 and browser.page == first
    with pytest.raises(ValueError):
        await browser.execute('manage_tabs', parse_call('manage_tabs', '{"action":"close","index":0}'))


async def test_extract_collection_structural_fallback_handles_div_card_grids(browser):
    await browser.page.set_content('''
      <main><div id="grid">
        <div><h3>Товар A</h3><p>Цена 100 ₽</p><button>В корзину A</button></div>
        <div><h3>Товар B</h3><p>Цена 200 ₽</p><button>В корзину B</button></div>
        <div><h3>Товар C</h3><p>Цена 300 ₽</p><button>В корзину C</button></div>
      </div></main>
    ''')
    await browser.observe()
    data=await browser.execute('extract_collection',parse_call('extract_collection',json.dumps({'query':'Товар B'})))
    assert data['items'] and 'Цена 200 ₽' in data['items'][0]['text']
    assert any(c['name']=='В корзину B' for c in data['items'][0]['controls'])
