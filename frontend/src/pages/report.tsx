import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { apiGet } from '../services/api';
import ReportView, { FindingsReport } from '../components/ReportView';

export default function ReportPage() {
  const navigate = useNavigate();
  const { flowName: flowNameParam } = useParams();
  const [report, setReport] = useState<FindingsReport | null>(null);
  const [remediation, setRemediation] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Flow comes from the URL (/flowname/report); fall back to the sessionStorage
  // value so the old "View report" path still works.
  const flowName = flowNameParam || sessionStorage.getItem('fb_flow_name') || '';

  useEffect(() => {
    if (!flowName) {
      setError('No assessment flow found. Please start an assessment first.');
      setLoading(false);
      return;
    }

    apiGet(`/api/assessments/report?flow_name=${encodeURIComponent(flowName)}`)
      .then((data) => {
        setReport(data.findings);
        setRemediation(data.remediation);
      })
      .catch((err) => setError(err.message || 'Failed to load report'))
      .finally(() => setLoading(false));
  }, [flowName]);

  if (loading) return <div style={{ color: '#888', padding: '2rem' }}>Loading report...</div>;
  if (error) return (
    <div>
      <div style={{ background: '#2d1215', border: '1px solid #7f1d1d', borderRadius: 6, padding: '1rem', color: '#fca5a5', marginBottom: '1rem' }}>
        {error}
      </div>
      <button onClick={() => navigate('/reports')} style={{ background: '#2563eb', color: '#fff', border: 'none', padding: '0.5rem 1.5rem', borderRadius: 4, cursor: 'pointer' }}>
        ← All reports
      </button>
    </div>
  );
  if (!report) return null;

  return (
    <div>
      <div style={{ marginBottom: '1.5rem' }}>
        <button onClick={() => navigate('/reports')} style={{
          background: 'transparent', color: '#e6c15a', border: '1px solid #333',
          padding: '0.3rem 1rem', borderRadius: 4, cursor: 'pointer', fontSize: '0.85rem', marginBottom: '1rem',
        }}>
          ← All reports
        </button>
        <h1 style={{ fontSize: '1.5rem', margin: 0, color: '#e6c15a' }}>⚔️ Assessment Report</h1>
        <p style={{ color: '#888', marginTop: '0.25rem' }}>
          {report.target_url} &middot; Flow: <code style={{ color: '#e6c15a' }}>{report.flow_name}</code> &middot; {report.run_timestamp}
        </p>
      </div>

      <ReportView report={report} remediation={remediation} />
    </div>
  );
}