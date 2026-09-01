import { useNavigate } from 'react-router-dom';

const AGENTS = [
  { icon: '🧭', name: 'Captain', role: 'Orchestrates the raid and owns the decision to call a bug.' },
  { icon: '🎥', name: 'Recorder', role: 'Drives the browser and captures the workflow as a HAR.' },
  { icon: '🔬', name: 'Analyst', role: 'Maps every endpoint and the state that guards it.' },
  { icon: '💣', name: 'Saboteur', role: 'Writes the mutation scripts that break the rules.' },
  { icon: '🔍', name: 'Prober', role: 'Fires the mutations and reads the verdict off the response.' },
];

const PHASES = ['🎥 RECORD', '🔬 ANALYZE', '💣 MUTATE', '🔍 PROBE', '📜 REPORT'];

export default function AboutPage() {
  const navigate = useNavigate();
  return (
    <div>
      <button onClick={() => navigate('/')} style={{
        background: 'transparent', color: '#e6c15a', border: '1px solid #333',
        padding: '0.3rem 1rem', borderRadius: 4, cursor: 'pointer', fontSize: '0.85rem', marginBottom: '1rem',
      }}>← Back</button>

      <h1 style={{ fontSize: '1.5rem', margin: '0 0 1rem', color: '#e6c15a' }}>⚔️ About FLOWBUSTERS</h1>
      <p style={{ color: '#94a3b8', lineHeight: 1.7, fontSize: '0.95rem', marginBottom: '1.5rem' }}>
        FlowBusters is a security portal for <strong style={{ color: '#e2e8f0' }}>stateful web workflows</strong>.
        It watches you complete a real flow in the browser, learns every step, then attacks the
        rules that should have protected it — skipping steps, swapping roles, tampering with data,
        replaying requests, and browsing pages it should never reach. Whatever the app can't
        stop becomes a bug, with a fix attached.
      </p>

      <h2 style={{ fontSize: '1.05rem', color: '#e2e8f0', marginBottom: '0.6rem' }}>The Five Phases</h2>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1.75rem' }}>
        {PHASES.map(p => (
          <span key={p} style={{
            background: '#1a1a3e', border: '1px solid #333', borderRadius: 4,
            padding: '0.35rem 0.7rem', fontSize: '0.82rem', color: '#e6c15a', fontWeight: 600,
          }}>{p}</span>
        ))}
      </div>

      <h2 style={{ fontSize: '1.05rem', color: '#e2e8f0', marginBottom: '0.6rem' }}>The Crew</h2>
      <div style={{ display: 'grid', gap: '0.6rem' }}>
        {AGENTS.map(a => (
          <div key={a.name} style={{
            background: '#12122a', border: '1px solid #2a2a4a', borderRadius: 6,
            padding: '0.7rem 0.9rem', display: 'flex', gap: '0.75rem', alignItems: 'flex-start',
          }}>
            <span style={{ fontSize: '1.3rem' }}>{a.icon}</span>
            <div>
              <div style={{ color: '#e6c15a', fontWeight: 700, fontSize: '0.95rem' }}>{a.name}</div>
              <div style={{ color: '#94a3b8', fontSize: '0.85rem', lineHeight: 1.5 }}>{a.role}</div>
            </div>
          </div>
        ))}
      </div>

      <p style={{ color: '#64748b', fontSize: '0.78rem', marginTop: '1.75rem', fontStyle: 'italic' }}>
        "The lock was only painted." — a warning to every app that trusts the browser.
      </p>
    </div>
  );
}