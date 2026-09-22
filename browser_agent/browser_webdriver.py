import asyncio
import json
from urllib.parse import urlsplit
from pathlib import Path

import httpx
from .errors import BrowserActionError, StaleRef
from .upload import resolve_upload_path
from .extraction import EXTRACT_PAGE_JS, EXTRACT_COLLECTION_JS

OBSERVER = Path(__file__).with_name("observer.js").read_text(encoding="utf-8")
W3C_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
WEBDRIVER_KEYS = {
    "Enter": "\ue007",
    "Tab": "\ue004",
    "Escape": "\ue00c",
    "ArrowDown": "\ue015",
    "ArrowUp": "\ue013",
    "ArrowLeft": "\ue012",
    "ArrowRight": "\ue014",
    "Space": "\ue00d",
    "PageDown": "\ue00f",
    "PageUp": "\ue00e",
    "Home": "\ue011",
    "End": "\ue010",
    "Backspace": "\ue003",
}


class _PageProxy:
    """Backward-compatible `browser.page.url` shim for older callers."""

    def __init__(self, browser):
        self._browser = browser

    @property
    def url(self):
        return self._browser.current_url


class WebDriverBrowser:
    def __init__(
        self,
        webdriver_url="http://127.0.0.1:9515",
        binary="/data/data/com.termux/files/usr/bin/headless_shell",
        headless=True,
        profile_dir=None,
        locale='ru-RU',
        upload_root=None,
    ):
        self.webdriver_url = webdriver_url.rstrip("/")
        self.binary = binary
        self.headless = headless
        self.profile_dir = profile_dir
        self.locale = locale
        self.upload_root = upload_root

        self.client = None
        self.session_id = None
        self.current_url = "about:blank"
        self.page = _PageProxy(self)

        self.generation = 0
        self.refs = {}
        self.ref_meta = {}
        self.known_handles = set()
        self.observed_handle = None
        self.observed_tabs = None

    async def __aenter__(self):
        # Local browser control must not be sent through ambient HTTP/SOCKS proxies.
        local_driver = urlsplit(self.webdriver_url).hostname in {'localhost', '127.0.0.1', '::1'}
        self.client = httpx.AsyncClient(timeout=30.0, trust_env=not local_driver)

        args = [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--single-process",
            "--no-zygote",
            f"--lang={self.locale}",
        ]
        if self.profile_dir:
            args.append(f"--user-data-dir={self.profile_dir}")
        if self.headless and Path(self.binary).name != "headless_shell":
            args.append("--headless=new")

        payload = {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "chrome",
                    "unhandledPromptBehavior": "dismiss",
                    "pageLoadStrategy": "eager",
                    "goog:chromeOptions": {
                        "binary": self.binary,
                        "args": args,
                    },
                }
            }
        }

        try:
            response = await self.client.post(
                f"{self.webdriver_url}/session",
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise BrowserActionError(
                f"WebDriver session transport failed: {type(exc).__name__}"
            ) from None

        try:
            data = response.json()
        except ValueError:
            raise BrowserActionError(
                f"WebDriver session returned non-JSON response (HTTP {response.status_code})"
            ) from None
        if not isinstance(data, dict):
            raise BrowserActionError("WebDriver session returned invalid JSON")
        value = data.get("value", {})

        if isinstance(value, dict) and value.get("error"):
            error = value.get("error")
            message = str(value.get("message", "")).splitlines()[0][:500]
            raise BrowserActionError(
                f"WebDriver session creation failed: {error}: {message} "
                f"(HTTP {response.status_code})"
            )
        if response.is_error:
            raise BrowserActionError(
                f"WebDriver session creation failed (HTTP {response.status_code})"
            )

        self.session_id = value.get("sessionId") or data.get("sessionId")
        if not self.session_id:
            raise RuntimeError("WebDriver did not return a sessionId")

        # Avoid waiting minutes for every analytics/ad resource on modern SPAs.
        await self._request("POST", "timeouts", json={"implicit": 0, "pageLoad": 20000, "script": 10000})
        handles = await self._request("GET", "window/handles")
        self.known_handles = set(handles or [])
        await self._refresh_url()
        return self

    async def __aexit__(self, *exc):
        try:
            if self.client and self.session_id:
                try:
                    await self.client.delete(self._endpoint(""))
                except Exception:
                    pass
        finally:
            if self.client:
                await self.client.aclose()

    def _endpoint(self, suffix):
        if not self.session_id:
            raise RuntimeError("WebDriver session is not active")
        suffix = suffix.lstrip("/")
        base = f"{self.webdriver_url}/session/{self.session_id}"
        return f"{base}/{suffix}" if suffix else base

    async def _request(self, method, suffix, **kwargs):
        try:
            response = await self.client.request(
                method,
                self._endpoint(suffix),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise BrowserActionError(
                f"WebDriver transport failed: {type(exc).__name__}"
            ) from None
        try:
            data = response.json()
        except ValueError:
            raise BrowserActionError(
                f"WebDriver returned non-JSON response (HTTP {response.status_code})"
            )

        value = data.get("value")

        if isinstance(value, dict) and value.get("error"):
            error = value.get("error")
            message = str(value.get("message", "")).splitlines()[0][:500]
            if error in (
                "stale element reference",
                "no such element",
                "no such window",
            ):
                raise StaleRef(message or error)
            raise BrowserActionError(
                f"WebDriver {error}: {message} "
                f"(HTTP {response.status_code})"
            )

        if response.is_error:
            raise BrowserActionError(
                f"WebDriver request failed (HTTP {response.status_code})"
            )
        return value

    async def _refresh_url(self):
        try:
            value = await self._request("GET", "url")
            if isinstance(value, str):
                self.current_url = value
        except Exception:
            pass

    async def _execute_script(self, script, args=None):
        return await self._request(
            "POST",
            "execute/sync",
            json={"script": script, "args": args or []},
        )

    async def _element_id(self, index):
        raw = await self._execute_script(
            "return window.__agentElements && window.__agentElements[arguments[0]];",
            [index],
        )
        if not isinstance(raw, dict):
            raise StaleRef("Element detached before action")
        element_id = raw.get(W3C_ELEMENT_KEY) or raw.get("ELEMENT")
        if not element_id:
            raise BrowserActionError("WebDriver did not serialize the target element")
        return element_id

    async def _sync_windows(self, follow_new=True):
        handles = await self._request("GET", "window/handles") or []
        if not handles:
            raise BrowserActionError("All browser windows are closed")

        try:
            current = await self._request("GET", "window")
        except StaleRef:
            current = None

        new_handles = [h for h in handles if h not in self.known_handles]
        target = new_handles[0] if follow_new and len(new_handles) == 1 else current
        if target not in handles:
            target = handles[-1]

        if current != target:
            await self._request("POST", "window", json={"handle": target})

        tabs = []
        selected = target
        for i, handle in enumerate(handles):
            if selected != handle:
                await self._request("POST", "window", json={"handle": handle})
                selected = handle
            url = await self._request("GET", "url")
            tabs.append({"index": i, "url": url or "", "active": handle == target})

        if selected != target:
            await self._request("POST", "window", json={"handle": target})

        self.known_handles = set(handles)
        self.listed_handles = list(handles)
        await self._refresh_url()
        return tabs

    async def observe(self):
        self.generation += 1
        self.refs = {}
        self.ref_meta = {}

        tabs = await self._sync_windows(follow_new=True)
        self.observed_tabs = list(self.listed_handles)
        self.observed_handle = await self._request("GET", "window")

        # observer.js is an expression in the Playwright backend. Wrapping it
        # in return lets WebDriver execute the same observer.
        data = await self._execute_script(f"return ({OBSERVER})();")

        if not isinstance(data, dict):
            raise RuntimeError("Observer returned invalid data")

        data.pop("version", None)

        elements = data.get("elements", [])
        for i, element in enumerate(elements):
            ref = f"s{self.generation}e{i}"
            self.refs[ref] = i
            self.ref_meta[ref] = {
                k: element.get(k)
                for k in ("role", "name", "href", "type", "form", "form_role", "form_action", "autocomplete")
            }
            element["ref"] = ref

        await self._refresh_url()

        title = await self._request("GET", "title")

        data.update(
            url=self.current_url,
            title=title or "",
            tabs=tabs,
        )
        return data

    async def resolve(self, ref):
        if ref not in self.refs:
            raise StaleRef("Unknown or expired ref; observe again")

        if self.observed_handle is not None and await self._request("GET", "window") != self.observed_handle:
            raise StaleRef("Window changed; observe again")
        index = self.refs[ref]
        expected = self.ref_meta.get(ref, {})

        result = await self._execute_script(
            """
            const i = arguments[0];
            const expected = arguments[1];
            const e = window.__agentElements && window.__agentElements[i];

            if (!e || !e.isConnected) {
                return {ok:false, reason:'detached'};
            }

            const role =
                e.getAttribute('role') ||
                ({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox'}[e.tagName]) ||
                (
                    e.tagName === 'INPUT'
                    ? (
                        {
                            checkbox:'checkbox',
                            radio:'radio',
                            submit:'button',
                            button:'button'
                        }[e.type] || 'textbox'
                    )
                    : (e.isContentEditable ? 'textbox' : 'generic')
                );

            const ids = e.getAttribute('aria-labelledby');

            const name =
                (
                    ids &&
                    ids.split(/\\s+/)
                        .map(id => document.getElementById(id)?.innerText || '')
                        .join(' ')
                ) ||
                e.getAttribute('aria-label') ||
                (
                    e.labels &&
                    Array.from(e.labels)
                        .map(l=>{const c=l.cloneNode(true);c.querySelectorAll('input,select,textarea,button,datalist').forEach(n=>n.remove());return c.textContent||'';})
                        .join(' ')
                ) ||
                e.getAttribute('alt') ||
                e.innerText ||
                e.getAttribute('placeholder') ||
                e.getAttribute('title') ||
                '';

            const current = {
                role: role,
                name: name.trim().slice(0,180),
                href: e.tagName === 'A' ? e.href : null,
                type: e.type || null,
                form: !!e.form,form_role:e.form?.getAttribute('role')||null,form_action:e.form?.action||null,autocomplete:e.getAttribute('autocomplete')
            };

            for (const k of ['role','name','href','type','form','form_role','form_action','autocomplete']) {
                if ((current[k] ?? null) !== (expected[k] ?? null)) {
                    return {
                        ok:false,
                        reason:'changed',
                        current:current
                    };
                }
            }

            return {ok:true};
            """,
            [index, expected],
        )

        if not result or not result.get("ok"):
            raise StaleRef("Target changed or detached; observe again")

        return index

    async def validate_tab(self, index):
        handles = await self._request('GET', 'window/handles') or []
        observed = self.observed_tabs if self.observed_tabs is not None else handles
        if index >= len(observed) or index >= len(handles) or observed[index] != handles[index]:
            raise StaleRef('Observed tab closed or order changed; observe again')
        return observed[index]

    async def execute(self, name, args):
        if name == "manage_tabs":
            handles = await self._request("GET", "window/handles") or []
            if args.action == "list":
                return {"tabs": await self._sync_windows(follow_new=False)}
            if args.action == "select":
                target = await self.validate_tab(args.index)
                await self._request("POST", "window", json={"handle": target})
                return {"tabs": await self._sync_windows(follow_new=False)}
            if args.action == "new":
                created = await self._request("POST", "window/new", json={"type": "tab"}) or {}
                handle = created.get("handle") if isinstance(created, dict) else None
                if handle:
                    await self._request("POST", "window", json={"handle": handle})
                else:
                    raise BrowserActionError("New window did not return an identity; observe before selecting")
                if args.url:
                    await self._request("POST", "url", json={"url": args.url})
                return {"tabs": await self._sync_windows(follow_new=False)}
            if args.action == "close":
                if len(handles) <= 1:
                    raise ValueError("Refusing to close the last browser tab")
                target = await self.validate_tab(args.index)
                current = await self._request("GET", "window")
                if current != target:
                    await self._request("POST", "window", json={"handle": target})
                remaining = await self._request("DELETE", "window") or []
                if not remaining:
                    remaining = [h for h in handles if h != target]
                await self._request("POST", "window", json={"handle": current if current in remaining else remaining[-1]})
                return {"tabs": await self._sync_windows(follow_new=False)}

        elif name == "switch_tab":
            handles = await self._request("GET", "window/handles") or []
            target = await self.validate_tab(args.index)
            await self._request("POST", "window", json={"handle": target})
            await self._sync_windows(follow_new=False)

        elif name == "navigate":
            await self._request(
                "POST",
                "url",
                json={"url": args.url},
            )
            await self._refresh_url()

        elif name == "extract_page":
            return await self._execute_script(
                f"return ({EXTRACT_PAGE_JS})(arguments[0]);",
                [{
                    "query": args.query,
                    "max_sections": args.max_sections,
                    "max_chars_per_section": args.max_chars_per_section,
                }],
            )

        elif name == "extract_collection":
            found = await self._execute_script(
                f"return ({EXTRACT_COLLECTION_JS})(arguments[0]);",
                [{
                    "query": args.query,
                    "max_items": args.max_items,
                    "max_chars_per_item": args.max_chars_per_item,
                }],
            )
            found = found if isinstance(found, dict) else {}
            matches = []
            for item in found.get("items", []):
                for control in item.get("controls", []):
                    index = control.pop("index")
                    ref = f"s{self.generation}e{index}"
                    self.refs[ref] = index
                    self.ref_meta[ref] = {
                        k: control.get(k) for k in ("role", "name", "href", "type", "form", "form_role", "form_action", "autocomplete")
                    }
                    control["ref"] = ref
                    matches.append(control)
            found["matches"] = matches
            return found

        elif name == "find_in_page":
            found = await self._execute_script(
                r"""
                const q = String(arguments[0] || '').trim().toLocaleLowerCase();
                const limit = arguments[1];
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
                    focused:(()=>{let active=document.activeElement;while(active?.shadowRoot?.activeElement)active=active.shadowRoot.activeElement;return e===active;})(), form:!!e.form, form_method:e.form?.method||null,form_action:e.form?.action||null,
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
                return {matches:matches,total_matches:total,text_snippets:snippets};
                """,
                [args.text, args.max_results],
            )
            found = found or {}
            matches = []
            for item in found.get("matches", []):
                index = item.pop("index")
                ref = f"s{self.generation}e{index}"
                self.refs[ref] = index
                self.ref_meta[ref] = {
                    k: item.get(k)
                    for k in ("role", "name", "href", "type", "form", "form_role", "form_action", "autocomplete")
                }
                item["ref"] = ref
                matches.append(item)
            return {
                "query": args.text,
                "matches": matches,
                "total_matches": int(found.get("total_matches", len(matches))),
                "text_snippets": (found.get("text_snippets") or [])[:5],
            }

        elif name == "click":
            index = await self.resolve(args.ref)
            element_id = await self._element_id(index)
            await self._request("POST", f"element/{element_id}/click", json={})
            await asyncio.sleep(0.3)
            await self._sync_windows(follow_new=True)

        elif name == "type_text":
            index = await self.resolve(args.ref)
            if self.ref_meta.get(args.ref, {}).get("type") == "file":
                raise BrowserActionError("File inputs require upload_file")
            element_id = await self._element_id(index)
            await self._request("POST", f"element/{element_id}/clear", json={})
            await self._request(
                "POST",
                f"element/{element_id}/value",
                json={"text": args.text, "value": list(args.text)},
            )

        elif name == "upload_file":
            path = resolve_upload_path(self.upload_root, args.relative_path)
            index = await self.resolve(args.ref)
            element_id = await self._element_id(index)
            value = str(path)
            await self._request(
                "POST",
                f"element/{element_id}/value",
                json={"text": value, "value": list(value)},
            )

        elif name == "fill_form":
            for field in args.fields:
                index = await self.resolve(field.ref)
                if self.ref_meta.get(field.ref, {}).get("type") == "file":
                    raise BrowserActionError("File inputs require upload_file")
                if field.kind == "select":
                    ok = await self._execute_script(
                        """
                        const e = window.__agentElements[arguments[0]];
                        if (!e || !e.isConnected || e.tagName !== 'SELECT' ||
                            e.disabled || e.getAttribute('aria-disabled') === 'true') return false;
                        const wanted = arguments[1];
                        const option = Array.from(e.options).find(
                            o => !o.disabled && (o.value === wanted || (o.text || '').trim() === wanted)
                        );
                        if (!option) return false;
                        e.value = option.value;
                        e.dispatchEvent(new Event('input', {bubbles:true}));
                        e.dispatchEvent(new Event('change', {bubbles:true}));
                        return true;
                        """,
                        [index, field.value],
                    )
                    if not ok:
                        raise ValueError("Select option not found")
                else:
                    element_id = await self._element_id(index)
                    await self._request("POST", f"element/{element_id}/clear", json={})
                    await self._request(
                        "POST",
                        f"element/{element_id}/value",
                        json={"text": field.value, "value": list(field.value)},
                    )

        elif name == "select_option":
            index = await self.resolve(args.ref)
            ok = await self._execute_script(
                """
                const e = window.__agentElements[arguments[0]];
                if (!e || !e.isConnected || e.tagName !== 'SELECT' ||
                    e.disabled || e.getAttribute('aria-disabled') === 'true') {
                    return false;
                }

                const wanted = arguments[1];

                const option = Array.from(e.options).find(
                    o => !o.disabled &&
                         (o.value === wanted || (o.text || '').trim() === wanted)
                );

                if (!option) return false;

                e.value = option.value;
                e.dispatchEvent(
                    new Event('input', {bubbles:true})
                );
                e.dispatchEvent(
                    new Event('change', {bubbles:true})
                );

                return true;
                """,
                [index, args.value],
            )
            if not ok:
                raise ValueError("Select option not found")

        elif name == "press_key":
            value = WEBDRIVER_KEYS[args.key]
            await self._request(
                "POST",
                "actions",
                json={
                    "actions": [{
                        "type": "key",
                        "id": "agent-keyboard",
                        "actions": [
                            {"type": "keyDown", "value": value},
                            {"type": "keyUp", "value": value},
                        ],
                    }]
                },
            )
            await self._request("DELETE", "actions")
            await asyncio.sleep(0.3)
            await self._sync_windows(follow_new=True)

        elif name == "scroll":
            amount = args.amount * (
                1 if args.direction == "down" else -1
            )
            await self._execute_script(
                "window.scrollBy(0, arguments[0]); return true;",
                [amount],
            )

        elif name == "go_back":
            await self._request("POST", "back", json={})
            await self._sync_windows(follow_new=False)

        elif name == "go_forward":
            await self._request("POST", "forward", json={})
            await self._sync_windows(follow_new=False)

        elif name == "wait":
            await asyncio.sleep(args.seconds)

        elif name == "read_page":
            text = await self._execute_script(
                "return document.body ? document.body.innerText : '';"
            )
            text = text or ""
            end = min(len(text), args.offset + 12000)
            return {
                "text": text[args.offset:end],
                "total_characters": len(text),
                "offset": args.offset,
                "next_offset": end if end < len(text) else None,
                "has_more": end < len(text),
            }

        else:
            raise ValueError("Unsupported browser action")

        return {}
