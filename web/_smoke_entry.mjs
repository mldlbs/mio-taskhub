import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import TopoView from './src/components/TopoView.jsx'

const cases = [
  { name: 'empty', tasks: [] },
  { name: 'single', tasks: [
      { id: 't1', title: 'Task 1', stage: 'ready', priority: 2, est_duration_min: 30, depends_on: [] },
    ] },
  { name: 'multi-critical', tasks: [
      { id: 't1', title: 'A', stage: 'planning', priority: 2, est_duration_min: 60, depends_on: [] },
      { id: 't2', title: 'B', stage: 'planning', priority: 2, est_duration_min: 30, depends_on: ['t1'] },
      { id: 't3', title: 'C', stage: 'planning', priority: 2, est_duration_min: 30, depends_on: ['t1'] },
      { id: 't4', title: 'D', stage: 'planning', priority: 2, est_duration_min: 10, depends_on: ['t2', 't3'] },
    ] },
  { name: 'deps', tasks: [
      { id: 't1', title: 'Root', stage: 'done', priority: 2, est_duration_min: 40, depends_on: [] },
      { id: 't2', title: 'Child', stage: 'implementing', priority: 2, est_duration_min: 20, depends_on: ['t1'] },
    ] },
]

let ok = true
for (const c of cases) {
  try {
    const html = renderToStaticMarkup(React.createElement(TopoView, { tasks: c.tasks, onOpen: () => {} }))
    const hasToggle = html.includes('topo__toggle')
    const hasNode = html.includes('data-task-id') || html.includes('topo-node')
    if (!hasToggle || !hasNode) {
      console.error(`[FAIL] ${c.name}: toggle=${hasToggle} node=${hasNode}`)
      console.error(html)
      ok = false
    } else {
      console.log(`[PASS] ${c.name} (${html.length} chars)`)
    }
  } catch (e) {
    console.error(`[FAIL] ${c.name} threw:`, e.message)
    ok = false
  }
}

if (!ok) {
  console.error('SMOKE FAILED')
  process.exit(1)
}
console.log('SMOKE OK')
