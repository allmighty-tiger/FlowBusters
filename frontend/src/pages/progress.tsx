import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

const RECORDED_STEPS = [
  { key: 'record', label: 'Record', detail: 'Capturing the workflow' },
  { key: 'analyze', label: 'Analyze', detail: 'Mapping state & endpoints' },
  { key: 'mutate', label: 'Mutate', detail: 'Generating attack scripts' },
  { key: 'probe', label: 'Probe', detail: 'Running adversarial tests' },
  { key: 'report', label: 'Report', detail: 'Compiling findings' },
];

const CROSS_FLOW_STEPS = [
  { key: 'record', label: 'Source flows', detail: 'Loading selected flow evidence' },
  { key: 'analyze', label: 'Application model', detail: 'Authenticating evidence and mapping state' },
  { key: 'mutate', label: 'Conflict scenarios', detail: 'Identifying cross-flow interactions' },
  { key: 'probe', label: 'Probe execution', detail: 'Running backend-owned executable probes' },
  { key: 'report', label: 'Verification', detail: 'Verifying signed evidence and final verdicts' },
];

type RunMode = 'RECORDED_FLOW' | 'CROSS_FLOW';
type ProgressMessage = {
  phase: string;
  message: string;
  done: boolean;
  error?: string | null;
  run_mode: RunMode;
  technical_detail?: string | null;
};

type StepStatus = 'pending' | 'active' | 'done' | 'error';

interface ProgressState {
  [key: string]: StepStatus;
}

const PHASE_TO_STEP: Record<string, string> = {
  record: 'record',
  analyze: 'analyze',
  mutate: 'mutate',
  probe: 'probe',
  report: 'report',
  complete: 'report',
};

export default function ProgressPage() {
  const navigate = useNavigate();
  const [progress, setProgress] = useState<ProgressState>(
    Object.fromEntries(RECORDED_STEPS.map(s => [s.key, 'pending' as StepStatus]))
  );
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<ProgressMessage[]>([]);
  const [runMode, setRunMode] = useState<RunMode>('RECORDED_FLOW');
  const [isComplete, setIsComplete] = useState(false);
  const [recordingDone, setRecordingDone] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const flowName = sessionStorage.getItem('fb_flow_name') || '';

  useEffect(() => {
    const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
    const es = new EventSource(`${API_BASE}/api/assessments/stream`);
    eventSourceRef.current = es;

    es.addEventListener('progress', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as ProgressMessage;
        const msg = data.message || '';
        const eventMode: RunMode = data.run_mode === 'CROSS_FLOW' ? 'CROSS_FLOW' : 'RECORDED_FLOW';
        const eventSteps = eventMode === 'CROSS_FLOW' ? CROSS_FLOW_STEPS : RECORDED_STEPS;
        setRunMode(eventMode);
        setEvents(prev => [...prev, { ...data, message: msg, run_mode: eventMode }]);

        const phase = data.phase;
        const isDone = data.done;

        setProgress(prev => {
          const next = { ...prev };
          const stepKey = PHASE_TO_STEP[phase];

          if (phase === 'complete') {
            eventSteps.forEach(s => { next[s.key] = 'done'; });
            setIsComplete(true);
          } else if (phase === 'failed') {
            eventSteps.forEach(s => {
              if (next[s.key] === 'active') next[s.key] = 'error';
            });
            setError(data.error || 'Assessment failed');
            setIsComplete(true);
          } else if (stepKey) {
            const idx = eventSteps.findIndex(s => s.key === stepKey);
            // Mark earlier steps done once a later phase begins (i < idx). The
            // current step (i === idx) becomes 'active' or 'done' per isDone —
            // but a step that's ALREADY 'done' must never regress back to
            // 'active' (e.g. a late "browser closed" record event arriving after
            // Record already finished). Use >= 0 (not > 0) so a done event for a
            // step (e.g. Probe done when findings.json lands) completes that
            // step itself too, not just the ones before it.
            for (let i = 0; i <= idx; i++) {
              const s = eventSteps[i];
              if (!s || next[s.key] === 'error') continue;
              if (i < idx) {
                next[s.key] = 'done';
              } else if (isDone || next[s.key] !== 'done') {
                next[s.key] = isDone ? 'done' : 'active';
              }
            }
            // When a phase's terminal artifact lands (done=true), the NEXT phase
            // is about to run. Activate it now (only if still pending) so there's
            // no gap where the next step sits grey while the crew quietly writes
            // its files — e.g. Analyze done (state_map.json) → Mutate goes active
            // immediately, instead of waiting for the whole mutations/ dir to land.
            if (isDone) {
              const nxt = eventSteps[idx + 1];
              if (nxt && next[nxt.key] === 'pending') next[nxt.key] = 'active';
            }
          }

          return next;
        });
      } catch {
        // ignore parse errors
      }
    });

    es.addEventListener('done', () => {
      setTimeout(() => es.close(), 1000);
    });

    es.onerror = () => {
      // SSE reconnects automatically; only close on 'done'
    };

    return () => { es.close(); };
  }, []);

  const steps = runMode === 'CROSS_FLOW' ? CROSS_FLOW_STEPS : RECORDED_STEPS;
  const recordingActive = runMode === 'RECORDED_FLOW' && progress['record'] === 'active' && !recordingDone;
  const latestEvent = events[events.length - 1];

  return (
    <div>
      <div style={{ marginBottom: '2.5rem' }}>
        <h1 style={{ fontSize: '1.4rem', margin: 0, fontWeight: 600, color: '#e2e8f0' }}>
          ⚔️ {runMode === 'CROSS_FLOW' ? 'Cross-flow assessment' : 'Assessment'}
        </h1>
        {flowName && (
          <p style={{ color: '#64748b', marginBottom: 0, fontSize: '0.9rem' }}>
            Flow: <span style={{ color: '#94a3b8' }}>{flowName}</span>
          </p>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {steps.map((step, i) => {
          const status: StepStatus = progress[step.key] ?? 'pending';
          const isLast = i === steps.length - 1;
          return (
            <div key={step.key} style={{ display: 'flex', gap: '1rem' }}>
              {/* indicator + connector */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <Indicator status={status} />
                {!isLast && (
                  <div style={{
                    width: 2, flex: 1, minHeight: 28,
                    background: status === 'done' ? '#334155' : '#1e293b',
                    marginTop: 2,
                  }} />
                )}
              </div>
              {/* label */}
              <div style={{ paddingBottom: isLast ? 0 : '1.5rem', paddingTop: '0.1rem' }}>
                <div style={{
                  color: status === 'pending' ? '#475569' : '#e2e8f0',
                  fontWeight: status === 'active' ? 600 : 500,
                  fontSize: '1rem',
                }}>
                  {step.label}
                  {status === 'active' && (
                    <span style={{
                      marginLeft: '0.6rem', fontSize: '0.8rem',
                      color: '#e6c15a', fontWeight: 400,
                    }}>
                      in progress
                    </span>
                  )}
                </div>
                <div style={{ color: '#475569', fontSize: '0.82rem' }}>{step.detail}</div>
              </div>
            </div>
          );
        })}
      </div>

      {latestEvent && (
        <div
          aria-live="polite"
          style={{
            marginTop: '1.75rem', background: '#101827', border: '1px solid #26344a',
            borderRadius: 8, padding: '0.9rem 1rem', color: '#dbe5f1',
            fontSize: '0.92rem', lineHeight: 1.5,
          }}
        >
          {latestEvent.message}
        </div>
      )}

      {recordingActive && (
        <div style={{
          marginTop: '2rem', background: '#1a160a', border: '1px solid #7a5c1e',
          borderRadius: 8, padding: '1.1rem 1.25rem',
        }}>
          <div style={{ color: '#e6c15a', fontWeight: 600, fontSize: '0.95rem', marginBottom: '0.6rem' }}>
            🏴‍☠️ Your turn
          </div>
          <ol style={{
            margin: 0, paddingLeft: '1.2rem', color: '#cbd5e1',
            fontSize: '0.9rem', lineHeight: 1.7,
          }}>
            <li>A browser window will open at the target URL (give it a few seconds).</li>
            <li>Log in and perform the full workflow you want assessed — click, fill, and navigate normally.</li>
            <li>When you've finished the flow, click <strong>Finish recording</strong> below.</li>
          </ol>
          <p style={{ margin: '0.7rem 0 0', color: '#64748b', fontSize: '0.82rem' }}>
            Keep the browser window open while you work — everything you do is captured.
          </p>
          <button
            onClick={async () => {
              setRecordingDone(true);
              const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
              const url = new URL(`${API_BASE}/api/assessments/finish-recording`);
              if (flowName) url.searchParams.set('flow_name', flowName);
              await fetch(url.toString(), { method: 'POST' });
            }}
            style={{
              marginTop: '1rem', background: 'linear-gradient(180deg, #e6c15a, #c99b2e)',
              color: '#1a1408', border: 'none', padding: '0.6rem 1.4rem', borderRadius: 6,
              fontSize: '0.9rem', fontWeight: 700, cursor: 'pointer',
            }}
          >
            Finish recording
          </button>
        </div>
      )}

      {runMode === 'RECORDED_FLOW' && recordingDone && (
        <p style={{ marginTop: '2rem', color: '#64748b', fontSize: '0.9rem' }}>
          Recording finished.
        </p>
      )}

      {error && (
        <div style={{
          marginTop: '2rem', background: '#1a0f11', border: '1px solid #7f1d1d',
          borderRadius: 6, padding: '1rem 1.25rem', color: '#fca5a5', fontSize: '0.9rem',
        }}>
          <span style={{ fontWeight: 600 }}>Failed.</span> {error}
        </div>
      )}

      {isComplete && !error && (
        <button
          onClick={() => navigate(flowName ? `/${flowName}/report` : '/reports')}
          style={{
            marginTop: '2rem', background: 'linear-gradient(180deg, #e6c15a, #c99b2e)',
            color: '#1a1408', border: 'none', padding: '0.7rem 1.6rem', borderRadius: 6,
            fontSize: '0.95rem', fontWeight: 700, cursor: 'pointer',
          }}
        >
          📜 View report
        </button>
      )}

      {events.length > 0 && (
        <details style={{ marginTop: '2.5rem' }}>
          <summary style={{ cursor: 'pointer', color: '#e6c15a', fontSize: '0.95rem', fontWeight: 700 }}>
            Technical details ({events.length})
          </summary>
          <pre style={{
            background: '#0b0b14', padding: '1rem', borderRadius: 6,
            fontSize: '0.8rem', overflow: 'auto', maxHeight: 300,
            marginTop: '0.75rem', color: '#e2e8f0', whiteSpace: 'pre-wrap',
            border: '1px solid #1e293b',
          }}>
            {events.map((event, i) => {
              const detail = event.technical_detail ? `\n   ${event.technical_detail}` : '';
              return `${i + 1}. [${event.run_mode}] ${event.message}${detail}`;
            }).join('\n')}
          </pre>
        </details>
      )}
    </div>
  );
}

function Indicator({ status }: { status: StepStatus }) {
  const base: React.CSSProperties = {
    width: 22, height: 22, borderRadius: '50%',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    flexShrink: 0, boxSizing: 'border-box',
  };
  if (status === 'done') {
    return (
      <div role="img" aria-label="Complete" style={{ ...base, background: '#16a34a', color: 'white', fontSize: '0.8rem', fontWeight: 800 }}>
        <span aria-hidden="true">✓</span>
      </div>
    );
  }
  if (status === 'active') {
    return (
      <div style={{ ...base, border: '2px solid #e6c15a' }}>
        <div style={{
          width: 8, height: 8, borderRadius: '50%', background: '#e6c15a',
          animation: 'fbPulse 1.2s ease-in-out infinite',
        }} />
        <style>{`@keyframes fbPulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.6); } }`}</style>
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div style={{ ...base, background: '#dc2626', color: 'white', fontSize: '0.9rem', fontWeight: 700 }}>
        ×
      </div>
    );
  }
  return <div style={{ ...base, border: '2px solid #334155' }} />;
}
