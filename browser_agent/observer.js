() => {
  if (!window.__agentState) {
    window.__agentState = {version: 0, roots: new WeakSet()};
    window.__agentState.observer = new MutationObserver(() => window.__agentState.version++);
    window.__agentState.observer.observe(document.documentElement,
      {subtree:true, childList:true, attributes:true, characterData:true});
  }
  const visible = e => {const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';};
  const role = e => e.getAttribute('role') || ({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox'}[e.tagName]) ||
    (e.tagName==='INPUT' ? ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'}[e.type]||'textbox') :
      (e.isContentEditable ? 'textbox' : 'generic'));
  const name = e => {
    const ids=e.getAttribute('aria-labelledby');
    return (ids && ids.split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ')) ||
      e.getAttribute('aria-label') || (e.labels && Array.from(e.labels).map(l=>{const c=l.cloneNode(true);c.querySelectorAll('input,select,textarea,button,datalist').forEach(n=>n.remove());return c.textContent||'';}).join(' ')) ||
      e.getAttribute('alt') || e.innerText || e.getAttribute('placeholder') || e.getAttribute('title') || '';
  };
  const all=[];
  const walk = root => { for(const e of root.querySelectorAll('*')) {
    const interactive =
      e.matches('a[href],a[onclick],button,input:not([type=hidden]),textarea,select,[role],[contenteditable=true],[tabindex]') ||
      (e.tagName === 'A' && getComputedStyle(e).cursor === 'pointer');
    if(interactive && visible(e)) all.push(e);
    if(e.shadowRoot) {
      if(!window.__agentState.roots.has(e.shadowRoot)) {
        window.__agentState.roots.add(e.shadowRoot);
        window.__agentState.observer.observe(e.shadowRoot,{subtree:true,childList:true,attributes:true,characterData:true});
      }
      walk(e.shadowRoot);
    }
  }};
  walk(document);
  const inViewport = e => {const r=e.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight;};
  all.sort((a,b)=>Number(inViewport(b))-Number(inViewport(a)));
  window.__agentAllElements=all;
  window.__agentElements=all.slice(0,180);
  // document.activeElement is a host inside Shadow DOM; follow to the focused leaf.
  const elements=window.__agentElements.map(e=>({role:role(e),name:name(e).trim().slice(0,180),
    type:e.type||null,placeholder:e.getAttribute('placeholder'),disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',
    checked:e.checked??null,selected:e.getAttribute('aria-selected'),href:e.tagName==='A'?e.href:null,
    focused:(()=>{let active=document.activeElement;while(active?.shadowRoot?.activeElement)active=active.shadowRoot.activeElement;return e===active;})(),
    form:!!e.form,
    form_method:e.form?.method||null,form_action:e.form?.action||null,
    form_role:e.form?.getAttribute('role')||null,
    form_name:e.form ? name(e.form).trim().slice(0,300) : null,
    autocomplete:e.getAttribute('autocomplete'),
    file_names:e.type==='file' ? Array.from(e.files||[]).slice(0,5).map(f=>f.name.slice(0,120)) : null,
    value:!['password','file'].includes(e.type)&&e.getAttribute('autocomplete')!=='one-time-code'&&'value' in e ? String(e.value).slice(0,4000) : null,
    required:!!e.required, readonly:!!e.readOnly, expanded:e.getAttribute('aria-expanded'),
    invalid:e.getAttribute('aria-invalid')==='true'||(!!e.willValidate&&!e.validity.valid),
    validation_message:e.validationMessage?.slice(0,300)||null,
    options:e.tagName==='SELECT' ? Array.from(e.options).slice(0,50).map(o=>({
      label:(o.text||'').trim().slice(0,120), value:(o.value||'').slice(0,120), selected:!!o.selected, disabled:!!o.disabled
    })) : null,
    in_viewport:(()=>{const r=e.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight;})()}));
  return {version:window.__agentState.version,elements,total_elements:all.length,
    text:document.body?.innerText?.slice(0,12000)||'',text_truncated:(document.body?.innerText?.length||0)>12000,dialogs:Array.from(document.querySelectorAll('dialog[open],[role=dialog]')).filter(visible).map(name),
    scroll_y:Math.round(window.scrollY||0), viewport_height:window.innerHeight||0,
    document_height:Math.max(document.documentElement?.scrollHeight||0, document.body?.scrollHeight||0)};
}
