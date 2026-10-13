import { useEffect, useState } from 'react'
import { api } from '../api'

/** 素材面板（只读）：每日创意生成链路的素材内容视图。
 *
 * 2026-10-10 去过程化（task 7dac69de，用户反馈）：主视图只保留素材内容
 * （洞察 / 选题 / 原始抓取 / 价值发现），不再渲染过程数据——采集调度器卡、
 * 验收指标卡、classification_note 口径长文、配对记录、假设记录。
 * 后端契约未变（material_panel.py / api.js 均未改动），仅 UI 层移除渲染；
 * 洞察正文摘要复用后端已返回的 preview 字段。
 */

const dt = (iso) => iso ? String(iso).replace('T', ' ').slice(0, 16) : '—'

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

function Section({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div style={{ marginTop: 14 }}>
      <button onClick={() => setOpen(o => !o)}
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

  async function load(d = days) {
    try {
      setData(await api.mioMaterialPanel(d))
      setErr(null)
    } catch (e) {
      setErr(e?.message || String(e))
    }
  }
  useEffect(() => { load(days) }, [days])

  if (err) return <div className="mio-view"><p className="detail-muted">加载失败：{err}</p></div>
  if (!data) return <div className="mio-view"><p className="detail-muted">加载中…</p></div>

  return (
    <div className="mio-view" style={{ padding: '0 4px', overflowY: 'auto', maxHeight: 'calc(100vh - 90px)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h2 style={{ margin: '10px 0' }}>素材面板</h2>
        <select value={days} onChange={e => setDays(Number(e.target.value))} style={{ fontSize: 13 }}>
          {[3, 7, 14, 30].map(d => <option key={d} value={d}>最近 {d} 天</option>)}
        </select>
        <span className="detail-muted" style={{ fontSize: 12 }}>只读 · 素材库 {data.observer_base}</span>
      </div>

      {/* 洞察产出：内容视图（主题 + 正文摘要；空产出标红） */}
      <Section title={`洞察产出（${data.insights.length} 条）`} defaultOpen>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr>
            <th style={th}>时间</th><th style={th}>主题</th><th style={th}>状态</th><th style={th}>正文摘要</th>
          </tr></thead>
          <tbody>
            {data.insights.map(i => (
              <tr key={i.id}>
                <td style={td}>{dt(i.generatedAt)}</td>
                <td style={td}>{i.topic || '—'}</td>
                <td style={td}>{i.llm_failed
                  ? <span style={{ color: '#c62828' }}>LLM 失败（空产出）</span>
                  : <span className="detail-muted">正常</span>}</td>
                <td style={td}>{i.preview || '—'}</td>
              </tr>
            ))}
            {data.insights.length === 0 && <tr><td style={td} colSpan={4}>窗口内无洞察</td></tr>}
          </tbody>
        </table>
      </Section>

      {/* 每日选题 */}
      <Section title={`每日选题（${data.topics.length} 条）`} defaultOpen>
        <table style={{ borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>选定时间</th><th style={th}>主题</th><th style={th}>fallback</th></tr></thead>
          <tbody>
            {data.topics.map(t => (
              <tr key={t.file}>
                <td style={td}>{dt(t.selectedAt)}</td>
                <td style={td}>{t.topic}</td>
                <td style={td}>{String(t.fallback)}</td>
              </tr>
            ))}
            {data.topics.length === 0 && <tr><td style={td} colSpan={3}>窗口内无选题</td></tr>}
          </tbody>
        </table>
      </Section>

      {/* 原始抓取素材 */}
      <Section title={`原始抓取素材（${data.observations.length} 天）`} defaultOpen>
        {data.observations.map(o => (
          <div key={o.date} style={{ marginBottom: 12 }}>
            <b style={{ fontSize: 13 }}>{o.date}</b>
            <span className="detail-muted" style={{ fontSize: 12 }}> 共 {o.total} 条</span>
            <table style={{ borderCollapse: 'collapse', marginTop: 4 }}>
              <thead><tr><th style={th}>来源</th><th style={th}>条数</th><th style={th}>样本标题（前 3 条）</th></tr></thead>
              <tbody>
                {o.sources.map(s => (
                  <tr key={s.source}>
                    <td style={td}>{s.source}</td>
                    <td style={td}>{s.count}</td>
                    <td style={td}>{s.samples.map((t, i) => <div key={i}>{t}</div>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {data.observations.length === 0 && <p className="detail-muted">窗口内无观测数据</p>}
      </Section>

      {/* R292：人工触发的价值发现实验 */}
      <Section title="价值发现实验（人工触发 · 烧 LLM · 允许 0 结果）" defaultOpen>
        <ValueDiscoverySection obsDates={data.observations.map(o => o.date)} />
      </Section>
    </div>
  )
}
