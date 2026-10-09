import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import './results.css';
import './modal.css';

const statusNames = { queued: 'Đang chờ', running: 'Đang chạy', retrying: 'Thử lại', completed: 'Hoàn thành', failed: 'Lỗi', agent_tuning: 'Agent đang tối ưu', agent_recommendation: 'Agent đề xuất tham số', category_tests: 'Đang kiểm tra category' };
const date = value => value ? new Date(value).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
const statusFilters = [['', 'Tất cả'], ['queued', 'Đang chờ'], ['running', 'Đang chạy'], ['retrying', 'Thử lại'], ['completed', 'Hoàn thành'], ['failed', 'Lỗi']];

function App() {
  const [tasks, setTasks] = useState([]);
  const [filter, setFilter] = useState('');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [dialog, setDialog] = useState(false);
  const [mode, setMode] = useState('llm');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');
  const [form, setForm] = useState({ normal: '/app/data/normal.txt', attack: '/app/data/attack.txt', maxTrials: 10, target: 0.9 });

  const notify = message => { setToast(message); window.setTimeout(() => setToast(''), 3500); };
  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/tasks');
      if (!response.ok) throw new Error('Không tải được danh sách job');
      const data = await response.json();
      setTasks(data.tasks || []);
    } catch (error) { notify(error.message); }
  }, []);

  const loadDetail = useCallback(async id => {
    if (!id) { setDetail(null); return; }
    try {
      const response = await fetch(`/tasks/${encodeURIComponent(id)}/process`);
      if (!response.ok) throw new Error('Không tải được chi tiết job');
      setDetail(await response.json());
    } catch (error) { notify(error.message); }
  }, []);

  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 3000); return () => window.clearInterval(timer); }, [refresh]);
  useEffect(() => { if (selectedId) loadDetail(selectedId); }, [selectedId, tasks, loadDetail]);
  useEffect(() => {
    if (!selectedId) return;
    const closeOnEscape = event => { if (event.key === 'Escape') setSelectedId(''); };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [selectedId]);

  const counts = {
    total: tasks.length,
    running: tasks.filter(task => ['running', 'agent_tuning', 'retrying'].includes(task.status)).length,
    queued: tasks.filter(task => task.status === 'queued').length,
    completed: tasks.filter(task => task.status === 'completed').length,
    failed: tasks.filter(task => task.status === 'failed').length,
  };
  const visible = tasks.filter(task => {
    const p = task.process || {};
    return (!filter || task.status === filter) && (!query || [task.job_id, task.status, p.model, p.message].some(value => String(value || '').toLowerCase().includes(query.toLowerCase())));
  });
  const updateForm = event => setForm(current => ({ ...current, [event.target.name]: event.target.value }));

  async function createJob(event) {
    event.preventDefault(); setBusy(true);
    try {
      const response = await fetch('/hook', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        mode, 'input-path': { normal: form.normal.trim(), attack: form.attack.trim() }, 'output-path': '/app/output',
        max_trials: Number(form.maxTrials), target_accuracy: Number(form.target),
      }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không tạo được job');
      setDialog(false); setMode('llm'); setSelectedId(data.job_id); notify('Đã thêm job vào hàng đợi'); await refresh();
    } catch (error) { notify(error.message); }
    finally { setBusy(false); }
  }

  const job = tasks.find(task => task.job_id === selectedId);
  const process = detail?.process || {};
  const result = detail?.result;
  const events = [...(detail?.events || [])].reverse();
  const trialPoints = result?.trials || (detail?.events || []).filter(event => event.trial && event.metrics).map(event => ({ model: event.model, metrics: event.metrics }));
  const progress = task => [task.process?.model, task.process?.trial && `Trial ${task.process.trial}`, task.process?.round && `Round ${task.process.round}`].filter(Boolean).join(' · ') || task.process?.message || '—';

  return <>
    <header><div className="brand"><span className="mark">✳</span> Auto ML <span className="slash">/ Management</span></div><div className="live"><i />Đang cập nhật · tối đa 4 worker</div></header>
    <main>
      <div className="heading"><div><div className="eyebrow">Training operations</div><h1>Quản lý training</h1><div className="sub">Theo dõi các job và kết quả huấn luyện.</div></div><button onClick={() => setDialog(true)}>＋ Tạo job</button></div>
      <section className="stats">
        <Stat label="Tổng số job" value={counts.total} note="Lịch sử lưu trong SQLite" />
        <Stat label="Đang chạy" value={counts.running} note="Tối đa 4 job cùng lúc" />
        <Stat label="Đang chờ" value={counts.queued} note="Sẽ chạy khi worker sẵn sàng" />
        <Stat label="Hoàn thành" value={counts.completed} note={`${counts.failed} job lỗi`} />
      </section>
      <div className="layout">
        <section className="card"><div className="panel-head"><h2>Danh sách job</h2><button className="secondary refresh" onClick={refresh} title="Làm mới">↻</button></div>
          <div className="filters">{statusFilters.map(([value, label]) => <button key={value} className={`chip ${filter === value ? 'active' : ''}`} onClick={() => setFilter(value)}>{label}</button>)}
            <label className="search"><span>⌕</span><input type="search" placeholder="Tìm job, model…" value={query} onChange={event => setQuery(event.target.value)} /></label>
          </div>
          <div className="table-wrap"><table><thead><tr><th>Job</th><th>Trạng thái</th><th>Tiến trình</th><th>Thời gian</th></tr></thead><tbody>
            {visible.length ? visible.map(task => <tr key={task.job_id} className={`job ${selectedId === task.job_id ? 'selected' : ''}`} onClick={() => setSelectedId(task.job_id)}>
              <td><div className="id">{task.job_id.slice(0, 12)}…</div><small>{date(task.created_at)}</small></td><td><Status value={task.status} /></td><td className="progress">{progress(task)}</td><td className="time">{date(task.updated_at)}</td>
            </tr>) : <tr><td colSpan="4" className="empty">{query || filter ? 'Không tìm thấy job phù hợp.' : 'Chưa có job nào. Tạo job đầu tiên để bắt đầu.'}</td></tr>}
          </tbody></table></div>
        </section>
      </div>
    </main>
    {selectedId && <div className="overlay detail-overlay" onMouseDown={event => event.target === event.currentTarget && setSelectedId('')}><section className="detail-dialog"><div className="modal-head"><div><h2>Chi tiết job</h2><span className="modal-subtitle">{selectedId}</span></div><div className="modal-controls">{detail && <Status value={detail.status} />}<button className="close" onClick={() => setSelectedId('')} aria-label="Đóng">×</button></div></div>
      {!detail ? <div className="placeholder">Đang tải chi tiết job…</div> : <div className="detail-content">
        <div className="detail-state"><strong>{statusNames[detail.status] || detail.status}</strong></div>
        <div className="kv"><span>Trạng thái</span><span>{process.message || statusNames[detail.status] || detail.status}</span><span>Model</span><span>{process.model || '—'}</span><span>Trial</span><span>{process.trial || '—'}</span><span>Round</span><span>{process.round || '—'}</span><span>Tạo lúc</span><span>{date(job?.created_at)}</span>{process.metrics && <><span>Metrics hiện tại</span><span>{JSON.stringify(process.metrics)}</span></>}{job?.error && <><span>Lỗi</span><span className="error">{job.error}</span></>}</div>
        {trialPoints.length > 0 && <TrialChart trials={trialPoints} />}
        {result?.best && <section className="result-card"><div className="result-heading"><h3>Kết quả tốt nhất</h3><span className={`target-pill ${result.target_met ? 'passed' : 'missed'}`}>{result.target_met ? 'Đạt mục tiêu' : 'Chưa đạt mục tiêu'}</span></div>
          <div className="result-model">{result.best.model}</div><div className="metric-grid">{Object.entries(result.best.metrics || {}).map(([key, value]) => <div className="metric" key={key}><span>{key}</span><strong>{typeof value === 'number' ? value.toFixed(4) : value}</strong></div>)}</div>
          <div className="result-meta"><span>Chọn theo</span><strong>{result.selection?.metric || '—'} · {result.selection?.score?.toFixed?.(4) ?? '—'}</strong><span>Validation đạt mục tiêu</span><strong>{result.validation_target_met ? 'Có' : 'Không'}</strong></div>
          {result.best.params && <details className="params"><summary>Hyperparameter tốt nhất</summary><pre>{JSON.stringify(result.best.params, null, 2)}</pre></details>}
          {result.category_tests && <div className="category-results"><h4>Category test</h4>{Object.entries(result.category_tests).map(([name, value]) => <div key={name}><strong>{name}</strong><span>{value.status === 'completed' ? `${value.passed}/${value.total} · ${(value.accuracy * 100).toFixed(1)}%` : value.status || '—'}</span></div>)}</div>}
          {result.artifacts && <div className="artifact-path">Artifacts: <code>{result.artifacts}</code></div>}
        </section>}
        <div className="events"><h3>Lịch sử cập nhật</h3>{events.length ? events.map((event, index) => <div className="event" key={`${event.timestamp}-${index}`}><time>{date(event.timestamp)}</time><strong>{statusNames[event.status] || event.status || 'Cập nhật'}</strong>{event.message && ` · ${event.message}`}{event.result && <div>{JSON.stringify(event.result)}</div>}</div>) : <span className="muted">Chưa có cập nhật.</span>}</div>
      </div>}
    </section></div>}
    {dialog && <div className="overlay" onMouseDown={event => event.target === event.currentTarget && setDialog(false)}><section className="dialog"><div className="modal-head"><h2>Tạo job training</h2><button className="close" onClick={() => setDialog(false)}>×</button></div>
      <form className="form" onSubmit={createJob}><div className="field"><label>Agent mode</label><div className="mode-picker">{[['llm', '✦', 'Custom'], ['codex', '⌘', 'Codex']].map(([value, icon, label]) => <button type="button" key={value} className={`mode-chip ${mode === value ? 'active' : ''}`} onClick={() => setMode(value)}><span>{icon}</span>{label}</button>)}</div></div>
        <div className="form-row"><Field label="File dữ liệu normal" name="normal" value={form.normal} onChange={updateForm} required /><Field label="File dữ liệu attack" name="attack" value={form.attack} onChange={updateForm} required /></div><div className="hint">Nhập đường dẫn bên trong container. Ví dụ: /app/data/normal.txt</div>
        <div className="form-row top-gap"><Field label="Số trial tối đa" name="maxTrials" type="number" min="1" max="100" value={form.maxTrials} onChange={updateForm} /><Field label="Accuracy mục tiêu" name="target" type="number" min="0" max="1" step="0.01" value={form.target} onChange={updateForm} /></div>
        <div className="form-actions"><button type="button" className="secondary" onClick={() => setDialog(false)}>Hủy</button><button disabled={busy}>{busy ? 'Đang gửi…' : 'Bắt đầu training'}</button></div>
      </form></section></div>}
    {toast && <div className="toast">{toast}</div>}
  </>;
}

function Stat({ label, value, note }) { return <div className="card stat"><label>{label}</label><strong>{value}</strong><small>{note}</small></div>; }
function Status({ value }) { return <span className={`status ${value}`}>{statusNames[value] || value}</span>; }
function Field({ label, ...props }) { return <div className="field"><label>{label}</label><input {...props} /></div>; }
function TrialChart({ trials }) {
  const metrics = Object.keys(trials.find(trial => trial.metrics)?.metrics || {});
  const [metric, setMetric] = useState(metrics.includes('f1') ? 'f1' : metrics[0]);
  useEffect(() => { if (!metrics.includes(metric) && metrics.length) setMetric(metrics.includes('f1') ? 'f1' : metrics[0]); }, [trials, metric]);
  const values = trials.filter(trial => Number.isFinite(Number(trial.metrics?.[metric])));
  const groups = Object.groupBy ? Object.groupBy(values, trial => trial.model || 'model') : values.reduce((acc, trial) => ((acc[trial.model || 'model'] ||= []).push(trial), acc), {});
  const all = values.map(trial => Number(trial.metrics[metric]));
  const low = Math.min(0, ...all), high = Math.max(1, ...all), span = high - low || 1;
  const x = (index, length) => 42 + (length < 2 ? 0 : index * 410 / (length - 1));
  const y = value => 145 - ((Number(value) - low) / span) * 120;
  const colors = ['#245eea', '#13845b', '#b26a12'];
  return <section className="trial-chart"><div className="chart-heading"><div><h3>Tiến trình theo trial</h3><span>{values.length} trial có metric</span></div><select aria-label="Metric biểu đồ" value={metric || ''} onChange={event => setMetric(event.target.value)}>{metrics.map(name => <option key={name} value={name}>{name}</option>)}</select></div>
    {values.length ? <><svg viewBox="0 0 480 180" role="img" aria-label={`Biểu đồ ${metric} theo trial`}><line x1="42" y1="25" x2="42" y2="145"/><line x1="42" y1="145" x2="460" y2="145"/>{[0, .5, 1].map(tick => { const val=low+span*tick; const yy=y(val); return <g key={tick}><line className="gridline" x1="42" y1={yy} x2="460" y2={yy}/><text x="36" y={yy+3} textAnchor="end">{val.toFixed(2)}</text></g>})}{Object.entries(groups).map(([name, series], groupIndex) => { const color=colors[groupIndex%colors.length];return <g key={name}><polyline fill="none" stroke={color} strokeWidth="2" points={series.map((trial,index)=>`${x(index,series.length)},${y(trial.metrics[metric])}`).join(' ')}/>{series.map((trial,index)=><circle key={`${name}-${index}`} cx={x(index,series.length)} cy={y(trial.metrics[metric])} r="4" fill={color}><title>{name} · trial {index+1}: {Number(trial.metrics[metric]).toFixed(4)}</title></circle>)}</g>})}</svg><div className="chart-legend">{Object.keys(groups).map((name,index)=><span key={name}><i style={{background:colors[index%colors.length]}}/>{name}</span>)}</div></> : <div className="muted">Chưa có điểm dữ liệu.</div>}
  </section>;
}

createRoot(document.getElementById('root')).render(<App />);
