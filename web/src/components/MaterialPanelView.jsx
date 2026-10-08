import { useEffect, useState } from 'react'
import { api } from '../api'

/** 素材面板（只读）：每日创意生成链路的素材可追溯视图。
 *
 * 数据来自 /mio/material-panel（聚合 observations/insights/topics/creativity store）。
 * 不写入、不触发 LLM。分类口径见后端 classification_note。
 */

const pct = (v) => v == null ? '—' : `${(v * 100).toFixed(0)}%`
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
        人工触发的价值发现实验（烧 LLM）：素材（GitHub Trending + Hacker News 原文清洗后）+ 内部问题清单 → 价值评估。
        每个机会必须引用素材编号证据、关联内部问题、给一周验证实验与成功判据；<b>允许 0 个结果</b>。
        结果只入实验存档（MIO_HOME/value-discovery/），不进任务/想法库。
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
          累计 {s.batches} 批 / {s.opportunities_total} 个机会 · 每批均值 {s.opportunity_rate} · 零机会批次 {s.zero_batches} ·
          证据覆盖 {p(s.evidence_coverage)} · 内部关联 {p(s.internal_relevance)} · 可行动 {p(s.actionability)} ·
          外部触发占比 {p(s.external_origin_rate)}（人工接受率需逐批人工判定）
        </p>
      )}
    </div>
  )
}

const CLASS_META = {
  'with-insight': { label: '生产·带洞察', color: '#2e7d32' },
  'template-only': { label: '生产·模板自配', color: '#b26a00' },
  test: { label: '测试残留', color: '#9e9e9e' },
  other: { label: '其他', color: '#455a64' },
  empty: { label: '空', color: '#9e9e9e' },
}

const SOURCE_TYPE_LABEL = {
  insight: '洞察', template: '模板', legacy: '早期标签',
  instruction: '指令', test: '测试', other: '其他', empty: '空',
}

function MetricCard({ label, value, hint, tone }) {
  return (
    <div style={{ border: '1px solid var(--border, #ddd)', borderRadius: 8, padding: '10px 14px', minWidth: 160 }}>
      <div className="detail-muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: tone }}>{value}</div>
      {hint && <div className="detail-muted" style={{ fontSize: 11 }}>{hint}</div>}
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

  const m = data.creativity.metrics
  const im = data.insight_metrics

  return (
    <div className="mio-view" style={{ padding: '0 4px', overflowY: 'auto', maxHeight: 'calc(100vh - 90px)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h2 style={{ margin: '10px 0' }}>素材面板</h2>
        <select value={days} onChange={e => setDays(Number(e.target.value))} style={{ fontSize: 13 }}>
          {[3, 7, 14, 30].map(d => <option key={d} value={d}>最近 {d} 天</option>)}
        </select>
        <span className="detail-muted" style={{ fontSize: 12 }}>只读 · 素材库 {data.observer_base}</span>
      </div>

      <p className="detail-muted" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{data.classification_note}</p>

      {/* 验收指标 */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8 }}>
        <MetricCard label="真实素材进入配对比例" tone="#2e7d32"
                    value={pct(m.real_material_pair_ratio)}
                    hint="配对含观察洞察 / 非测试配对（P0 修复前恒为 0）" />
        <MetricCard label="有效洞察比例" tone="#2e7d32" value={pct(im.valid_ratio)}
                    hint={`${im.valid} 有效 / ${im.llm_failed} LLM 失败 / 共 ${im.total}`} />
        <MetricCard label="模板自配配对" tone="#b26a00" value={m.combos_by_class['template-only'] || 0}
                    hint="配对双方均为模板文本 = LLM 没见到真实素材" />
        <MetricCard label="测试残留配对" tone="#9e9e9e" value={m.combos_by_class['test'] || 0}
                    hint="已从生产指标剔除" />
      </div>

      {/* 洞察列表 */}
      <Section title={`洞察产出（${data.insights.length} 条，含空 section / LLM 失败标记）`} defaultOpen>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr>
            <th style={th}>时间</th><th style={th}>主题</th><th style={th}>状态</th>
            <th style={th}>sections</th><th style={th}>置信度</th><th style={th}>耗时</th>
            <th style={th}>当天来源分布（匹配度核对）</th>
          </tr></thead>
          <tbody>
            {data.insights.map(i => (
              <tr key={i.id}>
                <td style={td}>{dt(i.generatedAt)}</td>
                <td style={td}>{i.topic}</td>
                <td style={td}>{i.llm_failed
                  ? <span style={{ color: '#c62828' }}>LLM 失败（空产出）</span>
                  : <span style={{ color: '#2e7d32' }}>正常</span>}</td>
                <td style={td}>{i.sections_count}</td>
                <td style={td}>{i.confidence ?? '—'}</td>
                <td style={td}>{i.durationMs ? `${(i.durationMs / 1000).toFixed(1)}s` : '—'}</td>
                <td style={td}>{Object.entries(i.same_day_sources || {}).map(([s, c]) => `${s}:${c}`).join('，') || '—'}</td>
              </tr>
            ))}
            {data.insights.length === 0 && <tr><td style={td} colSpan={7}>窗口内无洞察</td></tr>}
          </tbody>
        </table>
      </Section>

      {/* 选题记录 */}
      <Section title={`每日选题（${data.topics.length} 条）`}>
        <table style={{ borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>选定时间</th><th style={th}>主题</th><th style={th}>fallback</th></tr></thead>
          <tbody>{data.topics.map(t => (
            <tr key={t.file}>
              <td style={td}>{dt(t.selectedAt)}</td>
              <td style={td}>{t.topic}</td>
              <td style={td}>{String(t.fallback)}</td>
            </tr>
          ))}</tbody>
        </table>
      </Section>

      {/* 配对记录 */}
      <Section title={`配对记录（${data.creativity.combos.length} 条，新的在前）`} defaultOpen>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr>
            <th style={th}>时间</th><th style={th}>分类</th><th style={th}>配对来源（原文）</th><th style={th}>产出</th>
          </tr></thead>
          <tbody>
            {data.creativity.combos.map((c, idx) => (
              <tr key={idx}>
                <td style={td}>{dt(c.createdAtIso)}</td>
                <td style={td}><span style={{ color: CLASS_META[c.class]?.color }}>
                  {CLASS_META[c.class]?.label || c.class}</span></td>
                <td style={td} title={c.sources.join('\n---\n')}>
                  {c.sources.map((s, i) => (
                    <div key={i} style={{ marginBottom: 4 }}>
                      <span style={{ fontSize: 10, color: '#888' }}>[{SOURCE_TYPE_LABEL[c.sourceTypes[i]] || '?'}] </span>
                      {s.length > 120 ? s.slice(0, 120) + '…' : s}
                    </div>
                  ))}
                </td>
                <td style={td}>{c.description || '—'}</td>
              </tr>
            ))}
            {data.creativity.combos.length === 0 && <tr><td style={td} colSpan={4}>窗口内无配对</td></tr>}
          </tbody>
        </table>
      </Section>

      {/* 假设记录 */}
      <Section title={`假设记录（${data.creativity.hypotheses.length} 条）`}>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr><th style={th}>时间</th><th style={th}>分类</th><th style={th}>标题</th><th style={th}>状态/策略</th></tr></thead>
          <tbody>
            {data.creativity.hypotheses.map((h, idx) => (
              <tr key={idx}>
                <td style={td}>{dt(h.createdAtIso)}</td>
                <td style={td}><span style={{ color: CLASS_META[h.class]?.color }}>
                  {CLASS_META[h.class]?.label || h.class}</span></td>
                <td style={td}>{h.title}</td>
                <td style={td}>{h.status} / {h.strategy}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {/* 原始抓取素材（追溯终点） */}
      <Section title={`原始抓取素材（${data.observations.length} 天，每来源带样本标题）`}>
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
