import { useState } from 'react';

import type { MemoryFrame, MemoryStats } from '../api';
import { BACKEND_URL, ago, clock, usePoll } from '../api';

// Visual memory: the stored frames (privacy-protected), what was read and
// recognised in each, search by meaning or keyword, and Clear Memory.

type Props = { device: string | null; stats: MemoryStats | null };

function FrameCard({ f }: { f: MemoryFrame }) {
  return (
    <figure className="frame">
      <img src={`${BACKEND_URL}${f.image_url}`} alt={f.caption || 'Stored frame, not analysed yet'} loading="lazy" />
      <figcaption>
        <span className="small">{clock(f.captured_at)} · {ago(f.age_s)}{f.score != null ? ` · match ${f.score}` : ''}</span>
        {f.status !== 'analyzed' ? (
          <span className="muted">{f.status === 'failed' ? 'Analysis failed' : 'Waiting for analysis'}</span>
        ) : (
          <>
            <span>{f.caption}</span>
            {f.text && <span className="ocr" dir="auto">Text: {f.text}</span>}
            {f.objects.length > 0 && <span className="small">Objects: {f.objects.join(', ')}</span>}
            {f.hazards.length > 0 && <span className="hazard">Obstacle: {f.hazards.join(', ')}</span>}
          </>
        )}
        <span className="meta">
          {f.keep_reason && <span className="chip">{f.keep_reason}</span>}
          {f.enhanced && <span className="chip">enhanced: {f.enhanced}</span>}
          {(f.faces_blurred ?? 0) > 0 && <span className="chip">faces blurred: {f.faces_blurred}</span>}
        </span>
      </figcaption>
    </figure>
  );
}

export default function MemoryView({ device, stats }: Props) {
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<MemoryFrame[] | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState('');
  const only = device ? `&device=${encodeURIComponent(device)}` : '';
  const frames = usePoll<{ frames: MemoryFrame[] }>(`/memory/frames?minutes=30&limit=60${only}`, 4000, refresh);

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) { setResults(null); return; }
    try {
      const r = await fetch(`${BACKEND_URL}/memory/search?q=${encodeURIComponent(query)}&k=8${only}`);
      setResults(r.ok ? ((await r.json()) as { results: MemoryFrame[] }).results : []);
    } catch { setResults([]); }
  };

  const clear = async () => {
    setConfirm(false);
    try {
      const r = await fetch(`${BACKEND_URL}/memory${device ? `?device=${encodeURIComponent(device)}` : ''}`, { method: 'DELETE' });
      const data = (await r.json()) as { deleted_frames: number };
      setNotice(`Memory cleared: ${data.deleted_frames} frames and their questions deleted.`);
      setResults(null);
      setRefresh(n => n + 1);
    } catch { setNotice('Could not clear the memory.'); }
  };

  const s = stats?.store;
  const sel = stats?.selection ?? {};
  const rejected = (sel.duplicate ?? 0) + (sel.blurry ?? 0) + (sel.too_dark ?? 0) + (sel.uninformative ?? 0);
  const shown = results ?? frames.data?.frames ?? [];

  return (
    <>
      <div className="tiles">
        <div className="tile"><div className="label">Frames stored</div><div className="value">{s?.frames ?? '-'}</div>
          <div className="note">{s ? `${s.analyzed ?? 0} analysed · ${s.pending ?? 0} waiting` : ''}</div></div>
        <div className="tile"><div className="label">Frames rejected</div><div className="value">{rejected}</div>
          <div className="note">{`${sel.duplicate ?? 0} duplicate · ${sel.blurry ?? 0} blurry · ${sel.too_dark ?? 0} dark`}</div></div>
        <div className="tile"><div className="label">Analyses reused</div><div className="value">{stats?.gemini_budget.reused_analysis ?? '-'}</div>
          <div className="note">unchanged scenes, no AI request</div></div>
        <div className="tile"><div className="label">Faces blurred</div><div className="value">{stats?.privacy.faces ?? '-'}</div>
          <div className="note">before storage · no recognition</div></div>
        <div className="tile"><div className="label">Kept for</div><div className="value">{stats?.retention_min ?? 30}</div>
          <div className="note">minutes, then deleted</div></div>
      </div>

      <section className="card" aria-labelledby="mem-title">
        <div className="card-head">
          <div>
            <h2 id="mem-title">Visual memory{device ? ` · ${device}` : ''}</h2>
            <p className="muted">Sharp, new, informative frames from the stream, with the text and objects found in them.</p>
          </div>
          {confirm ? (
            <span className="meta" style={{ marginTop: 0 }}>
              <span className="muted">Delete all frames and questions{device ? ` of ${device}` : ''}?</span>
              <button className="btn danger" onClick={() => { void clear(); }}>Yes, delete</button>
              <button className="btn secondary" onClick={() => setConfirm(false)}>Cancel</button>
            </span>
          ) : (
            <button className="btn secondary" onClick={() => setConfirm(true)}>Clear memory</button>
          )}
        </div>
        <form onSubmit={e => { void search(e); }} role="search" style={{ marginBottom: 14 }}>
          <input className="search" value={query} onChange={e => setQuery(e.target.value)}
            placeholder="Search the memory by meaning or text, in any language (e.g. 'way out', 'chambre 12', 'مخرج')"
            aria-label="Search the visual memory" />
        </form>
        <p className="muted" aria-live="polite" style={{ minHeight: 18 }}>
          {notice || (results ? `${results.length} result(s) for "${query}"` : frames.error ? `Memory unavailable (${frames.error})` : '')}
        </p>
        {shown.length === 0 ? (
          <div className="empty">{results ? 'Nothing found.' : 'No frames yet. Start streaming from the phone.'}</div>
        ) : (
          <div className="grid-auto" style={{ marginTop: 10 }}>{shown.map(f => <FrameCard key={f.id} f={f} />)}</div>
        )}
      </section>
    </>
  );
}
