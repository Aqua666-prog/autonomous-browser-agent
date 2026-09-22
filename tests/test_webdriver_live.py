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
