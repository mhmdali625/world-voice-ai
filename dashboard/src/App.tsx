import { useState } from 'react';

import type { MemoryStats, QaRow, StreamStatus, TtsQuota } from './api';
import { usePoll } from './api';
import ConversationsView from './views/ConversationsView';
import LiveView from './views/LiveView';
import MemoryView from './views/MemoryView';
import PhotosView from './views/PhotosView';
import SystemView from './views/SystemView';
import WarningsView from './views/WarningsView';

// ==========================================
// WORLD VOICE AI - MONITORING DASHBOARD
// ==========================================
//
// Everything shown comes from the backend (no sample data):
// /stream/status, /memory/*, /tts/quota, /network, /api/images.

type Tab = 'live' | 'memory' | 'conversations' | 'warnings' | 'system' | 'photos';

const TABS: { id: Tab; label: string }[] = [
  { id: 'live', label: 'Live' },
  { id: 'memory', label: 'Visual memory' },
  { id: 'conversations', label: 'Questions & answers' },
  { id: 'warnings', label: 'Warnings' },
  { id: 'system', label: 'AI, voice & network' },
  { id: 'photos', label: 'Photo mode' },
];

const initialTab = (): Tab => {
  const hash = window.location.hash.replace('#', '');
  return (TABS.find(t => t.id === hash)?.id ?? 'live');
};

export default function App() {
  // Each tab has its own address (#memory, #warnings...) so it can be bookmarked.
  const [tab, setTabState] = useState<Tab>(initialTab);
  const setTab = (next: Tab) => {
    setTabState(next);
    window.history.replaceState(null, '', `#${next}`);
  };
  const [selected, setSelected] = useState<string | null>(null);

  const stream = usePoll<StreamStatus>(
    `/stream/status${selected ? `?device=${encodeURIComponent(selected)}` : ''}`, 2000);
  const devices = stream.data?.devices ?? [];
  // Memory of the phone shown (all phones while none is known yet).
  const device = selected ?? (devices.length > 0 ? stream.data?.device ?? null : null);

  const stats = usePoll<MemoryStats>(`/memory/stats${device ? `?device=${encodeURIComponent(device)}` : ''}`, 4000);
  const qa = usePoll<{ qa: QaRow[] }>(`/memory/qa?limit=40${device ? `&device=${encodeURIComponent(device)}` : ''}`, 3000);
  const tts = usePoll<TtsQuota>('/tts/quota', 10000);

  const backendUp = stream.error === null && stream.data !== null;
  const live = Boolean(stream.data?.live);
  const vision = stats.data?.vision;
  // The provider that would answer the next question (Gemini -> Groq -> ... -> local).
  const order = vision?.answer_chain ?? [];
  const next = order.find(p => p.usable);
  const usable = next ? [next.name] : [];
  const aiState = !vision ? '' : !next ? 'bad' : next === order[0] ? 'good' : 'warn';
  const voice = tts.data?.next_answer_provider;
  const voiceName = voice === 'gemini' ? 'Fola (Gemini)' : voice === 'azure' ? 'Rami (Azure)' : voice ?? 'unknown';

  return (
    <div>
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">WV</div>
          <div>
            <h1>World Voice AI</h1>
            <p>Hands-free visual assistant for blind and visually impaired people</p>
          </div>
        </div>
        <div className="pills" role="status" aria-live="polite">
          <span className="pill"><span className={`dot ${backendUp ? 'good' : 'bad'}`} />
            Backend {backendUp ? 'online' : 'offline'}</span>
          <span className="pill"><span className={`dot ${live ? 'good' : stream.data?.stalled ? 'warn' : 'bad'}`} />
            {live ? `Phone live · ${stream.data?.fps ?? '?'} fps` : stream.data?.stalled ? 'Phone stalled' : 'Phone not streaming'}</span>
          <span className="pill"><span className={`dot ${aiState}`} />
            AI {usable[0] ?? (vision ? 'unavailable' : '...')}</span>
          <span className="pill"><span className={`dot ${voice ? 'good' : 'bad'}`} />Voice {voiceName}</span>
          {devices.length > 1 && (
            <label className="pill">
              Phone{' '}
              <select className="device" value={selected ?? ''}
                onChange={e => setSelected(e.target.value || null)} aria-label="Phone shown">
                <option value="">Automatic (live phone)</option>
                {devices.map(d => (
                  <option key={d.device} value={d.device}>{d.device}{d.live ? ' (live)' : ''}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="Dashboard sections">
        {TABS.map(t => (
          <button key={t.id} className="tab" role="tab" aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}>
            {t.label}
            {t.id === 'memory' && stats.data && <span className="count">{stats.data.store.frames}</span>}
            {t.id === 'conversations' && qa.data && <span className="count">{qa.data.qa.length}</span>}
          </button>
        ))}
      </nav>

      <main className="page">
        {tab === 'live' && <LiveView selected={selected} device={device} stream={stream.data} qa={qa.data?.qa ?? []} />}
        {tab === 'memory' && <MemoryView device={device} stats={stats.data} />}
        {tab === 'conversations' && <ConversationsView rows={qa.data?.qa ?? []} />}
        {tab === 'warnings' && <WarningsView device={device} />}
        {tab === 'system' && <SystemView stats={stats.data} tts={tts.data} devices={devices} />}
        {tab === 'photos' && <PhotosView />}
      </main>
    </div>
  );
}
