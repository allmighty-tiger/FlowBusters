PROBE_PLAN = {'schema_version': 2, 'title': 'DOUBLE_SPEND / REPLAY_ATTACK: calling refund/complete twice may pay out twice', 'origin': 'http://localhost:3000', 'setup': '/api/demo/reset', 'state_read': '/api/order', 'claim_type': 'amount_bound', 'actions': [{'method': 'POST', 'path': '/api/order/refund/request', 'body': None}, {'method': 'POST', 'path': '/api/order/refund/complete', 'body': None}, {'method': 'POST', 'path': '/api/order/refund/complete', 'body': None}], 'predicate': 'business_rule_must_hold', 'invariant': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}, 'unsupported_reason': None, 'rule_origin': 'AI_INFERRED', 'inferred_invariant': {'statement': "Total returned to the customer (order.totalReturned) must never exceed the order's original amount (order.originalAmount = 100), even if POST /api/order/refund/complete is called a second time after the refund already completed. UIR-002 shows the 'Complete refund' button and availableActions.completeRefund both disappear after the first completion, implying the workflow intends completion to happen exactly once; this must be enforced server-side, not just hidden in the UI.", 'predicate': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}, 'required_actions': [{'method': 'POST', 'path': '/api/order/refund/request'}, {'method': 'POST', 'path': '/api/order/refund/complete'}, {'method': 'POST', 'path': '/api/order/refund/complete'}], 'terminal_state': [{'path': ['order', 'refund', 'status'], 'equals': 'completed'}]}, 'supporting_observations': [{'source_run': 'refund-rerun', 'artifact': 'demo.json', 'json_pointer': '/workflow_timeline/ui_states/3/elements/66', 'value': 'button "Complete refund" [cursor=pointer]'}, {'source_run': 'refund-rerun', 'artifact': 'demo.json', 'json_pointer': '/workflow_timeline/ui_states/5/changes_from_previous/disappeared/3', 'value': 'button "Complete refund" [cursor=pointer]'}, {'source_run': 'refund-rerun', 'artifact': 'recording.har', 'json_pointer': '/log/entries/2/response/content/text', 'value': '{"message":"Refund completed.","order":{"id":"1042","orderNumber":"#1042","product":{"name":"Northstar Weekender Pack","variant":"Slate · 28 L","quantity":1,"imageUrl":"/assets/weekender-pack.svg"},"purchaseDate":"2026-08-24T14:18:00.000Z","deliveryDate":"2026-08-29T16:42:00.000Z","originalAmount":100,"paymentStatus":"paid","orderStatus":"delivered","fulfillmentStatus":"delivered","cancellation":{"status":"none","reimbursementAmount":0,"completedAt":null},"priceProtection":{"status":"eligible","currentPrice":70,"adjustmentAmount":0,"completedAt":null},"refund":{"status":"completed","requestedAmount":100,"completedAmount":100,"requestedAt":"2026-09-22T23:27:12.771Z","completedAt":"2026-09-22T23:27:14.846Z"},"totalReturned":100,"netCustomerBenefit":0,"availableActions":{"requestRefund":false,"requestPriceAdjustment":false,"cancelOrder":false,"completeRefund":false},"history":[{"id":"evt-1","type":"order_placed","title":"Order placed","detail":"Payment confirmed · $100.00","amount":0,"occurredAt":"2026-08-24T14:18:00.000Z"},{"id":"evt-2","type":"order_delivered","title":"Delivered","detail":"Left at front door","amount":0,"occurredAt":"2026-08-29T16:42:00.000Z"},{"id":"evt-3","type":"refund_requested","title":"Refund requested","detail":"Refund is being reviewed","amount":100,"occurredAt":"2026-09-22T23:27:12.771Z"},{"id":"evt-4","type":"refund_completed","title":"Refund completed","detail":"Sent to original payment method","amount":100,"occurredAt":"2026-09-22T23:27:14.846Z"}]}}'}]}

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
