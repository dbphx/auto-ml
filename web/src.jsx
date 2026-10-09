import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import './results.css';
import './modal.css';
import './history.css';
import './s3.css';
import './s3-selection.css';

const statusNames = { queued: 'Đang chờ', running: 'Đang chạy', retrying: 'Thử lại', completed: 'Hoàn thành', failed: 'Lỗi', agent_tuning: 'Agent đang tối ưu', agent_recommendation: 'Agent đề xuất tham số', category_tests: 'Đang kiểm tra category' };
const date = value => value ? new Date(value).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
const statusFilters = [['', 'Tất cả'], ['queued', 'Đang chờ'], ['running', 'Đang chạy'], ['retrying', 'Thử lại'], ['completed', 'Hoàn thành'], ['failed', 'Lỗi']];

function App() {
  const [tasks, setTasks] = useState([]);
  const [filter, setFilter] = useState('');
  const [query, setQuery] = useState('');
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState(null);
  const [dialog, setDialog] = useState(false);
  const [mode, setMode] = useState('llm');
  const [llmSource, setLlmSource] = useState('internal');
  const [inputSource, setInputSource] = useState('local');
  const [outputSource, setOutputSource] = useState('local');
  const [s3Config, setS3Config] = useState({ has_credentials: false, region: '', endpoint_url: '', access_key_hint: '' });
  const [s3Draft, setS3Draft] = useState({ region: '', endpoint_url: '', access_key_id: '', secret_access_key: '', session_token: '' });
  const [s3SettingsOpen, setS3SettingsOpen] = useState(false);
  const [s3Buckets, setS3Buckets] = useState([]);
  const [s3Bucket, setS3Bucket] = useState('');
  const [s3Browse, setS3Browse] = useState(null);
  const [s3Uri, setS3Uri] = useState('');
  const [s3Files, setS3Files] = useState({ normal: '', attack: '' });
  const [s3PickTarget, setS3PickTarget] = useState('normal');
  const [outputS3Uri, setOutputS3Uri] = useState('');
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
  useEffect(() => { setPage(1); }, [filter, query, pageSize]);
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
  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const pageTasks = visible.slice((page - 1) * pageSize, page * pageSize);
  const updateForm = event => setForm(current => ({ ...current, [event.target.name]: event.target.value }));

  async function loadS3Config() {
    try {
      const response = await fetch('/settings/s3');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không tải được cấu hình S3');
      setS3Config(data);
      setS3Draft(current => ({ ...current, region: data.region || '', endpoint_url: data.endpoint_url || '' }));
      return data;
    } catch (error) { notify(error.message); return null; }
  }

  useEffect(() => { loadS3Config(); }, []);

  async function openS3Settings() {
    await loadS3Config();
    setS3SettingsOpen(true);
  }

  async function saveS3Settings(event) {
    event.preventDefault(); setBusy(true);
    try {
      const response = await fetch('/settings/s3', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(s3Draft) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không lưu được cấu hình S3');
      setS3Config(data); setS3SettingsOpen(false); notify('Đã lưu cấu hình S3');
      if (data.has_credentials && dialog && (inputSource === 's3' || outputSource === 's3')) await loadS3Buckets();
    } catch (error) { notify(error.message); }
    finally { setBusy(false); }
  }

  async function loadS3Buckets() {
    try {
      const response = await fetch('/s3/buckets');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không tải được danh sách bucket');
      setS3Buckets(data.buckets || []);
      const first = data.buckets?.[0] || '';
      setS3Bucket(first); setS3Browse(null);
      if (first) await browseS3(first, '');
    } catch (error) { setS3Buckets([]); setS3Browse(null); notify(error.message); }
  }

  async function browseS3(bucket, prefix) {
    if (!bucket) return;
    try {
      const query = new URLSearchParams({ bucket, prefix: prefix || '' });
      const response = await fetch(`/s3/browse?${query}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không duyệt được S3');
      setS3Bucket(bucket); setS3Browse(data);
    } catch (error) { setS3Browse(null); setS3Uri(''); notify(error.message); }
  }

  async function selectInputSource(source) {
    setInputSource(source); setS3Uri(''); setS3Files({ normal: '', attack: '' }); setS3PickTarget('normal');
    if (source === 's3') {
      const settings = await loadS3Config();
      if (!settings?.has_credentials) { setS3SettingsOpen(true); return; }
      await loadS3Buckets();
    }
  }

  async function selectOutputSource(source) {
    setOutputSource(source); setOutputS3Uri('');
    if (source === 's3') {
      const settings = await loadS3Config();
      if (!settings?.has_credentials) { setS3SettingsOpen(true); return; }
      if (!s3Buckets.length) await loadS3Buckets();
    }
  }

  async function createJob(event) {
    event.preventDefault(); setBusy(true);
    try {
      const response = await fetch('/hook', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        mode, 'input-path': inputSource === 's3' ? (s3Uri || { normal: s3Files.normal, attack: s3Files.attack }) : { normal: form.normal.trim(), attack: form.attack.trim() },
        ...(inputSource === 's3' ? { allow_local_fallback: false } : {}), 'output-path': '/app/output',
        ...(outputSource === 's3' ? { output_s3: outputS3Uri } : {}),
        ...(mode === 'codex' ? { llm: llmSource } : {}),
        max_trials: Number(form.maxTrials), category_target_accuracy: Number(form.target),
      }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không tạo được job');
      setDialog(false); setMode('llm'); setLlmSource('internal'); setInputSource('local'); setOutputSource('local'); setS3Uri(''); setOutputS3Uri(''); setSelectedId(data.job_id); notify('Đã thêm job vào hàng đợi'); await refresh();
    } catch (error) { notify(error.message); }
    finally { setBusy(false); }
  }

  async function clearHistory() {
    const finishedCount = counts.completed + counts.failed;
    if (!finishedCount || !window.confirm(`Xóa ${finishedCount} job đã kết thúc khỏi lịch sử? Job đang chờ hoặc đang chạy sẽ được giữ lại.`)) return;
    try {
      const response = await fetch('/tasks', { method: 'DELETE' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không xóa được lịch sử');
      setSelectedId(''); setPage(1); notify(`Đã xóa ${data.deleted} job khỏi lịch sử`); await refresh();
    } catch (error) { notify(error.message); }
  }

  async function deleteTask(event, task) {
    event.stopPropagation();
    if (!['completed', 'failed'].includes(task.status)) return;
    if (!window.confirm(`Xóa job ${task.job_id.slice(0, 12)} khỏi lịch sử?`)) return;
    try {
      const response = await fetch(`/tasks/${encodeURIComponent(task.job_id)}`, { method: 'DELETE' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không xóa được job');
      if (selectedId === task.job_id) setSelectedId('');
      notify('Đã xóa job khỏi lịch sử'); await refresh();
    } catch (error) { notify(error.message); }
  }

  const job = tasks.find(task => task.job_id === selectedId);
  const process = detail?.process || {};
  const result = detail?.result;
  const events = [...(detail?.events || [])].reverse();
  const trialPoints = result?.trials || (detail?.events || []).filter(event => event.trial && event.metrics).map(event => ({ model: event.model, metrics: event.metrics }));
  const progress = task => [task.process?.model, task.process?.trial && `Trial ${task.process.trial}`, task.process?.round && `Round ${task.process.round}`].filter(Boolean).join(' · ') || task.process?.message || '—';

  return <>
    <header><div className="brand"><span className="mark">✳</span> Auto ML <span className="slash">/ Management</span></div><div className="header-actions"><button className="secondary s3-settings-button" onClick={openS3Settings}>☁ S3 {s3Config.has_credentials ? '✓' : '⚙'}</button><div className="live"><i />Đang cập nhật · tối đa 4 worker</div></div></header>
    <main>
      <div className="heading"><div><div className="eyebrow">Training operations</div><h1><span className="mark heading-mark" aria-hidden="true">✦</span> Quản lý training</h1><div className="sub">Theo dõi các job và kết quả huấn luyện.</div></div><button onClick={() => setDialog(true)}>＋ Tạo job</button></div>
      <section className="stats">
        <Stat label="Tổng số job" value={counts.total} note="Lịch sử lưu trong SQLite" />
        <Stat label="Đang chạy" value={counts.running} note="Tối đa 4 job cùng lúc" />
        <Stat label="Đang chờ" value={counts.queued} note="Sẽ chạy khi worker sẵn sàng" />
        <Stat label="Hoàn thành" value={counts.completed} note={`${counts.failed} job lỗi`} />
      </section>
      <div className="layout">
        <section className="card"><div className="panel-head"><h2>Danh sách job</h2><div className="list-actions"><button className="danger-quiet" onClick={clearHistory} disabled={!counts.completed && !counts.failed}>Xóa lịch sử</button><button className="secondary refresh" onClick={refresh} title="Làm mới">↻</button></div></div>
          <div className="filters">{statusFilters.map(([value, label]) => <button key={value} className={`chip ${filter === value ? 'active' : ''}`} onClick={() => setFilter(value)}>{label}</button>)}
            <label className="search"><span>⌕</span><input type="search" placeholder="Tìm job, model…" value={query} onChange={event => setQuery(event.target.value)} /></label>
          </div>
          <div className="table-wrap"><table><thead><tr><th>Job</th><th>Trạng thái</th><th>Tiến trình</th><th>Thời gian</th><th aria-label="Thao tác" /></tr></thead><tbody>
            {pageTasks.length ? pageTasks.map(task => <tr key={task.job_id} className={`job ${selectedId === task.job_id ? 'selected' : ''}`} onClick={() => setSelectedId(task.job_id)}>
              <td><div className="id">{task.job_id.slice(0, 12)}…</div><small>{date(task.created_at)}</small></td><td><Status value={task.status} /></td><td className="progress">{progress(task)}</td><td className="time">{date(task.updated_at)}</td><td className="row-action"><button className="delete-row" aria-label={`Xóa job ${task.job_id}`} title={['completed', 'failed'].includes(task.status) ? 'Xóa khỏi lịch sử' : 'Chỉ xóa job đã kết thúc'} disabled={!['completed', 'failed'].includes(task.status)} onClick={event => deleteTask(event, task)}>×</button></td>
            </tr>) : <tr><td colSpan="5" className="empty">{query || filter ? 'Không tìm thấy job phù hợp.' : 'Chưa có job nào. Tạo job đầu tiên để bắt đầu.'}</td></tr>}
          </tbody></table></div>
          <div className="pager"><span>{visible.length ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, visible.length)} / ${visible.length}` : '0 kết quả'}</span><div className="pager-controls"><label>Số dòng<select value={pageSize} onChange={event => setPageSize(Number(event.target.value))}><option value="10">10</option><option value="20">20</option><option value="50">50</option><option value="100">100</option></select></label><button className="secondary page-button" disabled={page <= 1} onClick={() => setPage(current => Math.max(1, current - 1))}>‹</button><span className="page-number">{page} / {pageCount}</span><button className="secondary page-button" disabled={page >= pageCount} onClick={() => setPage(current => Math.min(pageCount, current + 1))}>›</button></div></div>
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
          <div className="result-meta"><span>Chọn theo</span><strong>{result.selection?.metric || '—'} · {result.selection?.score?.toFixed?.(4) ?? '—'}</strong><span>Category test đạt mục tiêu</span><strong>{result.target_met ? 'Có' : 'Không'}</strong></div>
          {result.best.params && <details className="params"><summary>Hyperparameter tốt nhất</summary><pre>{JSON.stringify(result.best.params, null, 2)}</pre></details>}
          {result.category_tests && <div className="category-results"><h4>Category test</h4>{Object.entries(result.category_tests).map(([name, value]) => <div key={name}><strong>{name}</strong><span>{value.status === 'completed' ? `${value.passed}/${value.total} · ${(value.accuracy * 100).toFixed(1)}%` : value.status || '—'}</span></div>)}</div>}
          {result.artifacts && <div className="artifact-path">Artifacts: <code>{result.artifacts}</code></div>}
        </section>}
        <div className="events"><h3>Lịch sử cập nhật</h3>{events.length ? events.map((event, index) => <div className="event" key={`${event.timestamp}-${index}`}><time>{date(event.timestamp)}</time><strong>{statusNames[event.status] || event.status || 'Cập nhật'}</strong>{event.message && ` · ${event.message}`}{event.result && <div>{JSON.stringify(event.result)}</div>}</div>) : <span className="muted">Chưa có cập nhật.</span>}</div>
      </div>}
    </section></div>}
    {dialog && <div className="overlay" onMouseDown={event => event.target === event.currentTarget && setDialog(false)}><section className="dialog"><div className="modal-head"><h2>Tạo job training</h2><button className="close" onClick={() => setDialog(false)}>×</button></div>
      <form className="form" onSubmit={createJob}><div className="field"><label>Agent mode</label><div className="mode-picker">{[['llm', '✦', 'Custom'], ['codex', '⌘', 'Codex']].map(([value, icon, label]) => <button type="button" key={value} className={`mode-chip ${mode === value ? 'active' : ''}`} onClick={() => setMode(value)}><span>{icon}</span>{label}</button>)}</div></div>
        {mode === 'codex' && <div className="field codex-source"><label>Nguồn model cho Codex</label><div className="mode-picker">{[['internal', '◉', 'Default'], ['external', '↗', 'External']].map(([value, icon, label]) => <button type="button" key={value} className={`mode-chip ${llmSource === value ? 'active' : ''}`} onClick={() => setLlmSource(value)}><span>{icon}</span>{label}</button>)}</div><div className="hint">Default dùng đăng nhập và model Codex. External dùng endpoint/API key trong môi trường; endpoint phải hỗ trợ OpenAI Responses API (/v1/responses).</div></div>}
        <div className="field"><label>Nguồn dữ liệu</label><div className="mode-picker">{[['local', '▣', 'Local'], ['s3', '☁', 'S3']].map(([value, icon, label]) => <button type="button" key={value} className={`mode-chip ${inputSource === value ? 'active' : ''}`} onClick={() => selectInputSource(value)}><span>{icon}</span>{label}</button>)}</div></div>
        {inputSource === 'local' && <><div className="form-row"><Field label="File dữ liệu normal" name="normal" value={form.normal} onChange={updateForm} required /><Field label="File dữ liệu attack" name="attack" value={form.attack} onChange={updateForm} required /></div><div className="hint">Nhập đường dẫn bên trong container. Ví dụ: /app/data/normal.txt</div></>}
        {inputSource === 's3' && <div className="s3-picker">
          {!s3Config.has_credentials ? <div className="s3-empty">Cần cấu hình credential để duyệt bucket. <button type="button" className="text-button" onClick={openS3Settings}>Cấu hình S3</button></div> : <>
            <div className="s3-file-fields"><button type="button" className={`s3-file-field ${s3PickTarget === 'normal' ? 'active' : ''}`} onClick={() => setS3PickTarget('normal')}><span>File Normal</span><strong>{s3Files.normal ? decodeURIComponent(s3Files.normal.split('/').pop()) : 'Chọn file Normal'}</strong></button><button type="button" className={`s3-file-field ${s3PickTarget === 'attack' ? 'active' : ''}`} onClick={() => setS3PickTarget('attack')}><span>File Attack</span><strong>{s3Files.attack ? decodeURIComponent(s3Files.attack.split('/').pop()) : 'Chọn file Attack'}</strong></button></div>
            <div className="s3-selection-hint">Chọn ô Normal hoặc Attack, sau đó chọn file trong danh sách bên dưới.</div>
            <div className="s3-browser-top"><label>Bucket<input list="s3-bucket-options" value={s3Bucket} onChange={event => setS3Bucket(event.target.value)} placeholder="Chọn hoặc nhập bucket" /><datalist id="s3-bucket-options">{s3Buckets.map(bucket => <option key={bucket} value={bucket} />)}</datalist></label><button type="button" className="secondary s3-browse-root" disabled={!s3Bucket} onClick={() => browseS3(s3Bucket, '')}>Duyệt</button><button type="button" className="secondary s3-up" disabled={!s3Browse?.parent} onClick={() => browseS3(s3Bucket, s3Browse.parent)}>↑ Lên</button></div>
            {s3Browse && <><div className="s3-current"><span>📁 {s3Bucket}/{s3Browse.prefix}</span><span>Chọn object bên dưới cho ô Normal hoặc Attack</span></div>
              <div className="s3-items">{s3Browse.folders.map(folder => <button type="button" className="s3-item folder" key={folder} onClick={() => browseS3(s3Bucket, folder)}><span>📁</span>{folder.slice(s3Browse.prefix.length)}</button>)}{s3Browse.files.map(file => { const objectKey = `${s3Browse.prefix}${file}`; const objectUri = `s3://${s3Bucket}/${objectKey.split('/').map(encodeURIComponent).join('/')}`; return <div className="s3-item file" key={file}><span>▤</span><span className="s3-filename">{file}</span><button type="button" className="s3-assign" disabled={s3Files[s3PickTarget === 'normal' ? 'attack' : 'normal'] === objectUri} onClick={() => { setS3Uri(''); setS3Files(current => ({ ...current, [s3PickTarget]: objectUri })); }}>Chọn</button></div>; })}{!s3Browse.folders.length && !s3Browse.files.length && <div className="s3-no-items">Thư mục trống</div>}</div>
              {outputSource === 's3' && <button type="button" className="s3-use-folder s3-output-use" onClick={() => setOutputS3Uri(`s3://${s3Bucket}/${s3Browse.prefix.split('/').filter(Boolean).map(encodeURIComponent).join('/')}`)}>Dùng thư mục này làm output</button>}
            </>}
            {s3Uri && <div className="s3-selected">Đã chọn prefix <code>{s3Uri}</code></div>}
          </>}
        </div>}
        <div className="field top-gap"><label>Nơi lưu kết quả</label><div className="mode-picker">{[['local', '▣', 'Local'], ['s3', '☁', 'S3']].map(([value, icon, label]) => <button type="button" key={value} className={`mode-chip ${outputSource === value ? 'active' : ''}`} onClick={() => selectOutputSource(value)}><span>{icon}</span>{label}</button>)}</div></div>
        {outputSource === 's3' && <>{inputSource === 'local' && <div className="s3-picker s3-output-picker">
          {!s3Config.has_credentials ? <div className="s3-empty">Cần cấu hình credential để chọn output. <button type="button" className="text-button" onClick={openS3Settings}>Cấu hình S3</button></div> : <>
            <div className="s3-browser-top"><label>Bucket<input list="s3-output-bucket-options" value={s3Bucket} onChange={event => setS3Bucket(event.target.value)} placeholder="Chọn hoặc nhập bucket" /><datalist id="s3-output-bucket-options">{s3Buckets.map(bucket => <option key={bucket} value={bucket} />)}</datalist></label><button type="button" className="secondary s3-browse-root" disabled={!s3Bucket} onClick={() => browseS3(s3Bucket, '')}>Duyệt</button><button type="button" className="secondary s3-up" disabled={!s3Browse?.parent} onClick={() => browseS3(s3Bucket, s3Browse.parent)}>↑ Lên</button></div>
            {s3Browse && <><div className="s3-current"><span>📁 {s3Bucket}/{s3Browse.prefix}</span><span>Chọn prefix đích cho artifacts</span></div><div className="s3-items">{s3Browse.folders.map(folder => <button type="button" className="s3-item folder" key={folder} onClick={() => browseS3(s3Bucket, folder)}><span>📁</span>{folder.slice(s3Browse.prefix.length)}</button>)}{!s3Browse.folders.length && <div className="s3-no-items">Không có thư mục con.</div>}</div><button type="button" className="s3-use-folder s3-output-use" onClick={() => setOutputS3Uri(`s3://${s3Bucket}/${s3Browse.prefix.split('/').filter(Boolean).map(encodeURIComponent).join('/')}`)}>Dùng thư mục này làm output</button></>}
          </>}
        </div>}<div className="s3-selected">{outputS3Uri ? <>Kết quả lưu tại <code>{outputS3Uri}/&lt;job_id&gt;</code></> : <span>Chưa chọn thư mục output.</span>}</div></>}
        <div className="form-row top-gap"><Field label="Số trial tối đa" name="maxTrials" type="number" min="1" max="100" value={form.maxTrials} onChange={updateForm} /><Field label="Ngưỡng category test" name="target" type="number" min="0" max="1" step="0.01" value={form.target} onChange={updateForm} /></div>
        <div className="form-actions"><button type="button" className="secondary" onClick={() => setDialog(false)}>Hủy</button><button disabled={busy || (inputSource === 's3' && !s3Uri && !(s3Files.normal && s3Files.attack))}>{busy ? 'Đang gửi…' : 'Bắt đầu training'}</button></div>
      </form></section></div>}
    {s3SettingsOpen && <div className="overlay s3-settings-overlay" onMouseDown={event => event.target === event.currentTarget && setS3SettingsOpen(false)}><section className="dialog s3-settings-dialog"><div className="modal-head"><div><h2>Cấu hình S3</h2><span className="s3-settings-sub">Credential được lưu trên máy chủ, không hiển thị lại.</span></div><button className="close" onClick={() => setS3SettingsOpen(false)}>×</button></div><form className="form" onSubmit={saveS3Settings}>
      <div className="form-row"><Field label="Access key ID" name="access_key_id" autoComplete="off" value={s3Draft.access_key_id} onChange={event => setS3Draft(current => ({ ...current, access_key_id: event.target.value }))} placeholder={s3Config.access_key_hint || 'Nhập access key'} /><Field label="Secret access key" name="secret_access_key" type="password" autoComplete="new-password" value={s3Draft.secret_access_key} onChange={event => setS3Draft(current => ({ ...current, secret_access_key: event.target.value }))} placeholder={s3Config.has_credentials ? 'Để trống để giữ key đã lưu' : 'Nhập secret key'} /></div>
      <div className="form-row"><Field label="Region" name="region" value={s3Draft.region} onChange={event => setS3Draft(current => ({ ...current, region: event.target.value }))} placeholder="ap-southeast-1" /><Field label="S3 endpoint (tùy chọn)" name="endpoint_url" value={s3Draft.endpoint_url} onChange={event => setS3Draft(current => ({ ...current, endpoint_url: event.target.value }))} placeholder="https://s3.example.com" /></div>
      <Field label="Session token (tùy chọn)" name="session_token" type="password" autoComplete="off" value={s3Draft.session_token} onChange={event => setS3Draft(current => ({ ...current, session_token: event.target.value }))} />
      <div className="hint">Để trống key đang lưu nếu không muốn thay đổi. Có thể cấu hình endpoint cho S3-compatible storage.</div><div className="form-actions"><button type="button" className="secondary" onClick={() => setS3SettingsOpen(false)}>Đóng</button><button disabled={busy}>{busy ? 'Đang lưu…' : 'Lưu cấu hình'}</button></div>
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
