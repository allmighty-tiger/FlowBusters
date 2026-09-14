
// Raw per-script execution log entry
export interface ScriptResult {
  script: string;
  mutation_type: string;
  outcome: string;
  status_code: number | null;
  url_tested: string;
  response_snippet: string | null;
  error_message: string | null;
  verification_reason?: string;
  finding_id?: string | null;
  execution?: { status: string; missing_precondition?: string; next_step?: string };
}

// A reported finding; confirmation requires independent evidence.
export interface Finding {
  verification?: { before?: Capture; actions?: Capture[]; action?: Capture; after?: Capture; resolved_invariant?: ResolvedInvariant };
  execution_trace?: Capture[];
  execution_id?: string;
  triggered_by?: string;
  id?: string;
  verification_status?: string;
  verification_reason?: string;
  execution?: { status: string; missing_precondition?: string; next_step?: string };
  related_findings?: string[];
  deduplicated_from?: string[];
  title: string;
  source: string; // AUTH_CHECK | MUTATION_SCRIPT | ANALYSIS
  severity?: string; // Missing values are not assessed
  cwe: string[];
  script: string | null;
  mutation_type: string | null;
  url_tested: string;
  // Free text, or a structured object {summary, requests, responses}
  evidence: string | { summary?: string; requests?: Record<string, unknown>[]; responses?: Record<string, unknown>[]; [k: string]: unknown };
  status_code?: number | null;
  expected_behavior?: string;
  actual_behavior?: string;
  remediation?: string;
}

interface Capture {
  sequence?: number;
  request?: Record<string, unknown>;
  response?: unknown;
  status_code?: number | null;
}

interface ResolvedInvariant { operator: string; terms: string[][]; limit: string[]; }
interface InvariantView { operands: { label: string; value: number; path: string }[]; total: number; maximum: number; maximumLabel: string; statement: string; }
interface RemediationView { cwe: string; severity: string; endpoints: string[]; issue: string; evidence: string; fixes: string[]; }
interface TraceView { label: string; role: string; summary: string; capture: Capture; }

export function evidenceCaptures(finding: Finding): Capture[] {
  // A backend trace, even an empty one, takes precedence over agent claims.
  if (Array.isArray(finding.execution_trace)) return finding.execution_trace;
  const v = finding.verification;
  if (v) return [v.before, ...(Array.isArray(v.actions) ? v.actions : [v.action]), v.after]
    .filter((capture): capture is Capture => !!capture && typeof capture === 'object');
  const evidence = typeof finding.evidence === 'object' ? finding.evidence : null;
  return Array.from({ length: Math.max(evidence?.requests?.length || 0, evidence?.responses?.length || 0) }, (_, i) =>
    ({ request: evidence?.requests?.[i], response: evidence?.responses?.[i] }));
}

// Legacy fields from reports generated before the vulnerability-centric schema
interface LegacyObservation {
  finding: string;
  cwe: string[];
  severity: string;
  evidence: string;
}

export interface FindingsReport {
  run_timestamp: string;
  target_url: string;
  flow_name: string;
  total_scripts: number;
  findings?: Finding[];
  results: ScriptResult[];
  summary: {
    bugs_found: number;
    critical_findings?: number;
    rejected: number;
    errors: number;
    not_executed?: number;
    potential_critical_findings?: number;
  };
  additional_observations?: LegacyObservation[];
}

const SEVERITY_RANK: Record<string, number> = { Critical: 0, High: 1, Medium: 2, Low: 3 };

function normalizeFindings(report: FindingsReport): Finding[] {
  if (Array.isArray(report.findings)) {
    return [...report.findings].sort(
      (a, b) => (SEVERITY_RANK[a.severity || ""] ?? 9) - (SEVERITY_RANK[b.severity || ""] ?? 9)
    );
  }
  const legacy: Finding[] = [];
  (report.additional_observations || []).forEach((o) => {
    legacy.push({
      title: o.finding,
      source: /login|auth|credential/i.test(o.finding) ? 'AUTH_CHECK' : 'ANALYSIS',
      severity: o.severity || 'Not assessed',
      cwe: o.cwe || [],
      script: null,
      mutation_type: null,
      url_tested: '',
      evidence: o.evidence || '',
    });
  });
  report.results.filter(r => r.outcome === 'BUG_FOUND').forEach((r) => {
    legacy.push({
      title: r.script,
      source: 'MUTATION_SCRIPT',
      severity: 'Not assessed',
      cwe: [],
      script: r.script,
      mutation_type: r.mutation_type,
      url_tested: r.url_tested,
      evidence: r.response_snippet || '',
      status_code: r.status_code,
    });
  });
  return legacy.sort((a, b) => (SEVERITY_RANK[a.severity || ""] ?? 9) - (SEVERITY_RANK[b.severity || ""] ?? 9));
}


const pretty = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const titleKey = (value: string) => value.toLowerCase().replace(/^finding\s+\d+[:.\s-]*|^\d+[.)]\s*/g, '').replace(/[^a-z0-9]+/g, '');
const pathLabels: Record<string, string> = { adjustmentAmount: 'price adjustment', completedAmount: 'completed refund', reimbursementAmount: 'cancellation reimbursement', requestedAmount: 'requested refund', originalAmount: 'original order amount', totalReturned: 'total returned' };
const labelForPath = (path: string[]) => pathLabels[path.at(-1) || ''] || (path.at(-1) || 'value').replace(/([A-Z])/g, ' $1').toLowerCase();
const readPath = (root: unknown, path: string[]) => path.reduce<unknown>((value, key) => value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>)[key] : undefined, root);
const money = (value: number) => `$${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;

export function invariantFor(finding: Finding): InvariantView | null {
  const invariant = finding.verification?.resolved_invariant;
  const after = [...evidenceCaptures(finding)].reverse().find(c => c.request?.method === 'GET');
  if (!invariant || invariant.operator !== 'sum_lte' || !after) return null;
  const operands = invariant.terms.map(path => ({ path: path.join('.'), label: labelForPath(path), value: readPath(after.response, path) }));
  const maximum = readPath(after.response, invariant.limit);
  if (operands.some(x => typeof x.value !== 'number' || !Number.isFinite(x.value)) || typeof maximum !== 'number' || !Number.isFinite(maximum)) return null;
  const typed = operands as { path: string; label: string; value: number }[];
  const total = typed.reduce((sum, item) => sum + item.value, 0);
  return { operands: typed, total, maximum, maximumLabel: labelForPath(invariant.limit),
    statement: `${typed.map(x => `${money(x.value)} ${x.label}`).join(' + ')} = ${money(total)} returned against a ${money(maximum)} ${labelForPath(invariant.limit)}.` };
}

const responseBody = (value: unknown) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 1 && 'actual' in value ? (value as Record<string, unknown>).actual : value;
const orderFrom = (capture?: Capture) => {
  const body = responseBody(capture?.response);
  return body && typeof body === 'object' && !Array.isArray(body) && (body as Record<string, unknown>).order && typeof (body as Record<string, unknown>).order === 'object'
    ? (body as Record<string, unknown>).order as Record<string, unknown> : null;
};
const endpoint = (url: unknown) => { try { return new URL(String(url)).pathname; } catch { return String(url || ''); } };
const nested = (value: Record<string, unknown> | null, ...keys: string[]) => keys.reduce<unknown>((current, key) => current && typeof current === 'object' ? (current as Record<string, unknown>)[key] : undefined, value);
const conciseChange = (previous: Capture | undefined, current: Capture, label: string) => {
  if (label === 'Test setup') return 'Reset the authorized test fixture; this is setup, not an attack action.';
  const before = orderFrom(previous), after = orderFrom(current);
  if (!after) return label === 'After state' ? 'Captured the final signed response.' : 'Captured signed HTTP evidence.';
  const fields: [string, string[], boolean][] = [
    ['totalReturned', ['totalReturned'], true], ['refund.status', ['refund', 'status'], false],
    ['refund.completedAmount', ['refund', 'completedAmount'], true], ['price adjustment', ['priceProtection', 'adjustmentAmount'], true],
    ['cancellation reimbursement', ['cancellation', 'reimbursementAmount'], true],
  ];
  const changes = fields.flatMap(([name, path, currency]) => {
    const from = nested(before, ...path), to = nested(after, ...path);
    if (from === undefined || to === undefined || from === to) return [];
    const format = (v: unknown) => currency && typeof v === 'number' ? money(v) : String(v);
    return [`${name}: ${format(from)} → ${format(to)}`];
  });
  if (changes.length) return changes.join(' · ');
  return label === 'Before state' ? 'Captured the baseline order state.' : label === 'After state' ? 'Confirmed the final state; no additional change.' : 'Request completed without a tracked order-state change.';
};

export function traceViews(finding: Finding): TraceView[] {
  const chain = evidenceCaptures(finding);
  const getIndexes = chain.map((c, i) => c.request?.method === 'GET' ? i : -1).filter(i => i >= 0);
  let action = 0;
  return chain.map((capture, i) => {
    const method = String(capture.request?.method || '');
    const path = endpoint(capture.request?.url);
    let label: string;
    if (method === 'POST' && path === '/api/demo/reset') label = 'Test setup';
    else if (method === 'GET' && i === getIndexes[0]) label = 'Before state';
    else if (method === 'GET' && i === getIndexes.at(-1)) label = 'After state';
    else if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) label = `Action ${++action}`;
    else label = 'Verification read';
    const recordedRole = capture.request?.role;
    return { label, role: typeof recordedRole === 'string' && recordedRole ? recordedRole : 'Not recorded', summary: conciseChange(chain[i - 1], capture, label), capture };
  });
}

export function transitionSummary(finding: Finding) {
  const states = evidenceCaptures(finding).map(orderFrom).filter((x): x is Record<string, unknown> => !!x);
  const series = (path: string[], format: (value: unknown) => string) => states.map(state => nested(state, ...path)).filter(value => value !== undefined).filter((value, i, all) => i === 0 || value !== all[i - 1]).map(format);
  return {
    totalReturned: series(['totalReturned'], value => typeof value === 'number' ? money(value) : String(value)),
    refundStatus: series(['refund', 'status'], String),
  };
}

const confirmedTitle = (finding: Finding, invariant: InvariantView | null) => invariant && finding.verification_status === 'CONFIRMED'
  ? `${invariant.operands.map((x, i) => `${i === 0 ? x.label.replace(/^./, c => c.toUpperCase()) : x.label} (${money(x.value)})`).join(' plus ')} exceeds the ${invariant.maximumLabel} (${money(invariant.maximum)}).`
  : finding.title;

const actionNames: Record<string, string> = { '/api/order/price-adjustment': 'price adjustment', '/api/order/refund/request': 'refund request', '/api/order/refund/complete': 'refund completion', '/api/order/cancel': 'order cancellation' };
const displayTitleFor = (finding: Finding, invariant: InvariantView | null) => {
  if (finding.verification_status === 'CONFIRMED') return confirmedTitle(finding, invariant);
  if (finding.verification_status !== 'NOT_REPRODUCED') return finding.title;
  const actions = evidenceCaptures(finding).filter(c => !['GET', 'HEAD', 'OPTIONS'].includes(String(c.request?.method || '')) && endpoint(c.request?.url) !== '/api/demo/reset').map(c => actionNames[endpoint(c.request?.url)] || endpoint(c.request?.url));
  if (!actions.length) return 'Tested scenario — control held.';
  const sequence = actions.join(' followed by ');
  return `${sequence.charAt(0).toUpperCase()}${sequence.slice(1)} — control held.`;
};

// Never attach guidance by array position: severity sorting changes that order.
export function guidanceFor(finding: Finding, markdown: string | null): string | undefined {
  if (finding.remediation) return finding.remediation;
  if (!markdown) return;
  const sections = [...markdown.matchAll(/^#{2,3} (.+)\r?\n([\s\S]*?)(?=^#{2,3} |$(?![\s\S]))/gm)];
  const matches = sections.filter(([, heading]) => {
    const text = heading || '';
    const ids = text.match(/\bF-\d+\b/gi) || [];
    return (finding.id && ids.some(id => id.toLowerCase() === finding.id!.toLowerCase())) || titleKey(text) === titleKey(finding.title);
  });
  return matches.length === 1 ? matches[0]?.[2]?.trim() : undefined;
}

export function remediationFor(finding: Finding, markdown: string | null, invariant: InvariantView | null): RemediationView {
  const text = guidanceFor(finding, markdown) || '';
  const field = (name: string) => text.match(new RegExp(`^- \\*\\*${name}:\\*\\*\\s*([\\s\\S]*?)(?=\\n- \\*\\*[A-Z][^:]*:\\*\\*|$(?![\\s\\S]))`, 'm'))?.[1]?.trim() || '';
  const recordedEndpoints = [...field('Endpoints').matchAll(/`([^`]+)`/g)].map(match => match[1] || '').filter(Boolean);
  const executedEndpoints = evidenceCaptures(finding).filter(c => !['GET', 'HEAD', 'OPTIONS'].includes(String(c.request?.method || '')) && endpoint(c.request?.url) !== '/api/demo/reset').map(c => `${c.request?.method} ${endpoint(c.request?.url)}`);
  const verified = finding.verification_status === 'CONFIRMED' && invariant;
  return {
    cwe: field('CWE') || finding.cwe?.join(', ') || 'Not recorded',
    severity: field('Severity') || finding.severity || 'Not assessed',
    endpoints: [...new Set(verified ? executedEndpoints : recordedEndpoints)],
    issue: verified ? `A signed cross-flow execution demonstrated that combined remedies exceeded the permitted order amount.` : field('Issue') || finding.title,
    evidence: verified ? `${invariant.statement} Verified by execution ${finding.execution_id || 'receipt not shown'}.` : invariant?.statement || finding.verification_reason || 'No verified evidence summary available.',
    fixes: [...field('Fix').matchAll(/^\s*-\s+(.+)$/gm)].map(match => (match[1] || '').trim()).filter(Boolean),
  };
}

export default function ReportView({ report, remediation }: { report: FindingsReport; remediation: string | null }) {
  const findings = normalizeFindings(report);
  const confirmed = findings.filter(f => f.verification_status === 'CONFIRMED');
  const review = findings.filter(f => !f.verification_status || f.verification_status === 'NEEDS_REVIEW');
  const coverage = findings.filter(f => ['NOT_REPRODUCED', 'NOT_EXECUTED', 'CHECK_ERROR'].includes(f.verification_status || ''));
  const notExecuted = report.summary.not_executed ?? findings.filter(f => f.verification_status === 'NOT_EXECUTED').length;
  const errors = Math.max(report.summary.errors, report.results.filter(r => (r.outcome === 'ERROR' || r.outcome === 'CHECK_ERROR')).length);
  const heldChecks = report.results.filter(r => r.outcome === 'NOT_REPRODUCED' || r.outcome === 'REJECTED').length;
  const visibleHeld = coverage.filter(f => f.verification_status === 'NOT_REPRODUCED').length;
  const deduplicatedHeld = Math.max(0, heldChecks - visibleHeld);
  const deduplicatedIds = findings.flatMap(f => f.deduplicated_from || []);
  const statusLabel: Record<string, string> = {
    CONFIRMED: 'Confirmed vulnerability', NEEDS_REVIEW: 'Needs review',
    NOT_REPRODUCED: 'Not reproduced', NOT_EXECUTED: 'Not executed', CHECK_ERROR: 'Check error',
  };
  const renderFinding = (finding: Finding, index: number) => {
    const chain = evidenceCaptures(finding);
    const invariant = invariantFor(finding);
    const steps = traceViews(finding);
    const transitions = transitionSummary(finding);
    const displayTitle = displayTitleFor(finding, invariant);
    const remediationView = remediationFor(finding, remediation, invariant);
    const severity = Object.keys(SEVERITY_RANK).find(level => level.toLowerCase() === finding.severity?.trim().toLowerCase());
    const status = finding.verification_status || 'NEEDS_REVIEW';
    const trace = steps.map(({ capture, label, role, summary }, i) => <section className={`report-probe trace-${label.toLowerCase().replaceAll(' ', '-')}`} key={i}>
      <div className="trace-step-heading"><span className="trace-step-number">{capture.sequence ?? i + 1}</span><div><strong>{label}</strong><code>{`${String(capture.request?.method || '')} ${endpoint(capture.request?.url)}`}</code></div><span className="trace-status">HTTP {capture.status_code ?? '—'}</span></div>
      {role !== 'Not recorded' && <p className="trace-role">Role: <strong>{role}</strong></p>}
      <p className="trace-change">{summary}</p>
      <details className="trace-response"><summary>View full response</summary><pre>{capture.response !== undefined ? pretty(capture.response) : 'Response not recorded'}</pre></details>
    </section>);
    return <details className={`report-finding status-${status.toLowerCase()}`} key={finding.id || index} open={status === 'CONFIRMED' || index === 0}>
      <summary className="finding-heading">
        <span className="finding-chevron" aria-hidden="true">›</span>
        <span className={`report-status status-${status.toLowerCase()}`}>{statusLabel[status] || status.replaceAll('_', ' ')}</span>
        <span className={`report-severity severity-${severity?.toLowerCase() || 'unassessed'}`}>{severity || 'Not assessed'}</span>
        <span className="report-meta">{finding.id ? `${finding.id} · ` : ''}{finding.cwe?.join(', ') || 'CWE not recorded'}</span>
        <h3>{displayTitle}</h3>
      </summary>
      <div className="finding-body">
        {invariant && <section className={`report-invariant ${status === 'CONFIRMED' ? 'invariant-confirmed' : ''}`} aria-label="Verified invariant">
          <p className="report-eyebrow">Backend-evaluated invariant</p>
          <div className="invariant-equation">{invariant.operands.map((item, i) => <span className="invariant-part" key={item.path}>{i > 0 && <span className="invariant-operator">+</span>}<strong>{money(item.value)}</strong><small>{item.label}</small></span>)}<span className="invariant-operator">=</span><span className="invariant-total"><strong>{money(invariant.total)}</strong><small>returned</small></span></div>
          <p className="invariant-limit">Allowed maximum: <strong>{money(invariant.maximum)}</strong> {invariant.maximumLabel}</p>
        </section>}
        {(!invariant || status === 'NEEDS_REVIEW') && <p className="report-verdict"><strong>{status === 'NEEDS_REVIEW' ? 'Missing evidence' : 'Verification'}</strong><span>{finding.verification_reason || 'No supported state verification recorded.'}</span></p>}
        {finding.verification_status === 'NOT_EXECUTED' && <section className="report-guidance"><h4>Missing prerequisite</h4><p>{finding.execution?.missing_precondition || finding.verification_reason}</p><h4>Next step / manual test</h4><p>{finding.execution?.next_step || 'No specific next step recorded.'}</p></section>}
        {!!finding.deduplicated_from?.length && <p className="report-deduplicated">Also represents deduplicated check: {finding.deduplicated_from.join(', ')}</p>}
        {finding.url_tested && <p className="report-endpoint"><strong>Tested endpoint</strong><code>{finding.url_tested}</code></p>}
        <section className="report-expected"><h4>Expected invariant</h4><p>{finding.expected_behavior || (invariant ? `${invariant.operands.map(x => x.label).join(' + ')} must not exceed ${invariant.maximumLabel} (${money(invariant.maximum)}).` : 'No executable invariant was recorded.')}</p></section>
        {(transitions.totalReturned.length > 1 || transitions.refundStatus.length > 1) && <section className="report-transitions"><h4>State transition summary</h4>{transitions.totalReturned.length > 1 && <p><code>totalReturned</code><strong>{transitions.totalReturned.join(' → ')}</strong></p>}{transitions.refundStatus.length > 1 && <p><code>refund.status</code><strong>{transitions.refundStatus.join(' → ')}</strong></p>}</section>}
        {status === 'CONFIRMED' ? <section className="report-evidence trace-primary"><h4>Complete ordered request/action trace <span>{chain.length} steps</span></h4><p className="report-muted">Execution: {finding.execution_id}</p>{trace}<p className="report-muted">Source: {finding.source.replaceAll('_', ' ')}{finding.script ? ` · Script: ${finding.script}` : ''}</p></section> : <details className="report-evidence"><summary>Inspect ordered request/action trace ({chain.length})</summary>{trace}</details>}
        {status === 'NOT_REPRODUCED' ? <section className="report-control-observed"><h4>Control observed</h4><p>{finding.verification_reason || 'The signed execution completed without violating the evaluated invariant.'}</p><details><summary>Original hypothesis</summary><p>{finding.title}</p>{remediationView.issue !== finding.title && <p>{remediationView.issue}</p>}</details></section> : <section className="report-remediation"><h4>Recommended remediation</h4><dl>
          <div><dt>CWE</dt><dd>{remediationView.cwe}</dd></div><div><dt>Severity</dt><dd>{remediationView.severity}</dd></div>
          <div><dt>Affected endpoints</dt><dd className="endpoint-chips">{remediationView.endpoints.length ? remediationView.endpoints.map(x => <code key={x}>{x}</code>) : 'Not recorded'}</dd></div>
          <div><dt>Issue</dt><dd>{remediationView.issue}</dd></div><div><dt>Evidence</dt><dd>{remediationView.evidence}</dd></div>
          <div><dt>Fix</dt><dd>{remediationView.fixes.length ? <ul>{remediationView.fixes.map(x => <li key={x}>{x}</li>)}</ul> : 'No specific fix recorded.'}</dd></div>
        </dl></section>}
      </div>
    </details>;
  };
  const primaryMessage = confirmed.length
    ? `${confirmed.length} confirmed ${confirmed.length === 1 ? 'vulnerability requires' : 'vulnerabilities require'} action.`
    : review.length
      ? `No vulnerability is confirmed yet. ${review.length} ${review.length === 1 ? 'item requires' : 'items require'} review.`
      : 'No vulnerability was confirmed by the executed checks.';
  return <section className="fb-report">
    <section className={`report-overview ${confirmed.length ? 'has-confirmed' : review.length ? 'has-review' : 'has-clear'}`}>
      <div><p className="report-eyebrow">Assessment outcome</p><h2>{primaryMessage}</h2>
        <p>Verdicts require captured state evidence. Severity alone is not confirmation.</p></div>
      <span className="report-risk-label">{confirmed.some(f => f.severity?.toLowerCase() === 'critical') ? 'Critical action' : confirmed.length ? 'Action required' : review.length ? 'Review required' : 'No confirmed issues'}</span>
    </section>
    <div className="report-metrics" aria-label="Assessment summary">
      {[[confirmed.length, 'Confirmed', 'confirmed', ''], [review.length, 'Needs review', 'review', ''], [heldChecks, 'Controls held', 'held', deduplicatedHeld ? `${visibleHeld} scenarios + ${deduplicatedHeld} deduplicated` : ''], [notExecuted + errors, 'Coverage gaps', 'gap', '']].map(([value, label, tone, detail]) =>
        <div className={`report-metric metric-${tone}`} key={label}><strong>{value}</strong><span>{label}</span>{detail && <small>{detail}</small>}</div>)}
    </div>
    {(notExecuted > 0 || errors > 0) && <p className="report-notice" role="status">Coverage is incomplete: {notExecuted} not executed, {errors} execution errors. These are unknown results, not a clean bill of health.</p>}

    <section className="report-section">
      <div className="report-section-heading"><div><p className="report-eyebrow">Evidence-backed</p><h2>Confirmed vulnerabilities</h2></div><span>{confirmed.length}</span></div>
      {confirmed.length ? confirmed.map(renderFinding) : <div className="report-empty"><strong>No confirmed vulnerabilities</strong><p>That means the current evidence did not cross the confirmation threshold—not that the target is secure.</p></div>}
    </section>

    {review.length > 0 && <section className="report-section">
      <div className="report-section-heading"><div><p className="report-eyebrow">Decision queue</p><h2>Needs review</h2></div><span>{review.length}</span></div>
      <p className="report-section-copy">Plausible risks with incomplete evidence. Reproduce them manually or rerun with the missing state.</p>
      {review.map(renderFinding)}
    </section>}

    {coverage.length > 0 && <section className="report-section">
      <div className="report-section-heading"><div><p className="report-eyebrow">Coverage</p><h2>Controls validated</h2></div><span>{coverage.length}{deduplicatedHeld ? ` + ${deduplicatedHeld} deduplicated` : ''}</span></div>
      {!!deduplicatedIds.length && <p className="report-section-copy">Deduplicated checks represented below: {deduplicatedIds.join(', ')}. Every execution remains visible in the probe execution log.</p>}
      {coverage.map(renderFinding)}
    </section>}

    <details className="report-appendix"><summary>Probe execution log <span>{report.results.length} attempts</span></summary>
      <p className="report-muted">Individual checks are supporting evidence, not additional vulnerabilities.</p>
      {report.results.length === 0 && <p>No execution records available.</p>}
      <div className="report-results">{report.results.map((result, i) => <details className="report-result" key={i} open={result.outcome === 'NOT_EXECUTED'}><summary><span className={`result-dot result-${result.outcome.toLowerCase()}`} /> <strong>{result.script}</strong><span>{result.outcome.replaceAll('_', ' ')}</span><span>HTTP {result.status_code ?? '—'}</span></summary><div><p>Mutation: {result.mutation_type}</p><code>{result.url_tested}</code>{result.verification_reason && <p>{result.verification_reason}</p>}{result.error_message && <p className="report-notice">{result.error_message}</p>}{result.response_snippet && <pre>{result.response_snippet}</pre>}</div></details>)}</div>
    </details>
  </section>;
}
