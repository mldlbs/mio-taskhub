// node scripts/smoke-gantt.mjs  （在 web/ 目录下执行）
// GanttView 冒烟渲染门禁：esbuild 单文件 bundle + react-dom/server 静态渲染四组 fixtures，
// 断言不抛异常且输出含期望 className。GanttView 未被 vite build 纳入，此脚本是唯一回归防线。
import { build } from 'esbuild'
import path from 'path'
import { createRequire } from 'module'
import { fileURLToPath } from 'url'

const require = createRequire(import.meta.url)
const here = path.dirname(fileURLToPath(import.meta.url))
const entry = path.join(here, '..', 'src', 'components', 'GanttView.jsx')
const outfile = path.join(here, '.smoke-gantt.cjs')

const rmOutfile = () => import('node:fs').then(fs => { try { fs.rmSync(outfile) } catch {} })

await build({
  entryPoints: [entry],
  bundle: true,
  format: 'cjs',
  jsx: 'automatic',
  platform: 'node',
  outfile,
  external: ['react', 'react-dom'],
  logLevel: 'silent',
})

const { default: GanttView } = require(outfile)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

// 组件是 hooks 函数组件，必须用 createElement 经 React 渲染，不能直接调用
function render(label, tasks, expectClass) {
  try {
    const html = renderToStaticMarkup(React.createElement(GanttView, { tasks, onOpen: () => {} }))
    if (expectClass && !html.includes(expectClass)) {
      throw new Error(`输出缺少期望 className "${expectClass}"`)
    }
    console.log(`OK   ${label}`)
    return html
  } catch (e) {
    console.error(`FAIL ${label}: ${e.message}`)
    process.exitCode = 1
    return null
  }
}

const T = (id, extra = {}) => ({ id, title: 'T' + id, est_duration_min: 60, depends_on: [], state: 'queued', stage: 'ready', ...extra })

// 空任务
render('empty', [], 'gantt__empty')
// 全零时长
render('all-zero', [T('a', { est_duration_min: 0 }), T('b', { est_duration_min: 0, depends_on: ['a'] })], 'data-gantt-bar="a"')
// 环输入
render('cycle', [T('a', { depends_on: ['b'] }), T('b', { depends_on: ['a'] })], 'data-gantt-bar="a"')
// 正常多任务（含 agent/project 分组 + 关键路径）
render('normal', [
  T('a', { est_duration_min: 120 }),
  T('b', { est_duration_min: 60, depends_on: ['a'] }),
  T('c', { est_duration_min: 180, target_agent_type: 'codex', project: 'p1' }),
  T('d', { est_duration_min: 30, depends_on: ['b', 'c'], target_agent_type: 'claude' }),
], 'data-gantt-bar="a"')

await rmOutfile()
if (process.exitCode) process.exit(process.exitCode)
console.log('Gantt smoke OK')