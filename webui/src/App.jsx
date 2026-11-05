import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, addEdge,
  useEdgesState, useNodesState, useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import FlowNode from './FlowNode'
import LivePanel from './LivePanel'
import ScreenPanel from './ScreenPanel'
import DatasetPanel from './DatasetPanel'
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
    addNodes({ id: `${type}_${Math.random().toString(36).slice(2, 8)}`, type, position: pos, data: {} })
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
  const loaded = useRef(false)
  const { screenToFlowPosition } = useReactFlow()
  const nodeTypes = useMemo(() => nodeTypesFor(meta.defs), [meta.defs])

  const refreshState = useCallback(async () => {
    const st = await api.get('/api/state')
    metaStore.set((m) => ({ ...m, labels: st.labels }))
    setSrvStatus({ status: st.status, error: st.error })
    setMode(st.mode)
    return st
  }, [])

  useEffect(() => {
    ;(async () => {
      const types = await api.get('/api/node-types')
      metaStore.set((m) => ({
        ...m,
        categories: types.categories,
        ioNames: types.io_names,
        defs: Object.fromEntries(types.types.map((t) => [t.type, t])),
      }))
      const st = await refreshState()
      setProject(st.project)
      setGraphError(st.graph_error)
      setNodes(st.project.graph.nodes || [])
      setEdges(st.project.graph.edges || [])
      const scr = await api.get('/api/screens')
      metaStore.set((m) => ({ ...m, screens: scr.screens || [] }))
      setTimeout(() => (loaded.current = true), 0)
    })()
    return connectLive((msg) => {
      if (msg.type === 'status') {
        setSrvStatus((old) => {
          if (old.status !== msg.status) refreshState()
          return { status: msg.status, error: msg.error }
        })
        setMode(msg.mode)
      } else if (msg.mode) setMode(msg.mode)
    })
  }, [refreshState, setEdges, setNodes])

  // autosave the graph (debounced) whenever nodes/edges change
  useEffect(() => {
    if (!loaded.current) return
    setSaved(false)
    const t = setTimeout(async () => {
      const graph = {
        nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })),
        edges: edges.map(({ id, source, sourceHandle, target, targetHandle }) => ({ id, source, sourceHandle, target, targetHandle })),
      }
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
    setNodes((ns) => [...ns, { id: `${type}_${Math.random().toString(36).slice(2, 8)}`, type, position, data: {} }])
  }

  const changeMode = async (m) => {
    if (m === 'live' && project?.settings?.source?.type !== 'demo') {
      const ok = window.confirm(
        '実際にキーボードとマウスを操作します。\n停止するときは F12 キー、またはこの画面の「停止」を押してください。\n\n開始しますか？',
      )
      if (!ok) return
    }
    await api.post('/api/mode', { mode: m })
    setMode(m)
  }

  const applySettings = async (s) => {
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
      api.get('/api/screens').then((r) => metaStore.set((m) => ({ ...m, screens: r.screens || [] })))
    })
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
          <span className="proj">{project?.name}</span>
        </div>
        <div className="modes">
          <button className={mode === 'stop' ? 'on stop' : ''} onClick={() => changeMode('stop')}>■ 停止</button>
          <button className={mode === 'watch' ? 'on watch' : ''} onClick={() => changeMode('watch')}>👁 見るだけ</button>
          <button className={mode === 'live' ? 'on live' : ''} onClick={() => changeMode('live')}>● 操作する</button>
        </div>
        <div className="right">
          <span className={`pill ${srvStatus.status}`}>
            {{ idle: '待機', loading: '読み込み中', running: '準備OK', error: 'エラー' }[srvStatus.status]}
          </span>
          <span className={`pill ${saved ? 'ok' : 'saving'}`}>{saved ? '保存済み' : '保存中…'}</span>
          <button className="ghost" onClick={() => {
            if (nodes.length && !window.confirm('キャンバスのノードをすべて消して、新しく作り始めますか？\n（先に「書き出し」で保存しておくと安心です）')) return
            setNodes([])
            setEdges([])
          }}>新規</button>
          <button className="ghost" onClick={exportProject}>書き出し</button>
          <label className="ghost file">
            読み込み
            <input type="file" accept=".json" onChange={(e) => e.target.files[0] && importProject(e.target.files[0])} />
          </label>
        </div>
      </header>
      {(graphError || srvStatus.error) && (
        <div className="banner">⚠ {graphError || srvStatus.error}</div>
      )}
      <main>
        <Palette />
        <div className="canvas" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
          <ReactFlow
            nodes={nodes}
            edges={styledEdges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            deleteKeyCode={['Delete', 'Backspace']}
            fitView
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
            colorMode="dark"
          >
            <Background gap={22} size={1.4} color="#2a3242" />
            <MiniMap pannable zoomable nodeColor={(n) => meta.categories[meta.defs[n.type]?.category]?.color || '#555'} maskColor="rgba(8,11,18,.7)" />
            <Controls />
          </ReactFlow>
        </div>
        <aside className="side">
          <div className="tabs">
            <button className={tab === 'live' ? 'on' : ''} onClick={() => setTab('live')}>ライブ画面</button>
            <button className={tab === 'screens' ? 'on' : ''} onClick={() => setTab('screens')}>画面</button>
            <button className={tab === 'dataset' ? 'on' : ''} onClick={() => setTab('dataset')}>学習</button>
            <button className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>設定</button>
          </div>
          <div className="side-body">
            {tab === 'live' && (
              <LivePanel highlightLabel={highlightLabel} onPick={onPick} />
            )}
            {tab === 'screens' && <ScreenPanel />}
            {tab === 'dataset' && <DatasetPanel />}
            {tab === 'settings' && (
              <SettingsPanel settings={project?.settings} labels={meta.labels} onApply={applySettings}
                status={srvStatus.status} error={srvStatus.error} />
            )}
          </div>
        </aside>
      </main>
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
