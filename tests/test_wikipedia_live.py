"""Opt-in public visible-UI smoke, without LLM; no direct article URL construction.
Run: RUN_LIVE_WIKIPEDIA=1 python -m pytest -q -s tests/test_wikipedia_live.py
"""
import json
import os
import pytest
pytest.importorskip('playwright.async_api')
from playwright.async_api import Error as PlaywrightError
from browser_agent.browser import Browser
from browser_agent.runtime import Runtime
from browser_agent.tools import parse_call

pytestmark = pytest.mark.skipif(os.getenv('RUN_LIVE_WIKIPEDIA')!='1', reason='Opt-in external Wikipedia visible-UI test')


async def test_wikipedia_visible_search():
    async with Browser(headless=True, executable_path=os.getenv('BROWSER_EXECUTABLE_PATH')) as browser:
        try:
            response=await browser.page.goto('https://ru.wikipedia.org/',wait_until='domcontentloaded')
        except PlaywrightError as exc:
            if any(marker in str(exc) for marker in ('ERR_EMPTY_RESPONSE','ERR_BLOCKED_BY','ERR_NAME_NOT_RESOLVED','ERR_CONNECTION','ERR_TUNNEL','ERR_CERT','ERR_PROXY')):
                pytest.skip('Environment network prevents Wikipedia access; no bypass attempted')
            raise
        if response and response.status in {403,429,502,503}:
            pytest.skip(f'Wikipedia/network access blocked with HTTP {response.status}; no bypass attempted')
        snapshot=await browser.observe()
        if Runtime._challenge(snapshot): pytest.skip('Human-verification challenge; no bypass attempted')
        found=await browser.execute('find_in_page',parse_call('find_in_page','{"text":"search","max_results":20}'))
        candidates=snapshot['elements']+found['matches']
        inputs=[e for e in candidates if e.get('type')=='search' or e.get('role') in {'textbox','searchbox'}]
        assert inputs, 'No visible search input observed'
        target=next((e for e in inputs if e.get('type')=='search'),inputs[0])
        await browser.execute('type_text',parse_call('type_text',json.dumps({'ref':target['ref'],'text':'Элеонора Аквитанская'})))
        await browser.execute('press_key',parse_call('press_key','{"key":"Enter"}'))
        await browser.page.wait_for_load_state('domcontentloaded')
        await browser.page.wait_for_timeout(700)
        snapshot=await browser.observe()
        if Runtime._challenge(snapshot): pytest.skip('Human challenge after search; no bypass attempted')
        if 'Элеонора Аквитанская' not in snapshot.get('title',''):
            found=await browser.execute('find_in_page',parse_call('find_in_page','{"text":"Элеонора Аквитанская","max_results":20}'))
            link=next((e for e in found['matches'] if e.get('role')=='link' and e.get('name')=='Элеонора Аквитанская'),None)
            assert link, 'Search result was not observed'
            await browser.execute('click',parse_call('click',json.dumps({'ref':link['ref']})))
            await browser.page.wait_for_load_state('domcontentloaded')
            snapshot=await browser.observe()
        text=snapshot.get('text','')
        assert '1137' in text and 'Франц' in text
        print('VISIBLE_UI_SOURCE:',browser.current_url)
        print('OBSERVED_YEAR: 1137; scripted browser smoke, not autonomous LLM E2E')
