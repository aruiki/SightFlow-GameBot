import { useEffect, useRef, useState } from 'react'
import { eyedropper, labelColor, useLive } from './store'

function arrow(ctx, x1, y1, x2, y2, color, width = 3) {
  const a = Math.atan2(y2 - y1, x2 - x1)
  ctx.strokeStyle = color
  ctx.fillStyle = color
  ctx.lineWidth = width
  ctx.beginPath()
  ctx.moveTo(x1, y1)
  ctx.lineTo(x2, y2)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(x2, y2)
  ctx.lineTo(x2 - 12 * Math.cos(a - 0.45), y2 - 12 * Math.sin(a - 0.45))
  ctx.lineTo(x2 - 12 * Math.cos(a + 0.45), y2 - 12 * Math.sin(a + 0.45))
  ctx.closePath()
  ctx.fill()
}

export default function LivePanel({ highlightLabel, onPick, onCrop }) {
  const [cropMode, setCropMode] = useState(false)
  const [drag, setDrag] = useState(null)     // {x0, y0, x1, y1} in frame px while dragging
  const [pending, setPending] = useState(null) // finished rectangle waiting for a name
  const [cropName, setCropName] = useState('')
  const live = useLive()
  const canvasRef = useRef(null)
  const imgRef = useRef(new Image())
  const [hover, setHover] = useState(null)

  useEffect(() => {
    if (!live?.image) return
    const img = imgRef.current
    img.onload = () => {
      const cv = canvasRef.current
      if (!cv) return
      cv.width = live.w
      cv.height = live.h
      const ctx = cv.getContext('2d')
      ctx.drawImage(img, 0, 0, live.w, live.h)
      const lw = Math.max(2, live.w / 400)
      ctx.font = `600 ${Math.max(12, live.w / 55)}px ui-sans-serif, system-ui`
      for (const d of live.dets) {
        const dim = highlightLabel && d.label !== highlightLabel
        const c = labelColor(d.label)
        ctx.globalAlpha = dim ? 0.25 : 1
        ctx.strokeStyle = c
        ctx.lineWidth = lw
        ctx.strokeRect(d.x - d.w / 2, d.y - d.h / 2, d.w, d.h)
        const tag = `${d.label} #${d.id}`
        const tw = ctx.measureText(tag).width + 8
        const th = Math.max(16, live.w / 40)
        ctx.fillStyle = c
        ctx.fillRect(d.x - d.w / 2, d.y - d.h / 2 - th, tw, th)
        ctx.fillStyle = '#0b0f17'
        ctx.fillText(tag, d.x - d.w / 2 + 4, d.y - d.h / 2 - th * 0.25)
      }
      ctx.globalAlpha = 1
      const me = live.me
      if (me) {
        ctx.strokeStyle = '#fff'
        ctx.lineWidth = lw * 1.5
        ctx.setLineDash([6, 4])
        ctx.beginPath()
        ctx.arc(me.x, me.y, Math.max(me.w, me.h) * 0.8, 0, Math.PI * 2)
        ctx.stroke()
        ctx.setLineDash([])
        const mv = live.intents?.move
        if (mv) arrow(ctx, me.x, me.y, me.x + mv[0] * 60, me.y + mv[1] * 60, '#ffffff', lw * 1.5)
      }
      for (const [fx, fy, tx, ty] of live.intents?.drags || []) arrow(ctx, fx, fy, tx, ty, '#ffd84a', lw * 1.5)
      for (const [x, y] of live.intents?.clicks || []) {
        ctx.strokeStyle = '#ffd84a'
        ctx.lineWidth = lw * 1.5
        ctx.beginPath()
        ctx.arc(x, y, 10, 0, Math.PI * 2)
        ctx.moveTo(x - 14, y)
        ctx.lineTo(x + 14, y)
        ctx.moveTo(x, y - 14)
        ctx.lineTo(x, y + 14)
        ctx.stroke()
      }
    }
    img.src = `data:image/jpeg;base64,${live.image}`
  }, [live, highlightLabel])

  const toFrame = (e) => {
    const cv = canvasRef.current
    const r = cv.getBoundingClientRect()
    return [((e.clientX - r.left) / r.width) * cv.width, ((e.clientY - r.top) / r.height) * cv.height]
  }
  const hit = (e) => {
    if (!live) return null
    const [x, y] = toFrame(e)
    return live.dets.find((d) => Math.abs(d.x - x) < d.w / 2 && Math.abs(d.y - y) < d.h / 2) || null
  }

  const t = live?.timing
  return (
    <div className="live">
      <div className="live-tools">
        <button className={`ghost ${cropMode ? 'tog' : ''}`} disabled={!live?.image}
          title="ライブ画面をドラッグして、探したいもの（ボタン・アイコンなど）を切り取る"
          onClick={() => { setCropMode((v) => !v); setPending(null) }}>
          ✂ {cropMode ? 'ドラッグで範囲を選んでください' : '画像を切り取って探す'}
        </button>
      </div>
      <div className="live-canvas">
        {live?.image ? (
          <canvas
            ref={canvasRef}
            onMouseDown={(e) => {
              if (!cropMode) return
              const [x, y] = toFrame(e)
              setDrag({ x0: x, y0: y, x1: x, y1: y })
            }}
            onMouseUp={() => {
              if (!cropMode || !drag) return
              const r = { x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1), w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) }
              setDrag(null)
              if (r.w > 4 && r.h > 4) { setPending(r); setCropName('') }
            }}
            onMouseMove={(e) => {
              if (cropMode && drag) { const [x, y] = toFrame(e); setDrag((d) => ({ ...d, x1: x, y1: y })) }
              else setHover(cropMode ? null : hit(e))
            }}
            onMouseLeave={() => setHover(null)}
            style={eyedropper.cb ? { cursor: 'copy' } : cropMode ? { cursor: 'crosshair' } : undefined}
            onClick={(e) => {
              if (cropMode) return
              const [x, y] = toFrame(e)
              if (eyedropper.cb) {   // pick a colour from the raw frame (without the overlay)
                const img = imgRef.current
                const k = img.naturalWidth / live.w
                const px = document.createElement('canvas')
                px.width = px.height = 1
                const c2 = px.getContext('2d', { willReadFrequently: true })
                c2.drawImage(img, x * k, y * k, 1, 1, 0, 0, 1, 1)
                const [r, g, b] = c2.getImageData(0, 0, 1, 1).data
                const cb = eyedropper.cb
                eyedropper.cb = null
                cb([r, g, b])
                return
              }
              onPick?.({ x: x / canvasRef.current.width, y: y / canvasRef.current.height, det: hit(e) })
            }}
          />
        ) : (
          <div className="live-empty">
            <div className="big">▶</div>
            上の「見るだけ」を押すと、ここに画面と検出結果が出ます
          </div>
        )}
        {live?.image && (drag || pending) && (() => {
          const r = pending || { x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1), w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) }
          return <div className="crop-rect" style={{ left: `${(r.x / live.w) * 100}%`, top: `${(r.y / live.h) * 100}%`, width: `${(r.w / live.w) * 100}%`, height: `${(r.h / live.h) * 100}%` }} />
        })()}
        {pending && (
          <form className="crop-form" onSubmit={(e) => {
            e.preventDefault()
            if (!cropName.trim()) return
            onCrop?.({ label: cropName.trim(), x: Math.round(pending.x), y: Math.round(pending.y), w: Math.round(pending.w), h: Math.round(pending.h) })
            setPending(null)
            setCropMode(false)
          }}>
            <input autoFocus placeholder="名前（例: startボタン）" value={cropName} onChange={(e) => setCropName(e.target.value)} />
            <button className="primary sm" type="submit">登録</button>
            <button className="ghost" type="button" onClick={() => setPending(null)}>やり直す</button>
          </form>
        )}
        {hover && (
          <div className="hover-card">
            <b style={{ color: labelColor(hover.label) }}>{hover.label}</b> #{hover.id}
            <span>信頼度 {(hover.score * 100).toFixed(0)}%</span>
            <span>距離 {hover.distance ?? '-'}px</span>
          </div>
        )}
      </div>
      {live && (
        <div className="live-stats">
          <div><span>検出</span><b>{live.dets.length}</b></div>
          <div><span>取得</span><b>{t?.capture}ms</b></div>
          <div><span>検出器</span><b>{t?.detect}ms</b></div>
          <div><span>判断</span><b>{t?.logic}ms</b></div>
          <div><span>速度</span><b>{live.motion?.speed}px/s</b></div>
          <div><span>引っかかり</span><b>{live.motion?.stuck}s</b></div>
        </div>
      )}
      {live && (
        <div className="legend">
          {[...new Set(live.dets.map((d) => d.label))].map((l) => (
            <span key={l} style={{ '--c': labelColor(l) }}>
              {l} × {live.dets.filter((d) => d.label === l).length}
            </span>
          ))}
          {live.intents?.taps?.length > 0 && <span className="tap">キー: {live.intents.taps.join(', ')}</span>}
        </div>
      )}
    </div>
  )
}
