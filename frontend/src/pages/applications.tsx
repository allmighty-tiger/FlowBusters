import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiGet, apiPost } from '../services/api';
import './applications.css';

interface ModelRun { run_id: string; timestamp: string; eligible: boolean; state_map: Record<string, unknown>; }
interface Application { target_url: string; runs: ModelRun[]; cross_flow_runs: {run_id: string; source_runs: string[]; model: unknown; candidates: unknown; report_available: boolean}[]; }

export default function ApplicationsPage() {
  const navigate = useNavigate();
  const [apps, setApps] = useState<Application[]>([]);
  const [target, setTarget] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = async () => {
    setLoading(true); setError('');
    try { const data = await apiGet('/api/applications'); setApps(data.applications); }
    catch (e) { setError(e instanceof Error ? e.message : 'Cannot load application models'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const app = apps.find(a => a.target_url === target);
  const start = async () => {
    setBusy(true); setError('');
    try {
      const result = await apiPost('/api/applications/cross-flow', {
        target_url: target, run_ids: selected, same_environment_confirmed: confirmed,
      });
      sessionStorage.setItem('fb_flow_name', result.flow_name);
      navigate('/progress');
    } catch (e) { setError(e instanceof Error ? e.message : 'Cross-flow analysis failed'); setBusy(false); }
  };
  return <div className="application-page">
    <header><p className="application-eyebrow">WORKFLOW INTELLIGENCE</p><h1>Application Model</h1>
      <p>Connect recorded business flows. Explore what happens where they meet.</p></header>
    {error && <p role="alert" className="application-error">{error}</p>}
    <div className="application-toolbar"><label>Application environment
      <select value={target} disabled={busy} onChange={e => { setTarget(e.target.value); setSelected([]); setConfirmed(false); }}>
        <option value="">Select a target</option>{apps.map(a => <option key={a.target_url}>{a.target_url}</option>)}
      </select></label><button onClick={() => void load()} disabled={loading || busy}>Refresh recordings</button></div>
    {loading && <p role="status">Reading saved state maps…</p>}
    {!loading && apps.length === 0 && <div className="application-empty"><h2>No recorded models yet</h2><p>Complete an assessment with a state map first. Its flow will appear here automatically when this page loads.</p><Link to="/">Record a flow →</Link></div>}
    {app && <>
      <section className="application-intro"><h2>{app.runs.length} recorded flows</h2><p>State maps are agent interpretations. Expand a flow to inspect its model; source UI and network evidence remain in its run.</p></section>
      <div className="application-flows">{app.runs.map(run => <article key={run.run_id}>
        <div className="application-flow-heading"><label><input type="checkbox" disabled={!run.eligible || busy || (!selected.includes(run.run_id) && selected.length >= 8)} checked={selected.includes(run.run_id)} onChange={e => { setSelected(old => e.target.checked ? [...old, run.run_id] : old.filter(id => id !== run.run_id)); setConfirmed(false); }} /> <strong>{run.run_id}</strong></label><span>Inferred model</span></div>
        <p className="application-date">{run.timestamp || 'Date not recorded'} · <Link to={`/${encodeURIComponent(run.run_id)}/report`}>View report</Link></p>
        {!run.eligible && <p>Missing recording or demo artifact — unavailable for cross-flow analysis.</p>}
        <details><summary>States, transitions & business rules</summary>{Object.entries(run.state_map).map(([key, value]) => <section className="application-model-field" key={key}><h3>{key.replaceAll('_', ' ')}</h3><pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre></section>)}</details>
      </article>)}</div>
      <section className="application-launch"><div><h2>Analyze cross-flow interactions</h2><p>Select 2–8 recordings. This starts a new agent run and executes HTTP checks against the current target using the configured scope.</p>
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} /> These recordings belong to the same application environment and compatible version.</label></div>
      <button disabled={selected.length < 2 || !confirmed || busy} onClick={() => void start()}>{busy ? 'Starting…' : `Analyze ${selected.length} flows →`}</button></section>
      <p className="application-footnote">Observed data → inferred intersections → executed checks. A model relationship is not a confirmed vulnerability. Results appear in <Link to="/reports">Reports</Link>.</p>
      {app.cross_flow_runs.length > 0 && <section className="application-intro"><h2>Cross-flow analyses</h2><div className="application-flows">{app.cross_flow_runs.map(run => <article key={run.run_id}><h2>{run.run_id}</h2><p>Sources: {run.source_runs.join(', ')}</p>{run.report_available && <Link to={`/${run.run_id}/report`}>View results →</Link>}<details><summary>Application state map & hypotheses</summary><div className="application-model-field"><h3>Application state map</h3><pre>{run.model ? JSON.stringify(run.model, null, 2) : 'Model not available yet. Refresh after analysis.'}</pre><h3>Cross-flow hypotheses</h3><pre>{run.candidates ? JSON.stringify(run.candidates, null, 2) : 'No candidate artifact available.'}</pre></div></details></article>)}</div></section>}
    </>}
  </div>;
}
