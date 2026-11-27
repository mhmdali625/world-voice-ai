import type { QaRow, StreamStatus, Warning } from '../api';
import { BACKEND_URL, ago, parseUsed, usePoll } from '../api';
import LiveCameraReceiver from '../LiveCamera';

// Live tab: the phone's camera, the latest answer (with the frames it
// used and its real first-word latency) and the warnings valid right now.

type Props = { selected: string | null; device: string | null; stream: StreamStatus | null; qa: QaRow[] };

export default function LiveView({ selected, device, stream, qa }: Props) {
  const warnings = usePoll<{ warnings: Warning[] }>(
    `/memory/alerts/history?limit=10${device ? `&device=${encodeURIComponent(device)}` : ''}`, 3000);
  const active = (warnings.data?.warnings ?? []).filter(w => w.active);
  const last = qa[0];
  const used = last ? parseUsed(last) : null;

  return (
    <div className="grid-2">
      <LiveCameraReceiver device={selected} />

      <div className="stack">
        <section className="card" aria-labelledby="stream-title">
          <div className="card-head">
            <h2 id="stream-title">Stream</h2>
            <span className={`badge ${stream?.live ? 'good' : stream?.stalled ? 'warn' : 'bad'}`}>
              {stream?.live ? 'Live' : stream?.stalled ? 'Stalled' : 'Offline'}
            </span>
          </div>
          <div className="tiles" style={{ marginBottom: 0 }}>
            <div className="tile"><div className="label">Phone</div><div className="value" style={{ fontSize: 16 }}>{stream?.device ?? '-'}</div></div>
            <div className="tile"><div className="label">Frame rate</div><div className="value">{stream?.fps ?? '-'}</div><div className="note">frames / s</div></div>
            <div className="tile"><div className="label">Resolution</div><div className="value" style={{ fontSize: 18 }}>{stream?.resolution ?? '-'}</div></div>
            <div className="tile"><div className="label">Last frame</div><div className="value">{stream?.last_frame_age_s ?? '-'}</div><div className="note">seconds ago</div></div>
          </div>
        </section>

        <section className="card" aria-labelledby="last-title" aria-live="polite">
          <h2 id="last-title">Latest answer</h2>
          {!last ? (
            <div className="empty">No question asked yet. Ask the assistant on the phone.</div>
          ) : (
            <>
              <p className="muted" dir="auto">Q: {last.question_heard || last.question}</p>
              <p dir="auto" style={{ fontSize: 17, margin: '6px 0 0', lineHeight: 1.5 }}>{last.answer}</p>
              <div className="meta">
                {last.first_word_s != null && <span className="chip">first word {last.first_word_s.toFixed(1)} s</span>}
                {last.latency_s != null && <span className="chip">answer ready {last.latency_s.toFixed(1)} s</span>}
                {last.evidence && <span className={`chip ${last.evidence}`}>evidence {last.evidence}</span>}
                {last.answer_language && <span className="chip">{last.answer_language}</span>}
                {used?.model && <span className="chip">{used.model}</span>}
                <span className="chip">{ago(Date.now() / 1000 - last.asked_at)}</span>
              </div>
              {used && used.evidence.length > 0 && (
                <div className="thumbs" aria-label="Stored frames this answer used">
                  {used.evidence.map(e => (
                    <img key={e.id} src={`${BACKEND_URL}${e.image_url}`} alt={`Frame used: ${e.text || e.label}`}
                      onError={ev => { ev.currentTarget.hidden = true; }} />
                  ))}
                </div>
              )}
            </>
          )}
        </section>

        <section className="card" aria-labelledby="warn-title">
          <div className="card-head">
            <h2 id="warn-title">Warnings now</h2>
            <span className={`badge ${active.length ? 'bad' : 'good'}`}>{active.length ? `${active.length} active` : 'none'}</span>
          </div>
          {active.length === 0 ? (
            <p className="muted">No obstacle warning is valid right now.</p>
          ) : (
            <ul className="qa">
              {active.map(w => (
                <li key={w.id}><span className="hazard">{w.message_en}</span>
                  <div className="small">{ago(Date.now() / 1000 - w.at)}{w.certain === false ? ' · position uncertain' : ''}</div></li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
