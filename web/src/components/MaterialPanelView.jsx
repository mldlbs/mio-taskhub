import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

/** 素材面板 v2 · 日刊流（task 164e9081，2026-10-12 设计稿确认）。
 *
 * 主用途=日常巡检产出：按日期分节（最新在前），每天一节回答
 * 「今天产出了什么」——选题行（污染 token 前端过滤并标注）、洞察卡
 * （默认折叠，点开读 sections 全文；空产出只给红标行不渲染空卡）、
 * 来源计数行 + 折叠原始素材抽屉（消防栓不铺开）。
 * 价值发现实验收进页底折叠区，顶栏按钮直达，主视图不再见表单。
 * 数据来自 /mio/material-panel（后端 task 164e9081 增量 sections 全文，
 * preview/classification_note 等旧字段保留兼容）。
 */

const pad = (n) => String(n).padStart(2, '0')
const dt = (iso) => iso ? String(iso).replace('T', ' ').slice(0, 16) : '—'
/** 本地时区的日期（YYYY-MM-DD）：选题/洞察按本地日归组，与 observations 的日期口径一致。 */
const localDay = (iso) => {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
const weekdayOf = (day) => {
  const d = new Date(`${day}T00:00:00`)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('zh-CN', { weekday: 'short' })
}

/** 选题污染 token 过滤（前端口径，已知问题：选题池被标题前缀 token 污染）。 */
const isJunkTopic = (t) => {
  const s = String(t ?? '').trim()
  if (!s) return true
  if (/^(show hn|ask hn|hn)\b/i.test(s)) return true          // Show HN: / HN: 前缀
  if (s.startsWith('【') && s.endsWith('】')) return true       // 【GitHub】类标题前缀
  if (/[:：]$/.test(s)) return true                            // 冒号结尾 token
  if (s.length <= 3) return true                               // 过短碎片
  return false
}
const splitTopics = (topics) => {
  const valid = [], junk = []
  for (const t of topics || []) (isJunkTopic(t.topic) ? junk : valid).push(t)
  return { valid, junk }
}

const DEFAULT_ISSUES = `- mio-taskhub 文档链门控在 UI 无法操作（DocPanel 只读、422 detail 被裸 JSON.stringify）
- observer 洞察的输入抽样窗口偏娱乐源，github-trending 抓取标题带 HTML 垃圾
- observer 的选题池被标题前缀 token 污染（【GitHub】/Show HN: 被当成语义主题）
- 每日创意 job 已暂停：产出全是「缺失/空集」变体，价值提取机制失败
- mio-taskhub 抢单模式（grab mode）A 批 9 项设计完成，代码未实施
- hub 前端 1.4MB 单 chunk 未做代码分割`

function ValueDiscoverySection({ obsDates }) {
  const [date, setDate] = useState(obsDates[0] || '')
  const [issues, setIssues] = useState(DEFAULT_ISSUES)
  const [label, setLabel] = useState('')
  const [running, setRunning] = useState(false)
  const [last, setLast] = useState(null)
  const [err, setErr] = useState(null)
  const [history, setHistory] = useState(null)

  const loadHistory = () => api.valueDiscoveryList().then(setHistory).catch(e => setErr(e?.message || String(e)))
  useEffect(() => { loadHistory() }, [])

  async function run() {
    if (!date) return
    setRunning(true); setErr(null)
    try {
      const result = await api.valueDiscoveryRun(date, issues.split('\n').map(s => s.trim()).filter(Boolean), label || null)
      setLast(result)
      await loadHistory()
    } catch (e) {
      setErr(e?.message || String(e))
    } finally {
      setRunning(false)
    }
  }

  const p = v => v == null ? '—' : `${(v * 100).toFixed(0)}%`
  const s = history?.stats

  return (
    <div>
      <p className="detail-muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>
        人工触发：素材（GitHub Trending + Hacker News 原文）+ 内部问题 → LLM 价值评估，每个机会必须引用素材证据；
        <b>允许 0 个结果</b>，结果只入实验存档（MIO_HOME/value-discovery/）。
      </p>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
        <select value={date} onChange={e => setDate(e.target.value)} style={{ fontSize: 13 }}>
          {obsDates.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <input value={label} onChange={e => setLabel(e.target.value)} placeholder="批次标签（可选）"
               style={{ fontSize: 13, width: 140 }} />
        <button onClick={run} disabled={running || !date} style={{ fontSize: 13, padding: '4px 12px' }}>
          {running ? '评估中…（约 30-60s）' : '跑价值评估（调 LLM）'}
        </button>
      </div>
      <textarea value={issues} onChange={e => setIssues(e.target.value)} rows={6}
                style={{ width: '100%', fontSize: 12, fontFamily: 'inherit' }} />
      {err && <p style={{ color: '#c62828', fontSize: 12 }}>失败（显式透传，不伪装）：{err}</p>}
      {last && (
        <div style={{ border: '1px solid var(--border, #ddd)', borderRadius: 8, padding: 10, marginTop: 8 }}>
          <b style={{ fontSize: 13 }}>{last.date}{last.label ? ` · ${last.label}` : ''}</b>
          <span className="detail-muted" style={{ fontSize: 12 }}>　{last.opportunities.length} 个机会 · {last.duration_s}s</span>
          {last.opportunities.length === 0 && (
            <p style={{ fontSize: 12 }}>本轮 0 个机会（合法结果）。verdict：{last.verdict}</p>
          )}
          {last.opportunities.map((o, i) => (
            <div key={i} style={{ borderTop: '1px solid var(--border, #eee)', paddingTop: 6, marginTop: 6 }}>
              <b style={{ fontSize: 13 }}>{i + 1}. {o.title}</b>
              <span style={{ fontSize: 11, marginLeft: 6, padding: '1px 6px', borderRadius: 8,
                             background: o.origin === 'external' ? '#e8f5e9' : '#fff3e0' }}>
                {o.origin === 'external' ? '外部素材触发' : '内部问题触发'}
              </span>
              <div style={{ fontSize: 12, marginTop: 4 }}><b>证据：</b>{(o.evidence || []).join('；')}</div>
              <div style={{ fontSize: 12 }}><b>发现：</b>{o.what}</div>
              <div style={{ fontSize: 12 }}><b>内部关联：</b>{o.internal_link}</div>
              <div style={{ fontSize: 12 }}><b>为何现在：</b>{o.why_now}</div>
              <div style={{ fontSize: 12 }}><b>一周实验：</b>{o.one_week_experiment}</div>
            </div>
          ))}
          {last.opportunities.length > 0 && <p style={{ fontSize: 12, marginTop: 6 }}><b>verdict：</b>{last.verdict}</p>}
        </div>
      )}
      {history && history.runs.length > 0 && (
        <table style={{ borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr>
            <th style={th}>批次</th><th style={th}>日期</th><th style={th}>机会数</th><th style={th}>外部触发占比</th>
          </tr></thead>
          <tbody>
            {history.runs.map(r => (
              <tr key={r.id}>
                <td style={td}>{r.label || r.id}</td>
                <td style={td}>{r.date}</td>
                <td style={td}>{(r.opportunities || []).length}</td>
                <td style={td}>{
                  (() => { const ops = r.opportunities || []
                    return ops.length ? `${ops.filter(o => o.origin === 'external').length}/${ops.length}` : '—' })()
                }</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {s && s.batches > 0 && (
        <p className="detail-muted" style={{ fontSize: 12, marginTop: 6 }}>
          累计 {s.batches} 批 / {s.opportunities_total} 个机会 · 零机会批次 {s.zero_batches} ·
          证据覆盖 {p(s.evidence_coverage)} · 内部关联 {p(s.internal_relevance)} · 可行动 {p(s.actionability)}（人工接受率需逐批人工判定）
        </p>
      )}
    </div>
  )
}

/** 洞察卡：正常=可折叠全文卡；LLM 失败=一行红标（不渲染空卡）。 */
function InsightCard({ insight }) {
  const [open, setOpen] = useState(false)
  const secs = (insight.sections || []).filter(s => s && (s.title || s.content))
  const time = dt(insight.generatedAt)

  if (insight.llm_failed || secs.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0',
                    borderBottom: '1px solid var(--border, #eee)' }}>
        <span style={{ fontSize: 12, color: '#c62828', fontWeight: 600 }}>LLM 失败（空产出）</span>
        <b style={{ fontSize: 13 }}>{insight.topic || '（无主题）'}</b>
        <span className="detail-muted" style={{ fontSize: 11, marginLeft: 'auto' }}>{time}</span>
      </div>
    )
  }
  return (
    <div style={{ border: '1px solid var(--border, #ddd)', borderRadius: 8, marginTop: 6, background: 'var(--bg, #fff)' }}>
      <button onClick={() => setOpen(o => !o)}
              style={{ display: 'flex', alignItems: 'baseline', gap: 8, width: '100%', textAlign: 'left',
                       background: 'none', border: 'none', cursor: 'pointer', padding: '8px 10px', fontSize: 13 }}>
        <span className="detail-muted">{open ? '▾' : '▸'}</span>
        <b>{insight.topic || '（无主题）'}</b>
        <span className="detail-muted" style={{ fontSize: 11, marginLeft: 'auto' }}>{time}</span>
      </button>
      {open && (
        <div style={{ padding: '0 12px 10px' }}>
          {secs.map((s, i) => (
            <div key={i} style={{ marginTop: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{s.title}</div>
              <div style={{ fontSize: 12, whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>{s.content}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 单日素材抽屉：来源计数 + 每源前 3 条标题（折叠，默认不铺开）。 */
function ObservationsDrawer({ obs }) {
  const [open, setOpen] = useState(false)
  if (!obs) return <div className="detail-muted" style={{ fontSize: 12, marginTop: 6 }}>今日无原始抓取</div>
  const counts = (obs.sources || []).map(s => `${s.source} ${s.count}`).join(' · ')
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span className="detail-muted" style={{ fontSize: 12 }}>来源：{counts || '—'}</span>
        <button onClick={() => setOpen(o => !o)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                         fontSize: 12, color: 'var(--link, #1976d2)' }}>
          {open ? '收起原始素材 ▴' : `原始素材 ${obs.total} 条 ▾`}
        </button>
      </div>
      {open && (
        <table style={{ borderCollapse: 'collapse', marginTop: 6 }}>
          <thead><tr><th style={th}>来源</th><th style={th}>条数</th><th style={th}>样本标题（前 3 条）</th></tr></thead>
          <tbody>
            {(obs.sources || []).map(s => (
              <tr key={s.source}>
                <td style={td}>{s.source}</td>
                <td style={td}>{s.count}</td>
                <td style={td}>{(s.samples || []).map((t, i) => <div key={i}>{t}</div>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** 一天一节：选题行 + 洞察卡 + 来源抽屉。 */
function DaySection({ day }) {
  const { valid, junk } = splitTopics(day.topics)
  return (
    <section style={{ border: '1px solid var(--border, #ddd)', borderRadius: 10,
                      padding: '12px 14px', marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>{day.date}</h3>
        <span className="detail-muted" style={{ fontSize: 12 }}>{weekdayOf(day.date)}</span>
        {day.insights.length > 0 &&
          <span className="detail-muted" style={{ fontSize: 12 }}>洞察 {day.insights.length} 条</span>}
        <span className="detail-muted" style={{ fontSize: 12, marginLeft: 'auto' }}>
          {day.obs ? `抓取 ${day.obs.total} 条` : ''}
        </span>
      </div>

      {/* 选题行 */}
      <div style={{ fontSize: 13, marginTop: 8 }}>
        {day.topics.length === 0
          ? <span className="detail-muted">今日无选题</span>
          : (<>
              <b>今日选题：</b>
              {valid.length > 0 ? valid.map(t => t.topic).join(' / ')
                                : <span className="detail-muted">（无有效选题）</span>}
              {junk.length > 0 &&
                <span className="detail-muted" style={{ fontSize: 11, marginLeft: 8 }}>
                  已过滤 {junk.length} 条无效 token 选题
                </span>}
            </>)}
      </div>

      {/* 洞察卡列表 */}
      <div style={{ marginTop: 8 }}>
        {day.insights.length === 0
          ? <div className="detail-muted" style={{ fontSize: 13 }}>今日无洞察产出</div>
          : day.insights
              .slice()
              .sort((a, b) => String(a.generatedAt).localeCompare(String(b.generatedAt)))
              .map(i => <InsightCard key={i.id} insight={i} />)}
      </div>

      <ObservationsDrawer obs={day.obs} />
    </section>
  )
}

function Section({ title, children, defaultOpen = false, open: openProp, onToggle }) {
  const [inner, setInner] = useState(defaultOpen)
  const open = openProp !== undefined ? openProp : inner
  const toggle = () => { if (onToggle) onToggle(!open); else setInner(o => !o) }
  return (
    <div style={{ marginTop: 14 }}>
      <button onClick={toggle}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                       fontWeight: 600, fontSize: 14, textAlign: 'left' }}>
        {open ? '▾' : '▸'} {title}
      </button>
      {open && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  )
}

const th = { textAlign: 'left', borderBottom: '1px solid var(--border, #ddd)', padding: '4px 8px', fontSize: 12 }
const td = { borderBottom: '1px solid var(--border, #eee)', padding: '4px 8px', fontSize: 12, verticalAlign: 'top' }

export default function MaterialPanelView() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [days, setDays] = useState(7)
  const [vdOpen, setVdOpen] = useState(false)

  async function load(d = days) {
    try {
      setData(await api.mioMaterialPanel(d))
      setErr(null)
    } catch (e) {
      setErr(e?.message || String(e))
    }
  }
  useEffect(() => { load(days) }, [days])

  const dayList = useMemo(() => {
    if (!data) return []
    const map = new Map()
    const ensure = (d) => {
      if (!map.has(d)) map.set(d, { date: d, insights: [], topics: [], obs: null })
      return map.get(d)
    }
    for (const i of data.insights || []) { const d = localDay(i.generatedAt); if (d) ensure(d).insights.push(i) }
    for (const t of data.topics || []) { const d = localDay(t.selectedAt); if (d) ensure(d).topics.push(t) }
    for (const o of data.observations || []) { const d = o.date; if (d) ensure(d).obs = o }
    return [...map.values()].sort((a, b) => b.date.localeCompare(a.date))
  }, [data])

  if (err) return <div className="mio-view"><p className="detail-muted">加载失败：{err}</p></div>
  if (!data) return <div className="mio-view"><p className="detail-muted">加载中…</p></div>

  return (
    <div className="mio-view" style={{ padding: '0 4px', overflowY: 'auto', maxHeight: 'calc(100vh - 90px)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h2 style={{ margin: '10px 0' }}>素材面板</h2>
        <select value={days} onChange={e => setDays(Number(e.target.value))} style={{ fontSize: 13 }}>
          {[3, 7, 14, 30].map(d => <option key={d} value={d}>最近 {d} 天</option>)}
        </select>
        <span style={{ flex: 1 }} />
        <button onClick={() => setVdOpen(o => !o)}
                style={{ fontSize: 13, padding: '4px 12px', cursor: 'pointer' }}>
          {vdOpen ? '收起价值发现 ▴' : '▶ 跑价值发现'}
        </button>
      </div>

      {/* 日刊流：一天一节，最新在前 */}
      {dayList.length === 0 && (
        <p className="detail-muted" style={{ marginTop: 20 }}>窗口内无任何素材/产出</p>
      )}
      {dayList.map(day => <DaySection key={day.date} day={day} />)}

      {/* 价值发现实验：页底折叠区，顶栏按钮直达 */}
      <Section title="价值发现实验（人工触发 · 烧 LLM · 允许 0 结果）"
               open={vdOpen} onToggle={setVdOpen}>
        <ValueDiscoverySection obsDates={(data.observations || []).map(o => o.date)} />
      </Section>
    </div>
  )
}
