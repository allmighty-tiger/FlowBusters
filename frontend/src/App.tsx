import { useEffect, useRef, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import PortalPage from './pages/portal';
import ProgressPage from './pages/progress';
import ReportPage from './pages/report';
import ReportsIndexPage from './pages/reports';
import TallTalePage from './pages/tall-tale';
import AboutPage from './pages/about';
import wavesBg from './assets/waves-bg.svg';

const MENU = [
  { icon: '🗺️', label: 'New Assessment', to: '/' },
  { icon: '🧭', label: 'Progress', to: '/progress' },
  { icon: '📜', label: 'Reports', to: '/reports' },
  { icon: '📖', label: 'A Tall Tale', to: '/tall-tale' },
  { icon: '⚔️', label: 'About', to: '/about' },
];

function ChestMenu() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        aria-label="Open menu"
        aria-expanded={open}
        style={{
          background: 'transparent', border: '1px solid #7a5c1e',
          color: '#e6c15a', fontSize: '1.3rem', lineHeight: 1,
          width: 44, height: 44, borderRadius: 6, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'background 0.15s ease',
        }}
      >
        {open ? '🔒' : '🧭'}
      </button>

      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 'calc(100% + 8px)',
          background: '#12122a', border: '1px solid #7a5c1e', borderRadius: 8,
          boxShadow: '0 8px 28px rgba(0,0,0,0.5)', minWidth: 190, overflow: 'hidden',
          zIndex: 50,
        }}>
          {MENU.map((m, i) => (
            <button
              key={m.to}
              onClick={() => { setOpen(false); navigate(m.to); }}
              style={{
                display: 'flex', alignItems: 'center', gap: '0.6rem',
                width: '100%', textAlign: 'left',
                background: 'transparent', border: 'none',
                borderTop: i ? '1px solid #1e1e38' : 'none',
                padding: '0.6rem 0.9rem', cursor: 'pointer',
                color: '#e2e8f0', fontSize: '0.9rem',
                transition: 'background 0.12s ease, color 0.12s ease',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = '#1a160a'; e.currentTarget.style.color = '#e6c15a'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#e2e8f0'; }}
            >
              <span style={{ fontSize: '1.05rem', width: '1.5rem', textAlign: 'center' }}>{m.icon}</span>
              {m.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function NavBar() {
  return (
    <nav style={{
      background: '#0f0f23', borderBottom: '1px solid #1a1a3e',
      padding: '0.75rem 2rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    }}>
      <a href="/" className="fb-logo" style={{
        textDecoration: 'none', fontSize: '1.2rem', fontWeight: 700,
        letterSpacing: '0.02em', color: '#e6c15a',
        display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
      }}>
        <span style={{ fontSize: '1.1rem' }}>⚔️</span>
        {'FLOWBUSTERS'.split('').map((ch, i) => (
          <span key={i} className="fb-logo-letter" style={{ animationDelay: `${i * 0.12}s` }}>{ch}</span>
        ))}
      </a>
      <style>{`
        .fb-logo-letter { animation: fbGoldSweep 2.4s ease-in-out infinite; }
        @keyframes fbGoldSweep {
          0%, 100% { text-shadow: none; color: #e6c15a; }
          15%       { text-shadow: 0 0 8px rgba(255,217,122,0.85), 0 0 22px rgba(255,200,80,0.6); color: #ffe9a8; }
          30%       { text-shadow: none; color: #e6c15a; }
        }
      `}</style>
      <ChestMenu />
    </nav>
  );
}

function AppContent() {
  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', minHeight: '100vh', color: '#e2e8f0',
      backgroundColor: '#0f0f23',
      backgroundImage: `url(${wavesBg})`,
      backgroundRepeat: 'no-repeat',
      backgroundSize: 'cover',
      backgroundPosition: 'center bottom' }}>
      <NavBar />
      <main style={{ maxWidth: 800, margin: '0 auto', padding: '2rem 1rem' }}>
        <Routes>
          <Route path="/" element={<PortalPage />} />
          <Route path="/progress" element={<ProgressPage />} />
          <Route path="/reports" element={<ReportsIndexPage />} />
          <Route path="/:flowName/report" element={<ReportPage />} />
          <Route path="/tall-tale" element={<TallTalePage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Router>
      <AppContent />
    </Router>
  );
}