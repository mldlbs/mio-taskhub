// Enhanced Idea Search Component with Filters and Scoring
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../api'

export default function IdeaSearch({ ideas: initialIdeas, onSelect, onRefresh }) {
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState({
    status: [],
    idea_type: '',
    category: '',
    project: '',
    labels: [],
    date_from: '',
    date_to: '',
    min_score: 0,
  })
  const [sortBy, setSortBy] = useState('updated_at')
  const [sortOrder, setSortOrder] = useState('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [results, setResults] = useState({ ideas: [], count: 0, total_pages: 1 })
  const [loading, setLoading] = useState(false)
  const [showFilters, setShowFilters] = useState(false)
  const [showScores, setShowScores] = useState(false)
  const [scores, setScores] = useState({})
  const [error, setError] = useState(null)

  // Debounced search
  const debouncedSearch = useMemo(
    () => {
      let timeoutId
      return (fn) => {
        clearTimeout(timeoutId)
        timeoutId = setTimeout(fn, 300)
      }
    },
    []
  )

  const performSearch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const payload = {
        query: query.trim() || null,
        status: filters.status.length ? filters.status : null,
        idea_type: filters.idea_type || null,
        category: filters.category || null,
        project: filters.project || null,
        labels: filters.labels.length ? filters.labels : null,
        date_from: filters.date_from || null,
        date_to: filters.date_to || null,
        min_score: filters.min_score || null,
        sort_by: sortBy,
        sort_order: sortOrder,
        page,
        page_size: pageSize,
      }
      const res = await api.post('/api/v1/ideas/search', payload)
      setResults(res)
      if (res.ideas?.length > 0) {
        // Fetch scores for results
        try {
          const scoresRes = await api.post('/api/v1/ideas/scores', {
            idea_ids: res.ideas.map(i => i.id),
          })
          setScores(scoresRes.scores || {})
        } catch (e) {
          console.warn('Failed to fetch scores', e)
        }
      }
    } catch (e) {
      setError(e.message || '搜索失败')
    } finally {
      setLoading(false)
    }
  }, [query, filters, sortBy, sortOrder, page, pageSize])

  useEffect(() => {
    performSearch()
  }, [performSearch])

  // Also load scores for initial ideas if no search
  useEffect(() => {
    if (!query && initialIdeas?.length) {
      try {
        const res = await api.post('/api/v1/ideas/scores', {
          idea_ids: initialIdeas.map(i => i.id),
        })
        setScores(res.scores || {})
      } catch (e) {
        console.warn('Failed to fetch initial scores', e)
      }
    }
  }, [initialIdeas])

  const toggleFilter = (key, value) => {
    setFilters(prev => {
      const arr = prev[key] || []
      const idx = arr.indexOf(value)
      if (idx >= 0) {
        return { ...prev, [key]: arr.filter(v => v !== value) }
      }
      return { ...prev, [key]: [...arr, value] }
    })
  }

  const clearFilters = () => {
    setFilters({
      status: [],
      idea_type: '',
      category: '',
      project: '',
      labels: [],
      date_from: '',
      date_to: '',
      min_score: 0,
    })
    setPage(1)
  }

  const getScoreInfo = (ideaId) => {
    return scores[ideaId] || { score: 0, factors: {}, recommendation: 'unknown' }
  }

  const getScoreColor = (score) => {
    if (score >= 8) return 'ok'
    if (score >= 4) return 'live'
    if (score >= 2) return 'warning'
    return 'dim'
  }

  return (
    <div className="idea-search">
      <div className="idea-search__header">
        <h3>🔍 想法搜索与评分</h3>
        <div className="idea-search__header-actions">
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={showScores}
              onChange={e => setShowScores(e.target.checked)}
            />
            显示优先级评分
          </label>
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={showFilters}
              onChange={e => setShowFilters(e.target.checked)}
            />
            高级筛选
          </label>
        </div>

        {/* Search Bar */}
        <div className="idea-search__bar">
          <input
            type="text"
            className="inp inp--lg"
            placeholder="搜索标题、描述... (回车搜索)"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') performSearch() }}
          />
          <button className="btn btn--primary" onClick={performSearch} disabled={loading}>
            {loading ? '搜索中…' : '搜索'}
          </button>
          {filters.status.length || filters.idea_type || filters.labels.length || filters.min_score > 0 ? (
            <button className="btn btn--ghost" onClick={clearFilters}>清除筛选</button>
          ) : null}
        </div>

        {/* Advanced Filters */}
        {showFilters && (
          <div className="idea-search__filters">
            <div className="filter-row">
              <div className="filter-group">
                <label>状态</label>
                <div className="filter-chips">
                  {['new', 'fermenting', 'formed', 'broken_down', 'archived', 'cancelled'].map(s => (
                    <button
                      key={s}
                      className={`filter-chip${filters.status.includes(s) ? ' is-on' : ''}`}
                      onClick={() => toggleFilter('status', s)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>

              <div className="filter-group">
                <label>类型</label>
                <select className="inp" value={filters.idea_type} onChange={e => setFilters(p => ({ ...p, idea_type: e.target.value }))}>
                  <option value="">全部</option>
                  <option value="idea">想法</option>
                  <option value="adr">ADR</option>
                </select>
              </div>

              <div className="filter-group">
                <label>项目</label>
                <input
                  className="inp"
                  placeholder="项目名"
                  value={filters.project}
                  onChange={e => setFilters(p => ({ ...p, project: e.target.value }))}
                />
              </div>

              <div className="filter-group">
                <label>标签</label>
                <input
                  className="inp"
                  placeholder="标签 (逗号分隔)"
                  value={filters.labels.join(', ')}
                  onChange={e => setFilters(p => ({ ...p, labels: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))}
                />
              </div>

              <div className="filter-group">
                <label>最低评分</label>
                <input
                  type="number"
                  className="inp"
                  step="0.1"
                  min="0"
                  max="20"
                  value={filters.min_score}
                  onChange={e => setFilters(p => ({ ...p, min_score: parseFloat(e.target.value) || 0 }))}
                />
              </div>

              <div className="filter-group">
                <label>日期范围</label>
                <div className="filter-date-range">
                  <input
                    type="date"
                    className="inp"
                    value={filters.date_from}
                    onChange={e => setFilters(p => ({ ...p, date_from: e.target.value }))}
                    placeholder="开始"
                  />
                  <span>至</span>
                  <input
                    type="date"
                    className="inp"
                    value={filters.date_to}
                    onChange={e => setFilters(p => ({ ...p, date_to: e.target.value }))}
                    placeholder="结束"
                  />
                </div>
              </div>

              <div className="filter-group">
                <label>排序</label>
                <div className="filter-sort">
                  <select className="inp inp--sm" value={sortBy} onChange={e => setSortBy(e.target.value)}>
                    <option value="updated_at">更新时间</option>
                    <option value="created_at">创建时间</option>
                    <option value="title">标题</option>
                    <option value="status">状态</option>
                  </select>
                  <select className="inp inp--sm" value={sortOrder} onChange={e => setSortOrder(e.target.value)}>
                    <option value="desc">降序</option>
                    <option value="asc">升序</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        )}

        {error && <div className="errorbar" role="alert"><span>▲</span><span>{error}</span><button onClick={() => setError(null)}>×</button></div>}

        {/* Results */}
        <div className="idea-search__results">
          {loading && <div className="search-loading">搜索中…</div>}

          {!loading && (!results.ideas || results.ideas.length === 0) && (
            <div className="search-empty">
              没有找到匹配的想法。尝试调整筛选条件或搜索关键词。
            </div>
          )}

          <div className="idea-search__list">
            {(results.ideas || []).map(idea => {
              const scoreInfo = getScoreInfo(idea.id)
              return (
                <div key={idea.id} className={`idea-search__item${onSelect ? ' clickable' : ''}`} onClick={() => onSelect?.(idea)}>
                  <div className="idea-search__item-main">
                    <div className="idea-search__item-title">
                      <span className={`badge badge--${(idea.idea_type === 'adr' ? { proposed: 'live', accepted: 'ok', rejected: 'dim', deprecated: 'dim', superseded: 'dim' }[idea.adr_status] || 'live') : { new: 'dim', fermenting: 'live', formed: 'ok', broken_down: 'ok', archived: 'dim', cancelled: 'danger' }[idea.status] || 'dim')}`}>
                        {idea.idea_type === 'adr' ? (idea.adr_status === 'proposed' ? 'ADR提议' : idea.adr_status === 'accepted' ? 'ADR接受' : idea.adr_status === 'rejected' ? 'ADR拒绝' : idea.adr_status === 'deprecated' ? 'ADR废弃' : 'ADR取代') : ({ new: '记录中', fermenting: '发酵中', formed: '已成形', broken_down: '已拆解', archived: '已归档', cancelled: '已取消' }[idea.status] || idea.status)}
                      </span>
                      <span className="idea-search__item-title-text">{idea.title}</span>
                      {showScores && scoreInfo.score > 0 && (
                        <span className={`score-badge score-badge--${getScoreColor(scoreInfo.score)}`}>
                          {scoreInfo.score.toFixed(1)}
                        </span>
                      )}
                    </div>
                    <div className="idea-search__item-meta">
                      {idea.project && <span className="tag tag--project">{idea.project}</span>}
                      {idea.labels?.map(l => <span key={l} className="tag">{l}</span>)}
                      <span className="mono">{new Date(idea.updated_at).toLocaleDateString()}</span>
                    </div>
                    {showScores && scoreInfo.factors && (
                      <div className="score-breakdown">
                        <span className="score-factor">触达: {scoreInfo.factors.reach}</span>
                        <span className="score-factor">影响: {scoreInfo.factors.impact}</span>
                        <span className="score-factor">置信: {scoreInfo.factors.confidence}</span>
                        <span className="score-factor">工作量: {scoreInfo.factors.effort}</span>
                        <span className={`score-recommendation score-recommendation--${scoreInfo.recommendation}`}>
                          {scoreInfo.recommendation}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Pagination */}
          {results.total_pages > 1 && (
            <div className="search-pagination">
              <button className="btn btn--ghost btn--sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}>
                上一页
              </button>
              <span className="pagination-info">
                第 {page} / {results.total_pages} 页，共 {results.count} 条
              </span>
              <button className="btn btn--ghost btn--sm" onClick={() => setPage(p => Math.min(results.total_pages, p + 1))} disabled={page >= results.total_pages}>
                下一页
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default IdeaSearch