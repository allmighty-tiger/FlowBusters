import '../components/report.css';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiGet } from '../services/api';

interface ReportSummary {
  flow_name: string;
  target_url: string;
  run_timestamp: string;
  modified: string;
  bugs_found: number;
  confirmed?: number;
  needs_review?: number;
  not_executed?: number;
  critical_findings?: number;
  rejected: number;
  errors: number;
  total_scripts: number;
}

export default function ReportsIndexPage() {
  const navigate = useNavigate();
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet('/api/assessments/reports')
      .then((data) => setReports(data.reports || []))
      .catch((err) => setError(err.message || 'Failed to load reports'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color: '#888', padding: '2rem' }}>Loading reports…</div>;

  return (
    <div className="fb-report-page">
      <div style={{ marginBottom: '1.5rem' }}>
        <button onClick={() => navigate('/')} style={{
          background: 'transparent', color: '#e6c15a', border: '1px solid #333',
          padding: '0.3rem 1rem', borderRadius: 4, cursor: 'pointer', fontSize: '0.85rem', marginBottom: '1rem',
        }}>
          ← New Assessment
        </button>
        <h1 style={{ fontSize: '1.5rem', margin: 0, color: '#e6c15a' }}>📜 All Reports</h1>
        <p style={{ color: '#888', marginTop: '0.25rem' }}>
          Review findings, evidence and execution coverage for each recorded assessment.
        </p>
      </div>

      {error && (
        <div style={{ background: '#2d1215', border: '1px solid #7f1d1d', borderRadius: 6, padding: '1rem', color: '#fca5a5' }}>
          {error}
        </div>
      )}

      {!error && reports && reports.length === 0 && (
        <div style={{ color: '#888', padding: '2rem', textAlign: 'center' }}>
          No reports on disk yet. Run an assessment and click "Finish recording" to generate one.
        </div>
      )}

      {!error && reports && reports.length > 0 && <div className="report-index-list">
        {reports.map(r => <article className="report-index-card" key={r.flow_name}>
          <div><p className="report-eyebrow">ASSESSMENT LOG</p><h2>{r.flow_name}</h2><p className="report-muted">{r.target_url}</p>
          <p className="report-muted">{r.run_timestamp ? `Run: ${r.run_timestamp}` : `File updated: ${r.modified}`}</p></div>
          <div className="report-index-result"><strong>{r.bugs_found} reported finding{r.bugs_found === 1 ? '' : 's'}</strong>
          {(r.critical_findings ?? 0) > 0 && <span className="report-severity severity-critical">{r.critical_findings} critical</span>}
          <p>{r.confirmed ?? 0} confirmed · {r.needs_review ?? r.bugs_found} need review{(r.not_executed ?? 0) > 0 ? ` · ${r.not_executed} not executed` : ''}</p><p>{r.total_scripts} scripts · {r.rejected} not reproduced</p>
          <p className={r.errors > 0 ? 'report-notice' : 'report-muted'}>{r.errors > 0 ? `${r.errors} execution errors · incomplete coverage` : 'No execution errors reported'}</p>
          <button className="report-button" onClick={() => navigate(`/${encodeURIComponent(r.flow_name)}/report`)}>Open report →</button></div>
        </article>)}
      </div>}
    </div>
  );
}
