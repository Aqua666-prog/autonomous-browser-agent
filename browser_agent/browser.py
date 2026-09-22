import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, Error as PlaywrightError
from .errors import StaleRef
from .upload import resolve_upload_path
from .extraction import EXTRACT_PAGE_JS, EXTRACT_COLLECTION_JS

OBSERVER = Path(__file__).with_name('observer.js').read_text(encoding='utf-8')

class Browser:
    def __init__(self, headless=False, executable_path=None, profile_dir=None, storage_state=None, cdp_url=None, locale='ru-RU', upload_root=None):
        self.headless = headless
        self.executable_path = executable_path
        self.profile_dir = profile_dir
        self.storage_state = storage_state
        self.cdp_url = cdp_url
        self.locale = locale
        self.upload_root = upload_root
        self.browser = None
        self._persistent_context = False
        self._cdp = False
        self.generation = 0
        self.refs = {}
        self.ref_meta = {}
        self.version = None
        self.observed_page = None
        self.observed_tabs = None

    async def __aenter__(self):
        self.pw = await async_playwright().start()
        try:
            if self.cdp_url:
                # Attach to the user's real Chrome/Chromium. This preserves its
                # existing authenticated session without copying credentials.
                self.browser = await self.pw.chromium.connect_over_cdp(self.cdp_url)
                self._cdp = True
                self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context(accept_downloads=False, locale=self.locale)
            elif self.profile_dir:
                # A dedicated persistent profile survives agent restarts. Do not
                # point this at a profile simultaneously used by another Chrome.
                self.context = await self.pw.chromium.launch_persistent_context(
                    self.profile_dir, headless=self.headless, executable_path=self.executable_path,
                    accept_downloads=False, locale=self.locale,
                )
                self._persistent_context = True
            else:
                self.browser = await self.pw.chromium.launch(headless=self.headless, executable_path=self.executable_path)
                kwargs = {'accept_downloads': False, 'locale': self.locale}
                if self.storage_state:
                    kwargs['storage_state'] = self.storage_state
                self.context = await self.browser.new_context(**kwargs)
            self.context.set_default_timeout(7000)
            self.context.set_default_navigation_timeout(20000)
            self.context.on('page', self._attach)
            pages = self.context.pages
            self.page = pages[-1] if pages else await self.context.new_page()
            for page in self.context.pages:
                page.on('dialog', lambda dialog: dialog.dismiss())
            return self
        except BaseException:
            await self.pw.stop()
            raise

    def _attach(self, page):
        # Follow popups; never auto-accept native dialogs.
        self.page = page
        page.on('dialog', lambda dialog: dialog.dismiss())

    async def __aexit__(self, *exc):
        try:
            # Disconnecting from CDP must not close the user's real browser.
            if self._cdp:
                return
            if self._persistent_context:
                await self.context.close()
            elif self.browser:
                await self.browser.close()
        finally:
            await self.pw.stop()

    @property
    def current_url(self):
        return self.page.url

    async def observe(self):
        for handle in self.refs.values():
            try: await handle.dispose()
            except Exception: pass
        self.refs = {}
        self.ref_meta = {}
        if self.page.is_closed():
            pages = self.context.pages
            if not pages: raise RuntimeError('All browser tabs are closed')
            self.page = pages[-1]
        self.generation += 1
        data = await self.page.evaluate(OBSERVER)
        self.observed_page = self.page
        self.observed_tabs = list(self.context.pages)
        self.version = data.pop('version')
        for i, element in enumerate(data['elements']):
            ref = f's{self.generation}e{i}'
            handle = await self.page.evaluate_handle('(i)=>window.__agentElements[i]', i)
            self.refs[ref] = handle.as_element()
            self.ref_meta[ref] = {k: element.get(k) for k in ('role','name','href','type','form','form_role','form_action','autocomplete')}
            element['ref'] = ref
        data.update(url=self.page.url, title=await self.page.title(),
            tabs=[{'index': i, 'url': p.url, 'active': p == self.page} for i,p in enumerate(self.context.pages)],
            frames=[{'url': f.url, 'name': f.name} for f in self.page.frames[:20]])
        return data

    async def resolve(self, ref):
        if ref not in self.refs: raise StaleRef('Unknown or expired ref; observe again')
        if self.page != self.observed_page:
            raise StaleRef('Page changed; observe again')
        element = self.refs[ref]
        if not element:
            raise StaleRef('Element detached')
        try:
            connected = await element.evaluate('(e)=>e.isConnected')
        except PlaywrightError:
            raise StaleRef('Page or element changed; observe again') from None
        if not connected:
            raise StaleRef('Element detached')
        # Background DOM churn elsewhere on modern pages must not invalidate a
        # perfectly stable target. Re-check the target's own identity instead.
        expected = self.ref_meta.get(ref, {})
        try:
            current = await element.evaluate(r'''e => {
          const role = e.getAttribute('role') || ({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox'}[e.tagName]) ||
            (e.tagName==='INPUT' ? ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'}[e.type]||'textbox') :
              (e.isContentEditable ? 'textbox' : 'generic'));
          const ids=e.getAttribute('aria-labelledby');
          const name=(ids && ids.split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ')) ||
            e.getAttribute('aria-label') || (e.labels && Array.from(e.labels).map(l=>{const c=l.cloneNode(true);c.querySelectorAll('input,select,textarea,button,datalist').forEach(n=>n.remove());return c.textContent||'';}).join(' ')) ||
            e.getAttribute('alt') || e.innerText || e.getAttribute('placeholder') || e.getAttribute('title') || '';
          return {role, name:name.trim().slice(0,180), href:e.tagName==='A'?e.href:null, type:e.type||null,
            form:!!e.form,form_role:e.form?.getAttribute('role')||null,form_action:e.form?.action||null,autocomplete:e.getAttribute('autocomplete')};
        }''')
        except PlaywrightError:
            raise StaleRef('Page or element changed; observe again') from None
        if any(current.get(k) != expected.get(k) for k in ('role','name','href','type','form','form_role','form_action','autocomplete')):
            raise StaleRef('Target changed; observe again')
        return element

    async def validate_tab(self, index):
        pages = self.context.pages
        observed = self.observed_tabs if self.observed_tabs is not None else pages
        if index >= len(observed) or observed[index] not in pages:
            raise StaleRef('Observed tab closed; observe again')
        if index >= len(pages) or pages[index] != observed[index]:
            raise StaleRef('Tab order changed; observe again')
        return observed[index]

    async def execute(self, name, args):
        # Explicit dispatch, never arbitrary getattr/eval from model input.
        p = self.page
        if name == 'manage_tabs':
            pages = self.context.pages
            if args.action == 'list':
                return {'tabs':[{'index':i,'url':page.url,'active':page==self.page} for i,page in enumerate(pages)]}
            if args.action == 'select':
                self.page = await self.validate_tab(args.index)
                await self.page.bring_to_front()
            elif args.action == 'new':
                self.page = await self.context.new_page()
                if args.url:
                    await self.page.goto(args.url, wait_until='domcontentloaded')
            elif args.action == 'close':
                if len(pages) <= 1:
                    raise ValueError('Refusing to close the last browser tab')
                target = await self.validate_tab(args.index)
                await target.close()
                remaining = self.context.pages
                if self.page == target or self.page.is_closed():
                    self.page = remaining[-1]
                await self.page.bring_to_front()
            pages = self.context.pages
            return {'tabs':[{'index':i,'url':page.url,'active':page==self.page} for i,page in enumerate(pages)]}
        elif name == 'switch_tab':
            pages = self.context.pages
            self.page = await self.validate_tab(args.index)
            await self.page.bring_to_front()
        elif name == 'navigate': await p.goto(args.url, wait_until='domcontentloaded')
        elif name == 'extract_page':
            return await p.evaluate(EXTRACT_PAGE_JS, {
                'query': args.query,
                'max_sections': args.max_sections,
                'max_chars_per_section': args.max_chars_per_section,
            })
        elif name == 'extract_collection':
            found = await p.evaluate(EXTRACT_COLLECTION_JS, {
                'query': args.query,
                'max_items': args.max_items,
                'max_chars_per_item': args.max_chars_per_item,
            })
            matches = []
            for item in found.get('items', []):
                for control in item.get('controls', []):
                    index = control.pop('index')
                    ref = f's{self.generation}e{index}'
                    if ref not in self.refs:
                        handle = await self.page.evaluate_handle('(i)=>window.__agentElements[i]', index)
                        self.refs[ref] = handle.as_element()
                    self.ref_meta[ref] = {k: control.get(k) for k in ('role','name','href','type','form','form_role','form_action','autocomplete')}
                    control['ref'] = ref
                    matches.append(control)
            found['matches'] = matches
            return found
        elif name == 'find_in_page':
            found = await p.evaluate(r'''({query, limit}) => {
              const q = String(query || '').trim().toLocaleLowerCase();
              const all = [...(window.__agentAllElements || window.__agentElements || [])];
              const active = window.__agentElements || (window.__agentElements = []);
              const role = e => e.getAttribute('role') || ({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox'}[e.tagName]) ||
                (e.tagName==='INPUT' ? ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'}[e.type]||'textbox') :
                  (e.isContentEditable ? 'textbox' : 'generic'));
              const name = e => {
                const ids=e.getAttribute('aria-labelledby');
                return (ids && ids.split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ')) ||
                  e.getAttribute('aria-label') || (e.labels && Array.from(e.labels).map(l=>{const c=l.cloneNode(true);c.querySelectorAll('input,select,textarea,button,datalist').forEach(n=>n.remove());return c.textContent||'';}).join(' ')) ||
                  e.getAttribute('alt') || e.innerText || e.getAttribute('placeholder') || e.getAttribute('title') || '';
              };
              const serialize = e => ({
                role:role(e), name:name(e).trim().slice(0,180), type:e.type||null,
                placeholder:e.getAttribute('placeholder'), disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',
                checked:e.checked??null, selected:e.getAttribute('aria-selected'), href:e.tagName==='A'?e.href:null,
                focused:e===document.activeElement, form:!!e.form, form_method:e.form?.method||null,form_action:e.form?.action||null,
                form_role:e.form?.getAttribute('role')||null, form_name:e.form ? name(e.form).trim().slice(0,300) : null,
                autocomplete:e.getAttribute('autocomplete'), file_names:e.type==='file' ? Array.from(e.files||[]).slice(0,5).map(f=>f.name.slice(0,120)) : null, value:!['password','file'].includes(e.type)&&e.getAttribute('autocomplete')!=='one-time-code'&&'value' in e ? String(e.value).slice(0,4000) : null,
    required:!!e.required, readonly:!!e.readOnly, expanded:e.getAttribute('aria-expanded'),
                invalid:e.getAttribute('aria-invalid')==='true'||(!!e.willValidate&&!e.validity.valid),
                validation_message:e.validationMessage?.slice(0,300)||null,
                options:e.tagName==='SELECT' ? Array.from(e.options).slice(0,50).map(o=>({
                  label:(o.text||'').trim().slice(0,120), value:(o.value||'').slice(0,120), selected:!!o.selected, disabled:!!o.disabled
                })) : null,
                in_viewport:(()=>{const r=e.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight;})()
              });
              const actionable=e=>['textbox','searchbox','combobox','button','link','checkbox','radio','tab','option'].includes(role(e));
              all.sort((a,b)=>Number(actionable(b))-Number(actionable(a)));
              const matches=[]; let total=0;
              for (const e of all) {
                if (!e || !e.isConnected) continue;
                const meta=serialize(e);
                const hay=[meta.role,meta.name,meta.placeholder,meta.href,meta.form_name].filter(Boolean).join(' ').toLocaleLowerCase();
                if (!hay.includes(q)) continue;
                total++;
                if (matches.length < limit) {
                  let index=active.indexOf(e);
                  if (index < 0) { index=active.length; active.push(e); }
                  matches.push({index, ...meta});
                }
              }
              const body=document.body?.innerText||'';
              const lower=body.toLocaleLowerCase();
              const snippets=[]; let start=0;
              while (snippets.length < 5) {
                const at=lower.indexOf(q,start);
                if (at < 0) break;
                snippets.push(body.slice(Math.max(0,at-120), Math.min(body.length,at+q.length+180)).replace(/\s+/g,' ').trim());
                start=at+Math.max(1,q.length);
              }
              return {matches,total_matches:total,text_snippets:snippets};
            }''', {'query': args.text, 'limit': args.max_results})
            matches = []
            for item in found.get('matches', []):
                index = item.pop('index')
                ref = f's{self.generation}e{index}'
                if ref not in self.refs:
                    handle = await self.page.evaluate_handle('(i)=>window.__agentElements[i]', index)
                    self.refs[ref] = handle.as_element()
                self.ref_meta[ref] = {k: item.get(k) for k in ('role','name','href','type','form','form_role','form_action','autocomplete')}
                item['ref'] = ref
                matches.append(item)
            return {
                'query': args.text,
                'matches': matches,
                'total_matches': int(found.get('total_matches', len(matches))),
                'text_snippets': found.get('text_snippets', [])[:5],
            }
        elif name == 'click': await (await self.resolve(args.ref)).click()
        elif name == 'type_text': await (await self.resolve(args.ref)).fill(args.text)
        elif name == 'upload_file':
            path = resolve_upload_path(self.upload_root, args.relative_path)
            await (await self.resolve(args.ref)).set_input_files(str(path))
        elif name == 'fill_form':
            resolved = [(field, await self.resolve(field.ref)) for field in args.fields]
            for field, el in resolved:
                el = await self.resolve(field.ref)
                if field.kind == 'select':
                    try: await el.select_option(label=field.value)
                    except Exception: await el.select_option(value=field.value)
                else:
                    await el.fill(field.value)
        elif name == 'select_option':
            el = await self.resolve(args.ref)
            try: await el.select_option(label=args.value)
            except Exception: await el.select_option(value=args.value)
        elif name == 'press_key': await p.keyboard.press(args.key)
        elif name == 'scroll':
            await p.mouse.wheel(0, args.amount * (1 if args.direction=='down' else -1))
            # Chromium may apply wheel scrolling on the next animation frame.
            await asyncio.sleep(0.05)
        elif name == 'go_back': await p.go_back(wait_until='domcontentloaded')
        elif name == 'go_forward': await p.go_forward(wait_until='domcontentloaded')
        elif name == 'wait': await asyncio.sleep(args.seconds)
        elif name == 'read_page':
            text = await p.locator('body').inner_text()
            end = min(len(text), args.offset + 12000)
            return {
                'text': text[args.offset:end],
                'total_characters': len(text),
                'offset': args.offset,
                'next_offset': end if end < len(text) else None,
                'has_more': end < len(text),
            }
        else: raise ValueError('Unsupported browser action')
        return {}
