"""Deterministic, read-only semantic extraction used by both browser backends.

The model never supplies JavaScript.  These fixed scripts compact unfamiliar pages into
repeatable structures (sections, list/table/card-like items) without site-specific rules.
"""

EXTRACT_PAGE_JS = r'''(params) => {
  const query = String(params?.query || '').trim().toLocaleLowerCase();
  const maxSections = Number(params?.max_sections || 10);
  const maxChars = Number(params?.max_chars_per_section || 1200);
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    if (!e || !e.isConnected) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
  };
  const label = e => {
    const ids=e.getAttribute?.('aria-labelledby');
    return norm((ids && ids.split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ')) ||
      e.getAttribute?.('aria-label') || e.getAttribute?.('title') || '');
  };
  const qtokens = query.split(/\s+/).filter(Boolean);
  const score = text => {
    if (!query) return 1;
    const lower=text.toLocaleLowerCase();
    if (lower.includes(query)) return 100 + query.length;
    return qtokens.reduce((n,t)=>n+(lower.includes(t)?1:0),0);
  };
  const compactLinks = root => Array.from(root.querySelectorAll?.('a[href]') || [])
    .filter(visible).slice(0,8).map(a=>({text:norm(a.innerText||a.getAttribute('aria-label')).slice(0,180),href:a.href}));
  const compactControls = root => Array.from(root.querySelectorAll?.('button,input:not([type=hidden]),textarea,select,[role=button],[role=checkbox],[role=radio]') || [])
    .filter(visible).slice(0,12).map(e=>({
      tag:e.tagName.toLowerCase(), role:e.getAttribute('role')||null, type:e.type||null,
      name:norm(label(e) || (e.labels && Array.from(e.labels).map(l=>{const c=l.cloneNode(true);c.querySelectorAll('input,select,textarea,button,datalist').forEach(n=>n.remove());return c.textContent||'';}).join(' ')) || e.innerText || e.placeholder).slice(0,180),
      value:(e.tagName==='SELECT'||e.type==='number') ? String(e.value||'').slice(0,120) : null,
      checked:typeof e.checked==='boolean' ? e.checked : null,
      disabled:!!e.disabled || e.getAttribute('aria-disabled')==='true'
    }));

  const headings=Array.from(document.querySelectorAll('h1,h2,h3,h4,[role=heading]'))
    .filter(visible).map(e=>({level:Number(e.getAttribute('aria-level')) || (/^H[1-6]$/.test(e.tagName)?Number(e.tagName[1]):null),text:norm(e.innerText).slice(0,300)}))
    .filter(h=>h.text && (!query || score(h.text)>0)).slice(0,30);

  const raw=Array.from(document.querySelectorAll('main,article,section,form,fieldset,[role=main],[role=region],[role=dialog]')).filter(visible);
  if (!raw.length && document.body) raw.push(document.body);
  const seenText=new Set();
  const sections=[];
  for (let order=0; order<raw.length; order++) {
    const e=raw[order];
    const text=norm(e.innerText);
    if (!text) continue;
    const s=score(text);
    if (query && s<=0) continue;
    const key=text.slice(0,400).toLocaleLowerCase();
    if (seenText.has(key)) continue;
    seenText.add(key);
    sections.push({
      _score:s, _order:order, tag:e.tagName.toLowerCase(), role:e.getAttribute('role')||null,
      label:label(e).slice(0,200), text:text.slice(0,maxChars), text_truncated:text.length>maxChars,
      links:compactLinks(e), controls:compactControls(e)
    });
  }
  sections.sort((a,b)=>(b._score-a._score)||(a._order-b._order));
  for (const s of sections) { delete s._score; delete s._order; }

  const body=norm(document.body?.innerText||'');
  const snippets=[];
  if (query) {
    const lower=body.toLocaleLowerCase(); let start=0;
    while (snippets.length<8) {
      const at=lower.indexOf(query,start); if (at<0) break;
      snippets.push(body.slice(Math.max(0,at-180),Math.min(body.length,at+query.length+260)));
      start=at+Math.max(1,query.length);
    }
  }
  return {query:params?.query||null, headings, sections:sections.slice(0,maxSections), snippets,
    page_text_characters:body.length};
}'''


EXTRACT_COLLECTION_JS = r'''(params) => {
  const query=String(params?.query||'').trim().toLocaleLowerCase();
  const maxItems=Number(params?.max_items||20);
  const maxChars=Number(params?.max_chars_per_item||900);
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const visible=e=>{if(!e||!e.isConnected)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
  const inViewport=e=>{const r=e.getBoundingClientRect();return r.bottom>0&&r.top<(innerHeight||0);};
  const role=e=>e.getAttribute('role')||({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox'}[e.tagName])||
    (e.tagName==='INPUT'?({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'}[e.type]||'textbox'):(e.isContentEditable?'textbox':'generic'));
  const name=e=>{
    const ids=e.getAttribute('aria-labelledby');
    return norm((ids&&ids.split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' '))||
      e.getAttribute('aria-label')||(e.labels&&Array.from(e.labels).map(l=>{const c=l.cloneNode(true);c.querySelectorAll('input,select,textarea,button,datalist').forEach(n=>n.remove());return c.textContent||'';}).join(' '))||
      e.getAttribute('alt')||e.innerText||e.getAttribute('placeholder')||e.getAttribute('title')||'');
  };
  const serialize=e=>({
    role:role(e),name:name(e).slice(0,180),type:e.type||null,placeholder:e.getAttribute('placeholder'),
    disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',checked:e.checked??null,selected:e.getAttribute('aria-selected'),
    href:e.tagName==='A'?e.href:null,focused:e===document.activeElement,form:!!e.form,form_method:e.form?.method||null,form_action:e.form?.action||null,
    form_role:e.form?.getAttribute('role')||null,form_name:e.form?name(e.form).slice(0,300):null,
    autocomplete:e.getAttribute('autocomplete'),file_names:e.type==='file'?Array.from(e.files||[]).slice(0,5).map(f=>f.name.slice(0,120)):null,
    value:!['password','file'].includes(e.type)&&e.getAttribute('autocomplete')!=='one-time-code'&&'value' in e?String(e.value).slice(0,4000):null,
    required:!!e.required,readonly:!!e.readOnly,expanded:e.getAttribute('aria-expanded'),
    invalid:e.getAttribute('aria-invalid')==='true'||(!!e.willValidate&&!e.validity.valid),validation_message:e.validationMessage?.slice(0,300)||null,
    options:e.tagName==='SELECT'?Array.from(e.options).slice(0,50).map(o=>({label:norm(o.text).slice(0,120),value:String(o.value||'').slice(0,120),selected:!!o.selected,disabled:!!o.disabled})):null,
    in_viewport:inViewport(e)
  });
  const qtokens=query.split(/\s+/).filter(Boolean);
  const relevance=text=>{
    if(!query)return 1;
    const lower=text.toLocaleLowerCase();
    if(lower.includes(query))return 100+query.length;
    return qtokens.reduce((n,t)=>n+(lower.includes(t)?1:0),0);
  };

  const candidates=[]; const seen=new Set(); let order=0;
  const add=e=>{
    if(!visible(e)||seen.has(e))return;
    const text=norm(e.innerText||e.getAttribute('aria-label'));
    if(text.length<2)return;
    seen.add(e); candidates.push({e,text,order:order++});
  };
  document.querySelectorAll('article,li,tr,[role=listitem],[role=row],[role=option],[role=article]').forEach(add);
  document.querySelectorAll('ul,ol,tbody,[role=list],[role=grid],[role=feed],main,section').forEach(parent=>{
    const kids=Array.from(parent.children||[]).filter(visible);
    if(kids.length<2||kids.length>200) return;
    for(const e of kids) {
      const text=norm(e.innerText||'');
      if(text.length>=8 && text.length<=12000) add(e);
    }
  });
  // Generic repeated-sibling fallback for modern card grids that omit semantic
  // article/list roles.  It uses only structural repetition, never site classes
  // or button labels, so the same rule works for shops, inboxes and job boards.
  Array.from(document.querySelectorAll('main div,section div,[role=main] div')).slice(0,1800).forEach(parent=>{
    const kids=Array.from(parent.children||[]).filter(visible);
    if(kids.length<3||kids.length>80) return;
    const groups=new Map();
    for(const child of kids){
      const text=norm(child.innerText||'');
      if(text.length<8||text.length>12000) continue;
      const interactiveCount=child.querySelectorAll?.('a[href],button,input,select,textarea,[role=button]')?.length||0;
      const signature=`${child.tagName}:${Math.min(4,child.children?.length||0)}:${Math.min(3,interactiveCount)}`;
      const arr=groups.get(signature)||[]; arr.push(child); groups.set(signature,arr);
    }
    for(const group of groups.values()){
      if(group.length>=3 && group.length>=Math.ceil(kids.length*0.5)) group.forEach(add);
    }
  });

  // Preserve explicit, visible labels instead of guessing missing domain fields.
  const recordFields = root => {
    const fields = {};
    const put = (key,value) => {
      key=norm(key).slice(0,100); value=norm(value).slice(0,500);
      if(key && value && Object.keys(fields).length<24 && !(key in fields)) fields[key]=value;
    };
    const heading=root.querySelector('h1,h2,h3,h4,[role=heading]');
    if(heading && visible(heading)) put('title',heading.innerText);
    for(const el of root.querySelectorAll('[itemprop]')) {
      if(visible(el)) put(el.getAttribute('itemprop'),el.innerText || el.getAttribute('content') || el.getAttribute('value'));
    }
    for(const dt of root.querySelectorAll('dt')) {
      const dd=dt.nextElementSibling;
      if(visible(dt)&&dd?.tagName==='DD'&&visible(dd)) put(dt.innerText,dd.innerText);
    }
    if(root.matches('tr,[role=row]')) {
      const table=root.closest('table,[role=grid],[role=table]');
      const headers=Array.from(table?.querySelectorAll('thead th,[role=columnheader]')||[]).filter(visible);
      const cells=Array.from(root.children).filter(e=>e.matches('td,[role=cell],[role=gridcell]'));
      cells.forEach((cell,i)=>{if(headers[i]&&visible(cell))put(headers[i].innerText,cell.innerText);});
    }
    for(const el of root.querySelectorAll('input,select,textarea,[role=status]')) {
      if(!visible(el)||el.type==='password'||el.type==='file'||el.getAttribute('autocomplete')==='one-time-code')continue;
      const key=name(el);
      if(el.getAttribute('role')==='status')put('status',el.innerText);
      else if(el.type==='checkbox'||el.type==='radio')put(key,String(el.checked));
      else put(key,el.tagName==='SELECT'?el.selectedOptions?.[0]?.text:el.value);
    }
    const times=Array.from(root.querySelectorAll('time')).filter(visible).slice(0,4)
      .map(e=>({text:norm(e.innerText),datetime:e.getAttribute('datetime')}));
    return {fields,times};
  };

  const active=window.__agentElements||(window.__agentElements=[]);
  const interactive='a[href],button,input:not([type=hidden]),textarea,select,[role],[contenteditable=true],[tabindex]';
  const rows=[];
  for(const c of candidates){
    // Skip wrappers containing multiple independent records.
    if(candidates.filter(other=>other!==c && c.e.contains(other.e)).length>=2)continue;
    if(c.e.matches('tr') && !c.e.querySelector('td'))continue;
    const rel=relevance(c.text); if(query&&rel<=0)continue;
    const controls=[]; const local=[];
    if(c.e.matches?.(interactive)) local.push(c.e);
    local.push(...Array.from(c.e.querySelectorAll?.(interactive)||[]));
    const localSeen=new Set();
    for(const el of local){
      if(!visible(el)||localSeen.has(el))continue; localSeen.add(el);
      let index=active.indexOf(el); if(index<0){index=active.length;active.push(el);}
      controls.push({index,...serialize(el)}); if(controls.length>=10)break;
    }
    const links=Array.from(c.e.querySelectorAll?.('a[href]')||[]).filter(visible).slice(0,8)
      .map(a=>({text:name(a).slice(0,180),href:a.href}));
    rows.push({_score:rel+(inViewport(c.e)?0.25:0),_order:c.order,tag:c.e.tagName.toLowerCase(),role:c.e.getAttribute('role')||null,
      text:c.text.slice(0,maxChars),text_truncated:c.text.length>maxChars,links,controls,...recordFields(c.e)});
  }
  rows.sort((a,b)=>(b._score-a._score)||(a._order-b._order));
  const selected=rows.slice(0,maxItems); for(const r of selected){delete r._score;delete r._order;}
  return {query:params?.query||null,total_candidates:rows.length,items:selected};
}'''
