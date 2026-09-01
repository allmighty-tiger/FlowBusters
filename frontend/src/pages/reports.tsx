import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiGet } from '../services/api';

interface ReportSummary {
  flow_name: string;
  target_url: string;
  run_timestamp: string;
  modified: string;
  bugs_found: number;
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
    <div>
      <div style={{ marginBottom: '1.5rem' }}>
        <button onClick={() => navigate('/')} style={{
          background: 'transparent', color: '#e6c15a', border: '1px solid #333',
          padding: '0.3rem 1rem', borderRadius: 4, cursor: 'pointer', fontSize: '0.85rem', marginBottom: '1rem',
        }}>
          ← New Assessment
        </button>
        <h1 style={{ fontSize: '1.5rem', margin: 0, color: '#e6c15a' }}>📜 All Reports</h1>
        <p style={{ color: '#888', marginTop: '0.25rem' }}>
          Every completed assessment on disk. Click View for the full report.
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

      {!error && reports && reports.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
          <thead>
            <tr style={{ color: '#888', fontSize: '0.8rem', textAlign: 'left', borderBottom: '1px solid #222' }}>
              <th style={{ padding: '0.5rem 0.75rem' }}>Flow</th>
              <th style={{ padding: '0.5rem 0.75rem' }}>Vulns</th>
              <th style={{ padding: '0.5rem 0.75rem' }}>Rejected</th>
              <th style={{ padding: '0.5rem 0.75rem' }}>Errors</th>
              <th style={{ padding: '0.5rem 0.75rem' }}>Scripts</th>
              <th style={{ padding: '0.5rem 0.75rem' }}>Finished</th>
              <th style={{ padding: '0.5rem 0.75rem', textAlign: 'right' }}>View</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => (
              <tr
                key={r.flow_name}
                style={{ borderBottom: '1px solid #1e1e2e' }}
              >
                <td style={{ padding: '0.6rem 0.75rem' }}>
                  <div style={{ color: '#e6c15a', fontWeight: 600 }}>{r.flow_name}</div>
                  <div style={{ color: '#475569', fontSize: '0.78rem' }}>{r.target_url}</div>
                </td>
                <td style={{ padding: '0.6rem 0.75rem' }}>
                  <span style={{ color: r.bugs_found > 0 ? '#e6c15a' : '#86efac', fontWeight: 600 }}>
                    {r.bugs_found}
                  </span>
                  {(r.critical_findings ?? 0) > 0 && (
                    <span style={{ color: '#fca5a5', fontWeight: 700, marginLeft: '0.5rem' }}>
                      🚨 {r.critical_findings} critical
                    </span>
                  )}
                </td>
                <td style={{ padding: '0.6rem 0.75rem', color: '#86efac' }}>{r.rejected}</td>
                <td style={{ padding: '0.6rem 0.75rem', color: r.errors > 0 ? '#fcd34d' : '#86efac' }}>{r.errors}</td>
                <td style={{ padding: '0.6rem 0.75rem', color: '#86efac' }}>{r.total_scripts}</td>
                <td style={{ padding: '0.6rem 0.75rem', color: '#86efac', fontSize: '0.82rem' }}>{r.modified}</td>
                <td style={{ padding: '0.6rem 0.75rem', textAlign: 'right' }}>
                  <button
                    onClick={() => navigate(`/${r.flow_name}/report`)}
                    style={{
                      background: '#e6c15a', color: '#14101f', border: 'none',
                      padding: '0.35rem 1rem', borderRadius: 4, cursor: 'pointer',
                      fontWeight: 700, fontSize: '0.85rem',
                    }}
                  >
                    View →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}