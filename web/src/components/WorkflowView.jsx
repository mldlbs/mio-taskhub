import { useState } from 'react'
import BoardView from './BoardView'
import FlowView from './FlowView'

const MODES = [
  { id: 'state', label: '按状态' },
  { id: 'stage', label: '按阶段' },
  { id: 'flow', label: '依赖图' },
]

export default function WorkflowView({ tasks, onMoveState, onMoveStage, onCancel, onOpen, onAdvance, loading, focus }) {
  const [mode, setMode] = useState('state')

  return (
    <div className="workflow-view">
      <div className="workflow-switch" role="tablist" aria-label="工作流维度">
        {MODES.map(m => (
          <button
            key={m.id}
            role="tab"
            aria-selected={mode === m.id}
            className={`workflow-switch__btn${mode === m.id ? ' is-on' : ''}`}
            onClick={() => setMode(m.id)}
          >{m.label}</button>
        ))}
      </div>

      {mode === 'state' && (
        <BoardView
          tasks={tasks} groupBy="state"
          onMove={onMoveState} onCancel={onCancel} onOpen={onOpen}
          loading={loading} focus={focus}
        />
      )}
      {mode === 'stage' && (
        <BoardView
          tasks={tasks} groupBy="stage"
          onMove={onMoveStage} onCancel={onCancel} onOpen={onOpen}
          loading={loading} focus={focus}
        />
      )}
      {mode === 'flow' && (
        <FlowView
          tasks={tasks}
          onOpen={onOpen} onCancel={onCancel}
          onAdvance={onAdvance} onMoveToStage={onMoveStage}
        />
      )}
    </div>
  )
}
