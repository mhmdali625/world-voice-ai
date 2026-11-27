import type { QaRow } from '../api';
import { BACKEND_URL, clock, parseUsed } from '../api';

// Every question with its answer, language, evidence, the stored frames it
// relied on, which AI model answered, and the measured latency.

export default function ConversationsView({ rows }: { rows: QaRow[] }) {
  const words = rows.map(r => r.first_word_s).filter((v): v is number => v != null).sort((a, b) => a - b);
  const median = words.length ? words[Math.floor(words.length / 2)] : null;
  const within = words.filter(v => v <= 5).length;

  return (
    <>
      <div className="tiles">
        <div className="tile"><div className="label">Questions</div><div className="value">{rows.length}</div></div>
        <div className="tile"><div className="label">First word (median)</div>
          <div className="value">{median != null ? `${median.toFixed(1)} s` : '-'}</div>
          <div className="note">user stops speaking → first audio</div></div>
        <div className="tile"><div className="label">Within 5 s</div>
          <div className="value">{words.length ? `${within}/${words.length}` : '-'}</div><div className="note">measured on the phone</div></div>
        <div className="tile"><div className="label">Slowest first word</div>
          <div className="value">{words.length ? `${words[words.length - 1].toFixed(1)} s` : '-'}</div></div>
      </div>

      <section className="card" aria-labelledby="qa-title">
        <h2 id="qa-title">Questions & answers</h2>
        {rows.length === 0 ? (
          <div className="empty">No question asked yet.</div>
        ) : (
          <ul className="qa">
            {rows.map(row => {
              const used = parseUsed(row);
              return (
                <li key={row.id}>
                  <div className="q" dir="auto">Q: {row.question_heard || row.question || '(spoken question)'}</div>
                  <div className="a" dir="auto">{row.answer}</div>
                  <div className="meta">
                    <span className="chip">{clock(row.asked_at)}</span>
                    {row.first_word_s != null && <span className="chip">first word {row.first_word_s.toFixed(1)} s</span>}
                    {row.latency_s != null && <span className="chip">answer ready {row.latency_s.toFixed(1)} s</span>}
                    {row.evidence && <span className={`chip ${row.evidence}`}>evidence {row.evidence}</span>}
                    {row.answer_language && <span className="chip">{row.answer_language}</span>}
                    {used.model && <span className="chip">{used.model}</span>}
                    {used.current && <span className="chip">current view</span>}
                    <span className="chip">{row.device}</span>
                  </div>
                  {used.evidence.length > 0 && (
                    <div className="thumbs" aria-label="Stored frames this answer used">
                      {used.evidence.map(e => (
                        <img key={e.id} src={`${BACKEND_URL}${e.image_url}`} title={e.text || e.label}
                          alt={`Frame used${e.text ? `: ${e.text}` : ''}, seen ${Math.round(e.age_s)} s before`}
                          onError={ev => { ev.currentTarget.hidden = true; }} />
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
