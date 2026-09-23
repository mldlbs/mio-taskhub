import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { api } from '../api'
import { docStateLabel, docStateTone, stateOfDoc, stateOfStatus, lifecycleTrack } from '../docLifecycle'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

marked.setOptions({ gfm: true, breaks: false, headerIds: false, mangle: false })
marked.use({ renderer: { heading(token) { const level = token.depth ?? token.level; const id = 'mdh-' + Math.random().toString(36).slice(2, 8); return `<h${level} id="${id}">${token.text}</h${level}>` } } })

const fmtSize = (n) => n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`
const sourceLabel = (s) =>
  s === 'field' ? '字段关联' :
  s === 'deliverable' ? '交付物' :
  s === 'file' ? '相关文件' :
  s === 'navigated' ? '文内跳转' : '相关'

const KIND_META = {
  spec:          { label: 'SPEC', group: 'Spec 设计文档', order: 0 },
  plan:          { label: 'PLAN', group: 'Plan 实现计划', order: 1 },
  review:        { label: '审查', group: 'Review 审查报告', order: 2 },
  requirement:   { label: '需求', group: 'Requirement 需求文档', order: 3 },
  test:          { label: '测试', group: 'Test 测试计划', order: 4 },
  architecture:  { label: '架构', group: 'Architecture 架构文档', order: 5 },
  api:           { label: 'API', group: 'API 接口文档', order: 6 },
  readme:        { label: 'README', group: 'README 说明', order: 7 },
  changelog:     { label: '变更', group: 'Changelog 变更记录', order: 8 },
  // ── 扩展文档类型（与 DOC_KINDS 保持一致，2026-09-17）──
  decision:      { label: '决策', group: 'Decision 决策记录', order: 9 },
  risk:          { label: '风险', group: 'Risk 风险登记', order: 10 },
  setup:         { label: '环境', group: 'Setup 环境搭建', order: 11 },
  runbook:       { label: '运维', group: 'Runbook 部署运维', order: 12 },
  glossary:      { label: '术语', group: 'Glossary 术语表', order: 13 },
  userguide:     { label: '手册', group: 'UserGuide 用户手册', order: 14 },
  research:      { label: '预研', group: 'Research 技术预研', order: 15 },
  retro:         { label: '复盘', group: 'Retro 复盘', order: 16 },
  milestone:     { label: '里程碑', group: 'Milestone 里程碑', order: 17 },
  'data-model':  { label: '数据', group: 'DataModel 数据模型', order: 18 },
  security:      { label: '安全', group: 'Security 安全', order: 19 },
  troubleshooting: { label: '排查', group: 'Troubleshooting 故障排查', order: 20 },
  incident:      { label: '事故', group: 'Incident 事故记录', order: 21 },
  // 非文档类的挂载项（来自 task.deliverables / task.files）
  deliverable:   { label: '交付物', group: 'Deliverables 交付物', order: 22 },
  file:          { label: '文件', group: 'Files 相关文件', order: 23 },
}
const kindMeta = (k) => KIND_META[k] || { label: k.toUpperCase(), group: k, order: 9 }

// 文档内相对资源（图片）解析：workspace 相对目录 + 引用路径
const ABS_SRC_RE = /^(?:[a-z][a-z0-9+.-]*:|\/\/|\/)/i
const decodeMaybe = (s) => { try { return decodeURIComponent(s) } catch { return s } }
const dirOf = (p) => { const s = (p || '').replace(/\\/g, '/'); return s.includes('/') ? s.slice(0, s.lastIndexOf('/')) : '' }
function joinRel(dir, src) {
  const out = (dir || '').split('/').filter(Boolean)
  for (const part of src.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') out.pop()
    else out.push(part)
  }
  return out.join('/')
}

const RISK_RE = /(风险|隐患|注意|警告|⚠️?|❗|‼|危险|禁止|必须|关键点|坑|caveat|risk|warning|caution|danger|important)/i
const RISK_TAGS = ['strong', 'em', 'li', 'p', 'blockquote', 'td']

function markRisks(root) {
  if (!root) return 0
  let count = 0
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const hits = []
  while (walker.nextNode()) {
    const n = walker.currentNode
    if (!n.nodeValue || !n.nodeValue.trim()) continue
    if (n.parentElement?.closest('pre, code, .md-risk')) continue
    if (RISK_RE.test(n.nodeValue)) hits.push(n)
  }
  for (const node of hits) {
    const el = node.parentElement
    if (!el || RISK_TAGS.includes(el.tagName.toLowerCase()) === false) {
      el?.classList?.add('md-risk')
      count++
      continue
    }
    el.classList.add('md-risk')
    count++
  }
  return count
}

function wordCount(text) {
  if (!text) return 0
  const ascii = (text.match(/[a-zA-Z0-9]+/g) || []).length
  const cjk = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]/g) || []).length
  return ascii + cjk
}

function readingTime(wc) {
  const min = Math.ceil(wc / 300)
  return min < 1 ? '< 1 分钟' : `~${min} 分钟`
}

export default function DocPanel({ task, onClose }) {
  const [docs, setDocs] = useState(null)
  const [selected, setSelected] = useState(null)
  const [html, setHtml] = useState('')
  const [rawContent, setRawContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [truncated, setTruncated] = useState(false)
  const [missing, setMissing] = useState(false)
  const [query, setQuery] = useState('')
  const [activeKind, setActiveKind] = useState(null)
  const [toc, setToc] = useState([])
  const [riskCount, setRiskCount] = useState(0)
  const [findQ, setFindQ] = useState('')
  const [findHits, setFindHits] = useState(0)
  const [progress, setProgress] = useState(0)
  const [lightbox, setLightbox] = useState(null)
  // 文档生命周期总览（kind -> {state, states, allowed_next, ...}），缺失不阻塞文档浏览
  const [statusMap, setStatusMap] = useState({})
  const viewRef = useRef(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') { if (lightbox) setLightbox(null); else onClose() } }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, lightbox])

  const select = useCallback((doc) => {
    setSelected(doc); setLoading(true); setErr(null); setHtml(''); setRawContent(''); setToc([]); setFindQ(''); setFindHits(0); setProgress(0)
    const p = doc.source === 'field'
      ? api.getTaskDoc(task.id, doc.kind)
      : api.getTaskFile(task.id, doc.rel_path)
    p.then(r => {
        if (r.binary) {
          // 交付物 / 相关文件可能是二进制（PDF、图片…），不能按文本渲染
          setTruncated(false); setMissing(false); setRawContent('')
          setHtml(`<p class="docpanel__notice">二进制文件（${fmtSize(r.size ?? 0)}），无法按文本预览。</p>`)
          return
        }
        const content = r.content || ''
        setTruncated(!!r.truncated); setMissing(!!r.missing)
        setRawContent(content)
        setHtml(DOMPurify.sanitize(marked.parse(content), { ADD_ATTR: ['target'] }))
      })
      .catch(e => { setErr(e.stack || e.message); setTruncated(false); setMissing(false) })
      .finally(() => setLoading(false))
  }, [task.id])

  useEffect(() => {
    let alive = true
    api.getTaskDocuments(task.id).then(r => { if (!alive) return; const list = r.documents || []; setDocs(list); if (list.length) select(list[0]) })
      .catch(e => { if (alive) { setDocs([]); setErr(e.message) } })
    // 生命周期总览：拿 states 全序列 + 合法后继，用于状态徽标与推进条
    api.getDocStatuses(task.id)
      .then(r => { if (alive) setStatusMap(r.statuses || {}) })
      .catch(() => { if (alive) setStatusMap({}) })
    return () => { alive = false }
  }, [task.id, select])

  useEffect(() => {
    const root = viewRef.current
    if (!root || !html) return
    root.querySelectorAll('pre code').forEach(b => { try { hljs.highlightElement(b) } catch {} })
    root.querySelectorAll('a[href]').forEach(a => {
      a.target = '_blank'
      a.rel = 'noopener noreferrer'
      const rawHref = a.getAttribute('href') || ''
      // 相对链接指向 workspace 内文件 → 改写到 /raw，否则浏览器会请求前端静态路径 → 404
      if (rawHref && !rawHref.startsWith('#') && !rawHref.includes('?') && !ABS_SRC_RE.test(rawHref)) {
        const baseDir = selected && selected.dir != null ? selected.dir : dirOf(selected && selected.rel_path)
        const resolved = joinRel(baseDir, decodeMaybe(rawHref))
        a.href = api.rawFileUrl(task.id, resolved)
        // 相对 .md 链接在面板内直接打开目标文档（拦截点击；href 保留 /raw 作为兜底）
        if (/\.(md|markdown)$/i.test(resolved)) {
          a.addEventListener('click', (e) => {
            e.preventDefault(); e.stopPropagation()
            select({
              name: resolved.split('/').pop() || resolved,
              rel_path: resolved,
              kind: 'file', source: 'navigated', dir: dirOf(resolved),
            })
          })
        }
      }
    })
    root.querySelectorAll('img').forEach(img => {
      // 相对引用的图片指向 workspace 内的原始文件，否则浏览器会请求前端静态路径 → 404
      const rawSrc = img.getAttribute('src') || ''
      if (rawSrc && !ABS_SRC_RE.test(rawSrc)) {
        const baseDir = selected && selected.dir != null ? selected.dir : dirOf(selected && selected.rel_path)
        img.src = api.rawFileUrl(task.id, joinRel(baseDir, decodeMaybe(rawSrc)))
      }
      img.loading = 'lazy'; img.style.maxWidth = '100%'; img.style.height = 'auto'
      img.style.cursor = 'zoom-in'
      img.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); setLightbox(img.src) })
    })
    root.querySelectorAll('table').forEach(t => { if (!t.parentElement.classList.contains('md-table-wrap')) { const w = document.createElement('div'); w.className = 'md-table-wrap'; t.before(w); w.appendChild(t) } })
    const rc = markRisks(root)
    setRiskCount(rc)
    const hs = [...root.querySelectorAll('h1,h2,h3')]
    setToc(hs.map((h, i) => { if (!h.id) h.id = 'mdh-' + i; return { id: h.id, text: h.textContent, level: Number(h.tagName[1]) } }))
  }, [html, selected, task.id, select])

  useEffect(() => {
    const root = viewRef.current
    if (!root) return
    root.querySelectorAll('mark.md-find').forEach(m => { const p = m.parentNode; p.replaceChild(document.createTextNode(m.textContent), m); p.normalize() })
    if (!findQ.trim()) { setFindHits(0); return }
    const needle = findQ.toLowerCase()
    let hits = 0
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    const targets = []
    while (walker.nextNode()) {
      const n = walker.currentNode
      if (n.nodeValue && n.nodeValue.toLowerCase().includes(needle) && !n.parentElement.closest('.md-find')) targets.push(n)
    }
    for (const node of targets) {
      const text = node.nodeValue
      const lower = text.toLowerCase()
      const frag = document.createDocumentFragment()
      let idx = 0
      while (true) {
        const at = lower.indexOf(needle, idx)
        if (at === -1) break
        frag.appendChild(document.createTextNode(text.slice(idx, at)))
        const mk = document.createElement('mark')
        mk.className = 'md-find'
        mk.textContent = text.slice(at, at + findQ.length)
        frag.appendChild(mk)
        idx = at + findQ.length
        hits++
        if (hits > 500) break
      }
      frag.appendChild(document.createTextNode(text.slice(idx)))
      node.parentNode.replaceChild(frag, node)
    }
    setFindHits(hits)
  }, [findQ, html])

  useEffect(() => {
    const el = viewRef.current
    if (!el) return
    const onScroll = () => {
      const total = el.scrollHeight - el.clientHeight
      setProgress(total > 0 ? Math.min(100, Math.round(el.scrollTop / total * 100)) : 0)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  const goTo = (id) => { const el = document.getElementById(id); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }) }

  const jumpFind = (dir) => {
    const marks = [...(viewRef.current?.querySelectorAll('mark.md-find') || [])]
    if (!marks.length) return
    const cur = marks.findIndex(m => m.classList.contains('is-cur'))
    const next = dir > 0 ? Math.min(cur + 1, marks.length - 1) : Math.max(cur - 1, 0)
    marks.forEach(m => m.classList.remove('is-cur'))
    marks[next]?.classList.add('is-cur')
    marks[next]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const printDoc = () => {
    const content = viewRef.current?.querySelector('.md')?.innerHTML
    if (!content) return
    const w = window.open('', '_blank')
    w.document.write(`<!DOCTYPE html><html><head><title>${selected?.name || '文档'}</title>
      <style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;line-height:1.7;color:#222}
      pre{background:#f5f5f5;padding:12px;border-radius:6px;overflow-x:auto}
      code{font-size:0.9em}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:6px 10px}
      img{max-width:100%}blockquote{border-left:3px solid #ccc;margin:0;padding-left:16px;color:#555}
      @media print{body{margin:0}pre{white-space:pre-wrap}}</style></head><body>`)
    w.document.write(content)
    w.document.close()
    w.print()
  }

  const all = docs || []
  const q = query.trim().toLowerCase()
  const filtered = q ? all.filter(d => d.name.toLowerCase().includes(q) || d.rel_path.toLowerCase().includes(q)) : all

  // 顶部「切换类型」标签：列出清单里实际存在的文档类型（按 KIND_META 顺序）
  const kindsPresent = useMemo(() => {
    const counts = {}
    for (const d of all) counts[d.kind] = (counts[d.kind] || 0) + 1
    return Object.keys(counts)
      .sort((a, b) => kindMeta(a).order - kindMeta(b).order)
      .map(k => ({ kind: k, label: kindMeta(k).label, count: counts[k] }))
  }, [all])

  // 按 kind 分组（受顶部类型筛选影响）
  const groups = {}
  for (const d of filtered) {
    if (activeKind && d.kind !== activeKind) continue
    const meta = kindMeta(d.kind)
    if (!groups[d.kind]) groups[d.kind] = { meta, items: [] }
    groups[d.kind].items.push(d)
  }
  const sortedGroups = Object.values(groups).sort((a, b) => a.meta.order - b.meta.order)

  const switchKind = (kind) => {
    setActiveKind(kind)
    if (kind) {
      const first = all.find(d => d.kind === kind)
      if (first) select(first)
    }
  }

  const wc = wordCount(rawContent)

  // 选中文档的生命周期：状态以清单条目为准（回退总览），赛道全序列来自总览接口
  const selStatus = selected ? (stateOfDoc(selected) || stateOfStatus(statusMap[selected.kind])) : null
  const selTrack = selected ? lifecycleTrack((statusMap[selected.kind] || {}).states, selStatus) : []

  return (
    <div className="overlay docpanel-overlay" onClick={onClose}>
      <div className="docpanel" role="dialog" aria-modal="true" aria-label="文档浏览" onClick={e => e.stopPropagation()}>
        <header className="docpanel__head">
          <span className="docpanel__eyebrow">文档浏览</span>
          <h2 className="docpanel__title">{task.title}</h2>
          <span className="docpanel__sub docpanel__ws">{task.workspace ? task.workspace : '无工作区'}</span>
          <div className="docpanel__head-actions">
            {selected && <button className="btn btn--ghost btn--xs" onClick={printDoc} title="打印 / 导出 PDF">🖨 打印</button>}
            <button className="modal__close" onClick={onClose} aria-label="关闭">×</button>
          </div>
        </header>
        <div className="docpanel__toolbar">
          <input className="docpanel__search-input" placeholder="搜索文档名 / 路径" value={query} onChange={e => setQuery(e.target.value)} />
          <input className="docpanel__search-input docpanel__find-input" placeholder="全文查找…" value={findQ} onChange={e => setFindQ(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); jumpFind(e.shiftKey ? -1 : 1) } }} />
          {findQ && <span className="docpanel__find-count">{findHits} 处命中（Enter ↴ / Shift+Enter ↰）</span>}
          <div className="docpanel__kinds">
            <button className={`docpanel__kindtab${activeKind ? '' : ' is-active'}`} onClick={() => switchKind(null)}>全部<span>{all.length}</span></button>
            {kindsPresent.map(k => (
              <button key={k.kind} className={`docpanel__kindtab${activeKind === k.kind ? ' is-active' : ''}`} onClick={() => switchKind(k.kind)}>
                {k.label}<span>{k.count}</span>
              </button>
            ))}
          </div>
          <span className="docpanel__count">{docs ? `${docs.length} 篇文档` : '加载中…'}{riskCount > 0 && ` · ${riskCount} 处风险标记`}</span>
        </div>
        <div className="docpanel__body">
          <nav className="docpanel__list">
            {docs === null && <p className="detail-muted">加载中…</p>}
            {docs && docs.length === 0 && <p className="detail-muted">该任务无关联文档</p>}
            {sortedGroups.map(g => (
              <div key={g.meta.group} className="docpanel__group">
                <div className="docpanel__group-title">{g.meta.group}</div>
                {g.items.map(d => {
                  const km = kindMeta(d.kind)
                  const st = stateOfDoc(d)
                  return (
                    <button key={d.rel_path} className={`docpanel__item${selected?.rel_path === d.rel_path ? ' is-active' : ''}`} onClick={() => select(d)}>
                      <span className={`docpanel__kind docpanel__kind--${d.kind}`}>{km.label}</span>
                      <span className="docpanel__name">
                        {d.name}
                        {st && <span className={`docpanel__docstate docpanel__docstate--${docStateTone(st)}`}
                                     title={`生命周期状态：${docStateLabel(st)}`}>{docStateLabel(st)}</span>}
                      </span>
                      {d.exists === false && <span className="docpanel__missing" title="路径已登记但文件不存在">缺失</span>}
                      <span className="docpanel__src">{sourceLabel(d.source)}</span>
                      <span className="docpanel__sub mono">{d.rel_path}{d.size != null ? ` · ${fmtSize(d.size)}` : ''}</span>
                    </button>
                  )
                })}
              </div>
            ))}
            {docs && docs.length > 0 && filtered.length === 0 && <p className="detail-muted">无匹配「{query}」</p>}
          </nav>
          <aside className="docpanel__toc">
            <div className="docpanel__group-title">目录</div>
            {toc.length === 0 && <p className="detail-muted">无标题</p>}
            {toc.map(t => (
              <button key={t.id} className={`docpanel__toc-item lv${t.level}`} onClick={() => goTo(t.id)} title={t.text}>{t.text}</button>
            ))}
          </aside>
          <article className={`docpanel__view${progress > 0 ? ' has-progress' : ''}`} ref={viewRef}>
            {selected && (
              <div className="docpanel__meta-bar">
                {rawContent && <span>{wc} 字 · {readingTime(wc)}</span>}
                {selStatus && (
                  <span className={`docpanel__docstate docpanel__docstate--${docStateTone(selStatus)}`}
                        title={`生命周期状态：${docStateLabel(selStatus)}`}>
                    生命周期：{docStateLabel(selStatus)}
                  </span>
                )}
                {/* 该 kind 有生命周期就显示赛道：未落状态的文档全节点为「待推进」，
                    这样 8 类生命周期在 UI 上都可见，而不是只有已落状态的那几个。 */}
                {selTrack.length > 1 && (
                  <span className="docpanel__flow">
                    {selTrack.map((n, i) => (
                      <span key={n.state} className="docpanel__flow-node">
                        {i > 0 && <span className="docpanel__flow-arrow">→</span>}
                        <span className={`docpanel__flow-label is-${n.phase} tone-${n.tone}`}
                              title={`${n.label}${n.phase === 'current' ? '（当前）' : ''}`}>
                          {n.label}
                        </span>
                      </span>
                    ))}
                  </span>
                )}
                {truncated && <span className="docpanel__meta-warn">内容已截断</span>}
              </div>
            )}
            {loading && <div className="docpanel__loading"><span className="spinner" /> 渲染中…</div>}
            {err && <div className="docpanel__notice docpanel__notice--err">{err}</div>}
            {!loading && !err && missing && <div className="docpanel__notice docpanel__notice--warn">文档缺失：{selected && selected.rel_path}</div>}
            {!loading && !err && !missing && truncated && <div className="docpanel__notice docpanel__notice--warn">内容已截断（超过 20 万字符），仅显示前部分。</div>}
            {!loading && !err && !missing && html && <div className="md" dangerouslySetInnerHTML={{ __html: html }} />}
            {!loading && !err && !missing && !html && docs && <p className="detail-muted">请选择左侧文档。</p>}
            {!loading && html && progress > 3 && (
              <button className="docpanel__backtop" onClick={() => viewRef.current?.scrollTo({ top: 0, behavior: 'smooth' })} aria-label="返回顶部">↑ 顶部 · {progress}%</button>
            )}
          </article>
        </div>
      </div>
      {lightbox && (
        <div className="lightbox-overlay" onClick={() => setLightbox(null)}>
          <img src={lightbox} alt="preview" className="lightbox-img" />
          <button className="lightbox-close" onClick={() => setLightbox(null)}>×</button>
        </div>
      )}
    </div>
  )
}
