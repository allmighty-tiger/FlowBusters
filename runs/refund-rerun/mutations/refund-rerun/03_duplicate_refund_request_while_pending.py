PROBE_PLAN = {'schema_version': 2, 'title': 'REPLAY_ATTACK: refund/request accepted a second time while a refund is already pending', 'origin': 'http://localhost:3000', 'setup': '/api/demo/reset', 'state_read': '/api/order', 'claim_type': 'unsupported', 'actions': [{'method': 'POST', 'path': '/api/order/refund/request', 'body': None}, {'method': 'POST', 'path': '/api/order/refund/request', 'body': None}], 'predicate': 'unsupported_business_rule', 'invariant': None, 'unsupported_reason': "This tests whether a second POST /api/order/refund/request is rejected while refund.status is already 'pending' (UIR-001: the 'Request refund' button and availableActions.requestRefund both disappear after the first request). requestedAmount is fixed at the order's originalAmount (100) either way, and totalReturned is unaffected until completion, so no numeric field can exceed a bound to prove a duplicate request was wrongly accepted. Confirming this requires reviewing order.history for a duplicate 'refund_requested' event or a changed requestedAt timestamp, which sum_lte cannot express."}

import json, math, httpx
def numeric(body, path):
    for key in path:
        body = body[key]
    if type(body) not in (int, float) or not math.isfinite(body):
        raise ValueError('Non-finite/non-numeric AFTER operand: ' + repr(path))
    return body
with httpx.Client(timeout=30, follow_redirects=False) as client:
    def capture(method, path, body=None):
        response = client.request(method, PROBE_PLAN['origin'] + path,
                                  **({'json': body} if body is not None else {}))
        try:
            value = response.json()
        except ValueError:
            value = response.text
        return {'sequence': response.extensions['flowbusters_sequence'],
                'status_code': response.status_code, 'complete': True,
                'request': {'method': method, 'url': str(response.request.url), 'body': body},
                'response': value}
    setup = []
    if PROBE_PLAN.get('setup'):
        setup = [capture('POST', PROBE_PLAN['setup'], {})]
        if setup[0]['status_code'] != 200:
            raise ValueError('Authorized setup failed')
    before = capture('GET', PROBE_PLAN['state_read'])
    actions = [capture(a['method'], a['path'], a['body']) for a in PROBE_PLAN['actions']]
    after = capture('GET', PROBE_PLAN['state_read'])
    verification = {'predicate': PROBE_PLAN['predicate'], 'setup': setup,
        'before': before, 'actions': actions, 'after': after,
        'rule': {'source': 'agent_inference', 'reference': 'AI-proposed test condition; business meaning requires user review'}}
    verification['rule_origin'] = 'AI_INFERRED'
    verification['inferred_invariant'] = PROBE_PLAN.get('inferred_invariant')
    verification['supporting_observations'] = PROBE_PLAN.get('supporting_observations', [])
    if PROBE_PLAN.get('missing_evidence'):
        verification['missing_evidence'] = PROBE_PLAN['missing_evidence']
    if PROBE_PLAN['predicate'] == 'business_rule_must_hold':
        invariant = PROBE_PLAN['invariant']
        total = sum(numeric(after['response'], p) for p in invariant['terms'])
        maximum = numeric(after['response'], invariant['limit'])
        verification.update(invariant=invariant, violation={'observed': total > maximum,
            'description': f'Final GET: {total} > {maximum} = {total > maximum}'})
    else:
        verification['unsupported_reason'] = PROBE_PLAN['unsupported_reason']
    print(json.dumps({'title': PROBE_PLAN['title'], 'mutation_type':
        'FINANCIAL_BOUND' if PROBE_PLAN['claim_type'] == 'amount_bound' else 'UNSUPPORTED_CLAIM',
        'verification': verification}))
