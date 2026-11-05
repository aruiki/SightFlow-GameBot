import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './store'

const PRESET_MODELS = ['yolo11n.pt', 'yolo11s.pt', 'yolov8n.pt', 'yolov8s.pt']

function colorOf(cls) {
  return `hsl(${(Number(cls) * 67) % 360} 85% 62%)`
}

export default function DatasetPanel() {
  const [name, setName] = useState('dataset')
  const [info, setInfo] = useState(null)
  const [selected, setSelected] = useState(null)
  const [frame, setFrame] = useState(null)
  const [boxes, setBoxes] = useState([])
  const [cls, setCls] = useState(0)
  const [drag, setDrag] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const [models, setModels] = useState([])
  const [model, setModel] = useState('yolo11n.pt')
  const [epochs, setEpochs] = useState(40)
  const [imgsz, setImgsz] = useState(960)
  const [batch, setBatch] = useState(8)
  const [train, setTrain] = useState(null)
  const [record, setRecord] = useState(null)
  const [recInterval, setRecInterval] = useState(2)
  const canvasRef = useRef(null)

  const flash = (t) => {
    setToast(t)
    setTimeout(() => setToast(null), 1800)
  }

  const loadModels = useCallback(() => {
    api.get('/api/models').then((r) => setModels(r.models || [])).catch(() => {})
  }, [])

  const load = useCallback(async (datasetName) => {
    try {
      const r = await api.get(`/api/dataset?name=${encodeURIComponent(datasetName)}`)
      if (r.ok === false) throw new Error(r.error || 'データセットを開けません')
      setInfo(r)
      setSelected((prev) => (r.frames.some((f) => f.id === prev)
        ? prev : (r.frames[0]?.id || null)))
    } catch (e) {
      setError(String(e.message || e))
    }
  }, [])

  const loadFrame = useCallback(async (id, datasetName) => {
    if (!id) {
      setFrame(null)
      setBoxes([])
      return
    }
    try {
      const r = await api.get(`/api/dataset/frame?name=${encodeURIComponent(datasetName)}&frame=${encodeURIComponent(id)}`)
      if (r.ok === false) throw new Error(r.error || 'フレームを開けません')
      setFrame(r)
      setBoxes(r.boxes || [])
      setCls((c) => Math.min(c, Math.max(0, (r.classes || []).length - 1)))
    } catch (e) {
      setError(String(e.message || e))
    }
  }, [])

  useEffect(() => {
    api.get('/api/state').then((st) => {
      const n = String(st.project_file || 'dataset').replace(/\.json$/i, '') || 'dataset'
      setName(n)
      load(n)
    }).catch(() => load('dataset'))
    loadModels()
    api.get('/api/train').then(setTrain).catch(() => {})
    api.get('/api/dataset/record').then(setRecord).catch(() => {})
  }, [load, loadModels])

  useEffect(() => {
    if (selected) loadFrame(selected, info?.name || name)
  }, [selected, info?.name, name, loadFrame])

  // ---------------------------------------------------------------- canvas
  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || !frame) return
    cv.width = frame.width
    cv.height = frame.height
    const ctx = cv.getContext('2d')
    const img = new Image()
    img.onload = () => {
      ctx.drawImage(img, 0, 0, frame.width, frame.height)
      const draw = (b, dashed = false) => {
        const x = (b.x - b.w / 2) * frame.width
        const y = (b.y - b.h / 2) * frame.height
        const w = b.w * frame.width
        const h = b.h * frame.height
        ctx.strokeStyle = colorOf(b.cls)
        ctx.fillStyle = colorOf(b.cls)
        ctx.lineWidth = Math.max(2, frame.width / 400)
        if (dashed) ctx.setLineDash([6, 4])
        ctx.strokeRect(x, y, w, h)
        ctx.setLineDash([])
        const label = (frame.classes || [])[b.cls] ?? String(b.cls)
        const tw = ctx.measureText(label).width + 8
        ctx.fillRect(x, Math.max(0, y - 18), tw, 18)
        ctx.fillStyle = '#08111a'
        ctx.fillText(label, x + 4, Math.max(12, y - 5))
      }
      boxes.forEach((b) => draw(b))
      if (drag) {
        draw({ cls, x: (drag.x0 + drag.x1) / 2, y: (drag.y0 + drag.y1) / 2,
               w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) }, true)
      }
    }
    img.src = `data:image/jpeg;base64,${frame.image}`
  }, [frame, boxes, drag, cls])

  const point = (e) => {
    const cv = canvasRef.current
    const r = cv.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    }
  }

  // --------------------------------------------------------------- actions
  const capture = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.post('/api/dataset/capture', { dataset: name })
      if (!r.ok) throw new Error(r.error || 'キャプチャできません（見るだけにする）')
      await load(name)
      setSelected(r.id)
      flash('フレームを保存しました')
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  const saveLabels = async () => {
    if (!selected) return
    setBusy(true); setError(null)
    try {
      const r = await api.put('/api/dataset/labels', { dataset: name, frame: selected, boxes })
      if (!r.ok) throw new Error(r.error || '保存できません')
      await load(name)
      flash(`保存しました（${r.saved}箱）`)
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  const autolabel = async (all = false) => {
    setBusy(true); setError(null)
    try {
      const r = await api.post('/api/dataset/autolabel', { dataset: name, frame: all ? undefined : selected })
      if (!r.ok) throw new Error(r.error || '自動ラベルできません')
      await load(name)
      if (selected) await loadFrame(selected)
      flash(`自動ラベル: ${r.frames}枚 / ${r.boxes}箱`)
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  const removeFrame = async () => {
    if (!selected || !window.confirm('このフレームを削除しますか？')) return
    const r = await api.del(`/api/dataset/frame?name=${encodeURIComponent(name)}&frame=${encodeURIComponent(selected)}`)
    if (!r.ok) { setError('削除できません'); return }
    setSelected(null); setFrame(null); setBoxes([])
    await load(name)
  }

  const startRecord = async () => {
    setError(null)
    const r = await api.post('/api/dataset/record', {
      action: 'start', dataset: name, interval: Number(recInterval),
    })
    if (!r.ok) { setError(r.error || '自動収集を開始できません'); return }
    setRecord(r)
    flash('自動収集を開始しました（見るだけ/操作するで動作）')
  }

  const stopRecord = async () => {
    setError(null)
    const r = await api.post('/api/dataset/record', { action: 'stop' })
    if (!r.ok) { setError(r.error || '停止できません'); return }
    setRecord(r)
    await load(name)
    flash(`自動収集を停止（${r.count}枚）`)
  }

  useEffect(() => {
    if (!record?.running) return
    const id = setInterval(async () => {
      try {
        const r = await api.get('/api/dataset/record')
        setRecord(r)
        if (!r.running) await load(name)
      } catch { /* keep polling */ }
    }, 1000)
    return () => clearInterval(id)
  }, [record?.running, load, name])

  const exportYaml = async () => {
    const r = await api.get(`/api/dataset/export?name=${encodeURIComponent(name)}`)
    if (!r.ok) { setError(r.error || '書き出せません'); return }
    flash(r.path)
  }

  const startTrain = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.post('/api/train', {
        dataset: name, model, epochs: Number(epochs), imgsz: Number(imgsz), batch: Number(batch),
      })
      if (!r.ok) throw new Error(r.error || '学習を開始できません')
      setTrain(r)
      flash('学習を開始しました')
    } catch (e) { setError(String(e.message || e)) } finally { setBusy(false) }
  }

  useEffect(() => {
    if (!train || train.status !== 'running') return
    const id = setInterval(async () => {
      try {
        const r = await api.get('/api/train')
        setTrain(r)
        if (r.status !== 'running') {
          loadModels()
          load(name)
        }
      } catch { /* keep polling */ }
    }, 1000)
    return () => clearInterval(id)
  }, [train?.status, load, loadModels, name])

  const classes = info?.classes || frame?.classes || []
  const stats = info || { images: 0, labeled: 0, boxes: 0, train: 0, val: 0 }
  const modelOptions = [...new Set([...PRESET_MODELS, ...models.map((m) => m.name), model])]

  return (
    <div className="settings dataset-panel">
      <section>
        <h3>データセット</h3>
        <div className="row">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="dataset" />
          <button className="ghost" onClick={() => load(name)}>開く</button>
        </div>
        <div className="ds-stats">
          <span>画像 <b>{stats.images}</b></span>
          <span>注釈済み <b>{stats.labeled}</b></span>
          <span>箱 <b>{stats.boxes}</b></span>
          <span>train/val <b>{stats.train}/{stats.val}</b></span>
        </div>
        <div className="row">
          <button className="primary" onClick={capture} disabled={busy}>今のフレームを保存</button>
          <button className="ghost" onClick={() => autolabel(false)} disabled={busy || !selected}>
            選択を自動ラベル
          </button>
          <button className="ghost" onClick={() => autolabel(true)} disabled={busy || !stats.images}>
            全件を自動ラベル
          </button>
          <button className="ghost" onClick={exportYaml} disabled={!stats.images}>data.yaml</button>
        </div>
        <div className="row">
          <span className="lbl">自動収集 間隔(秒)</span>
          <input type="number" min="0.2" max="60" step="0.2" value={recInterval}
            onChange={(e) => setRecInterval(e.target.value)} />
          {record?.running
            ? <button className="ghost danger" onClick={stopRecord}>停止</button>
            : <button className="ghost" onClick={startRecord}>開始</button>}
          <span className="hint">
            {record?.running ? `収集中 ${record.count}枚` : '見るだけ/操作するで一定間隔ごとに保存'}
          </span>
        </div>
        <p className="hint">自動ラベルは「見るだけ」で読み込んだ検出器を使います。色検出の位置をYOLO形式に変換します。</p>
        {error && <p className="error">{error}</p>}
        {toast && <p className="ok-msg">{toast}</p>}
      </section>

      <section>
        <h3>フレーム</h3>
        {(!info || info.frames.length === 0) && <p className="hint">まだフレームがありません。見るだけで画面を出して「今のフレームを保存」。</p>}
        <div className="frame-strip">
          {(info?.frames || []).map((f) => (
            <button key={f.id} className={`frame-thumb ${f.id === selected ? 'on' : ''}`}
              onClick={() => setSelected(f.id)} title={`${f.split} / ${f.boxes}箱`}>
              {f.thumbnail ? <img src={`data:image/jpeg;base64,${f.thumbnail}`} alt={f.id} /> : <span />}
              <i>{f.boxes}</i>
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3>注釈</h3>
        {!frame && <p className="hint">フレームを選ぶと、ここで箱を描けます。</p>}
        {frame && (
          <>
            <div className="annotate-wrap">
              <canvas ref={canvasRef}
                onMouseDown={(e) => { const p = point(e); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }) }}
                onMouseMove={(e) => { if (drag) { const p = point(e); setDrag({ ...drag, x1: p.x, y1: p.y }) } }}
                onMouseUp={(e) => {
                  if (!drag) return
                  const p = point(e)
                  const x0 = Math.min(drag.x0, p.x), x1 = Math.max(drag.x0, p.x)
                  const y0 = Math.min(drag.y0, p.y), y1 = Math.max(drag.y0, p.y)
                  if (x1 - x0 > 0.01 && y1 - y0 > 0.01) {
                    setBoxes((bs) => [...bs, { cls, x: (x0 + x1) / 2, y: (y0 + y1) / 2, w: x1 - x0, h: y1 - y0 }])
                  }
                  setDrag(null)
                }}
                onMouseLeave={() => setDrag(null)}
              />
            </div>
            <div className="row">
              <span className="lbl">次の箱のクラス</span>
              <select value={cls} onChange={(e) => setCls(Number(e.target.value))}>
                {classes.map((c, i) => <option key={c} value={i}>{c}</option>)}
              </select>
              <button className="primary" onClick={saveLabels} disabled={busy}>ラベルを保存</button>
              <button className="ghost danger" onClick={removeFrame}>フレーム削除</button>
            </div>
            <div className="box-list">
              {boxes.map((b, i) => (
                <div className="box-row" key={i}>
                  <select value={b.cls} onChange={(e) => {
                    const v = Number(e.target.value)
                    setBoxes((bs) => bs.map((x, j) => (j === i ? { ...x, cls: v } : x)))
                  }}>
                    {classes.map((c, j) => <option key={c} value={j}>{c}</option>)}
                  </select>
                  <span>x {b.x.toFixed(2)} y {b.y.toFixed(2)} w {b.w.toFixed(2)} h {b.h.toFixed(2)}</span>
                  <button className="ghost" onClick={() => setBoxes((bs) => bs.filter((_, j) => j !== i))}>✕</button>
                </div>
              ))}
              {boxes.length === 0 && <p className="hint">箱がありません。ドラッグで追加。</p>}
            </div>
          </>
        )}
      </section>

      <section>
        <h3>学習</h3>
        <div className="row">
          <span className="lbl">モデル</span>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="row">
          <span className="lbl">epochs / imgsz / batch</span>
          <input type="number" min="1" max="300" value={epochs} onChange={(e) => setEpochs(e.target.value)} />
          <input type="number" min="320" max="1920" step="32" value={imgsz} onChange={(e) => setImgsz(e.target.value)} />
          <input type="number" min="1" max="64" value={batch} onChange={(e) => setBatch(e.target.value)} />
        </div>
        <div className="row">
          <button className="primary" onClick={startTrain}
            disabled={busy || stats.images === 0 || train?.status === 'running'}>
            {train?.status === 'running' ? '学習中…' : '学習を開始'}
          </button>
        </div>
        {train && train.status !== 'idle' && (
          <div className={`train-state ${train.status}`}>
            <div className="progress"><i style={{ width: `${Math.round((train.progress || 0) * 100)}%` }} /></div>
            <div className="train-msg">
              <b>{train.status}</b> {train.message}
              {train.model && <span> / models/{train.model}</span>}
            </div>
          </div>
        )}
        <p className="hint">学習結果は models/&lt;dataset&gt;.pt に保存されます。設定 → 学習済みYOLO で選べます。</p>
      </section>
    </div>
  )
}

