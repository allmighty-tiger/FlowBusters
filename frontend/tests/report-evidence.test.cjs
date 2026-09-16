const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const filename = path.resolve(__dirname, '../src/components/ReportView.tsx');
const loaded = new Module(filename, module);
loaded.paths = module.paths;
loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2021 },
}).outputText, filename);
const { default: ReportView, evidenceCaptures, invariantFor, remediationFor } = loaded.exports;

const capture = (method, endpoint) => ({ request: { method, url: `http://localhost:3000${endpoint}`, body: null }, response: { ok: true }, status_code: 200 });
const finding = { id: 'F-1', title: 'Reimbursement overlap', source: 'MUTATION_SCRIPT', script: 'probe.py', cwe: [], evidence: {}, verification_status: 'NEEDS_REVIEW',
  verification: { before: capture('GET', '/api/order'), actions: [capture('POST', '/api/order/price-adjustment'), capture('POST', '/api/order/refund/request'), capture('POST', '/api/order/refund/complete')], after: capture('GET', '/api/order') } };

test('report renders all three POST actions and both state reads', () => {
  assert.equal(evidenceCaptures(finding).length, 5);
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { findings: [finding], results: [], summary: { errors: 0 } }, remediation: null }));
  for (const endpoint of ['price-adjustment', 'refund/request', 'refund/complete']) assert.ok(html.includes(`POST /api/order/${endpoint}`));
  assert.equal((html.match(/class="report-probe/g) || []).length, 5);
});

test('authenticated trace replaces unexecuted agent claims, including empty trace', () => {
  assert.deepEqual(evidenceCaptures({ ...finding, execution_trace: [] }), []);
  const trace = [capture('GET', '/api/order')];
  assert.deepEqual(evidenceCaptures({ ...finding, execution_trace: trace }), trace);
});

const northstarFinding = {
  ...finding,
  id: 'F-001',
  title: 'Price adjustment plus completed refund may exceed the order amount',
  severity: 'Critical',
  cwe: ['CWE-841'],
  verification_status: 'CONFIRMED',
  execution_id: 'signed-execution-1',
  verification: { resolved_invariant: { operator: 'sum_lte', terms: [['order', 'priceProtection', 'adjustmentAmount'], ['order', 'refund', 'completedAmount']], limit: ['order', 'originalAmount'] } },
  execution_trace: (() => {
    const state = (totalReturned, refundStatus, completedAmount, adjustmentAmount) => ({ order: { originalAmount: 100, totalReturned, priceProtection: { adjustmentAmount }, refund: { status: refundStatus, completedAmount } } });
    return [
      { ...capture('POST', '/api/demo/reset'), response: state(0, 'none', 0, 0) },
      { ...capture('GET', '/api/order'), response: state(0, 'none', 0, 0) },
      { ...capture('POST', '/api/order/price-adjustment'), response: state(30, 'none', 0, 30) },
      { ...capture('POST', '/api/order/refund/request'), response: state(30, 'pending', 0, 30) },
      { ...capture('POST', '/api/order/refund/complete'), response: state(130, 'completed', 100, 30) },
      { ...capture('GET', '/api/order'), response: state(130, 'completed', 100, 30) },
    ];
  })(),
};

test('confirmed invariant is primary and renders operands, result, limit, and complete trace', () => {
  const invariant = invariantFor(northstarFinding);
  assert.equal(invariant.total, 130);
  assert.equal(invariant.maximum, 100);
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { findings: [northstarFinding], results: [], summary: { errors: 0 } }, remediation: null }));
  for (const text of ['$30', 'price adjustment', '$100', 'completed refund', '$130', 'Allowed maximum:']) assert.ok(html.includes(text), text);
  assert.ok(html.includes('Complete ordered request/action trace'));
  assert.equal((html.match(/class="report-probe/g) || []).length, 6);
  assert.ok(!html.includes('Not recorded separately'));
  assert.ok(html.includes('1 confirmed vulnerability requires action.'));
  assert.ok(html.includes('Price adjustment ($30) plus completed refund ($100) exceeds the original order amount ($100).'));
  assert.ok(!html.includes('may exceed'));
  for (const label of ['Test setup', 'Before state', 'Action 1', 'Action 2', 'Action 3', 'After state']) assert.ok(html.includes(label), label);
  assert.ok(html.includes('Reset the authorized test fixture; this is setup, not an attack action.'));
  assert.equal((html.match(/View full response/g) || []).length, 6);
  assert.ok(html.includes('totalReturned</code><strong>$0 → $30 → $130'));
  assert.ok(html.includes('refund.status</code><strong>none → pending → completed'));
});

test('backend-controlled requirement shows its assertion date and retrospective scope', () => {
  const withRequirement = { ...northstarFinding, backend_requirement: {
    id: 'NSM-RETURN-CAP-2026-09-16', product: 'Northstar Market',
    asserted_on: '2026-09-16', effective_from: '2026-09-16',
    statement: 'Combined compensation must not exceed originalAmount.',
    allowed_run_ids: ['northstar-refund-v4-reanalysis'],
    application_mode: 'retrospective_evidence_evaluation',
    assertion_context: 'Asserted after this execution; it did not exist as a recorded requirement before the run.',
  } };
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: {
    findings: [withRequirement], results: [], summary: { errors: 0 },
  }, remediation: null }));
  for (const text of ['Backend-controlled requirement', 'NSM-RETURN-CAP-2026-09-16',
    'Northstar Market', 'Asserted 2026-09-16', 'retrospective evidence evaluation',
    'Scoped run: northstar-refund-v4-reanalysis',
    'did not exist as a recorded requirement before the run']) assert.ok(html.includes(text), text);
});

test('refund reanalysis uses accurate execution, pre-action, severity, and remediation labels', () => {
  const state = (cancelled, refunded) => ({ order: { originalAmount: 100,
    cancellation: { reimbursementAmount: cancelled }, refund: { completedAmount: refunded } } });
  const refundFinding = { ...northstarFinding, severity: 'High',
    title: 'Cancel then complete may reimburse twice',
    backend_requirement: { id: 'NSM-RETURN-CAP-2026-09-16', product: 'Northstar Market',
      asserted_on: '2026-09-16', effective_from: '2026-09-16',
      statement: 'Combined compensation must not exceed originalAmount.',
      allowed_run_ids: ['northstar-refund-v4-reanalysis'],
      application_mode: 'retrospective_evidence_evaluation', assertion_context: 'Asserted after this run.' },
    verification: { resolved_invariant: { operator: 'sum_lte',
      terms: [['order', 'cancellation', 'reimbursementAmount'], ['order', 'refund', 'completedAmount']],
      limit: ['order', 'originalAmount'] } },
    execution_trace: [
      { ...capture('POST', '/api/demo/reset'), response: state(0, 0) },
      { ...capture('POST', '/api/order/refund/request'), response: state(0, 0) },
      { ...capture('GET', '/api/order'), response: state(0, 0) },
      { ...capture('POST', '/api/order/cancel'), response: state(100, 0) },
      { ...capture('POST', '/api/order/refund/complete'), response: state(100, 100) },
      { ...capture('GET', '/api/order'), response: state(100, 100) },
    ] };
  const markdown = '## Finding F-001\n\n- **Severity:** High (suspected)\n- **Issue:** Draft issue.\n- **Fix:** Draft fix.\n';
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: {
    normalized_run_id: 'northstar-refund-v4-reanalysis', findings: [refundFinding], results: [],
    summary: { errors: 0 },
  }, remediation: markdown }));
  for (const text of ['signed recorded-flow reanalysis', 'signed pre-action order state',
    'Proven facts', 'Recommendation (not evidence)', 'atomically recompute their combined amount',
    'Perform the limit check and compensation-state update in the same transaction']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('signed cross-flow execution'));
  assert.ok(!html.includes('baseline order state'));
  assert.ok(!html.includes('High (suspected)'));
});

test('price invariant labels current price as a price operand, not returned money', () => {
  const state = { order: { originalAmount: 100, totalReturned: 30,
    priceProtection: { adjustmentAmount: 30, currentPrice: 70 } } };
  const priceFinding = {
    ...finding,
    id: 'F-007',
    title: 'Price adjustment endpoint may trust a client-supplied adjustment amount',
    verification_status: 'NEEDS_REVIEW',
    verification_reason: 'The signed request supplied adjustmentAmount=999999, but sum_lte does not express client-input trust.',
    verification: { resolved_invariant: { operator: 'sum_lte',
      terms: [['order', 'priceProtection', 'adjustmentAmount'], ['order', 'priceProtection', 'currentPrice']],
      limit: ['order', 'originalAmount'] } },
    execution_trace: [
      { ...capture('POST', '/api/demo/reset'), response: state },
      { ...capture('GET', '/api/order'), response: state },
      { ...capture('POST', '/api/order/price-adjustment'), response: state },
      { ...capture('GET', '/api/order'), response: state },
    ],
  };
  const invariant = invariantFor(priceFinding);
  assert.equal(invariant.total, 100);
  assert.equal(invariant.totalLabel, 'evaluated total');
  assert.equal(invariant.statement, '$30 price adjustment + $70 current price = $100 evaluated total; allowed maximum: $100 original order amount.');
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: {
    findings: [priceFinding], results: [], summary: { errors: 0, finding_count: 1 },
  }, remediation: null }));
  for (const text of ['$70', 'current price', 'evaluated total', 'Claim under review',
    'Price adjustment endpoint may trust a client-supplied adjustment amount',
    'Inspect ordered request/action trace (4)']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('$100</strong><small>returned'));
});

test('deduplicated receipt stays visible as evidence for its canonical finding', () => {
  const result = { script: '03_interleave_concurrent_race.py', execution_id: 'receipt-3',
    mutation_type: 'STATE_INTERLEAVING', outcome: 'CONFIRMED', status_code: 200,
    url_tested: '/api/order/refund/complete', response_snippet: null, error_message: null,
    presentation_status: 'DEDUPLICATED_PRIMARY_CHAIN', deduplicated_into: 'F-001',
    presentation_reason: 'Deduplicated into F-001: same state-changing action chain and invariant.',
    verification_reason: 'The receipt proves the same invariant violation; it does not independently establish concurrent execution.' };
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: {
    findings: [northstarFinding], results: [result], summary: { errors: 0, finding_count: 1,
      execution_attempts: 1, completed_executions: 1, execution_result_counts: { confirmed: 1 } },
  }, remediation: null }));
  assert.ok(html.includes('Deduplicated evidence for F-001'));
  assert.ok(html.includes('does not independently establish concurrent execution'));
});

test('summary keeps not-reproduced findings separate from execution attempts', () => {
  const held = { ...finding, id: 'F-002', verification_status: 'NOT_REPRODUCED', deduplicated_from: ['F-003'] };
  const heldCancel = { ...finding, id: 'F-004', title: 'Cancel then refund may double-pay', verification_status: 'NOT_REPRODUCED', verification_reason: 'The refund request was rejected and total returned stayed within the allowed maximum.', execution_trace: [capture('POST', '/api/demo/reset'), capture('GET', '/api/order'), capture('POST', '/api/order/cancel'), { ...capture('POST', '/api/order/refund/request'), status_code: 409 }, capture('GET', '/api/order')] };
  const held2 = { ...finding, id: 'F-005', verification_status: 'NOT_REPRODUCED' };
  const results = ['one', 'two', 'three', 'four'].map(script => ({ script, mutation_type: 'STATE_INTERLEAVING', outcome: 'NOT_REPRODUCED', status_code: 409, url_tested: '/api/order', response_snippet: null, error_message: null }));
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { findings: [northstarFinding, held, heldCancel, held2], results, summary: { errors: 0 } }, remediation: null }));
  assert.ok(html.includes('3</strong><span>Not-reproduced findings'));
  assert.ok(html.includes('Deduplicated primary chains: <strong>1'));
  assert.ok(html.includes('0</strong><span>Findings needing review'));
  assert.ok(html.includes('Not reproduced'));
  assert.ok(html.includes('F-003'));
  assert.ok(html.includes('Order cancellation followed by refund request — control held.'));
  assert.ok(!html.includes('may double-pay</h3>'));
});

test('backend-derived summary separates findings, attempts, and deduplicated results', () => {
  const held = { ...finding, id: 'F-001', verification_status: 'NOT_REPRODUCED', deduplicated_from: ['F-002', 'F-003'] };
  const reviews = [4, 5, 6, 7].map(id => ({ ...finding, id: `F-00${id}`, verification_status: 'NEEDS_REVIEW' }));
  const results = [
    ...['one', 'two', 'three'].map(script => ({ script, mutation_type: 'STATE_INTERLEAVING', outcome: 'NOT_REPRODUCED', status_code: 409, url_tested: '/api/order', response_snippet: null, error_message: null })),
    ...['four', 'five', 'six', 'seven'].map(script => ({ script, mutation_type: 'STATE_INTERLEAVING', outcome: 'NEEDS_REVIEW', status_code: 200, url_tested: '/api/order', response_snippet: null, error_message: null })),
  ];
  const summary = { errors: 0, finding_count: 5, controls_held: 3,
    deduplicated_execution_results: 2, planned_executions: 7,
    execution_attempts: 7, completed_executions: 7, pending_execution: 0,
    execution_errors: 0, trace_mismatches: 0 };
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { findings: [held, ...reviews], results, summary }, remediation: null }));
  assert.ok(html.includes('Finding verdicts: <strong>5</strong> normalized findings'));
  assert.ok(html.includes('1</strong><span>Not-reproduced findings'));
  assert.ok(html.includes('Deduplicated primary chains: <strong>2'));
  for (const pair of ['Planned</dt><dd>7', 'Attempts</dt><dd>7', 'Authenticated terminal receipts</dt><dd>7', 'Pending</dt><dd>0', 'Process errors</dt><dd>0', 'Evidence-contract errors</dt><dd>0', 'Trace mismatches</dt><dd>0']) assert.ok(html.includes(pair), pair);
  assert.ok(html.includes('Probe execution log <span>7 attempts'));
  assert.ok(!html.includes('Pending</dt><dd>7'));
});

test('atomic model renders findings, partial coverage, setup exclusion, and all receipts separately', () => {
  const held = [2, 4, 5, 6].map(id => ({ ...finding, id: `F-00${id}`, verification_status: 'NOT_REPRODUCED' }));
  const reviewFinding = { ...finding, id: 'F-001', verification_status: 'NEEDS_REVIEW' };
  const results = [
    { script: '01.py', execution_id: 'e1', mutation_type: 'STATE_INTERLEAVING', outcome: 'NEEDS_REVIEW', status_code: 200, url_tested: '/api/order', response_snippet: null, error_message: null },
    { script: '02.py', execution_id: 'e2', mutation_type: 'STATE_INTERLEAVING', outcome: 'NOT_REPRODUCED', status_code: 409, url_tested: '/api/order', response_snippet: null, error_message: null },
    { script: '03.py', execution_id: 'e3', mutation_type: 'REPLAY_ATTACK', outcome: 'NEEDS_REVIEW', presentation_status: 'EXCLUDED_SETUP_PATH', presentation_reason: 'Excluded setup-path scenario: fixture setup.', status_code: 200, url_tested: '/api/demo/reset', response_snippet: null, error_message: null },
    ...[4, 5, 6].map(id => ({ script: `0${id}.py`, execution_id: `e${id}`, mutation_type: 'DATA_TAMPER', outcome: 'NOT_REPRODUCED', status_code: 200, url_tested: '/api/order', response_snippet: null, error_message: null })),
  ];
  const report = {
    findings: [reviewFinding, ...held], results,
    partial_coverage: [
      { id: 'F-002:cancel', title: 'Cancel after price adjustment', status: 'PARTIAL_COVERAGE', reason: 'Missing independent invariant.', source_finding_id: 'F-002', candidate_id: 'XF-002', execution_id: 'e2' },
      { id: 'F-004:refund', title: 'Refund amount override', status: 'PARTIAL_COVERAGE', reason: 'Missing independent invariant.', source_finding_id: 'F-004', candidate_id: 'XF-004', execution_id: 'e4' },
    ],
    excluded_setup_executions: [{ id: 'F-003', script: '03.py', execution_id: 'e3', status: 'EXCLUDED_SETUP_PATH', reason: '/api/demo/reset is authorized fixture setup.' }],
    summary: { errors: 0, finding_count: 5, planned_executions: 6, execution_attempts: 6, completed_executions: 6, pending_execution: 0, execution_errors: 0, trace_mismatches: 0, deduplicated_primary_chains: 0, execution_result_counts: { needs_review: 2, not_reproduced: 4, confirmed: 0, check_error: 0 } },
  };
  const html = renderToStaticMarkup(React.createElement(ReportView, { report, remediation: null }));
  assert.ok(html.includes('Finding verdicts: <strong>5</strong> normalized findings'));
  assert.ok(html.includes('Partial coverage'));
  assert.ok(html.includes('2 supplementary scenarios'));
  assert.ok(html.includes('Excluded setup-path scenarios'));
  assert.ok(html.includes('Primary execution results: <strong>2</strong> need review, <strong>4</strong> not reproduced'));
  assert.ok(html.includes('Probe execution log <span>6 attempts'));
  assert.equal((html.match(/Excluded setup-path scenario/g) || []).length >= 2, true);
});

test('execution summary separates process, evidence-contract, and trace failures', () => {
  const result = { script: 'invalid.py', execution_id: 'bad-1', mutation_type: 'STATE_INTERLEAVING',
    outcome: 'NEEDS_REVIEW', status_code: 200, url_tested: '/api/order', response_snippet: null,
    error_message: null, evidence_validation_status: 'invalid', evidence_error_category: 'evidence_contract_error',
    verification_reason: 'Probe output contract error: invariant missing' };
  const report = { findings: [], results: [result], summary: { errors: 0, finding_count: 0,
    planned_executions: 2, execution_attempts: 1, completed_executions: 1, pending_execution: 1,
    process_errors: 0, evidence_contract_errors: 1, trace_mismatches: 0, execution_errors: 1 } };
  const html = renderToStaticMarkup(React.createElement(ReportView, { report, remediation: null }));
  for (const text of ['Authenticated terminal receipts', 'Process errors', 'Evidence-contract errors',
    'Trace mismatches', 'A terminal receipt with invalid evidence is not a successful check',
    'A zero process exit does not make evidence valid', 'Evidence invalid:']) assert.ok(html.includes(text), text);
});

test('held control hides absent roles and speculative active remediation', () => {
  const held = { ...finding, id: 'F-004', title: 'Cancel then refund may double-pay', verification_status: 'NOT_REPRODUCED', verification_reason: 'The refund request returned HTTP 409 and the invariant remained within bounds.', execution_trace: [capture('GET', '/api/order'), capture('POST', '/api/order/cancel'), { ...capture('POST', '/api/order/refund/request'), status_code: 409 }, capture('GET', '/api/order')] };
  const markdown = '## Finding F-004\n\n- **Issue:** A draft speculative issue.\n- **Fix:**\n  - Change the refund workflow.\n';
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { findings: [held], results: [{ outcome: 'NOT_REPRODUCED' }], summary: { errors: 0 } }, remediation: markdown }));
  assert.ok(html.includes('Control observed'));
  assert.ok(html.includes('Original hypothesis'));
  assert.ok(html.includes('Order cancellation followed by refund request — control held.'));
  assert.ok(!html.includes('Role:'));
  assert.ok(!html.includes('Recommended remediation'));
  assert.ok(!html.includes('Change the refund workflow.'));
});

test('markdown remediation is rendered as structured fields without stale speculation', () => {
  const markdown = '## Finding F-001\n\n- **CWE:** CWE-841: Workflow\n- **Severity:** Critical\n- **Endpoints:** `POST /api/a`, `POST /api/b`\n- **Issue:** No single recording ever tested this speculative sequence.\n- **Evidence:** Draft evidence.\n- **Fix:**\n  - Enforce one remedy atomically.\n  - Cap returned value.\n';
  const view = remediationFor(northstarFinding, markdown, invariantFor(northstarFinding));
  assert.deepEqual(view.endpoints, ['POST /api/order/price-adjustment', 'POST /api/order/refund/request', 'POST /api/order/refund/complete']);
  assert.ok(view.fixes[0].includes('atomically recompute their combined amount'));
  assert.ok(view.fixes[1].includes('same transaction'));
  assert.ok(!view.issue.includes('No single recording'));
  assert.ok(view.evidence.includes('signed-execution-1'));
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { findings: [northstarFinding], results: [], summary: { errors: 0 } }, remediation: markdown }));
  for (const label of ['CWE', 'Severity', 'Affected endpoints', 'Issue', 'Proven facts', 'Recommendation (not evidence)']) assert.ok(html.includes(label));
  assert.ok(!html.includes('Analyst remediation draft'));
  assert.ok(!html.includes('**CWE:**'));
  assert.equal((html.match(/class="endpoint-chips"/g) || []).length, 1);
});
