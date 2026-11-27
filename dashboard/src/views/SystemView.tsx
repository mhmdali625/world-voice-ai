import type { DeviceStatus, MemoryStats, NetworkInfo, TtsQuota } from '../api';
import { ago, usePoll } from '../api';

// AI models and their daily quota, fallbacks, voices, phones and network
// addresses. Technical details live here so the Live tab stays clean.

function Meter({ used, limit }: { used: number; limit: number }) {
  const share = limit ? used / limit : 0;
  const level = share >= 0.9 ? 'bad' : share >= 0.6 ? 'warn' : '';
  return (
    <div className="meter" role="img" aria-label={`${used} of ${limit} requests used today`}>
      <span className={level} style={{ width: `${Math.min(100, share * 100)}%` }} />
    </div>
  );
}

type Props = { stats: MemoryStats | null; tts: TtsQuota | null; devices: DeviceStatus[] };

export default function SystemView({ stats, tts, devices }: Props) {
  const network = usePoll<NetworkInfo>('/network', 15000);
  const budget = stats?.gemini_budget;
  const vision = stats?.vision;
  const chain = vision?.chain ?? [];

  return (
    <div className="stack">
      <section className="card" aria-labelledby="ai-title">
        <div className="card-head">
          <div>
            <h2 id="ai-title">AI models (vision and answers)</h2>
            <p className="muted">Tried in this order. A model whose daily quota is used up, that is overloaded or too
              slow hands over to the next one. {budget ? `Quota day ${budget.day} (resets at midnight Pacific time).` : ''}</p>
          </div>
          {budget && <span className="badge">{budget.used_last_minute}/{budget.per_minute} this minute</span>}
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>#</th><th>Model</th><th>Today</th><th style={{ width: '32%' }}>Daily quota</th><th>State</th></tr></thead>
            <tbody>
              {chain.map((m, i) => {
                const use = budget?.models[m];
                const usable = vision?.usable_for_questions.includes(m);
                return (
                  <tr key={m}>
                    <td>{i + 1}</td>
                    <td>{m}{vision?.last_model.user === m && <span className="chip" style={{ marginLeft: 6 }}>answered last</span>}</td>
                    <td>{use ? `${use.used_today} / ${use.daily_limit}` : '0'}</td>
                    <td><Meter used={use?.used_today ?? 0} limit={use?.daily_limit ?? 500} /></td>
                    <td>{use?.exhausted
                      ? <span className="badge bad">quota used up · back in {Math.round(use.available_again_in_s / 3600)} h</span>
                      : usable ? <span className="badge good">available</span> : <span className="badge warn">not usable now</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <h2 style={{ marginTop: 16 }}>Order in which a question is answered</h2>
        <ol className="muted" style={{ margin: '6px 0 0', paddingLeft: 20, lineHeight: 1.9 }}>
          {(vision?.answer_chain ?? []).map(p => (
            <li key={p.name}>{p.name} <span className={`badge ${p.usable ? 'good' : ''}`}>{p.usable ? 'ready' : 'not available now'}</span></li>
          ))}
          <li>a short spoken message that the AI is busy (never silence, never a guess)</li>
        </ol>
        <div className="meta">
          {budget && <span className="chip">{budget.user_daily_reserve} requests/day kept for questions</span>}
          {budget && <span className="chip">fallbacks: {budget.fallbacks}</span>}
          {budget && <span className="chip">analyses reused: {budget.reused_analysis}</span>}
          {budget && budget.background_paused_s > 0 && <span className="chip">background paused {budget.background_paused_s} s</span>}
        </div>
        {(vision?.recent_fallbacks.length ?? 0) > 0 && (
          <ul className="qa" style={{ marginTop: 10 }}>
            {vision!.recent_fallbacks.slice().reverse().map((e, i) => (
              <li key={i} className="small">{ago(Date.now() / 1000 - e.at)}: {e.model} skipped for {e.kind} ({e.reason})</li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid-2">
        <section className="card" aria-labelledby="voice-title">
          <h2 id="voice-title">Voices</h2>
          <p className="muted">Next answer: <b>{tts?.next_answer_provider === 'gemini' ? 'Fola (Gemini TTS)' :
            tts?.next_answer_provider === 'azure' ? 'Rami (Azure Speech)' : tts?.next_answer_provider ?? 'none available'}</b>.
            One voice per answer, never switched mid-answer.</p>
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table className="data">
              <thead><tr><th>Provider</th><th>State</th></tr></thead>
              <tbody>
                {Object.entries(tts?.providers ?? {}).map(([name, p]) => (
                  <tr key={name}><td>{name}</td><td>{p.available === false
                    ? <span className="badge warn">paused{p.resets_in_s ? ` · ${Math.round(p.resets_in_s / 3600)} h` : ''}</span>
                    : p.configured === false ? <span className="badge">not configured</span>
                      : <span className="badge good">available</span>}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card" aria-labelledby="net-title">
          <h2 id="net-title">Connect a phone</h2>
          <p className="muted">On the phone: Settings → Server address. {network.data?.access_code_required
            ? 'An access code is required.' : 'No access code is set (WV_ACCESS_TOKEN).'}</p>
          <ul className="qa">
            {(network.data?.addresses ?? []).map(a => (
              <li key={a.address}><b>{a.url}</b>{a.recommended && <span className="badge good" style={{ marginLeft: 8 }}>use this</span>}
                <div className="small">{a.kind}</div></li>
            ))}
          </ul>
        </section>
      </div>

      <div className="grid-2">
        <section className="card" aria-labelledby="dev-title">
          <h2 id="dev-title">Phones</h2>
          {devices.length === 0 ? <div className="empty">No phone has streamed yet.</div> : (
            <table className="data">
              <thead><tr><th>Phone</th><th>State</th><th>fps</th><th>Resolution</th><th>Viewers</th></tr></thead>
              <tbody>
                {devices.map(d => (
                  <tr key={d.device}><td>{d.device}</td>
                    <td><span className={`badge ${d.live ? 'good' : d.stalled ? 'warn' : 'bad'}`}>{d.live ? 'live' : d.stalled ? 'stalled' : 'offline'}</span></td>
                    <td>{d.fps ?? '-'}</td><td>{d.resolution ?? '-'}</td><td>{d.viewers}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card" aria-labelledby="pipe-title">
          <h2 id="pipe-title">Image pipeline</h2>
          <table className="data">
            <tbody>
              <tr><td>Frames seen / kept</td><td>{stats?.selection.seen ?? '-'} / {stats?.selection.kept ?? '-'}</td></tr>
              <tr><td>Analysis requests (frames)</td><td>{String(stats?.analysis.requests ?? '-')} ({String(stats?.analysis.frames ?? '-')})</td></tr>
              <tr><td>Waiting for quota</td><td>{String(stats?.analysis.waiting_for_quota ?? 0)}</td></tr>
              <tr><td>Faces blurred / frames checked</td><td>{stats?.privacy.faces ?? '-'} / {stats?.privacy.frames ?? '-'}</td></tr>
              <tr><td>Warnings created / withdrawn / not repeated</td>
                <td>{stats?.warnings.created ?? '-'} / {stats?.warnings.withdrawn ?? '-'} / {stats?.warnings.repeated ?? '-'}</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
