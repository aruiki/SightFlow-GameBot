import { useEffect, useState } from 'react'
import { api, eyedropper, hsvToCss, rangesAround, rgbToHsv } from './store'

const WORLD_MODELS = [
  ['yolov8s-worldv2.pt', 'S（速い・おすすめ）'],
  ['yolov8m-worldv2.pt', 'M（バランス）'],
  ['yolov8l-worldv2.pt', 'L（高精度）'],
  ['yolov8x-worldv2.pt', 'X（最高精度・重い）'],
]

const mid = ([lo, hi]) => lo.map((v, i) => Math.round((v + hi[i]) / 2))
const asRanges = (hsv) => (Array.isArray(hsv?.[0]?.[0]) ? hsv : hsv ? [hsv] : [[[0, 0, 0], [179, 255, 255]]])

/** Colour-detector rules, edited without touching JSON (numbers + eyedropper on the live screen). */
function ColorRules({ rules, onChange, onPickFromLive }) {
  const upd = (i, patch) => onChange(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const setRange = (i, ri, which, k, v) => {
    const ranges = asRanges(rules[i].hsv).map((r) => r.map((x) => [...x]))
    ranges[ri][which][k] = v
    upd(i, { hsv: ranges.length > 1 ? ranges : ranges[0] })
  }
  const delRange = (i, ri) => {
    const ranges = asRanges(rules[i].hsv).filter((_, j) => j !== ri)
    upd(i, { hsv: ranges.length > 1 ? ranges : ranges[0] })
  }
  return (
    <div className="color-rules">
      {rules.map((r, i) => {
        const ranges = asRanges(r.hsv)
        return (
          <div className="color-rule" key={i}>
            <div className="row">
              <i className="swatch" style={{ background: hsvToCss(mid(ranges[0])) }} />
              <input placeholder="ラベル（ノードで使う名前）" value={r.label || ''} onChange={(e) => upd(i, { label: e.target.value })} />
              <button className="ghost" title="ライブ画面をクリックして色を選ぶ" onClick={() => onPickFromLive(i)}>💧 スポイト</button>
              <button className="ghost" onClick={() => onChange(rules.filter((_, j) => j !== i))}>✕</button>
            </div>
            {ranges.map((rg, ri) => (
              <div className="row hsv" key={ri}>
                <span className="lbl">色範囲 {ranges.length > 1 ? ri + 1 : ''}</span>
                {['H', 'S', 'V'].map((n, k) => (
                  <span className="hsv-pair" key={n}>
                    <small>{n}</small>
                    <input type="number" value={rg[0][k]} onChange={(e) => setRange(i, ri, 0, k, Number(e.target.value))} />
                    <input type="number" value={rg[1][k]} onChange={(e) => setRange(i, ri, 1, k, Number(e.target.value))} />
                  </span>
                ))}
                {ranges.length > 1 && <button className="ghost" onClick={() => delRange(i, ri)}>−</button>}
              </div>
            ))}
            <div className="row">
              <span className="lbl">最小サイズ(px²)</span>
              <input type="number" min="1" value={r.min_area ?? 30} onChange={(e) => upd(i, { min_area: Number(e.target.value) })} />
            </div>
          </div>
        )
      })}
      <button className="ghost add" onClick={() => onChange([...rules, { type: 'color', label: '', hsv: [[0, 120, 120], [179, 255, 255]], min_area: 100 }])}>
        ＋ 色のルールを追加
      </button>
    </div>
  )
}

export default function SettingsPanel({ settings, labels, onApply, status, error, onPickFromLive }) {
  const [s, setS] = useState(settings)
  const [windows, setWindows] = useState([])
  useEffect(() => setS(settings), [settings])
  const isWindowSrc = s?.source?.type === 'window'
  useEffect(() => { if (isWindowSrc) api.get('/api/windows').then((r) => setWindows(r.windows)) }, [isWindowSrc])
  if (!s) return null
  const set = (path, value) =>
    setS((old) => {
      const next = structuredClone(old)
      let o = next
      const keys = path.split('.')
      keys.slice(0, -1).forEach((k) => (o = o[k] ??= {}))
      o[keys.at(-1)] = value
      return next
    })
  const src = s.source || {}
  const det = s.detector || {}
  const classes = det.classes || []
  const loadWindows = () => api.get('/api/windows').then((r) => setWindows(r.windows))

  return (
    <div className="settings">
      <section>
        <h3>画面の取り込み元</h3>
        <div className="seg">
          {[
            ['demo', 'デモゲーム'],
            ['window', 'ウィンドウ'],
            ['screen', '画面の範囲'],
            ['video', '動画ファイル'],
          ].map(([k, t]) => (
            <button key={k} className={src.type === k ? 'on' : ''} onClick={() => set('source', { type: k })}>
              {t}
            </button>
          ))}
        </div>
        {src.type === 'window' && (
          <div className="row">
            <input list="win-list" placeholder="ウィンドウ名の一部（例: BlueStacks）" value={src.title || ''}
              onFocus={loadWindows} onChange={(e) => set('source.title', e.target.value)} />
            <datalist id="win-list">
              {windows.map((w) => <option key={w} value={w} />)}
            </datalist>
            <button className="ghost" onClick={loadWindows} title="開いているウィンドウを探し直す">↻ 一覧</button>
          </div>
        )}
        {src.type === 'window' && windows.length > 0 && (
          <div className="win-chips">
            {windows.slice(0, 12).map((w) => (
              <button key={w} className={`chip ${src.title === w ? 'on' : ''}`} onClick={() => set('source.title', w)}>{w}</button>
            ))}
          </div>
        )}
        {src.type === 'screen' && (
          <div className="row">
            <input placeholder="左, 上, 幅, 高さ（空欄=画面全体）" value={(src.region || []).join(', ')}
              onChange={(e) => {
                const v = e.target.value.split(',').map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x))
                set('source.region', v.length === 4 ? v : null)
              }} />
          </div>
        )}
        {src.type === 'video' && (
          <div className="row">
            <input placeholder="動画ファイルのパス（.mp4 など）" value={src.path || ''}
              onChange={(e) => set('source.path', e.target.value)} />
          </div>
        )}
      </section>

      <section>
        <h3>検出器</h3>
        <div className="seg">
          {[
            ['yolo_world', 'YOLO-World（言葉で指定）'],
            ['yolo', '学習済みYOLO'],
            ['color', '色（デモ用）'],
          ].map(([k, t]) => (
            <button key={k} className={det.type === k ? 'on' : ''} onClick={() => { set('detector.type', k); if (k === 'color' && !det.color?.length) set('detector.color', [{ type: 'color', label: 'player', hsv: [[100, 150, 150], [118, 255, 255]], min_area: 150 }]) }}>{t}</button>
          ))}
        </div>
        {det.type === 'yolo_world' && (
          <div className="row">
            <span className="lbl">モデル</span>
            <select value={det.model || WORLD_MODELS[0][0]} onChange={(e) => set('detector.model', e.target.value)}>
              {WORLD_MODELS.map(([m, t]) => <option key={m} value={m}>{t}</option>)}
            </select>
          </div>
        )}
        {det.type === 'yolo' && (
          <div className="row">
            <span className="lbl">モデル</span>
            <input placeholder="best.pt のパス、または yolo11s.pt" value={det.model || ''}
              onChange={(e) => set('detector.model', e.target.value)} />
          </div>
        )}
        {det.type !== 'color' && (
          <>
            <table className="classes">
              <thead>
                <tr>
                  <th>ラベル（ノードで使う名前）</th>
                  <th>{det.type === 'yolo_world' ? '探すもの（英語で説明）' : 'モデルのクラス名'}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {classes.map((c, i) => (
                  <tr key={i}>
                    <td><input value={c.label} onChange={(e) => set(`detector.classes.${i}.label`, e.target.value)} /></td>
                    <td><input value={c.prompt} onChange={(e) => set(`detector.classes.${i}.prompt`, e.target.value)} /></td>
                    <td><button className="ghost" onClick={() => set('detector.classes', classes.filter((_, j) => j !== i))}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="ghost add" onClick={() => set('detector.classes', [...classes, { label: '', prompt: '' }])}>
              ＋ ラベルを追加
            </button>
            <div className="row">
              <span className="lbl">信頼度のしきい値</span>
              <input type="range" min="0.05" max="0.9" step="0.05" value={det.conf ?? 0.25}
                onChange={(e) => set('detector.conf', Number(e.target.value))} />
              <b>{det.conf ?? 0.25}</b>
            </div>
            <div className="row">
              <span className="lbl">入力サイズ</span>
              <select value={det.imgsz ?? 640} onChange={(e) => set('detector.imgsz', Number(e.target.value))}>
                {[320, 480, 640, 960, 1280].map((v) => <option key={v} value={v}>{v}px</option>)}
              </select>
            </div>
          </>
        )}
        {det.type === 'color' && (
          <>
            <p className="hint">指定した色の塊を検出します。「スポイト」を押してライブ画面をクリックすると、その色が自動で入ります。</p>
            <ColorRules rules={det.color || []} onChange={(v) => set('detector.color', v)}
              onPickFromLive={(i) => {
                eyedropper.cb = (rgb) => {
                  const hsv = rangesAround(rgbToHsv(...rgb))
                  const rules = (det.color || []).map((r, j) => (j === i ? { ...r, hsv: hsv.length > 1 ? hsv : hsv[0] } : r))
                  const next = structuredClone(s)
                  next.detector.color = rules
                  onApply(next)
                }
                onPickFromLive()
              }} />
          </>
        )}
      </section>

      <section>
        <h3>動作</h3>
        <div className="row">
          <span className="lbl">自分のラベル</span>
          <select value={s.anchor || ''} onChange={(e) => set('anchor', e.target.value)}>
            <option value="">（なし＝画面中央）</option>
            {[...new Set([...labels, ...classes.map((c) => c.label), s.anchor].filter(Boolean))].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>
        <div className="row">
          <span className="lbl">フレームレート</span>
          <input type="number" min="1" max="60" value={s.fps ?? 20} onChange={(e) => set('fps', Number(e.target.value))} />
        </div>
        <div className="row">
          <span className="lbl">移動キー 上/下/左/右</span>
          {['up', 'down', 'left', 'right'].map((k) => (
            <input key={k} className="key" value={s.input?.move_keys?.[k] ?? ''}
              onChange={(e) => set(`input.move_keys.${k}`, e.target.value)} />
          ))}
        </div>
      </section>

      <div className="apply">
        {status === 'loading' && <span className="loading">読み込み中…（初回はモデルのダウンロードがあります）</span>}
        {error && <span className="error">{error}</span>}
        <button className="primary" onClick={() => onApply(s)}>設定を適用</button>
      </div>
    </div>
  )
}
