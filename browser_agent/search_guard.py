"""Bound page-local discovery independently of wording and temporary refs."""
from collections import OrderedDict


class SearchBudget:
    LIMIT = 2

    def __init__(self):
        self.states = OrderedDict()
        self.rejections = 0

    def state(self, fingerprint):
        if fingerprint not in self.states:
            self.states[fingerprint] = {'used': 0, 'actionable': False, 'inspected': False,
                                        'queries': [], 'targets': []}
        self.states.move_to_end(fingerprint)
        while len(self.states) > 16:
            self.states.popitem(last=False)
        return self.states[fingerprint]

    def check(self, fingerprint, query, refinement_reason=None):
        state = self.state(fingerprint)
        normalized = ' '.join(query.casefold().split())
        reason = None
        if state['used'] >= self.LIMIT:
            reason = 'PageSearchBudgetExhausted'
        elif normalized in state['queries']:
            reason = 'RepeatedPageSearch'
        elif state['actionable'] and not state['inspected'] and not refinement_reason:
            reason = 'UseDiscoveredControl'
        if reason:
            self.rejections += 1
            return {'reason': reason, 'remaining_searches': max(0, self.LIMIT-state['used']),
                    'targets': state['targets'],
                    'next_actions': ['act on a current matching control', 'read_page at a new offset',
                                     'extract_page', 'revise plan or finish blocked'],
                    'detail': 'Changing query wording, refs or plans does not replenish this page budget.'}
        state['used'] += 1
        state['queries'].append(normalized)
        return None

    def found(self, fingerprint, matches):
        state = self.state(fingerprint)
        useful = [m for m in matches if not m.get('disabled') and m.get('role') in
                  {'button','link','textbox','searchbox','combobox','checkbox','radio','tab','option','search'}]
        state['actionable'] = bool(useful)
        state['inspected'] = False
        state['targets'] = [{k: m.get(k) for k in ('role','name','href')} for m in useful[:8]]

    def progress(self, fingerprint, name):
        if name in {'read_page','extract_page','extract_collection','click','type_text','fill_form',
                    'select_option','press_key','navigate','scroll','switch_tab','manage_tabs'}:
            self.state(fingerprint)['inspected'] = True
            self.rejections = 0
