import React from 'react';

// Raw per-script execution log entry
export interface ScriptResult {
  script: string;
  mutation_type: string;
  outcome: string;
  status_code: number | null;
  url_tested: string;
  response_snippet: string | null;
  error_message: string | null;
}

// A confirmed vulnerability, regardless of where it came from
export interface Finding {
  id?: string;
  title: string;
  source: string; // AUTH_CHECK | MUTATION_SCRIPT | ANALYSIS
  severity: string; // Critical | High | Medium | Low
  cwe: string[];
  script: string | null;
  mutation_type: string | null;
  url_tested: string;
  // Free text, or a structured object {summary, requests, responses}
  evidence: string | { summary?: string; requests?: Record<string, unknown>[]; responses?: Record<string, unknown>[]; [k: string]: unknown };
  status_code?: number | null;
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
  };
  additional_observations?: LegacyObservation[];
}

const SEVERITY_RANK: Record<string, number> = { Critical: 0, High: 1, Medium: 2, Low: 3 };

const SEVERITY_STYLE: Record<string, { border: string; bg: string; color: string; badge: string }> = {
  Critical: { border: '#7f1d1d', bg: '#2d1215', color: '#fca5a5', badge: '🚨 CRITICAL' },
  High: { border: '#7c2d12', bg: '#2b1210', color: '#fdba74', badge: '⚠️ HIGH' },
  Medium: { border: '#7a5c1e', bg: '#241c07', color: '#e6c15a', badge: 'MEDIUM' },
  Low: { border: '#166534', bg: '#14231a', color: '#86efac', badge: 'LOW' },
};

const SOURCE_LABEL: Record<string, string> = {
  AUTH_CHECK: 'Auth check',
  MUTATION_SCRIPT: 'Mutation',
  ANALYSIS: 'Analysis',
};

function outcomeBadge(outcome: string) {
  const styles: Record<string, React.CSSProperties> = {
    BUG_FOUND: { background: '#241c07', color: '#e6c15a' },
    REJECTED: { background: '#14231a', color: '#86efac' },
    ERROR: { background: '#2b2410', color: '#fcd34d' },
  };
  const icons: Record<string, string> = { BUG_FOUND: '⚡', REJECTED: '✅', ERROR: '⚠️' };
  const style = styles[outcome] || { background: '#1a1a3e', color: '#ccc' };
  return (
    <span style={{ ...style, padding: '0.2rem 0.6rem', borderRadius: 4, fontSize: '0.8rem', fontWeight: 600 }}>
      {(icons[outcome] ? icons[outcome] + ' ' : '') + outcome.replace('_', ' ')}
    </span>
  );
}

// Split remediation.md into a leading summary (text before the first "## Finding" / "## ")
// and one block per finding heading, in document order.
function parseRemediation(remediation: string): { summary: string; bugs: string[] } {
  const lines = remediation.split('\n');
  const heads: number[] = [];
  lines.forEach((l, i) => {
    if (l.startsWith('## ') && !l.startsWith('## Summary')) heads.push(i);
  });
  if (heads.length === 0) return { summary: remediation, bugs: [] };
  const summary = lines.slice(0, heads[0]).join('\n').trim();
  const bugs = heads.map((h, k) =>
    lines.slice(h, k + 1 < heads.length ? heads[k + 1] : lines.length).join('\n').trim()
  );
  return { summary, bugs };
}

// Render a markdown-ish remediation block as styled lines.
function renderRemediationBlock(text: string) {
  return text.split('\n').map((line, i) => {
    const t = line.trim();
    if (t.startsWith('## ')) return <h4 key={i} style={{ margin: '0.25rem 0 0.5rem', color: '#e2e8f0', fontSize: '0.95rem' }}>{t.replace('## ', '')}</h4>;
    if (t.startsWith('### ')) return <h5 key={i} style={{ margin: '0.5rem 0 0.4rem', color: '#cbd5e1', fontSize: '0.9rem' }}>{t.replace('### ', '')}</h5>;
    if (t === '---' || t === '') return <div key={i} style={{ height: '0.4rem' }} />;
    const bold = (s: string) => s.replace(/(\*\*.*?\*\*)/g, '<strong style="color:#e2e8f0">$1</strong>');
    if (t.startsWith('- **')) return <p key={i} style={{ margin: '0.2rem 0', color: '#94a3b8', fontSize: '0.85rem', lineHeight: 1.6 }} dangerouslySetInnerHTML={{ __html: '-' + bold(t.slice(1)) }} />;
    if (t.startsWith('- ')) return <p key={i} style={{ margin: '0.2rem 0', color: '#94a3b8', fontSize: '0.85rem', lineHeight: 1.6 }}>- {bold(t.slice(2))}</p>;
    if (t.startsWith('**')) return <p key={i} style={{ margin: '0.3rem 0', color: '#e2e8f0', fontSize: '0.85rem', fontWeight: 600 }}>{t.replace(/\*\*/g, '')}</p>;
    return <p key={i} style={{ margin: '0.2rem 0', color: '#94a3b8', fontSize: '0.85rem', lineHeight: 1.6 }}>{bold(t)}</p>;
  });
}

// Structured evidence → plain-text transcript + compact "Request / Response" pairs.
function evidenceText(ev: Finding['evidence']): string {
  if (typeof ev === 'string') return ev;
  const out: string[] = [];
  if (ev.summary) out.push(ev.summary, '');
  (ev.requests || []).forEach((req, i) => {
    out.push(`── Request: ${String(req.label ?? i + 1)} ──`);
    if (req.method) out.push(`  ${req.method} ${req.url ?? ''}`.trimEnd());
    const rest = { ...req }; delete rest.label; delete rest.method; delete rest.url;
    if (Object.keys(rest).length) out.push('  ' + JSON.stringify(rest, null, 2).split('\n').join('\n  '));
    const resp = (ev.responses || [])[i];
    if (resp) {
      out.push(`── Response: ${String(resp.label ?? i + 1)} ──`);
      if (resp.status_code != null) out.push(`  HTTP ${resp.status_code}`);
      const body = resp.response_body;
      if (body) out.push(typeof body === 'string' ? `  ${body}` : '  ' + JSON.stringify(body, null, 2).split('\n').join('\n  '));
    }
    out.push('');
  });
  return out.join('\n').trim() || JSON.stringify(ev, null, 2);
}

function isStructuredEvidence(ev: Finding['evidence']): ev is Record<string, unknown> {
  return typeof ev !== 'string';
}

// Normalize to the vulnerability-centric view. New reports have findings[];
// older ones only have results[] (+ optional additional_observations) — rebuild.
function normalizeFindings(report: FindingsReport): Finding[] {
  if (report.findings && report.findings.length >= 0) {
    return [...report.findings].sort(
      (a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9)
    );
  }
  const legacy: Finding[] = [];
  (report.additional_observations || []).forEach((o) => {
    legacy.push({
      title: o.finding,
      source: /login|auth|credential/i.test(o.finding) ? 'AUTH_CHECK' : 'ANALYSIS',
      severity: o.severity || 'High',
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
      severity: 'High',
      cwe: [],
      script: r.script,
      mutation_type: r.mutation_type,
      url_tested: r.url_tested,
      evidence: r.response_snippet || '',
      status_code: r.status_code,
    });
  });
  return legacy.sort((a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9));
}

export default function ReportView({ report, remediation }: { report: FindingsReport; remediation: string | null }) {
  const rem = remediation ? parseRemediation(remediation) : { summary: '', bugs: [] as string[] };
  const findings = normalizeFindings(report);
  const criticalCount = findings.filter(f => f.severity === 'Critical').length;

  return (
    <div>
      {/* Summary Cards */}
      <div style={{ display: 'flex', gap: '1rem', marginBottom: '2rem', flexWrap: 'wrap' }}>
        <div style={{
          flex: 1, minWidth: 130, background: report.summary.bugs_found > 0 ? '#241c07' : '#14231a',
          border: `1px solid ${report.summary.bugs_found > 0 ? '#7a5c1e' : '#166534'}`,
          borderRadius: 8, padding: '1rem', textAlign: 'center',
        }}>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: report.summary.bugs_found > 0 ? '#e6c15a' : '#86efac' }}>
            {findings.length}
          </div>
          <div style={{ fontSize: '0.8rem', color: '#888', textTransform: 'uppercase' }}>Vulnerabilities</div>
        </div>
        <div style={{
          flex: 1, minWidth: 130, background: criticalCount > 0 ? '#2d1215' : '#14231a',
          border: `1px solid ${criticalCount > 0 ? '#7f1d1d' : '#166534'}`,
          borderRadius: 8, padding: '1rem', textAlign: 'center',
        }}>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: criticalCount > 0 ? '#fca5a5' : '#86efac' }}>{criticalCount}</div>
          <div style={{ fontSize: '0.8rem', color: '#888', textTransform: 'uppercase' }}>Critical</div>
        </div>
        <div style={{
          flex: 1, minWidth: 130, background: '#14231a', border: '1px solid #166534', borderRadius: 8, padding: '1rem', textAlign: 'center',
        }}>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: '#86efac' }}>{report.summary.rejected}</div>
          <div style={{ fontSize: '0.8rem', color: '#888', textTransform: 'uppercase' }}>Properly Rejected</div>
        </div>
        <div style={{
          flex: 1, minWidth: 130, background: '#2b2410', border: '1px solid #713f12', borderRadius: 8, padding: '1rem', textAlign: 'center',
        }}>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: '#fcd34d' }}>{report.summary.errors}</div>
          <div style={{ fontSize: '0.8rem', color: '#888', textTransform: 'uppercase' }}>Errors</div>
        </div>
      </div>

      {/* Findings — the vulnerability list, Critical first */}
      <h2 style={{ fontSize: '1.1rem', marginBottom: '1rem' }}>
        Findings <span style={{ color: '#475569', fontSize: '0.9rem' }}>({findings.length})</span>
      </h2>
      {findings.length === 0 && (
        <div style={{ color: '#86efac', padding: '1rem', background: '#14231a', border: '1px solid #166534', borderRadius: 6, marginBottom: '2rem' }}>
          ✅ No vulnerabilities confirmed — all mutations were properly rejected or errored.
        </div>
      )}
      {findings.map((f, i) => {
        const s = SEVERITY_STYLE[f.severity] || { border: '#333', bg: '#111', color: '#ccc', badge: f.severity || 'NOTE' };
        const isCritical = f.severity === 'Critical';
        const ev = isStructuredEvidence(f.evidence) ? f.evidence : null;
        return (
          <details key={i} style={{
            background: isCritical ? '#1a0f14' : '#151228',
            border: `1px solid ${isCritical ? '#7f1d1d' : '#222'}`,
            borderRadius: 8, marginBottom: '0.75rem', overflow: 'hidden',
          }}>
            <summary style={{
              cursor: 'pointer', padding: '0.85rem 1.1rem', listStyle: 'none',
              display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap',
            }}>
              <span style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.color, padding: '0.15rem 0.5rem', borderRadius: 4, fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.03em' }}>
                {s.badge}
              </span>
              <span style={{ color: '#e2e8f0', fontSize: '0.95rem', fontWeight: 600 }}>{f.title}</span>
              <span style={{ color: '#64748b', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                {SOURCE_LABEL[f.source] || f.source}
              </span>
              {f.cwe && f.cwe.length > 0 && (
                <span style={{ color: '#475569', fontSize: '0.8rem' }}>{f.cwe.join(', ')}</span>
              )}
              <span style={{ marginLeft: 'auto', color: '#475569', fontSize: '0.8rem' }}>details</span>
            </summary>
            <div style={{ padding: '0 1.1rem 1.1rem', borderTop: '1px solid #222' }}>
              <div style={{ paddingTop: '0.9rem' }}>
                {f.url_tested && (
                  <p style={{ margin: '0 0 0.4rem', color: '#94a3b8', fontSize: '0.85rem' }}>
                    Endpoint: <code style={{ color: '#e2e8f0' }}>{f.url_tested}</code>
                  </p>
                )}
                {f.script && (
                  <p style={{ margin: '0 0 0.4rem', color: '#94a3b8', fontSize: '0.85rem' }}>
                    Script: <code style={{ color: '#e2e8f0' }}>{f.script}</code>
                    {f.mutation_type && <span style={{ color: '#64748b' }}> ({f.mutation_type})</span>}
                    {f.status_code != null && <span style={{ color: '#64748b' }}> — status {f.status_code}</span>}
                  </p>
                )}
                {typeof f.evidence === 'string' && f.evidence && (
                  <pre style={{
                    margin: '0.5rem 0', background: '#0b0b14', border: '1px solid #1e293b',
                    padding: '0.5rem', borderRadius: 4, fontSize: '0.75rem', color: '#94a3b8',
                    overflow: 'auto', maxHeight: 160, whiteSpace: 'pre-wrap',
                  }}>
                    {f.evidence}
                  </pre>
                )}
                {ev && (
                  <div style={{ marginTop: '0.5rem' }}>
                    {ev.summary && (
                      <p style={{ margin: '0 0 0.6rem', color: '#cbd5e1', fontSize: '0.85rem', lineHeight: 1.6 }}>
                        {ev.summary}
                      </p>
                    )}
                    {(ev.requests?.length ?? 0) > 0 && (
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem', marginBottom: '0.6rem' }}>
                        <thead>
                          <tr style={{ color: '#475569', textAlign: 'left', borderBottom: '1px solid #1e293b' }}>
                            <th style={{ padding: '0.35rem 0.5rem' }}>Probe</th>
                            <th style={{ padding: '0.35rem 0.5rem' }}>Request</th>
                            <th style={{ padding: '0.35rem 0.5rem' }}>Status</th>
                            <th style={{ padding: '0.35rem 0.5rem' }}>Response</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(ev.requests || []).map((req, i) => {
                            const resp = ev.responses?.[i];
                            const status = typeof resp?.status_code === 'number' ? resp.status_code : null;
                            const body = resp?.response_body;
                            const bodyStr = typeof body === 'string' ? body : body ? JSON.stringify(body) : '';
                            return (
                              <tr key={i} style={{ borderBottom: '1px solid #16162a', verticalAlign: 'top' }}>
                                <td style={{ padding: '0.35rem 0.5rem', color: '#94a3b8' }}>{String(req.label ?? i + 1)}</td>
                                <td style={{ padding: '0.35rem 0.5rem', color: '#e2e8f0', fontFamily: 'monospace', fontSize: '0.75rem' }}>
                                  {req.method ? `${req.method} ${req.url ?? ''}` : ''}
                                  <div style={{ color: '#475569', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                    {JSON.stringify({ ...req, label: undefined, method: undefined, url: undefined })}
                                  </div>
                                </td>
                                <td style={{ padding: '0.35rem 0.5rem', color: status == null ? '#888' : (status >= 400 ? '#86efac' : '#fca5a5') }}>
                                  {status ?? '—'}
                                </td>
                                <td style={{ padding: '0.35rem 0.5rem', color: '#94a3b8', fontFamily: 'monospace', fontSize: '0.72rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxWidth: 260 }}>
                                  {bodyStr}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                    <details>
                      <summary style={{ cursor: 'pointer', color: '#475569', fontSize: '0.78rem', marginBottom: '0.5rem' }}>
                        full request/response transcript
                      </summary>
                      <pre style={{
                        margin: '0.4rem 0 0', background: '#0b0b14', border: '1px solid #1e293b',
                        padding: '0.5rem', borderRadius: 4, fontSize: '0.72rem', color: '#94a3b8',
                        overflow: 'auto', maxHeight: 220, whiteSpace: 'pre-wrap',
                      }}>
                        {evidenceText(f.evidence)}
                      </pre>
                    </details>
                  </div>
                )}
              </div>
              {rem.bugs[i] && (
                <div style={{ marginTop: '0.75rem', borderTop: '1px solid #222', paddingTop: '0.75rem' }}>
                  <div style={{ color: '#64748b', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
                    Remediation
                  </div>
                  {renderRemediationBlock(rem.bugs[i])}
                </div>
              )}
            </div>
          </details>
        );
      })}

      {remediation && (
        <details style={{ marginBottom: '1.5rem' }}>
          <summary style={{ cursor: 'pointer', color: '#94a3b8', fontSize: '0.9rem' }}>
            Remediation summary
          </summary>
          <div style={{
            marginTop: '0.75rem', background: '#111128', border: '1px solid #222',
            borderRadius: 8, padding: '1rem 1.25rem',
          }}>
            {renderRemediationBlock(rem.summary)}
          </div>
        </details>
      )}

      {/* Per-script execution log */}
      <h2 style={{ fontSize: '1.1rem', marginBottom: '1rem' }}>
        Probe Execution Log <span style={{ color: '#475569', fontSize: '0.9rem' }}>({report.results.length} scripts)</span>
      </h2>
      <div style={{ overflowX: 'auto', marginBottom: '2rem' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #333' }}>
              <th style={{ textAlign: 'left', padding: '0.5rem', color: '#888' }}>Script</th>
              <th style={{ textAlign: 'left', padding: '0.5rem', color: '#888' }}>Type</th>
              <th style={{ textAlign: 'left', padding: '0.5rem', color: '#888' }}>Outcome</th>
              <th style={{ textAlign: 'left', padding: '0.5rem', color: '#888' }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {report.results.map((r, i) => (
              <tr key={i} style={{
                borderBottom: '1px solid #1a1a3e',
                background: r.outcome === 'BUG_FOUND' ? 'rgba(230,193,90,0.08)' : 'transparent',
              }}>
                <td style={{ padding: '0.6rem 0.5rem', color: '#e2e8f0' }}>{r.script}</td>
                <td style={{ padding: '0.6rem 0.5rem', color: '#888' }}>{r.mutation_type}</td>
                <td style={{ padding: '0.6rem 0.5rem' }}>{outcomeBadge(r.outcome)}</td>
                <td style={{ padding: '0.6rem 0.5rem', color: '#888' }}>{r.status_code || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}