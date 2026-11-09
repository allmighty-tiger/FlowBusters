PROBE_PLAN = {'schema_version': 2, 'title': "DATA_TAMPER / integrity: repeated cancel calls may append duplicate 'order_canceled' ledger entries to order history", 'origin': 'http://localhost:3000', 'setup': '/api/demo/reset', 'state_read': '/api/order', 'claim_type': 'unsupported', 'actions': [{'method': 'POST', 'path': '/api/order/cancel', 'body': None}, {'method': 'POST', 'path': '/api/order/cancel', 'body': None}], 'predicate': 'unsupported_business_rule', 'invariant': None, 'unsupported_reason': "The hypothesis is that order.history should contain exactly one 'order_canceled' entry regardless of how many times cancel is called (an array-length/uniqueness check on the event ledger), not a numeric sum comparison. sum_lte only expresses 'sum of numeric fields <= a numeric limit' and cannot count array entries or check for duplicate event types. This requires a count/uniqueness predicate not currently supported by the plan schema.", 'rule_origin': 'AI_INFERRED', 'inferred_invariant': None, 'supporting_observations': [{'source_run': 'cancel-demo', 'artifact': 'recording.har', 'json_pointer': '/log/entries/1/response/content/text', 'value': '{"message":"Order canceled and reimbursement issued.","order":{"id":"1042","orderNumber":"#1042","product":{"name":"Northstar Weekender Pack","variant":"Slate · 28 L","quantity":1,"imageUrl":"/assets/weekender-pack.svg"},"purchaseDate":"2026-08-24T14:18:00.000Z","deliveryDate":"2026-08-29T16:42:00.000Z","originalAmount":100,"paymentStatus":"paid","orderStatus":"canceled","fulfillmentStatus":"delivered","cancellation":{"status":"completed","reimbursementAmount":100,"completedAt":"2026-09-22T23:51:02.570Z"},"priceProtection":{"status":"eligible","currentPrice":70,"adjustmentAmount":0,"completedAt":null},"refund":{"status":"none","requestedAmount":0,"completedAmount":0,"requestedAt":null,"completedAt":null},"totalReturned":100,"netCustomerBenefit":0,"availableActions":{"requestRefund":false,"requestPriceAdjustment":false,"cancelOrder":false,"completeRefund":false},"history":[{"id":"evt-1","type":"order_placed","title":"Order placed","detail":"Payment confirmed · $100.00","amount":0,"occurredAt":"2026-08-24T14:18:00.000Z"},{"id":"evt-2","type":"order_delivered","title":"Delivered","detail":"Left at front door","amount":0,"occurredAt":"2026-08-29T16:42:00.000Z"},{"id":"evt-3","type":"order_canceled","title":"Order canceled","detail":"Reimbursement issued to original payment method","amount":100,"occurredAt":"2026-09-22T23:51:02.570Z"}]}}'}]}

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
