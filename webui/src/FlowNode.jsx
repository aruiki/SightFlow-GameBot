import { memo } from 'react'
import { Handle, Position, useEdges, useReactFlow } from '@xyflow/react'
import { IO_NAMES, TYPE_COLORS, useMeta, useNodeStatus } from './store'

function StepsEditor({ value, onChange }) {
  const steps = Array.isArray(value) ? value : []
  const upd = (i, patch) => onChange(steps.map((st, j) => (j === i ? { ...st, ...patch } : st)))
  const num = (v) => (v === '' ? 0 : Number(v))
  return (
    <div className="steps nodrag nowheel">
      {steps.length === 0 && <div className="steps-empty">上の「● 記録」で操作を録るか、下の ＋ で追加</div>}
      {steps.map((st, i) => (
        <div className="step" key={i}>
          <span className="step-n">{i + 1}</span>
          <input className="prop-input w" type="number" step="0.1" min="0" title="前の操作からの待ち時間（秒）"
            value={st.wait ?? 0} onChange={(e) => upd(i, { wait: num(e.target.value) })} />
          <span className="step-s">秒後</span>
          <select className="prop-input k" value={st.kind} onChange={(e) => upd(i, { kind: e.target.value })}>
            <option value="click">クリック</option>
            <option value="key">キー</option>
          </select>
          {st.kind === 'key' ? (
            <input className="prop-input v" value={st.key ?? ''} placeholder="space" onChange={(e) => upd(i, { key: e.target.value })} />
          ) : (
            <>
              <input className="prop-input xy" type="number" step="0.01" title="x (0-1)" value={st.x ?? 0.5} onChange={(e) => upd(i, { x: num(e.target.value) })} />
              <input className="prop-input xy" type="number" step="0.01" title="y (0-1)" value={st.y ?? 0.5} onChange={(e) => upd(i, { y: num(e.target.value) })} />
            </>
          )}
          <button className="step-del" title="この操作を消す" onClick={() => onChange(steps.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div className="step-add">
        <button onClick={() => onChange([...steps, { kind: 'click', x: 0.5, y: 0.5, wait: 0.5 }])}>＋ クリック</button>
        <button onClick={() => onChange([...steps, { kind: 'key', key: 'space', wait: 0.5 }])}>＋ キー</button>
      </div>
    </div>
  )
}

function PropEditor({ prop, value, onChange, labels, disabled }) {
  if (prop.kind === 'steps') return <StepsEditor value={value} onChange={onChange} />
  const common = { className: 'nodrag prop-input', disabled }
  if (prop.kind === 'select') {
    return (
      <select {...common} value={value ?? prop.default} onChange={(e) => onChange(e.target.value)}>
        {prop.options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    )
  }
  if (prop.kind === 'label') {
    return (
      <select {...common} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">（選ぶ）</option>
        {[...new Set([...labels, value].filter(Boolean))].map((l) => (
          <option key={l} value={l}>{l}</option>
        ))}
      </select>
    )
  }
  if (prop.kind === 'number') {
    return (
      <input
        {...common}
        type="number"
        step="any"
        value={value ?? prop.default}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
      />
    )
  }
  return <input {...common} value={value ?? prop.default} onChange={(e) => onChange(e.target.value)} />
}

function FlowNode({ id, type, data, selected }) {
  const { defs, categories, labels, ioNames } = useMeta()
  const ioName = (n) => ioNames?.[n] ?? IO_NAMES[n] ?? n
  const def = defs[type]
  const st = useNodeStatus(id)
  const edges = useEdges()
  const { updateNodeData } = useReactFlow()
  if (!def) return <div className="fn err">不明なノード: {type}</div>
  const cat = categories[def.category] || {}
  const connected = new Set(edges.filter((e) => e.target === id).map((e) => e.targetHandle))
  const cls = ['fn', selected && 'sel', st.active && 'active', st.error && 'err'].filter(Boolean).join(' ')
  const rows = Math.max(def.inputs.length, def.outputs.length)

  return (
    <div className={cls} style={{ '--cat': cat.color }} title={def.desc}>
      <div className="fn-head">
        <span className="fn-cat">{cat.title}</span>
        <span className="fn-title">{def.title}</span>
      </div>
      {rows > 0 && (
        <div className="fn-io">
          {Array.from({ length: rows }).map((_, i) => {
            const inp = def.inputs[i]
            const out = def.outputs[i]
            return (
              <div className="io-row" key={i}>
                <div className="io in">
                  {inp && (
                    <>
                      <Handle type="target" position={Position.Left} id={inp.name}
                        className="h" style={{ '--h': TYPE_COLORS[inp.type] }} />
                      <span>{ioName(inp.name)}</span>
                    </>
                  )}
                </div>
                <div className="io out">
                  {out && (
                    <>
                      <span>{ioName(out.name)}</span>
                      <Handle type="source" position={Position.Right} id={out.name}
                        className="h" style={{ '--h': TYPE_COLORS[out.type] }} />
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {def.props.length > 0 && (
        <div className="fn-props">
          {def.props.map((p) => {
            const linked = connected.has(p.name)
            return (
              <label key={p.name} className={[linked && 'linked', p.kind === 'steps' && 'wide'].filter(Boolean).join(' ')}>
                <span>{p.label}</span>
                <PropEditor prop={p} value={data[p.name]} labels={labels} disabled={linked}
                  onChange={(v) => updateNodeData(id, { [p.name]: v })} />
              </label>
            )
          })}
        </div>
      )}
      <div className="fn-status" title={st.error || ''}>
        {st.error ? `⚠ ${st.error}` : st.text ?? '—'}
      </div>
    </div>
  )
}

export default memo(FlowNode)
