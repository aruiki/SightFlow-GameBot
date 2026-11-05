import { useEffect, useState } from 'react'
import { api } from './store'

const WORLD_MODELS = [
  ['yolov8s-worldv2.pt', 'S（速い・おすすめ）'],
  ['yolov8m-worldv2.pt', 'M（バランス）'],
  ['yolov8l-worldv2.pt', 'L（高精度）'],
  ['yolov8x-worldv2.pt', 'X（最高精度・重い）'],
]

export default function SettingsPanel({ settings, labels, onApply, status, error }) {
  const [s, setS] = useState(settings)
  const [windows, setWindows] = useState([])
  const [models, setModels] = useState([])
  useEffect(() => setS(settings), [settings])
  useEffect(() => {
    api.get('/api/models').then((r) => setModels(r.models || [])).catch(() => {})
  }, [])
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
            ['region', '領域（色）'],
          ].map(([k, t]) => (
            <button key={k} className={det.type === k ? 'on' : ''} onClick={() => set('detector.type', k)}>{t}</button>
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
            <input list="model-files" placeholder="best.pt のパス、または yolo11s.pt" value={det.model || ''}
              onChange={(e) => set('detector.model', e.target.value)} />
            <datalist id="model-files">
              {models.map((m) => (
                <option key={m.name} value={m.name}>{Math.round(m.size / 1024 / 1024)}MB</option>
              ))}
            </datalist>
          </div>
        )}
        {det.type !== 'color' && (
          <p className="hint">
            models/: {models.length} 個
            {models.length > 0 && `（${models.slice(0, 3).map((m) => m.name).join(', ')}${models.length > 3 ? ' …' : ''}）`}
          </p>
        )}
        {(det.type === 'yolo_world' || det.type === 'yolo') && (
          <>
            <table className="classes">
              <thead>
                <tr>
                  <th>ラベル（ノードで使う名前）</th>
                  <th>{det.type === 'yolo_world' ? '探すもの（英語で説明）' : 'モデルのクラス名'}</th>
                  <th>しきい値</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {classes.map((c, i) => (
                  <tr key={i}>
                    <td><input value={c.label} onChange={(e) => set(`detector.classes.${i}.label`, e.target.value)} /></td>
                    <td><input value={c.prompt} onChange={(e) => set(`detector.classes.${i}.prompt`, e.target.value)} /></td>
                    <td>
                      <input type="number" min="0.05" max="0.99" step="0.05"
                        placeholder={String(det.conf ?? 0.25)}
                        value={c.conf ?? ''}
                        onChange={(e) => set(`detector.classes.${i}.conf`, e.target.value === '' ? null : Number(e.target.value))} />
                    </td>
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
          <p className="hint">色の範囲で検出します（付属デモ用）。設定はプロジェクトのJSONで編集します。</p>
        )}
        {det.type === 'region' && (
          <p className="hint">色の領域（ポリゴン）で検出します。草むら・毒ガス向け。HSV・最小面積はプロジェクトのJSONの <code>region</code> に書きます。</p>
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
