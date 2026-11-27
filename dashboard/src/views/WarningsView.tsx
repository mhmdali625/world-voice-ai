import type { Warning } from '../api';
import { ago, clock, usePoll } from '../api';

// Spoken obstacle warnings: valid now and earlier. A warning is spoken only
// while the camera still shows the obstacle, expires after 10 s and is not
// repeated within 45 s.

export default function WarningsView({ device }: { device: string | null }) {
  const { data, error } = usePoll<{ warnings: Warning[] }>(
    `/memory/alerts/history?limit=50${device ? `&device=${encodeURIComponent(device)}` : ''}`, 3000);
  const rows = data?.warnings ?? [];

  return (
    <section className="card" aria-labelledby="w-title">
      <div className="card-head">
        <div>
          <h2 id="w-title">Obstacle warnings</h2>
          <p className="muted">Announced without being asked, only for obstacles in the walking path that the camera
            still shows. Spoken in the user's language. Never claims a path is safe.</p>
        </div>
        <span className={`badge ${rows.some(r => r.active) ? 'bad' : 'good'}`}>
          {rows.filter(r => r.active).length} active</span>
      </div>
      {error && <p className="muted">Unavailable ({error}).</p>}
      {rows.length === 0 ? (
        <div className="empty">No warning so far.</div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Time</th><th>Warning</th><th>Position</th><th>Phone</th><th>State</th></tr></thead>
            <tbody>
              {rows.map(w => (
                <tr key={w.id}>
                  <td>{clock(w.at)}<div className="small">{ago(Date.now() / 1000 - w.at)}</div></td>
                  <td>{w.message_en}</td>
                  <td>{w.position ?? '-'}{w.certain === false ? ' (uncertain)' : ''}</td>
                  <td>{w.device}</td>
                  <td><span className={`badge ${w.active ? 'bad' : ''}`}>{w.active ? 'active' : 'expired'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
