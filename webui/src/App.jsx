import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, addEdge,
  useEdgesState, useNodesState, useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import FlowNode from './FlowNode'
import LivePanel from './LivePanel'
import SettingsPanel from './SettingsPanel'
import { TYPE_COLORS, api, connectLive, metaStore, useMeta } from './store'

const nodeTypesFor = (defs) => Object.fromEntries(Object.keys(defs).map((k) => [k, FlowNode]))

function Palette() {
  const { defs, categories } = useMeta()
  const [q, setQ] = useState('')
  const { screenToFlowPosition, addNodes } = useReactFlow()
  const groups = useMemo(() => {
    const out = {}
    Object.values(defs)
      .filter((d) => !q || (d.title + d.desc + d.type).toLowerCase().includes(q.toLowerCase()))
      .forEach((d) => (out[d.category] ??= []).push(d))
    return out
  }, [defs, q])
  const addAtCenter = (type) => {
    const r = document.querySelector('.canvas').getBoundingClientRect()
    const pos = screenToFlowPosition({ x: r.left + r.width / 2 - 100, y: r.top + r.height / 2 - 60 })
    addNodes({ id: newId(type), type, position: pos, data: {} })
  }
  return (
    <aside className="palette">
      <input className="search" placeholder="ノードを検索…" value={q} onChange={(e) => setQ(e.target.value)} />
      {Object.entries(categories).map(([key, cat]) =>
        groups[key]?.length ? (
          <div key={key} className="pal-group">
            <div className="pal-title" style={{ color: cat.color }}>{cat.title}</div>
            {groups[key].map((d) => (
              <div key={d.type} className="pal-item" style={{ '--cat': cat.color }} draggable
                title={d.desc}
                onDragStart={(e) => e.dataTransfer.setData('application/sightflow', d.type)}
                onDoubleClick={() => addAtCenter(d.type)}>
                <b>{d.title}</b>
                <small>{d.desc}</small>
              </div>
            ))}
          </div>
        ) : null,
      )}
      <div className="pal-help">
        ドラッグで置く・ダブルクリックで追加<br />
        線の色＝データの種類
        <div className="wire-legend">
          {Object.entries({ list: 'リスト', object: '物体', number: '数', bool: 'はい/いいえ' }).map(([t, n]) => (
            <span key={t}><i style={{ background: TYPE_COLORS[t] }} />{n}</span>
          ))}
        </div>
      </div>
    </aside>
  )
}


const newId = (type) => `${type}_${Math.random().toString(36).slice(2, 8)}`

/** ComfyUI-style "double-click the canvas -> type to add a node" popup. */
function QuickAdd({ x, y, onPick, onClose }) {
  const { defs, categories } = useMeta()
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const ref = useRef(null)
  useEffect(() => ref.current?.focus(), [])
  const items = useMemo(() => {
    const ql = q.toLowerCase()
    return Object.values(defs).filter((d) => !ql || (d.title + d.desc + d.type + (categories[d.category]?.title || '')).toLowerCase().includes(ql))
  }, [defs, categories, q])
  useEffect(() => setIdx(0), [q])
  const onKey = (e) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter' && items[idx]) onPick(items[idx].type)
  }
  const left = Math.min(x, window.innerWidth - 340)
  const top = Math.min(y, window.innerHeight - 380)
  return (
    <>
      <div className="scrim" onMouseDown={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <div className="quick" style={{ left, top }}>
        <input ref={ref} placeholder="ノードを検索して Enter で追加…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
        <div className="quick-list">
          {items.length === 0 && <div className="quick-empty">見つかりません</div>}
          {items.map((d, i) => (
            <div key={d.type} className={`quick-item ${i === idx ? 'on' : ''}`} style={{ '--cat': categories[d.category]?.color }}
              onMouseEnter={() => setIdx(i)} onClick={() => onPick(d.type)}>
              <i />
              <b>{d.title}</b>
              <small>{d.desc}</small>
              <em>{categories[d.category]?.title}</em>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

function ContextMenu({ x, y, items, onClose }) {
  return (
    <>
      <div className="scrim" onMouseDown={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <div className="ctx" style={{ left: Math.min(x, window.innerWidth - 190), top: Math.min(y, window.innerHeight - 40 * items.length) }}>
        {items.map((it) => (
          <button key={it.label} className={it.danger ? 'danger' : ''} onClick={() => { it.run(); onClose() }}>{it.label}</button>
        ))}
      </div>
    </>
  )
}

/** Project switcher: pick / create / rename / import / export, all from the GUI. */
function ProjectMenu({ project, onSwitch, onRename, onExport, onImport }) {
  const [open, setOpen] = useState(false)
  const [list, setList] = useState([])
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const toggle = async () => {
    if (!open) setList((await api.get('/api/projects')).projects)
    setOpen(!open)
    setCreating(false)
  }
  const create = async () => {
    await api.post('/api/projects/new', { name })
    setName('')
    setOpen(false)
    onSwitch()
  }
  return (
    <div className="projmenu">
      <input className="projname" value={project?.name || ''} title="プロジェクト名（クリックして変更）"
        onChange={(e) => onRename(e.target.value, false)} onBlur={(e) => onRename(e.target.value, true)}
        onKeyDown={(e) => e.key === 'Enter' && e.target.blur()} />
      <button className="ghost" onClick={toggle} title="プロジェクトを切り替え">▾ プロジェクト</button>
      {open && (
        <>
          <div className="scrim" onMouseDown={() => setOpen(false)} />
          <div className="dropdown">
            {list.map((p) => (
              <button key={p.file} className={p.current ? 'cur' : ''} onClick={async () => {
                if (!p.current) { await api.post('/api/projects/open', { file: p.file }); onSwitch() }
                setOpen(false)
              }}>{p.current ? '✓ ' : ''}{p.name}</button>
            ))}
            <hr />
            {creating ? (
              <div className="newrow">
                <input autoFocus placeholder="プロジェクト名" value={name} onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') create(); if (e.key === 'Escape') setCreating(false) }} />
                <button className="primary sm" onClick={create}>作成</button>
              </div>
            ) : (
              <button onClick={() => setCreating(true)}>＋ 新規プロジェクト</button>
            )}
            <button onClick={() => { onExport(); setOpen(false) }}>⭳ ファイルに書き出し</button>
            <label className="item">
              ⭱ ファイルから読み込み
              <input type="file" accept=".json" onChange={(e) => { e.target.files[0] && onImport(e.target.files[0]); setOpen(false) }} />
            </label>
          </div>
        </>
      )}
    </div>
  )
}

function Modal({ title, children, actions, onClose }) {
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="modal">
        <h2>{title}</h2>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">{actions}</div>
      </div>
    </div>
  )
}

const WELCOME_KEY = 'sightflow-welcome-v1'
function seenWelcome() {
  try { return localStorage.getItem(WELCOME_KEY) === '1' } catch { return false }
}
function markWelcome() {
  try { localStorage.setItem(WELCOME_KEY, '1') } catch { /* private window */ }
}

function Welcome({ onDemo, onNew }) {
  const [name, setName] = useState('')
  return (
    <Modal title="SightFlow へようこそ" onClose={onDemo} actions={<>
      <button className="ghost" onClick={onDemo}>まずデモを見る</button>
      <input className="welcome-name" placeholder="ゲーム名（例: マイゲーム）" value={name} onChange={(e) => setName(e.target.value)} />
      <button className="primary" onClick={() => onNew(name.trim() || 'マイゲーム')}>自分のゲームで始める</button>
    </>}>
      <p>コードを書かずに、ゲームを自動で操作するボットを作れます。</p>
      <ol className="welcome-steps">
        <li><b>取り込み元を選ぶ</b><span>右の「設定」でゲームのウィンドウを選び、「設定を適用」</span></li>
        <li><b>見るだけ</b><span>上の「👁 見るだけ」で、ライブ画面に検出結果が出ます（まだ操作はしません）</span></li>
        <li><b>作る</b><span>「● 記録」で自分の操作を録る／「✂ 画像を切り取って探す」で目印を登録／キャンバスをダブルクリックしてノードを追加</span></li>
        <li><b>操作する</b><span>「▶ 操作する」で実際に動かします。止めるときは <kbd>F12</kbd></span></li>
      </ol>
    </Modal>
  )
}

function Editor() {
  const meta = useMeta()
  const [project, setProject] = useState(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [mode, setMode] = useState('stop')
  const [srvStatus, setSrvStatus] = useState({ status: 'idle', error: null })
  const [graphError, setGraphError] = useState(null)
  const [tab, setTab] = useState('live')
  const [saved, setSaved] = useState(true)
  const [toast, setToast] = useState(null)
  const [showPal, setShowPal] = useState(false)
  const [showSide, setShowSide] = useState(true)
  const [quick, setQuick] = useState(null)   // {x, y} screen position of the add-node popup
  const [ctx, setCtx] = useState(null)       // {x, y, items}
  const [recording, setRecording] = useState(false)
  const [askLive, setAskLive] = useState(false)
  const [welcome, setWelcome] = useState(() => !seenWelcome())
  const hist = useRef({ stack: [], idx: -1, skip: false })
  const finishing = useRef(false)
  const loaded = useRef(false)
  const { screenToFlowPosition, getNodes } = useReactFlow()
  const flash = useCallback((msg, ms = 2200) => { setToast(msg); setTimeout(() => setToast(null), ms) }, [])
  const nodeTypes = useMemo(() => nodeTypesFor(meta.defs), [meta.defs])

  const refreshState = useCallback(async () => {
    const st = await api.get('/api/state')
    metaStore.set((m) => ({ ...m, labels: st.labels }))
    setSrvStatus({ status: st.status, error: st.error })
    setMode(st.mode)
    return st
  }, [])

  const loadProject = useCallback(async () => {
    loaded.current = false
    const st = await refreshState()
    setProject(st.project)
    setGraphError(st.graph_error)
    setNodes(st.project.graph.nodes || [])
    setEdges(st.project.graph.edges || [])
    hist.current = { stack: [JSON.stringify(st.project.graph)], idx: 0, skip: false }
    setSaved(true)
    setTimeout(() => (loaded.current = true), 80)
  }, [refreshState, setEdges, setNodes])

  useEffect(() => {
    ;(async () => {
      const types = await api.get('/api/node-types')
      metaStore.set((m) => ({
        ...m,
        categories: types.categories,
        ioNames: types.io_names,
        defs: Object.fromEntries(types.types.map((t) => [t.type, t])),
      }))
      await loadProject()
    })()
    return connectLive((msg) => {
      if (msg.type === 'status') {
        setSrvStatus((old) => {
          if (old.status !== msg.status) refreshState()
          return { status: msg.status, error: msg.error }
        })
        setMode(msg.mode)
        setRecording((was) => {
          if (was && msg.recording === false) window.dispatchEvent(new Event('sightflow-record-ended'))
          return !!msg.recording
        })
      } else if (msg.mode) setMode(msg.mode)
    })
  }, [refreshState, loadProject])

  // autosave the graph (debounced) whenever nodes/edges change
  useEffect(() => {
    if (!loaded.current) return
    setSaved(false)
    const t = setTimeout(async () => {
      const graph = {
        nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })),
        edges: edges.map(({ id, source, sourceHandle, target, targetHandle }) => ({ id, source, sourceHandle, target, targetHandle })),
      }
      const h = hist.current
      const snap = JSON.stringify(graph)
      if (!h.skip && snap !== h.stack[h.idx]) {
        h.stack = [...h.stack.slice(Math.max(0, h.idx - 99), h.idx + 1), snap]
        h.idx = h.stack.length - 1
      }
      h.skip = false
      const r = await api.put('/api/graph', graph)
      setGraphError(r.error)
      setSaved(true)
    }, 400)
    return () => clearTimeout(t)
  }, [nodes, edges])

  const handleType = useCallback(
    (nodeId, handleId, kind) => {
      const n = nodes.find((x) => x.id === nodeId)
      const d = n && meta.defs[n.type]
      const list = kind === 'source' ? d?.outputs : d?.inputs
      return list?.find((h) => h.name === handleId)?.type
    },
    [nodes, meta.defs],
  )
  const isValidConnection = useCallback(
    (c) => {
      if (c.source === c.target) return false
      const a = handleType(c.source, c.sourceHandle, 'source')
      const b = handleType(c.target, c.targetHandle, 'target')
      return a === b || a === 'any' || b === 'any'
    },
    [handleType],
  )
  const onConnect = useCallback(
    (c) =>
      setEdges((eds) => {
        const color = TYPE_COLORS[handleType(c.source, c.sourceHandle, 'source')] || '#888'
        const rest = eds.filter((e) => !(e.target === c.target && e.targetHandle === c.targetHandle))
        return addEdge({ ...c, id: `${c.source}.${c.sourceHandle}-${c.target}.${c.targetHandle}`, style: { stroke: color } }, rest)
      }),
    [setEdges, handleType],
  )
  // colour existing edges by wire type
  const styledEdges = useMemo(
    () =>
      edges.map((e) => ({
        ...e,
        style: { stroke: TYPE_COLORS[handleType(e.source, e.sourceHandle, 'source')] || '#888', strokeWidth: 2.2 },
      })),
    [edges, handleType],
  )

  const onDrop = (e) => {
    e.preventDefault()
    const type = e.dataTransfer.getData('application/sightflow')
    if (!type) return
    const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
    setNodes((ns) => [...ns, { id: newId(type), type, position, data: {} }])
  }

  const changeMode = async (m) => {
    if (m === 'live' && project?.settings?.source?.type !== 'demo' && !askLive) {
      setAskLive(true)
      return
    }
    setAskLive(false)
    await api.post('/api/mode', { mode: m })
    setMode(m)
  }

  const applySettings = async (s) => {
    setTab('settings')
    await api.put('/api/settings', s)
    setProject((p) => ({ ...p, settings: s }))
    setSrvStatus({ status: 'loading', error: null })
    setToast('設定を適用しました')
    setTimeout(() => setToast(null), 1800)
  }

  const exportProject = () => {
    const graph = {
      nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })),
      edges: edges.map(({ id, source, sourceHandle, target, targetHandle }) => ({ id, source, sourceHandle, target, targetHandle })),
    }
    const blob = new Blob([JSON.stringify({ ...project, graph }, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${project?.name || 'sightflow'}.json`
    a.click()
  }
  const importProject = (file) => {
    file.text().then((txt) => {
      const p = JSON.parse(txt)
      setNodes(p.graph?.nodes || [])
      setEdges(p.graph?.edges || [])
      if (p.settings) applySettings(p.settings)
      setProject(p)
    })
  }

  const addNodeAt = (type, clientX, clientY) => {
    const position = screenToFlowPosition({ x: clientX - 90, y: clientY - 20 })
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { id: newId(type), type, position, data: {}, selected: true }])
    setQuick(null)
  }
  const duplicateNode = (id) => {
    const n = getNodes().find((x) => x.id === id)
    if (!n) return
    setNodes((ns) => [...ns, { ...n, id: newId(n.type), position: { x: n.position.x + 30, y: n.position.y + 30 }, selected: false, data: { ...n.data } }])
  }
  const deleteNode = (id) => {
    setNodes((ns) => ns.filter((n) => n.id !== id))
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id))
  }
  const onPaneContextMenu = (e) => {
    e.preventDefault()
    setCtx({ x: e.clientX, y: e.clientY, items: [
      { label: '＋ ノードを追加…', run: () => setQuick({ x: e.clientX, y: e.clientY }) },
      { label: '▭ ノード一覧を開く', run: () => setShowPal(true) },
      { label: '↶ 元に戻す (Ctrl+Z)', run: undo },
      { label: '↷ やり直す (Ctrl+Y)', run: redo },
    ] })
  }
  const onNodeContextMenu = (e, node) => {
    e.preventDefault()
    setCtx({ x: e.clientX, y: e.clientY, items: [
      { label: '複製', run: () => duplicateNode(node.id) },
      { label: '削除', danger: true, run: () => deleteNode(node.id) },
    ] })
  }
  // ComfyUI: double-click empty canvas to search + add a node
  const onCanvasDoubleClick = (e) => {
    if (e.target.classList?.contains('react-flow__pane')) setQuick({ x: e.clientX, y: e.clientY })
  }
  const travel = useCallback((d) => {
    const h = hist.current
    const to = h.idx + d
    if (to < 0 || to >= h.stack.length) return
    h.idx = to
    h.skip = true
    const g = JSON.parse(h.stack[to])
    setNodes(g.nodes || [])
    setEdges(g.edges || [])
  }, [setNodes, setEdges])
  const undo = useCallback(() => travel(-1), [travel])
  const redo = useCallback(() => travel(1), [travel])

  // “/” opens the search; Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo and redo
  useEffect(() => {
    const onKey = (e) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return
      const mod = e.ctrlKey || e.metaKey
      if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); return e.shiftKey ? redo() : undo() }
      if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); return redo() }
      if (e.key === '/' ) { e.preventDefault(); setQuick({ x: window.innerWidth / 2 - 160, y: window.innerHeight / 3 }) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  // ---------------------------------------------------------- FRep-style record
  const centerPos = () => {
    const r = document.querySelector('.canvas').getBoundingClientRect()
    return screenToFlowPosition({ x: r.left + r.width / 2 - 140, y: r.top + r.height / 2 - 120 })
  }
  const finishRecording = useCallback(async () => {
    if (finishing.current) return
    finishing.current = true
    try {
      const r = await api.post('/api/record/stop', {})
      setRecording(false)
      const steps = r.steps || []
      if (!steps.length) {
        flash('操作が記録されませんでした（ゲーム画面の中をクリック・キー入力してください）', 3500)
        return
      }
      setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })),
        { id: newId('macro'), type: 'macro', position: centerPos(), data: { steps, repeat: '1回' }, selected: true }])
      flash(`${steps.length}個の操作を記録して「記録した操作」ノードにしました`, 3500)
    } finally {
      finishing.current = false
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flash, setNodes])
  useEffect(() => {
    const h = () => finishRecording()
    window.addEventListener('sightflow-record-ended', h)
    return () => window.removeEventListener('sightflow-record-ended', h)
  }, [finishRecording])
  const startRecording = async () => {
    const r = await api.post('/api/record/start', {})
    if (!r.ok) return flash(r.error || '記録を始められませんでした', 4000)
    setRecording(true)
    setMode('stop')
    flash('記録中です。ゲーム画面で操作して、終わったら F12 か「■ 記録終了」を押してください', 4000)
  }

  // ---------------------------------------------------------- cut-out image -> detector
  const cropImage = async (body) => {
    const r = await api.post('/api/templates', body)
    if (!r.ok) return flash(r.error || '登録できませんでした', 3500)
    setProject((p) => ({ ...p, settings: r.settings }))
    setSrvStatus({ status: 'loading', error: null })
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })),
      { id: newId('objects'), type: 'objects', position: centerPos(), data: { label: body.label }, selected: true }])
    flash(`「${body.label}」を登録しました。画面に出ると「物体」ノードで見つかります`, 3500)
  }

  const renameProject = async (name, commit) => {
    setProject((p) => ({ ...p, name }))
    if (commit && name.trim()) await api.put('/api/project/name', { name })
  }

  const selected = nodes.find((n) => n.selected)
  const highlightLabel = selected?.type === 'objects' ? selected.data?.label : null

  const onPick = ({ x, y, det }) => {
    const txt = det ? `${det.label} #${det.id}` : `x=${x.toFixed(3)}, y=${y.toFixed(3)}`
    navigator.clipboard?.writeText(det ? det.label : `${x.toFixed(3)}, ${y.toFixed(3)}`)
    setToast(`${txt}（コピーしました）`)
    setTimeout(() => setToast(null), 1800)
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">◉</span> SightFlow
        </div>
        <ProjectMenu project={project} onSwitch={loadProject} onRename={renameProject}
          onExport={exportProject} onImport={importProject} />
        <div className="modes">
          <button className={mode === 'stop' ? 'on stop' : ''} onClick={() => changeMode('stop')}>■ 停止</button>
          <button className={mode === 'watch' ? 'on watch' : ''} onClick={() => changeMode('watch')}>👁 見るだけ</button>
          <button className={mode === 'live' ? 'on live' : ''} onClick={() => changeMode('live')}>▶ 操作する</button>
        </div>
        {recording ? (
          <button className="rec on" onClick={finishRecording}>■ 記録終了 <small>F12</small></button>
        ) : (
          <button className="rec" onClick={startRecording} title="自分のクリックやキー入力を録って、再生できるノードにします">● 記録</button>
        )}
        <div className="right">
          <span className={`pill ${srvStatus.status}`}>
            {{ idle: '待機', loading: '読み込み中', running: '準備OK', error: 'エラー' }[srvStatus.status]}
          </span>
          <span className={`pill ${saved ? 'ok' : 'saving'}`}>{saved ? '保存済み' : '保存中…'}</span>
          <button className="ghost icon" onClick={undo} title="元に戻す (Ctrl+Z)">↶</button>
          <button className="ghost icon" onClick={redo} title="やり直す (Ctrl+Y)">↷</button>
          <button className="ghost icon" onClick={() => setWelcome(true)} title="はじめかた">?</button>
          <button className={`ghost ${showPal ? 'tog' : ''}`} onClick={() => setShowPal((v) => !v)} title="ノード一覧の表示/非表示">ノード</button>
          <button className={`ghost ${showSide ? 'tog' : ''}`} onClick={() => setShowSide((v) => !v)} title="ライブ画面・設定の表示/非表示">パネル</button>
        </div>
      </header>
      {(graphError || srvStatus.error) && (
        <div className="banner">⚠ {graphError || srvStatus.error}</div>
      )}
      <main className={`${showPal ? 'with-pal' : ''} ${showSide ? 'with-side' : ''}`}>
        {showPal && <Palette />}
        <div className="canvas" onDragOver={(e) => e.preventDefault()} onDrop={onDrop} onDoubleClick={onCanvasDoubleClick}>
          <ReactFlow
            nodes={nodes}
            edges={styledEdges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            deleteKeyCode={['Delete', 'Backspace']}
            zoomOnDoubleClick={false}
            onPaneContextMenu={onPaneContextMenu}
            onNodeContextMenu={onNodeContextMenu}
            fitView
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
            colorMode="dark"
          >
            <Background gap={22} size={1.4} color="#2a3242" />
            <MiniMap pannable zoomable nodeColor={(n) => meta.categories[meta.defs[n.type]?.category]?.color || '#555'} maskColor="rgba(8,11,18,.7)" />
            <Controls />
          </ReactFlow>
          {nodes.length === 0 && (
            <div className="empty-hint"><b>ダブルクリック</b>でノードを追加<br /><span>右クリックでメニュー ・ / キーで検索</span></div>
          )}
        </div>
        {showSide && <aside className="side">
          <div className="tabs">
            <button className={tab === 'live' ? 'on' : ''} onClick={() => setTab('live')}>ライブ画面</button>
            <button className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>設定</button>
          </div>
          <div className="side-body">
            {tab === 'live' ? (
              <LivePanel highlightLabel={highlightLabel} onPick={onPick} onCrop={cropImage} />
            ) : (
              <SettingsPanel settings={project?.settings} labels={meta.labels} onApply={applySettings}
                status={srvStatus.status} error={srvStatus.error}
                onPickFromLive={() => { setTab('live'); setToast('ライブ画面で拾いたい色をクリックしてください'); setTimeout(() => setToast(null), 2500) }} />
            )}
          </div>
        </aside>}
      </main>
      {quick && <QuickAdd x={quick.x} y={quick.y} onPick={(t) => addNodeAt(t, quick.x, quick.y)} onClose={() => setQuick(null)} />}
      {ctx && <ContextMenu {...ctx} onClose={() => setCtx(null)} />}
      {askLive && (
        <Modal title="実際に操作を始めます" onClose={() => setAskLive(false)} actions={<>
          <button className="ghost" onClick={() => setAskLive(false)}>やめる</button>
          <button className="primary danger" onClick={() => changeMode('live')}>▶ 操作を始める</button>
        </>}>
          <p>このパソコンのキーボードとマウスを SightFlow が動かします。</p>
          <p className="big-stop">止めるときは <kbd>F12</kbd></p>
          <p className="muted">または、この画面の「■ 停止」を押してください。</p>
        </Modal>
      )}
      {welcome && (
        <Welcome
          onDemo={() => { markWelcome(); setWelcome(false) }}
          onNew={async (name) => {
            markWelcome()
            setWelcome(false)
            await api.post('/api/projects/new', { name })
            await loadProject()
            setShowSide(true)
            setTab('settings')
            flash('まず右の設定で、ゲームのウィンドウを選んでください', 3500)
          }} />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  )
}
