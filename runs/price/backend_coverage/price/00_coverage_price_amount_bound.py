CONFIG = {'origin': 'http://localhost:3000', 'setup': '/api/demo/reset', 'state_read': '/api/order', 'actions': [{'endpoint': '/api/order/price-adjustment', 'body': {'adjustmentAmount': 130}}], 'invariant': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}, 'name': 'price amount bound'}

import json
import math
import httpx

def numeric(value, path):
    for key in path:
        value = value[key]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Non-numeric invariant operand: ' + repr(path))
    return value

with httpx.Client(timeout=15, follow_redirects=False) as client:
    def capture(method, path, body=None):
        response = client.request(method, CONFIG['origin'] + path,
                                  **({'json': body} if body is not None else {}))
        return {'sequence': response.extensions['flowbusters_sequence'],
                'complete': True, 'status_code': response.status_code,
                'request': {'method': method, 'url': str(response.request.url), 'body': body},
                'response': response.json()}
    setup = capture('POST', CONFIG['setup'], {})
    if setup['status_code'] != 200:
        raise ValueError('Authorized fixture reset failed')
    before = capture('GET', CONFIG['state_read'])
    actions = [capture('POST', action['endpoint'], action['body']) for action in CONFIG['actions']]
    after = capture('GET', CONFIG['state_read'])
    invariant = CONFIG['invariant']
    total = sum(numeric(after['response'], path) for path in invariant['terms'])
    limit = numeric(after['response'], invariant['limit'])
    verification = {'predicate': 'business_rule_must_hold',
        'rule': {'source': 'agent_inference', 'reference': 'Resolve independent backend requirement on report load'},
        'setup': [setup], 'before': before, 'actions': actions, 'after': after,
        'invariant': invariant,
        'violation': {'observed': total > limit, 'description': f'Final state: {total} > {limit} = {total > limit}'}}
    print(json.dumps({'title': CONFIG['name'], 'mutation_type': 'FINANCIAL_BOUND',
                      'verification': verification}))
