
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
  execution?: { status: string; missing_precondition?: string; next_step?: string };
}

// A reported finding; confirmation requires independent evidence.
export interface Finding {
  id?: string;
  verification_status?: string;
  verification_reason?: string;
  execution?: { status: string; missing_precondition?: string; next_step?: string };
  related_findings?: string[];
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

export default function ReportView({ report, remediation }: { report: FindingsReport; remediation: string | null }) {
  const findings = normalizeFindings(report);
  const notExecuted = report.summary.not_executed ?? findings.filter(f => f.verification_status === 'NOT_EXECUTED').length;
  const errors = Math.max(report.summary.errors, report.results.filter(r => (r.outcome === 'ERROR' || r.outcome === 'CHECK_ERROR')).length);
  return <section className="fb-report">
    <div className="report-metrics">
      {[[findings.filter(f => f.verification_status === 'CONFIRMED').length, 'Confirmed vulnerabilities'], [findings.filter(f => !f.verification_status || f.verification_status === 'NEEDS_REVIEW').length, 'Needs review'], [findings.filter(f => f.verification_status === 'NOT_REPRODUCED').length, 'Not reproduced findings'], [errors, 'Execution errors']].map(([value, label]) =>
        <div className="report-metric" key={label}><strong>{value}</strong><span>{label}</span></div>)}
    </div>
    <div className="report-metric"><strong>{notExecuted}</strong><span>Not executed</span></div>
    {notExecuted > 0 && <p className="report-notice">Incomplete coverage: some checks could not run because prerequisites were missing. Their results remain unknown.</p>}
    {errors > 0 && <p className="report-notice" role="status">Incomplete coverage: {errors} probe(s) failed. Review the execution log before drawing conclusions.</p>}
    <h2>Findings and checks <span className="report-count">{findings.length}</span></h2>
    <p className="report-muted">Severity describes potential impact. An HTTP status alone does not confirm a vulnerability.</p>
    {findings.length === 0 && <div className="report-finding"><h3>No findings reported</h3><p>{errors ? 'Some probes could not be evaluated. This is not a clean assessment.' : 'No issues were reported by these probes. This does not establish that the target is secure.'}</p></div>}
    {findings.map((finding, index) => {
      const evidence = typeof finding.evidence === 'string' ? null : finding.evidence;
      const guidance = guidanceFor(finding, remediation);
      const severity = Object.keys(SEVERITY_RANK).find(level => level.toLowerCase() === finding.severity?.trim().toLowerCase());
      return <details className="report-finding" key={finding.id || index} open={index === 0}>
        <summary className="finding-heading">
          <strong>{(finding.verification_status || 'NEEDS_REVIEW').replaceAll('_', ' ')}</strong>
          <span className={`report-severity severity-${severity?.toLowerCase() || "unassessed"}`}>Severity: {severity || "Not assessed"}</span>
          <span className="report-meta">{severity ? "AI-assessed · " : ""}{finding.cwe?.join(', ') || 'CWE not recorded'}</span>
          <h3>{finding.title}</h3>
        </summary>
        <p className="report-notice"><strong>{({CONFIRMED: 'Confirmed', NEEDS_REVIEW: 'Needs review', NOT_REPRODUCED: 'Not reproduced', NOT_EXECUTED: 'Not executed — result unknown', CHECK_ERROR: 'Check error'} as Record<string, string>)[finding.verification_status || 'NEEDS_REVIEW'] || 'Needs review'}</strong><br />{finding.verification_reason || 'No supported state verification recorded.'}</p>
        {finding.verification_status === 'NOT_EXECUTED' && <section className="report-guidance"><h4>Missing prerequisite</h4><p>{finding.execution?.missing_precondition || finding.verification_reason}</p><h4>Next step / manual test</h4><p>{finding.execution?.next_step || 'No specific next step recorded. Review the missing prerequisite before retesting.'}</p></section>}
        {!!finding.related_findings?.length && <section><h4>Related evidence</h4><p>{finding.related_findings.filter(id => id !== finding.id && findings.some(f => f.id === id)).join(', ') || 'No matching finding in this report.'}</p><p className="report-muted">Related findings do not independently confirm this scenario.</p></section>}
        {finding.url_tested && <p className="report-endpoint"><strong>Tested endpoint</strong><code>{finding.url_tested}</code></p>}
        <div className="report-comparison">
          <section><h4>Expected behavior</h4><p>{finding.expected_behavior || 'Not recorded separately. Review the evidence summary below.'}</p></section>
          <section><h4>Observed behavior</h4><p>{finding.actual_behavior || 'Not recorded separately. Review the captured responses below.'}</p></section>
        </div>
        <h4>Evidence summary</h4>
        <p className="report-summary">{evidence ? (evidence.summary || 'No summary recorded. Inspect the evidence below.') : (finding.evidence as string || 'No evidence recorded.')}</p>
        <details className="report-evidence"><summary>Inspect requests and responses ({Math.max(evidence?.requests?.length || 0, evidence?.responses?.length || 0)})</summary>
          {Array.from({length: Math.max(evidence?.requests?.length || 0, evidence?.responses?.length || 0)}, (_, i) => {
            const request = evidence?.requests?.[i];
            const response = evidence?.responses?.[i];
            return <section className="report-probe" key={i}><h4>{String(request?.label ?? `Probe ${i + 1}`)}</h4><h5>Request</h5><pre>{request ? pretty(request) : 'Request not recorded'}</pre><h5>Response</h5><pre>{response ? pretty(response) : 'Response not recorded'}</pre></section>;
          })}
          <h5>Complete evidence</h5><pre>{pretty(finding.evidence)}</pre>
          <p className="report-muted">Source: {finding.source.replaceAll('_', ' ')}{finding.script ? ` · Script: ${finding.script}` : ''}</p>
          {finding.status_code != null && <p>HTTP status: {finding.status_code}</p>}
        </details>
        <section className="report-guidance"><h4>Recommended fix</h4>{guidance ? <pre className="report-prose">{guidance}</pre> : <p className="report-muted">{remediation ? 'No unambiguous match to this finding. See the complete remediation document below.' : 'No remediation guidance recorded.'}</p>}</section>
      </details>;
    })}
    {remediation && <details className="report-finding"><summary>Complete remediation document</summary><pre className="report-prose">{remediation}</pre></details>}
    <details className="report-finding"><summary>Probe execution log ({report.results.length} scripts)</summary>
      {report.results.length === 0 && <p>No execution records available.</p>}
      {report.results.filter(r => r.outcome === 'NOT_EXECUTED').map((r, i) => <section className="report-notice" key={`gap-${i}`}><strong>{r.script} — Not executed</strong><p>{r.execution?.missing_precondition || r.verification_reason}</p><p>Next step: {r.execution?.next_step || 'Review the missing prerequisite before retesting.'}</p></section>)}
      {report.results.map((result, i) => <details className="report-probe" key={i}><summary>{result.script} · {result.outcome.replaceAll('_', ' ')} · HTTP {result.status_code ?? 'not recorded'}</summary><p>Mutation: {result.mutation_type}</p><code>{result.url_tested}</code>{result.error_message && <p className="report-notice">{result.error_message}</p>}{result.response_snippet && <pre>{result.response_snippet}</pre>}</details>)}
    </details>
  </section>;
}
