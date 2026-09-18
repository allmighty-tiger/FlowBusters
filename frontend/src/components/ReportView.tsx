
// Raw per-script execution log entry
export interface ScriptResult {
  probe_origin?: string;
  script: string;
  mutation_type: string;
  outcome: string;
  status_code: number | null;
  url_tested: string;
  response_snippet: string | null;
  error_message: string | null;
  verification_reason?: string;
  finding_id?: string | null;
  execution_id?: string;
  presentation_status?: string;
  presentation_reason?: string;
  deduplicated_into?: string;
  evidence_validation_status?: string;
  evidence_error_category?: string | null;
  execution?: { status: string; missing_precondition?: string; next_step?: string };
}

// A reported finding; confirmation requires independent evidence.
export interface Finding {
  probe_origin?: string;
  coverage_status?: string;
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
  backend_requirement?: {
    id: string; product: string; asserted_on: string; effective_from: string;
    statement: string; application_mode: string; assertion_context: string;
    requirements_version?: string; retrospective?: boolean;
    application_identity?: { application_id: string; identity_version: string };
    allowed_run_ids?: string[];
  };
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
  original_hypothesis?: string;
  normalized_issue?: string;
}

interface Capture {
  sequence?: number;
  request?: Record<string, unknown>;
  response?: unknown;
  status_code?: number | null;
}

interface ResolvedInvariant { operator: string; terms: string[][]; limit: string[]; }
interface InvariantView { operands: { label: string; value: number; path: string }[]; total: number; totalLabel: string; maximum: number; maximumLabel: string; statement: string; }
interface RemediationView { cwe: string; severity: string; endpoints: string[]; issue: string; evidence: string; fixes: string[]; }
interface TraceView { label: string; role: string; summary: string; capture: Capture; }
interface PartialCoverage {
  id: string; title: string; source_finding_id?: string; candidate_id?: string;
  execution_id?: string; script?: string; status: 'PARTIAL_COVERAGE'; reason: string;
}
interface ExcludedSetupExecution {
  id?: string; title?: string; script?: string; execution_id?: string;
  status: 'EXCLUDED_SETUP_PATH'; reason: string;
}

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
  vulnerabilities?: { id: string; representative_finding_id: string; finding_ids: (string | undefined)[]; execution_ids: string[]; grouping_basis: string }[];
  normalized_run_id?: string;
  recording_metadata?: { flow_name: string; target_url: string; run_timestamp: string; source: 'validated_recording' };
  run_timestamp: string;
  target_url: string;
  flow_name: string;
  total_scripts: number;
  findings?: Finding[];
  results: ScriptResult[];
  partial_coverage?: PartialCoverage[];
  validated_ai_candidates?: number;
  unverified_candidates?: {candidate_id: string; message: string}[];
  excluded_setup_executions?: ExcludedSetupExecution[];
  summary: {
    unique_vulnerabilities?: number;
    coverage_gaps?: number;
    probe_origin_counts?: Record<string, number>;
    bugs_found: number;
    critical_findings?: number;
    rejected: number;
    errors: number;
    not_executed?: number;
    potential_critical_findings?: number;
    finding_count?: number;
    planned_executions?: number;
    execution_attempts?: number;
    completed_executions?: number;
    pending_execution?: number;
    missing_receipts?: number;
    process_errors?: number;
    evidence_contract_errors?: number;
    execution_errors?: number;
    trace_mismatches?: number;
    controls_held?: number;
    not_reproduced?: number;
    deduplicated_execution_results?: number;
    deduplicated_primary_chains?: number;
    partial_coverage?: number;
    excluded_setup_executions?: number;
    unlinked_execution_results?: number;
    execution_result_counts?: Record<string, number>;
  };
  additional_observations?: LegacyObservation[];
}

const SEVERITY_RANK: Record<string, number> = { Critical: 0, High: 1, Medium: 2, Low: 3 };
const originLabel = (origin?: string) => ({ AI_PROBE: 'AI-generated probe', BACKEND_COVERAGE: 'Backend coverage plan', LEGACY_COVERAGE_TEMPLATE: 'Legacy coverage template (author not recorded)', VERIFICATION_PROBE: 'Follow-up verification probe' }[origin || ''] || 'Generation origin not recorded');

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
  const leaves = invariant.terms.map(path => path.at(-1));
  const totalLabel = leaves.some(leaf => ['completedAmount', 'reimbursementAmount', 'totalReturned'].includes(leaf || ''))
    && !leaves.some(leaf => ['currentPrice', 'requestedAmount'].includes(leaf || ''))
    ? 'returned' : 'evaluated total';
  const maximumLabel = labelForPath(invariant.limit);
  return { operands: typed, total, totalLabel, maximum, maximumLabel,
    statement: totalLabel === 'returned'
      ? `${typed.map(x => `${money(x.value)} ${x.label}`).join(' + ')} = ${money(total)} returned against a ${money(maximum)} ${maximumLabel}.`
      : `${typed.map(x => `${money(x.value)} ${x.label}`).join(' + ')} = ${money(total)} evaluated total; allowed maximum: ${money(maximum)} ${maximumLabel}.` };
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
  return label === 'Before state' ? 'Captured the signed pre-action order state.' : label === 'After state' ? 'Confirmed the final state; no additional change.' : 'Request completed without a tracked order-state change.';
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

export function remediationFor(finding: Finding, markdown: string | null, invariant: InvariantView | null, normalizedRunId?: string): RemediationView {
  const text = guidanceFor(finding, markdown) || '';
  const field = (name: string) => text.match(new RegExp(`^- \\*\\*${name}:\\*\\*\\s*([\\s\\S]*?)(?=\\n- \\*\\*[A-Z][^:]*:\\*\\*|$(?![\\s\\S]))`, 'm'))?.[1]?.trim() || '';
  const recordedEndpoints = [...field('Endpoints').matchAll(/`([^`]+)`/g)].map(match => match[1] || '').filter(Boolean);
  const executedEndpoints = evidenceCaptures(finding).filter(c => !['GET', 'HEAD', 'OPTIONS'].includes(String(c.request?.method || '')) && endpoint(c.request?.url) !== '/api/demo/reset').map(c => `${c.request?.method} ${endpoint(c.request?.url)}`);
  const verified = finding.verification_status === 'CONFIRMED' && invariant;
  const executionKind = normalizedRunId === 'northstar-refund-v4-reanalysis'
    ? 'recorded-flow reanalysis' : 'backend execution';
  const verifiedFixes = invariant ? [
    `Before committing ${invariant.operands.map(x => x.label).join(' or ')}, atomically recompute their combined amount and reject the state transition if it would exceed ${invariant.maximumLabel}.`,
    'Perform the limit check and compensation-state update in the same transaction, with idempotency or version enforcement on both state-changing endpoints.',
  ] : [];
  return {
    cwe: field('CWE') || finding.cwe?.join(', ') || 'Not recorded',
    severity: verified ? finding.severity || 'Not assessed' : field('Severity') || finding.severity || 'Not assessed',
    endpoints: [...new Set(verified ? executedEndpoints : recordedEndpoints)],
    issue: finding.normalized_issue || (verified ? `A signed ${executionKind} demonstrated that combined compensation exceeded the permitted order amount.` : field('Issue') || finding.title),
    evidence: verified ? `${invariant.statement} Verified by execution ${finding.execution_id || 'receipt not shown'}.` : invariant?.statement || finding.verification_reason || 'No verified evidence summary available.',
    fixes: verified ? verifiedFixes : [...field('Fix').matchAll(/^\s*-\s+(.+)$/gm)].map(match => (match[1] || '').trim()).filter(Boolean),
  };
}

export function ReportRunMeta({ report }: { report: FindingsReport }) {
  const metadata = report.recording_metadata;
  const timestamp = metadata?.run_timestamp || report.run_timestamp;
  const date = timestamp ? new Date(timestamp) : null;
  return <div className="report-run-meta"><span>{metadata?.target_url || report.target_url}</span>
    <span>Flow: {metadata?.flow_name || report.normalized_run_id || report.flow_name || 'Unknown'}</span>
    <span>{metadata ? 'Recording completed: ' : 'Run: '}{date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : 'Date unavailable'}</span></div>;
}

export default function ReportView({ report, remediation }: { report: FindingsReport; remediation: string | null }) {
  const findings = normalizeFindings(report);
  const confirmed = findings.filter(f => f.verification_status === 'CONFIRMED');
  const vulnerabilities = report.vulnerabilities || confirmed.map((finding, index) => ({
    id: finding.id || `legacy-${index}`, representative_finding_id: finding.id,
    finding_ids: [finding.id], execution_ids: finding.execution_id ? [finding.execution_id] : [], grouping_basis: '',
  }));
  const uniqueVulnerabilities = report.summary.unique_vulnerabilities ?? vulnerabilities.length;
  const coverageGaps = report.summary.coverage_gaps || 0;
  const review = findings.filter(f => !f.verification_status || f.verification_status === 'NEEDS_REVIEW');
  const coverage = findings.filter(f => ['NOT_REPRODUCED', 'NOT_EXECUTED', 'CHECK_ERROR'].includes(f.verification_status || ''));
  const notExecuted = report.summary.not_executed ?? findings.filter(f => f.verification_status === 'NOT_EXECUTED').length;
  const errors = Math.max(report.summary.errors, report.results.filter(r => (r.outcome === 'ERROR' || r.outcome === 'CHECK_ERROR')).length);
  const visibleHeld = coverage.filter(f => f.verification_status === 'NOT_REPRODUCED').length;
  const findingCount = report.summary.finding_count ?? findings.length;
  const executionAttempts = report.summary.execution_attempts ?? report.results.length;
  const plannedExecutions = report.summary.planned_executions ?? report.total_scripts ?? executionAttempts;
  const completedExecutions = report.summary.completed_executions ?? executionAttempts;
  const noChecks = findings.length === 0 && executionAttempts === 0 && completedExecutions === 0;
  const pendingExecutions = report.summary.pending_execution ?? Math.max(0, plannedExecutions - completedExecutions);
  const executionErrors = report.summary.execution_errors ?? errors;
  const processErrors = report.summary.process_errors ?? errors;
  const evidenceContractErrors = report.summary.evidence_contract_errors ?? 0;
  const traceMismatches = report.summary.trace_mismatches ?? 0;
  const deduplicatedIds = findings.flatMap(f => f.deduplicated_from || []);
  const partialCoverage = report.partial_coverage || [];
  const excludedSetup = report.excluded_setup_executions || [];
  const executionResultCounts = report.summary.execution_result_counts || {};
  const statusLabel: Record<string, string> = {
    CONFIRMED: 'Confirmed finding', NEEDS_REVIEW: 'Needs review',
    NOT_REPRODUCED: 'Not reproduced', NOT_EXECUTED: 'Not executed', CHECK_ERROR: 'Check error',
  };
  const renderFinding = (finding: Finding, index: number) => {
    const chain = evidenceCaptures(finding);
    const invariant = invariantFor(finding);
    const steps = traceViews(finding);
    const transitions = transitionSummary(finding);
    const displayTitle = displayTitleFor(finding, invariant);
    const remediationView = remediationFor(finding, remediation, invariant, report.normalized_run_id);
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
        <p className="report-muted">Generation: {originLabel(finding.probe_origin)}</p>
        {invariant && <section className={`report-invariant ${status === 'CONFIRMED' ? 'invariant-confirmed' : ''}`} aria-label="Verified invariant">
          <p className="report-eyebrow">Backend-evaluated invariant</p>
          <div className="invariant-equation">{invariant.operands.map((item, i) => <span className="invariant-part" key={item.path}>{i > 0 && <span className="invariant-operator">+</span>}<strong>{money(item.value)}</strong><small>{item.label}</small></span>)}<span className="invariant-operator">=</span><span className="invariant-total"><strong>{money(invariant.total)}</strong><small>{invariant.totalLabel}</small></span></div>
          <p className="invariant-limit">Allowed maximum: <strong>{money(invariant.maximum)}</strong> {invariant.maximumLabel}</p>
        </section>}
        {(!invariant || status === 'NEEDS_REVIEW') && <p className="report-verdict"><strong>{status === 'NEEDS_REVIEW' ? 'Missing evidence' : 'Verification'}</strong><span>{finding.verification_reason || 'No supported state verification recorded.'}</span></p>}
        {finding.backend_requirement && <section className="report-expected"><h4>Backend-controlled requirement</h4><p><strong>{finding.backend_requirement.id}</strong> · {finding.backend_requirement.product}</p><p>{finding.backend_requirement.statement}</p>{!!finding.backend_requirement.allowed_run_ids?.length && <p className="report-muted">Scoped run: {finding.backend_requirement.allowed_run_ids.join(', ')}</p>}<p className="report-muted">Asserted {finding.backend_requirement.asserted_on}; applied as {finding.backend_requirement.application_mode.replaceAll('_', ' ')}. {finding.backend_requirement.assertion_context}</p></section>}
        {finding.backend_requirement?.application_identity && <p className="report-muted">Application: {finding.backend_requirement.application_identity.application_id} · identity {finding.backend_requirement.application_identity.identity_version} · requirements {finding.backend_requirement.requirements_version}</p>}
        {status === 'NEEDS_REVIEW' && (finding.original_hypothesis || finding.title)?.trim() && <section className="report-expected"><h4>Claim under review</h4><p>{finding.original_hypothesis || finding.title}</p></section>}
        {finding.verification_status === 'NOT_EXECUTED' && <section className="report-guidance"><h4>Missing prerequisite</h4><p>{finding.execution?.missing_precondition || finding.verification_reason}</p><h4>Next step / manual test</h4><p>{finding.execution?.next_step || 'No specific next step recorded.'}</p></section>}
        {!!finding.deduplicated_from?.length && <p className="report-deduplicated">Also represents deduplicated check: {finding.deduplicated_from.join(', ')}</p>}
        {finding.url_tested && <p className="report-endpoint"><strong>Tested endpoint</strong><code>{finding.url_tested}</code></p>}
        <section className="report-expected"><h4>Expected invariant</h4><p>{finding.expected_behavior || (invariant ? `${invariant.operands.map(x => x.label).join(' + ')} must not exceed ${invariant.maximumLabel} (${money(invariant.maximum)}).` : 'No executable invariant was recorded.')}</p></section>
        {(transitions.totalReturned.length > 1 || transitions.refundStatus.length > 1) && <section className="report-transitions"><h4>State transition summary</h4>{transitions.totalReturned.length > 1 && <p><code>totalReturned</code><strong>{transitions.totalReturned.join(' → ')}</strong></p>}{transitions.refundStatus.length > 1 && <p><code>refund.status</code><strong>{transitions.refundStatus.join(' → ')}</strong></p>}</section>}
        {status === 'CONFIRMED' ? <section className="report-evidence trace-primary"><h4>Complete ordered request/action trace <span>{chain.length} steps</span></h4><p className="report-muted">Execution: {finding.execution_id}</p>{trace}<p className="report-muted">Source: {finding.source.replaceAll('_', ' ')}{finding.script ? ` · Script: ${finding.script}` : ''}</p></section> : <details className="report-evidence"><summary>Inspect ordered request/action trace ({chain.length})</summary>{trace}</details>}
        {finding.coverage_status ? <section className="report-guidance"><h4>Scenario not established</h4><p>{finding.verification_reason}</p><p>Resolve the required endpoint and rerun the complete chain before assessing this control.</p></section> : status === 'NOT_REPRODUCED' ? <section className="report-control-observed"><h4>Control observed</h4><p>{finding.verification_reason || 'The signed execution completed without violating the evaluated invariant.'}</p>{finding.title?.trim() && <details><summary>Original hypothesis</summary><p>{finding.title}</p>{remediationView.issue !== finding.title && <p>{remediationView.issue}</p>}</details>}</section> : <section className="report-remediation"><h4>Recommended remediation</h4><dl>
          <div><dt>CWE</dt><dd>{remediationView.cwe}</dd></div><div><dt>Severity</dt><dd>{remediationView.severity}</dd></div>
          <div><dt>Affected endpoints</dt><dd className="endpoint-chips">{remediationView.endpoints.length ? remediationView.endpoints.map(x => <code key={x}>{x}</code>) : 'Not recorded'}</dd></div>
          <div><dt>Issue</dt><dd>{remediationView.issue}</dd></div><div><dt>{status === 'CONFIRMED' ? 'Proven facts' : 'Evidence available'}</dt><dd>{remediationView.evidence}</dd></div>
          <div><dt>Recommendation (not evidence)</dt><dd>{remediationView.fixes.length ? <ul>{remediationView.fixes.map(x => <li key={x}>{x}</li>)}</ul> : 'No specific recommendation recorded.'}</dd></div>
        </dl></section>}
      </div>
    </details>;
  };
  const primaryMessage = noChecks
    ? (plannedExecutions === 0 ? '0 probes generated / no security checks executed' : 'No security checks executed')
    : uniqueVulnerabilities
    ? `${uniqueVulnerabilities} confirmed ${uniqueVulnerabilities === 1 ? 'vulnerability requires' : 'vulnerabilities require'} action.`
    : review.length
      ? `No vulnerability is confirmed yet. ${review.length} ${review.length === 1 ? 'item requires' : 'items require'} review.`
      : 'No vulnerability was confirmed by the executed checks.';
  return <section className="fb-report">
    <section className={`report-overview ${noChecks ? 'has-review' : confirmed.length ? 'has-confirmed' : review.length ? 'has-review' : 'has-clear'}`}>
      <div><p className="report-eyebrow">Assessment outcome</p><h2>{primaryMessage}</h2>
        <p>{noChecks ? 'No executed security checks means no conclusion about vulnerabilities or held controls.' : 'Verdicts require captured state evidence. Severity alone is not confirmation.'}</p></div>
      <span className="report-risk-label">{noChecks ? 'Not assessed' : confirmed.some(f => f.severity?.toLowerCase() === 'critical') ? 'Critical action' : confirmed.length ? 'Action required' : review.length ? 'Review required' : 'No confirmed issues'}</span>
    </section>
    <div className="report-metrics" aria-label="Assessment summary">
      {[[confirmed.length, 'Confirmed findings', 'confirmed', ''], [review.length, 'Findings needing review', 'review', ''], [visibleHeld, 'Not-reproduced findings', 'held', 'security findings'], [notExecuted + errors + coverageGaps, 'Finding coverage gaps', 'gap', '']].map(([value, label, tone, detail]) =>
        <div className={`report-metric metric-${tone}`} key={label}><strong>{value}</strong><span>{label}</span>{detail && <small>{detail}</small>}</div>)}
    </div>
    <p className="report-count-context">Finding verdicts: <strong>{findingCount}</strong> normalized findings after deduplication. <strong>{uniqueVulnerabilities}</strong> unique vulnerabilities from <strong>{confirmed.length}</strong> confirmed findings. <strong>{coverageGaps}</strong> findings with incomplete scenario coverage.</p>
    <section className="report-execution-summary" aria-label="Execution summary">
      <div><p className="report-eyebrow">Backend-derived</p><h3>Execution attempts</h3></div>
      <dl>
        <div><dt>Planned</dt><dd>{plannedExecutions}</dd></div>
        <div><dt>Attempts</dt><dd>{executionAttempts}</dd></div>
        <div><dt>Authenticated terminal receipts</dt><dd>{completedExecutions}</dd></div>
        <div><dt>Pending</dt><dd>{pendingExecutions}</dd></div>
        <div><dt>Process errors</dt><dd>{processErrors}</dd></div>
        <div><dt>Evidence-contract errors</dt><dd>{evidenceContractErrors}</dd></div>
        <div><dt>Trace mismatches</dt><dd>{traceMismatches}</dd></div>
      </dl>
      <p>Primary execution results: <strong>{executionResultCounts.needs_review || 0}</strong> need review, <strong>{executionResultCounts.not_reproduced || 0}</strong> not reproduced, <strong>{executionResultCounts.confirmed || 0}</strong> confirmed, and <strong>{executionResultCounts.check_error || 0}</strong> check errors.</p>
      <p>Deduplicated primary chains: <strong>{report.summary.deduplicated_primary_chains ?? deduplicatedIds.length}</strong>. All {executionAttempts} authenticated attempts remain listed below.</p>
      {report.summary.probe_origin_counts && <p>Probe generation: {Object.entries(report.summary.probe_origin_counts).filter(([, count]) => count > 0).map(([origin, count]) => `${originLabel(origin)}: ${count} execution results`).join('; ')}.</p>}
    </section>
    {(notExecuted > 0 || errors > 0 || pendingExecutions > 0 || executionErrors > 0) && <p className="report-notice" role="status">Coverage is incomplete: {notExecuted} not executed findings, {pendingExecutions} pending probes, {processErrors} process errors, {evidenceContractErrors} evidence-contract errors, and {traceMismatches} signed-trace mismatches. A terminal receipt with invalid evidence is not a successful check.</p>}

    <section className="report-section">
      <div className="report-section-heading"><div><p className="report-eyebrow">Evidence-backed</p><h2>Confirmed vulnerabilities</h2></div><span>{uniqueVulnerabilities}</span></div>
      {vulnerabilities.length ? vulnerabilities.map(group => {
        const members = confirmed.filter(f => group.finding_ids.includes(f.id));
        return <section className="report-vulnerability-group" key={group.id} aria-label="Unique vulnerability">
          <p className="report-count-context">{group.id}: {members.length} confirmed findings · {group.execution_ids.length} authenticated receipts.</p>
          {members.slice(0, 1).map(renderFinding)}
          {members.length > 1 && <details className="report-evidence"><summary>Additional reproductions of this vulnerability ({members.length - 1})</summary><p>{group.grouping_basis} Each payload and signed trace remains separately inspectable.</p>{members.slice(1).map(renderFinding)}</details>}
        </section>;
      }) : <div className="report-empty"><strong>No confirmed vulnerabilities</strong><p>That means the current evidence did not cross the confirmation threshold—not that the target is secure.</p></div>}
    </section>

    {review.length > 0 && <section className="report-section">
      <div className="report-section-heading"><div><p className="report-eyebrow">Decision queue</p><h2>Needs review</h2></div><span>{review.length}</span></div>
      <p className="report-section-copy">Plausible risks with incomplete evidence. Reproduce them manually or rerun with the missing state.</p>
      {review.map(renderFinding)}
    </section>}

    {coverage.length > 0 && <section className="report-section">
      <div className="report-section-heading"><div><p className="report-eyebrow">Security findings</p><h2>Not reproduced</h2></div><span>{visibleHeld}</span></div>
      {!!deduplicatedIds.length && <p className="report-section-copy">Deduplicated equivalent primary chains: {deduplicatedIds.join(', ')}. Every execution attempt remains visible in the probe execution log.</p>}
      {coverage.map(renderFinding)}
    </section>}

    {report.validated_ai_candidates !== undefined && <section className="report-section">
      <h2>AI candidate coverage</h2>
      <p>{report.validated_ai_candidates} validated AI candidates. Backend coverage probes are independently planned and labelled in the execution log.</p>
      {(report.unverified_candidates || []).map(c => <p key={c.candidate_id}>{c.candidate_id}: Unverified coverage — {c.message}</p>)}
    </section>}
    {partialCoverage.length > 0 && <section className="report-section report-partial-coverage">
      <div className="report-section-heading"><div><p className="report-eyebrow">Non-verdict evidence</p><h2>Partial coverage</h2></div><span>{partialCoverage.length} supplementary scenarios</span></div>
      <p className="report-section-copy">These traces support analysis but lack an independently evaluated invariant and therefore are not security findings or execution verdicts.</p>
      {partialCoverage.map(item => <article className="report-guidance" key={item.id}><h3>{item.title}</h3><p>{item.reason}</p><p className="report-muted">Candidate: {item.candidate_id || 'not recorded'} · Primary finding: {item.source_finding_id || 'not recorded'} · Execution: {item.execution_id || 'not recorded'}</p></article>)}
    </section>}

    {excludedSetup.length > 0 && <section className="report-section report-excluded-setup">
      <div className="report-section-heading"><div><p className="report-eyebrow">Fixture execution</p><h2>Excluded setup-path scenarios</h2></div><span>{excludedSetup.length}</span></div>
      {excludedSetup.map(item => <article className="report-guidance" key={item.execution_id || item.id}><h3>Excluded setup-path scenario</h3><p>{item.reason}</p><p className="report-muted">{item.script} · Execution: {item.execution_id}</p></article>)}
    </section>}

    <details className="report-appendix"><summary>Probe execution log <span>{executionAttempts} attempts</span></summary>
      <p className="report-muted">Individual checks are supporting evidence, not additional vulnerabilities. A zero process exit does not make evidence valid.</p>
      {report.results.length === 0 && <p>No execution records available.</p>}
      <div className="report-results">{report.results.map((result, i) => <details className="report-result" key={result.execution_id || i} open={result.outcome === 'NOT_EXECUTED' || result.evidence_validation_status === 'invalid'}><summary><span className={`result-dot result-${result.outcome.toLowerCase()}`} /> <strong>{result.script}</strong><span>{result.presentation_status === 'EXCLUDED_SETUP_PATH' ? 'Excluded setup-path scenario' : result.presentation_status === 'DEDUPLICATED_PRIMARY_CHAIN' ? `Deduplicated evidence for ${result.deduplicated_into}` : result.outcome.replaceAll('_', ' ')}</span><span>HTTP {result.status_code ?? '—'}</span></summary><div><p>Generation: {originLabel(result.probe_origin)} · Execution: {result.execution_id || 'Not recorded'}</p><p>Mutation: {result.mutation_type}</p><code>{result.url_tested}</code>{result.evidence_validation_status === 'invalid' && <p className="report-notice"><strong>Evidence invalid:</strong> {result.evidence_error_category?.replaceAll('_', ' ') || 'contract or trace validation failed'}. This receipt is not a successful check.</p>}{result.presentation_reason && <p><strong>{result.presentation_reason}</strong></p>}{result.verification_reason && <p>{result.verification_reason}</p>}{result.error_message && <p className="report-notice">{result.error_message}</p>}{result.response_snippet && <pre>{result.response_snippet}</pre>}</div></details>)}</div>
    </details>
  </section>;
}
