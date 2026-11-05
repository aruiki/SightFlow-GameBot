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
export const metaStore = createStore({ defs: {}, categories: {}, labels: [], screens: [] })

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
  del: (p) => fetch(p, { method: 'DELETE' }).then((r) => r.json()),
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
