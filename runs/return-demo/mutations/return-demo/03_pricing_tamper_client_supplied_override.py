PROBE_PLAN = {'schema_version': 2, 'title': 'PRICING_TAMPER: client-supplied adjustmentAmount/totalReturned overrides in the request body may inflate the returned amount beyond originalAmount', 'origin': 'http://localhost:3000', 'setup': '/api/demo/reset', 'state_read': '/api/order', 'claim_type': 'amount_bound', 'actions': [{'method': 'POST', 'path': '/api/order/price-adjustment', 'body': {'adjustmentAmount': 9999, 'totalReturned': 9999, 'netCustomerBenefit': 9999, 'amount': 9999}}], 'predicate': 'business_rule_must_hold', 'invariant': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}, 'rule': {'source': 'agent_inference', 'provenance': {'schema_version': 1, 'source_run': 'return-demo', 'artifact': 'state_map.json', 'note': "critical_endpoints[].why 'Agent inference (unverified)' hypothesis on POST /api/order/price-adjustment: server may trust client-supplied amount fields instead of recomputing them."}}, 'rule_origin': 'AI_INFERRED', 'inferred_invariant': {'statement': 'The recorded call sent no request body at all (bodySize 0), so the server computed adjustmentAmount=30 itself. If the endpoint actually reads amount fields from the body when present, a client could inflate totalReturned past originalAmount by supplying oversized values.', 'predicate': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}, 'required_actions': [{'method': 'POST', 'path': '/api/order/price-adjustment'}], 'terminal_state': [{'path': ['order', 'priceProtection', 'status'], 'equals': 'completed'}]}, 'supporting_observations': [{'source_run': 'return-demo', 'artifact': 'recording.har', 'json_pointer': '/log/entries/0/response/content/text', 'value': '{"order":{"id":"1042","orderNumber":"#1042","product":{"name":"Northstar Weekender Pack","variant":"Slate · 28 L","quantity":1,"imageUrl":"/assets/weekender-pack.svg"},"purchaseDate":"2026-08-24T14:18:00.000Z","deliveryDate":"2026-08-29T16:42:00.000Z","originalAmount":100,"paymentStatus":"paid","orderStatus":"delivered","fulfillmentStatus":"delivered","cancellation":{"status":"none","reimbursementAmount":0,"completedAt":null},"priceProtection":{"status":"eligible","currentPrice":70,"adjustmentAmount":0,"completedAt":null},"refund":{"status":"none","requestedAmount":0,"completedAmount":0,"requestedAt":null,"completedAt":null},"totalReturned":0,"netCustomerBenefit":-100,"availableActions":{"requestRefund":true,"requestPriceAdjustment":true,"cancelOrder":true,"completeRefund":false},"history":[{"id":"evt-1","type":"order_placed","title":"Order placed","detail":"Payment confirmed · $100.00","amount":0,"occurredAt":"2026-08-24T14:18:00.000Z"},{"id":"evt-2","type":"order_delivered","title":"Delivered","detail":"Left at front door","amount":0,"occurredAt":"2026-08-29T16:42:00.000Z"}]}}'}, {'source_run': 'return-demo', 'artifact': 'recording.har', 'json_pointer': '/log/entries/1/response/content/text', 'value': '{"message":"Price adjustment issued.","order":{"id":"1042","orderNumber":"#1042","product":{"name":"Northstar Weekender Pack","variant":"Slate · 28 L","quantity":1,"imageUrl":"/assets/weekender-pack.svg"},"purchaseDate":"2026-08-24T14:18:00.000Z","deliveryDate":"2026-08-29T16:42:00.000Z","originalAmount":100,"paymentStatus":"paid","orderStatus":"delivered","fulfillmentStatus":"delivered","cancellation":{"status":"none","reimbursementAmount":0,"completedAt":null},"priceProtection":{"status":"completed","currentPrice":70,"adjustmentAmount":30,"completedAt":"2026-09-23T00:10:56.109Z"},"refund":{"status":"none","requestedAmount":0,"completedAmount":0,"requestedAt":null,"completedAt":null},"totalReturned":30,"netCustomerBenefit":-70,"availableActions":{"requestRefund":true,"requestPriceAdjustment":false,"cancelOrder":false,"completeRefund":false},"history":[{"id":"evt-1","type":"order_placed","title":"Order placed","detail":"Payment confirmed · $100.00","amount":0,"occurredAt":"2026-08-24T14:18:00.000Z"},{"id":"evt-2","type":"order_delivered","title":"Delivered","detail":"Left at front door","amount":0,"occurredAt":"2026-08-29T16:42:00.000Z"},{"id":"evt-3","type":"price_adjustment_issued","title":"Price adjustment issued","detail":"Returned to original payment method","amount":30,"occurredAt":"2026-09-23T00:10:56.109Z"}]}}'}]}

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
