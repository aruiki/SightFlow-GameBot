import { useCallback, useEffect, useRef, useState } from 'react'
import { api, metaStore, useLive } from './store'

const METHODS = [
  ['template', 'テンプレート一致'],
  ['phash', 'pHash'],
  ['ssim', 'SSIM'],
]

function ScreenRow({ s, score, current, onChange, onDelete, onRecapture, onAddTemplate, onRemoveTemplate }) {
  const [threshold, setThreshold] = useState(s.threshold)
  useEffect(() => setThreshold(s.threshold), [s.threshold])
  return (
    <div className={`screen-item ${current ? 'current' : ''}`}>
      {s.thumbnail
        ? <img className="screen-thumb" src={`data:image/jpeg;base64,${s.thumbnail}`} alt={s.name} />
        : <div className="screen-thumb empty">no image</div>}
      <div className="screen-info">
        <div className="screen-name">
          {s.name}
          {current && <span className="badge">今の画面</span>}
        </div>
        <div className="score-bar"><i style={{ width: `${Math.round((score ?? 0) * 100)}%` }} /></div>
        <div className="screen-meta">
          <span>一致 {Math.round((score ?? 0) * 100)}%</span>
          <span>しきい値 {Math.round((threshold ?? 0) * 100)}%</span>
          <span>テンプレート {s.template_count ?? s.templates?.length ?? 1}枚</span>
        </div>
      </div>
      <div className="screen-actions">
        <input type="range" min="0.5" max="0.99" step="0.01" value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          onMouseUp={() => onChange(s.id, { threshold: Number(threshold) })}
          onTouchEnd={() => onChange(s.id, { threshold: Number(threshold) })} />
        <select value={s.method} onChange={(e) => onChange(s.id, { method: e.target.value })}>
          {METHODS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
        <button className="ghost" onClick={() => onRecapture(s.id)}>今の画面で更新</button>
        <button className="ghost" onClick={() => onAddTemplate(s.id)}>＋テンプレート</button>
        {(s.template_count ?? s.templates?.length ?? 1) > 1 && (
          <button className="ghost"
            onClick={() => onRemoveTemplate(s.id, (s.template_count ?? s.templates.length) - 1)}>
            最後を削除
          </button>
        )}
        <button className="ghost danger" onClick={() => onDelete(s)}>削除</button>
      </div>
    </div>
  )
}

export default function ScreenPanel() {
  const live = useLive()
  const [screens, setScreens] = useState([])
  const [current, setCurrent] = useState(null)
  const [name, setName] = useState('')
  const [method, setMethod] = useState('template')
  const [threshold, setThreshold] = useState(0.85)
  const [enterFrames, setEnterFrames] = useState(2)
  const [exitFrames, setExitFrames] = useState(2)
  const [roiText, setRoiText] = useState('')
  const [preview, setPreview] = useState(null)
  const [roiDrag, setRoiDrag] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const canvasRef = useRef(null)

  const flash = (t) => {
    setToast(t)
    setTimeout(() => setToast(null), 1800)
  }

  const load = useCallback(async () => {
    try {
      const r = await api.get('/api/screens')
      setScreens(r.screens || [])
      setCurrent(r.current || null)
      metaStore.set((m) => ({ ...m, screens: r.screens || [] }))
    } catch (e) {
      setError(String(e.message || e))
    }
  }, [])

  useEffect(() => { load() }, [load])

  const scores = live?.screen?.scores || current?.scores || {}
  const currentId = live?.screen?.current ?? current?.current ?? null

  const parseRoi = () => {
    const v = roiText.split(',').map((x) => Number(x.trim()))
    return v.length === 4 && v.every((x) => Number.isFinite(x)) ? v : null
  }

  const loadPreview = useCallback(async () => {
    try {
      const r = await api.get('/api/frame')
      if (r.ok === false) throw new Error(r.error || 'フレームがありません。見るだけにしてください')
      setPreview(r)
    } catch (e) {
      setError(String(e.message || e))
    }
  }, [])

  useEffect(() => { loadPreview() }, [loadPreview])

  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || !preview) return
    cv.width = preview.width
    cv.height = preview.height
    const ctx = cv.getContext('2d')
    const img = new Image()
    img.onload = () => {
      ctx.drawImage(img, 0, 0, preview.width, preview.height)
      const r = roiDrag || (() => {
        const v = roiText.split(',').map((x) => Number(x.trim()))
        return v.length === 4 && v.every((x) => Number.isFinite(x))
          ? { x0: v[0], y0: v[1], x1: v[0] + v[2], y1: v[1] + v[3] } : null
      })()
      if (r) {
        const x = Math.min(r.x0, r.x1) * preview.width
        const y = Math.min(r.y0, r.y1) * preview.height
        const w = Math.abs(r.x1 - r.x0) * preview.width
        const h = Math.abs(r.y1 - r.y0) * preview.height
        ctx.strokeStyle = '#22d3ee'
        ctx.lineWidth = Math.max(2, preview.width / 400)
        ctx.strokeRect(x, y, w, h)
      }
    }
    img.src = `data:image/jpeg;base64,${preview.image}`
  }, [preview, roiText, roiDrag])

  const point = (e) => {
    const cv = canvasRef.current
    const r = cv.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    }
  }

  const startWatch = async () => {
    const r = await api.post('/api/mode', { mode: 'watch' })
    if (!r.ok) {
      setError('見るだけにできませんでした')
      return
    }
    flash('見るだけにしました')
  }

  const register = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.post('/api/screens', {
        name: name.trim() || '画面',
        method, threshold: Number(threshold),
        enter_frames: Number(enterFrames), exit_frames: Number(exitFrames),
        roi: parseRoi(),
      })
      if (!r.ok) throw new Error(r.error || '登録できませんでした')
      setName('')
      await load()
      flash('画面を登録しました')
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const update = async (id, fields) => {
    setError(null)
    const r = await api.put(`/api/screens/${id}`, fields)
    if (!r.ok) {
      setError(r.error || '更新できませんでした')
      return
    }
    await load()
  }

  const recapture = async (id) => {
    setError(null)
    const r = await api.post(`/api/screens/${id}/capture`, {})
    if (!r.ok) {
      setError(r.error || '更新できませんでした')
      return
    }
    await load()
    flash('テンプレートを更新しました')
  }

  const addTemplate = async (id) => {
    setError(null)
    const r = await api.post(`/api/screens/${id}/templates`, {})
    if (!r.ok) {
      setError(r.error || 'テンプレートを追加できませんでした')
      return
    }
    await load()
    flash('テンプレートを追加しました')
  }

  const removeTemplate = async (id, index) => {
    setError(null)
    const r = await api.del(`/api/screens/${id}/templates/${index}`)
    if (!r.ok) {
      setError(r.error || 'テンプレートを削除できませんでした')
      return
    }
    await load()
    flash('テンプレートを削除しました')
  }

  const remove = async (s) => {
    if (!window.confirm(`画面「${s.name}」を削除しますか？`)) return
    const r = await api.del(`/api/screens/${s.id}`)
    if (!r.ok) {
      setError('削除できませんでした')
      return
    }
    await load()
    flash('削除しました')
  }

  return (
    <div className="settings screen-panel">
      <section>
        <h3>今の画面を登録</h3>
        <p className="hint">
          「見るだけ」で対象の画面を出してから登録します。画面ごとにテンプレート画像と一致しきい値を持ちます。
        </p>
        <div className="row">
          <span className="lbl">ROI プレビュー</span>
          <button className="ghost" onClick={loadPreview}>現在のフレーム</button>
          <button className="ghost" onClick={() => setRoiText('')}>クリア</button>
        </div>
        {preview ? (
          <div className="roi-wrap">
            <canvas ref={canvasRef}
              onMouseDown={(e) => { const p = point(e); setRoiDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }) }}
              onMouseMove={(e) => { if (roiDrag) { const p = point(e); setRoiDrag({ ...roiDrag, x1: p.x, y1: p.y }) } }}
              onMouseUp={(e) => {
                if (!roiDrag) return
                const p = point(e)
                const x0 = Math.min(roiDrag.x0, p.x), x1 = Math.max(roiDrag.x0, p.x)
                const y0 = Math.min(roiDrag.y0, p.y), y1 = Math.max(roiDrag.y0, p.y)
                if (x1 - x0 > 0.01 && y1 - y0 > 0.01) {
                  setRoiText([x0, y0, x1 - x0, y1 - y0].map((v) => v.toFixed(3)).join(', '))
                }
                setRoiDrag(null)
              }}
              onMouseLeave={() => setRoiDrag(null)}
            />
          </div>
        ) : (
          <p className="hint">見るだけで画面を出すと、ここをドラッグしてROIを指定できます。</p>
        )}
        <div className="row">
          <input placeholder="画面の名前（例: タイトル）" value={name}
            onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="row">
          <span className="lbl">ROI (左,上,幅,高さ 0-1)</span>
          <input placeholder="空欄=画面全体" value={roiText}
            onChange={(e) => setRoiText(e.target.value)} />
        </div>
        <div className="row">
          <span className="lbl">方式</span>
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            {METHODS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </div>
        <div className="row">
          <span className="lbl">しきい値</span>
          <input type="range" min="0.5" max="0.99" step="0.01" value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))} />
          <b>{Math.round(threshold * 100)}%</b>
        </div>
        <div className="row">
          <span className="lbl">安定フレーム 入る/出る</span>
          <input type="number" min="1" max="30" value={enterFrames}
            onChange={(e) => setEnterFrames(e.target.value)} />
          <input type="number" min="1" max="30" value={exitFrames}
            onChange={(e) => setExitFrames(e.target.value)} />
        </div>
        <div className="row">
          <button className="primary" onClick={register} disabled={busy}>
            {busy ? '登録中…' : '現在のフレームで登録'}
          </button>
          <button className="ghost" onClick={startWatch}>見るだけにする</button>
        </div>
        {error && <p className="error">{error}</p>}
        {toast && <p className="ok-msg">{toast}</p>}
      </section>

      <section>
        <h3>登録した画面 ({screens.length})</h3>
        {screens.length === 0 && (
          <p className="hint">まだ画面がありません。タイトル・モード選択・試合など、判定したい画面を登録してください。</p>
        )}
        {screens.map((s) => (
          <ScreenRow key={s.id} s={s} score={scores[s.id]} current={s.id === currentId}
            onChange={update} onDelete={remove} onRecapture={recapture}
            onAddTemplate={addTemplate} onRemoveTemplate={removeTemplate} />
        ))}
      </section>

      <section>
        <h3>いまの判定</h3>
        <div className="row">
          <span className="lbl">画面</span>
          <b>{live?.screen?.name || current?.name || '不明'}</b>
        </div>
        <div className="row">
          <span className="lbl">一致度</span>
          <b>{Math.round((live?.screen?.score ?? current?.score ?? 0) * 100)}%</b>
        </div>
        {(live?.screen?.candidates || current?.candidates || []).slice(0, 3).map((c) => (
          <div className="candidate" key={c.id}>
            <span>{c.name}</span><b>{Math.round(c.score * 100)}%</b>
          </div>
        ))}
        <p className="hint">
          画面名はノードの「画面が」「画面を待つ」から選べます。クリック座標は「画面座標をクリック」で指定します。
        </p>
      </section>
    </div>
  )
}
