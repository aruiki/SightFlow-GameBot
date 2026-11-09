// Tiny external stores so that 20 fps live updates only re-render the pieces
// that changed (one node's status pill, the live canvas), not the whole graph.
import { useSyncExternalStore } from 'react'

function createStore(initial) {
  let state = initial
  const subs = new Set()
  return {
    get: () => state,
    set: (next) => {
      state = typeof next === 'function' ? next(state) : next
      subs.forEach((f) => f())
    },
    subscribe: (f) => {
      subs.add(f)
      return () => subs.delete(f)
    },
  }
}

export const liveStore = createStore(null) // last tick message from the server
export const metaStore = createStore({ defs: {}, categories: {}, labels: [] })

const EMPTY = {}
export function useNodeStatus(id) {
  return useSyncExternalStore(liveStore.subscribe, () => liveStore.get()?.nodes?.[id] ?? EMPTY)
}
export function useLive() {
  return useSyncExternalStore(liveStore.subscribe, liveStore.get)
}
export function useMeta() {
  return useSyncExternalStore(metaStore.subscribe, metaStore.get)
}

export const TYPE_COLORS = {
  list: '#b18cff',
  object: '#4fb3ff',
  number: '#4ade80',
  bool: '#fbbf24',
  any: '#cbd5e1',
}

export const IO_NAMES = {
  list: 'リスト', object: '物体', count: '数', any: 'ある', run: '実行', target: '対象',
  a: 'a', b: 'b', result: '結果', value: '値', in: '入力', out: '出力', me: '自分',
  found: '見つかった', speed: '速度', stuck_time: '引っかかり秒', stuck: '引っかかり', t: '秒',
  radius: '半径', id: 'ID', set: '記憶する',
}

export function labelColor(label) {
  let h = 0
  for (const c of label || '') h = (h * 31 + c.charCodeAt(0)) % 360
  return `hsl(${h} 85% 62%)`
}

// --------------------------------------------------------------------- API
export const api = {
  get: (p) => fetch(p).then((r) => r.json()),
  put: (p, body) =>
    fetch(p, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json()),
  post: (p, body) =>
    fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json()),
}

export function connectLive(onStatus) {
  let ws
  let closed = false
  const open = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/ws`)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'tick') liveStore.set(msg)
      onStatus?.(msg)
    }
    ws.onclose = () => {
      if (!closed) setTimeout(open, 1000)
    }
  }
  open()
  return () => {
    closed = true
    ws?.close()
  }
}

// ------------------------------------------------------------ eyedropper
// SettingsPanel sets `eyedropper.cb`; the next click on the live screen calls it with [r, g, b].
export const eyedropper = { cb: null }

/** RGB (0-255) -> OpenCV HSV (H 0-179, S/V 0-255). */
export function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn
  let h = 0
  if (d) {
    if (mx === r) h = ((g - b) / d) % 6
    else if (mx === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  return [Math.round(h / 2) % 180, Math.round(mx ? (d / mx) * 255 : 0), Math.round(mx * 255)]
}

/** A colour range around a picked HSV colour; red-ish hues wrap around 0/179 so they become two ranges. */
export function rangesAround([h, s, v], tolH = 10, tolS = 70, tolV = 70) {
  const lo = (x, t, min = 0) => Math.max(min, x - t)
  const hi = (x, t, max = 255) => Math.min(max, x + t)
  const sl = lo(s, tolS), sh = hi(s, tolS), vl = lo(v, tolV), vh = hi(v, tolV)
  if (h - tolH < 0) return [[[0, sl, vl], [h + tolH, sh, vh]], [[180 + h - tolH, sl, vl], [179, sh, vh]]]
  if (h + tolH > 179) return [[[h - tolH, sl, vl], [179, sh, vh]], [[0, sl, vl], [h + tolH - 180, sh, vh]]]
  return [[[h - tolH, sl, vl], [h + tolH, sh, vh]]]
}

export function hsvToCss([h, s, v]) {
  const S = s / 255, V = v / 255, H = (h * 2) / 60
  const f = (n) => { const k = (n + H) % 6; return V - V * S * Math.max(0, Math.min(k, 4 - k, 1)) }
  return `rgb(${Math.round(f(5) * 255)} ${Math.round(f(3) * 255)} ${Math.round(f(1) * 255)})`
}
