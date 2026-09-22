"""Live-regression reproductions with deliberately uncooperative decisions."""
import json
import pytest
from browser_agent.runtime import Runtime
from browser_agent.llm import Decision
from browser_agent.search_guard import SearchBudget
from browser_agent.tools import parse_call
from browser_agent.errors import StaleRef


class Page:
    current_url = 'https://example.test/'
    def __init__(self, matches=True):
        self.generation = 0
        self.calls = []
        self.matches = matches
        self.text = 'Search the encyclopedia'
        self.focus = False
    async def observe(self):
        self.generation += 1
        return {'url': self.current_url, 'text': self.text, 'elements': [
            {'ref': f's{self.generation}e0', 'name': 'Search', 'role': 'textbox', 'focused': self.focus}]}
    async def resolve(self, ref):
        if ref != f's{self.generation}e0':
            raise StaleRef('Expired')
    async def execute(self, name, args):
        self.calls.append(name)
        if name == 'find_in_page':
            return {'matches': [{'ref': f's{self.generation}e0', 'role': 'textbox', 'name': 'Search'}] if self.matches else []}
        if name == 'click':
            await self.resolve(args.ref)
            self.focus = True
        if name == 'type_text':
            await self.resolve(args.ref)
            self.text = 'Article results'
        if name == 'read_page':
            return {'text': self.text}
        return {}


class Provider:
    def __init__(self, actions): self.actions = iter(actions); self.payloads = []
    async def decide(self, messages, tools):
        state = json.loads(messages[1]['content']); self.payloads.append(state)
        name, args = next(self.actions); args = dict(args)
        if args.get('ref') == 'current': args['ref'] = state['observation']['elements'][0]['ref']
        return Decision(name, json.dumps(args))


DONE = ('finish', {'status': 'complete', 'result': 'Observed'})
def find(text, **kwargs): return ('find_in_page', {'text': text, **kwargs})
async def run(page, actions):
    p = Provider(actions); r = Runtime(page, p, max_steps=len(actions), emit=lambda *_: None)
    return await r.run('Search using the visible UI'), r, p


async def test_successful_find_ref_focus_click_does_not_need_verification():
    b=Page()
    result,r,p=await run(b,[find('Search'),('click',{'ref':'current','expected_outcome':'Search field focused'}), DONE])
    assert result.status=='complete' and not r.pending_verification
    assert b.calls==['find_in_page','click'] and b.generation==2
    assert p.payloads[1]['observation']['elements'][0]['ref']=='s1e0'


@pytest.mark.parametrize('matches', [True,False])
async def test_synonym_search_loop_is_bounded(matches):
    b=Page(matches)
    actions=[find(q) for q in ['Search','lookup','encyclopedia','find article','query','textbox','input','again']]
    result,r,_=await run(b, actions)
    assert result.status=='blocked'
    assert b.calls.count('find_in_page') == (1 if matches else 2)
    assert 'search budget' in result.result


async def test_page_change_replenishes_budget():
    b=Page(False)
    result,r,_=await run(b,[find('x'),find('y'),('type_text',{'ref':'current','text':'article'}),find('z'),DONE])
    assert result.status=='complete' and b.calls.count('find_in_page')==3


@pytest.mark.parametrize('inspection', [True,False])
async def test_legitimate_second_search(inspection):
    b=Page(); actions=[find('Search')]
    if inspection: actions.append(('read_page',{}))
    actions.append(find('textbox', **({} if inspection else {'refinement_reason':'Need the input instead of its enclosing landmark'})))
    result,_,_=await run(b,actions+[DONE])
    assert result.status=='complete' and b.calls.count('find_in_page')==2


async def test_ref_generation_and_planning_do_not_replenish_search_budget():
    b=Page(False)
    result,r,_=await run(b,[find('x'),('update_plan',{'steps':['Inspect']}),find('y'),
                          ('update_plan',{'steps':['Revise']}),find('z'),DONE])
    assert b.generation>=3 and b.calls.count('find_in_page')==2
    assert r.memory.counts['find_in_page:error']==1


async def test_discovery_appending_refs_does_not_change_search_fingerprint():
    class Crowded(Page):
        async def execute(self,name,args):
            data=await super().execute(name,args)
            if name=='find_in_page': data['matches']=[{'ref':'s1e181','role':'button','name':'Search'}]
            return data
    b=Crowded()
    _,r,_=await run(b,[find('Search'),find('textbox'),DONE])
    assert b.calls==['find_in_page'] and r.memory.counts['find_in_page:error']==1


@pytest.mark.parametrize('role,name', [('button','Open menu'),('search','Поиск'),('textbox','Search'),('textbox','Order number'),('link','Read article'),('tab','Details')])
def test_ui_expected_outcome_is_not_business_verification(role,name):
    r=Runtime(Page(),None)
    snap={'elements':[{'ref':'s1e0','role':role,'name':name}]}
    args=parse_call('click',json.dumps({'ref':'s1e0','expected_outcome':'Control opened and focused'}))
    assert not r._important('click',args,snap)


@pytest.mark.parametrize('name', ['Delete Account','Send message','Pay','Submit application','Удалить письмо'])
def test_consequential_controls_keep_both_gates(name):
    r=Runtime(Page(),None); snap={'elements':[{'ref':'s1e0','role':'button','name':name}]}
    args=parse_call('click','{"ref":"s1e0"}')
    assert r._important('click',args,snap) and r._needs_confirmation('click',args,snap)


async def test_stale_ref_recovery_does_not_leave_phantom_transaction():
    class Stale(Page):
        async def observe(self):
            snap=await super().observe(); snap['elements'][0].update(role='button',name='Add to cart'); return snap
        async def resolve(self,ref):
            raise StaleRef('Detached before dispatch')
    b=Stale()
    _,r,p=await run(b,[('click',{'ref':'current'}),DONE])
    assert r.pending_verification is None
    assert p.payloads[1]['last_action_error']['error']=='StaleRef'


def test_guard_memory_is_bounded():
    g=SearchBudget()
    for i in range(1000): g.check(str(i),'query')
    assert len(g.states)==16


async def test_consequential_timeout_is_recorded_as_uncertain_dispatch():
    class Timeout(Page):
        async def observe(self):
            snapshot=await super().observe()
            snapshot['elements'][0].update(name='Delete item',role='button')
            return snapshot
        async def execute(self,name,args):
            if name=='click': raise TimeoutError('Uncertain transaction')
            return {}
    b=Timeout(); p=Provider([('click',{'ref':'current'}),('finish',{'status':'blocked','result':'Uncertain'})])
    async def approve(_):return True
    r=Runtime(b,p,confirm=approve,emit=lambda *_:None)
    await r.run('Delete after approval')
    assert r.pending_verification and r.memory.action_trace[0]['outcome']=='uncertain'
    assert r.memory.action_trace[0]['dispatch_error']=='TimeoutError'


@pytest.mark.parametrize('title',['CAPTCHA','SmartCaptcha','Two-factor authentication','2FA verification'])
def test_challenge_titles_require_human(title):
    assert Runtime._challenge({'title':title,'text':'','elements':[]})


async def test_duplicate_verify_loop_stops_without_consuming_step_limit():
    b=Page()
    actions=[('verify_action',{'outcome':'achieved','evidence':'Search','explanation':'Repeated'})]*10
    result,r,_=await run(b,actions)
    assert result.status=='blocked' and result.steps==3 and not b.calls
    assert r.memory.counts['verify_action:error']==3
