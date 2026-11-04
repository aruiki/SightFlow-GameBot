import { memo } from 'react'
import { Handle, Position, useEdges, useReactFlow } from '@xyflow/react'
import { IO_NAMES, TYPE_COLORS, useMeta, useNodeStatus } from './store'

function PropEditor({ prop, value, onChange, labels, disabled }) {
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
  const { defs, categories, labels } = useMeta()
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
                      <span>{IO_NAMES[inp.name] ?? inp.name}</span>
                    </>
                  )}
                </div>
                <div className="io out">
                  {out && (
                    <>
                      <span>{IO_NAMES[out.name] ?? out.name}</span>
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
              <label key={p.name} className={linked ? 'linked' : ''}>
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
