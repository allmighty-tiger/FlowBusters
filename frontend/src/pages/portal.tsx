import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiPost } from '../services/api';
import banner from '../assets/flowbusters-banner.svg';

export default function PortalPage() {
  const navigate = useNavigate();
  const [appName, setAppName] = useState('');
  const [targetUrl, setTargetUrl] = useState('');
  const [flowName, setFlowName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!appName.trim()) { setError('Application name is required'); return; }
    if (!targetUrl.trim()) { setError('Target URL is required'); return; }

    const resolvedFlowName = flowName.trim() || `assessment-${Date.now()}`;

    setLoading(true);
    try {
      await apiPost('/api/assessments', {
        app_name: appName.trim(),
        target_url: targetUrl.trim(),
        flow_name: resolvedFlowName,
      });
      // Store flow name for progress/report pages
      sessionStorage.setItem('fb_flow_name', resolvedFlowName);
      navigate('/progress');
    } catch (err: any) {
      setError(err.message || 'Failed to start assessment');
      setLoading(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '0.75rem 1rem', background: '#1a1a3e', border: '1px solid #333',
    borderRadius: 6, color: '#e2e8f0', fontSize: '1rem', outline: 'none', boxSizing: 'border-box',
  };

  const labelStyle: React.CSSProperties = {
    display: 'block', marginBottom: '0.4rem', fontSize: '0.85rem', color: '#888', textTransform: 'uppercase', letterSpacing: '0.05em',
  };

  return (
    <div>
      <div style={{ marginBottom: '1.5rem', maxWidth: 500, marginLeft: 'auto', marginRight: 'auto' }}>
        <img src={banner} alt="FlowBusters — hunt business logic flaws before attackers do"
          style={{ width: '100%', height: 'auto', borderRadius: 10, display: 'block' }} />
      </div>

      <form onSubmit={handleSubmit} style={{ maxWidth: 500, marginLeft: 'auto', marginRight: 'auto' }}>
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Application Name</label>
          <input
            style={inputStyle}
            value={appName}
            onChange={(e) => setAppName(e.target.value)}
            placeholder="e.g. FlowShop"
          />
        </div>

        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Target URL</label>
          <input
            style={inputStyle}
            value={targetUrl}
            onChange={(e) => setTargetUrl(e.target.value)}
            placeholder="https://example.com/workflow"
          />
        </div>

        <div style={{ marginBottom: '1.5rem' }}>
          <label style={labelStyle}>Flow Name <span style={{ color: '#555', textTransform: 'none' }}>(optional, kebab-case)</span></label>
          <input
            style={inputStyle}
            value={flowName}
            onChange={(e) => setFlowName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))}
            placeholder="e.g. happy-path (auto-generated if empty)"
          />
        </div>

        {error && (
          <div style={{
            background: '#2d1215', border: '1px solid #7f1d1d', borderRadius: 6,
            padding: '0.75rem 1rem', marginBottom: '1rem', color: '#fca5a5', fontSize: '0.9rem',
          }}>
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          style={{
            background: loading ? '#4a4a3a' : 'linear-gradient(180deg, #e6c15a, #c99b2e)',
            color: '#1a1408', border: 'none', padding: '0.75rem 2rem',
            borderRadius: 6, fontSize: '1rem', fontWeight: 700,
            cursor: loading ? 'not-allowed' : 'pointer', width: '100%',
          }}
        >
          {loading ? 'Starting...' : '⚔️ Start Assessment'}
        </button>
      </form>
    </div>
  );
}
