import '../components/report.css';
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
    let active = true;
    setLoading(true);
    setError(null);
    setReport(null);
    if (!flowName) {
      setError('No assessment flow found. Please start an assessment first.');
      setLoading(false);
      return;
    }

    apiGet(`/api/assessments/report?flow_name=${encodeURIComponent(flowName)}`)
      .then((data) => {
        if (!active) return;
        setReport(data.findings);
        setRemediation(data.remediation);
      })
      .catch((err) => { if (active) setError(err.message || 'Failed to load report'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [flowName]);

  if (loading) return <div style={{ color: '#888', padding: '2rem' }}>Loading report...</div>;
  if (error) return (
    <div className="fb-report-page">
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
    <div className="fb-report-page">
      <header className="report-page-header">
        <button className="report-back" onClick={() => navigate('/reports')}>
          ← All reports
        </button>
        <p className="report-eyebrow">FlowBusters / Security assessment</p>
        <h1>Assessment report</h1>
        <div className="report-run-meta"><span>{report.target_url}</span><span>Flow: {report.flow_name}</span><span>{new Date(report.run_timestamp).toLocaleString()}</span></div>
      </header>

      <ReportView report={report} remediation={remediation} />
    </div>
  );
}
