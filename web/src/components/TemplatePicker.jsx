// Idea Template Picker Component
import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

export default function TemplatePicker({ onGenerate, onClose, initialTemplate }) {
  const [templates, setTemplates] = useState([])
  const [selectedTemplate, setSelectedTemplate] = useState(initialTemplate || '')
  const [formData, setFormData] = useState({})
  const [numIdeas, setNumIdeas] = useState(3)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [results, setResults] = useState([])
  const [syncing, setSyncing] = useState(false)

  useEffect(() => {
    loadTemplates()
  }, [])

  const loadTemplates = async () => {
    try {
      const res = await api.get('/api/v1/ideas/templates')
      setTemplates(res.templates)
    } catch (e) {
      setError('加载模板失败: ' + e.message)
    }
  }

  const handleGenerate = async () => {
    if (!selectedTemplate) {
      setError('请选择一个模板')
      return
    }

    // Validate required fields
    const template = templates.find(t => t.id === selectedTemplate)
    const requiredFields = template.fields.filter(f => f.required)
    const missing = requiredFields.filter(f => !formData[f.key]?.trim())
    if (missing.length > 0) {
      setError('请填写必填字段: ' + missing.map(f => f.label).join(', '))
      return
    }

    setLoading(true)
    setError(null)
    setResults([])

    try {
      const res = await api.post('/api/v1/ideas/templates/generate', {
        template_id: selectedTemplate,
        values: formData,
        num_ideas: numIdeas,
        sync_to_hub: false, // We'll handle sync separately
      })
      setResults(res.ideas || [])
      if (onGenerate) {
        onGenerate(res.ideas || [])
      }
    } catch (e) {
      setError(e.message || '生成失败')
    } finally {
      setLoading(false)
    }
  }

  const handleSync = async () => {
    if (!results.length) return
    setSyncing(true)
    try {
      // Sync via existing sync script endpoint or direct API
      // For now, just create ideas one by one
      let created = 0
      for (const idea of results) {
        try {
          await api.createIdea({
            title: idea.title,
            description: idea.description,
            labels: ['mio-intelligence', 'auto-generated', 'template-generated'],
          })
          created++
        } catch (e) {
          console.warn('Sync failed for', idea.title, e)
        }
      }
      alert(`已同步 ${created} 个想法到 taskhub`)
    } catch (e) {
      setError('同步失败: ' + e.message)
    } finally {
      setSyncing(false)
    }
  }

  const handleFieldChange = (key, value) => {
    setFormData(prev => ({ ...prev, [key]: value }))
  }

  const template = templates.find(t => t.id === selectedTemplate)

  return (
    <div className="template-picker modal-overlay" onClick={onClose}>
      <div className="modal modal--large" onClick={e => e.stopPropagation()}>
        <div className="modal__head">
          <h3>🧠 基于模板生成想法</h3>
          <button className="modal__close" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <div className="modal__body">
          {/* Template Selection */}
          <div className="template-picker__section">
            <h4>选择模板</h4>
            <div className="template-grid">
              {[
                { id: 'feature-request', label: '功能需求', icon: '✨', desc: '新功能或改进建议', category: 'product' },
                { id: 'tech-debt', label: '技术债/重构', icon: '🔧', desc: '代码质量、架构优化', category: 'engineering' },
                { id: 'bug-fix', label: '缺陷修复', icon: '🐛', desc: 'Bug报告、异常处理', category: 'quality' },
                { id: 'exploration', label: '技术探索', icon: '🔬', desc: '新技术调研、可行性验证', category: 'research' },
                { id: 'process-improvement', label: '流程/工具改进', icon: '⚙️', desc: 'CI/CD、工具链、协作优化', category: 'process' },
                { id: 'ux-improvement', label: '体验优化', icon: '🎨', desc: 'UI/UX、交互、性能优化', category: 'ux' },
                { id: 'quick-capture', label: '快速记录', icon: '💡', desc: '灵感闪现、碎片化想法', category: 'quick' },
              ].map(t => (
                <button
                  key={t.id}
                  className={`template-card${selectedTemplate === t.id ? ' is-selected' : ''}`}
                  onClick={() => {
                    setSelectedTemplate(t.id)
                    // Reset form for new template
                    setFormData({})
                  }}
                >
                  <span className="template-card__icon">{t.icon}</span>
                  <span className="template-card__name">{t.name}</span>
                  <span className="template-card__desc">{t.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Form Fields */}
          {selectedTemplate && (
            <div className="template-picker__section">
              <h4>填写信息</h4>
              <div className="template-form">
                {template.fields.map(f => (
                  <div key={f.key} className="form-field">
                    <label htmlFor={`field-${f.key}`}>
                      {f.label}
                      {f.required && <span className="required">*</span>}
                    </label>
                    {f.type === 'select' && f.options ? (
                      <select
                        id={`field-${f.key}`}
                        className="inp"
                        value={formData[f.key] || ''}
                        onChange={e => handleFieldChange(f.key, e.target.value)}
                      >
                        <option value="">请选择</option>
                        {f.options.map(opt => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    ) : f.type === 'textarea' ? (
                      <textarea
                        id={`field-${f.key}`}
                        className="inp"
                        rows={f.rows || 3}
                        placeholder={f.placeholder || ''}
                        value={formData[f.key] || ''}
                        onChange={e => handleFieldChange(f.key, e.target.value)}
                      />
                    ) : (
                      <input
                        id={`field-${f.key}`}
                        type="text"
                        className="inp"
                        placeholder={f.placeholder || ''}
                        value={formData[f.key] || ''}
                        onChange={e => handleFieldChange(f.key, e.target.value)}
                      />
                    )}
                    {f.required && <span className="field-required">必填</span>}
                  </div>
                ))}
                <div className="form-field">
                  <label htmlFor="num-ideas">生成数量</label>
                  <input
                    id="num-ideas"
                    type="number"
                    className="inp inp--sm"
                    min={1}
                    max={10}
                    value={numIdeas}
                    onChange={e => setNumIdeas(parseInt(e.target.value) || 3)}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Generate Button */}
          <div className="template-picker__actions">
            <button
              className="btn btn--primary btn--lg"
              onClick={handleGenerate}
              disabled={loading || !selectedTemplate}
            >
              {loading ? '生成中…' : `生成 ${numIdeas} 个想法`}
            </button>
            {error && <span className="error-msg">{error}</span>}
          </div>

          {/* Results */}
          {results.length > 0 && (
            <div className="template-picker__results">
              <h4>生成结果 ({results.length})</h4>
              <div className="results-list">
                {results.map((idea, idx) => (
                  <div key={idx} className="result-item">
                    <div className="result-item__header">
                      <span className="result-item__strategy">
                        {idea.provenance?.strategy && (
                          <span className="tag">{idea.provenance.strategy}</span>
                        )}
                      </span>
                      <h5>{idea.title}</h5>
                    </div>
                    <p className="result-item__desc">{idea.description?.slice(0, 200)}...</p>
                    <div className="result-item__meta">
                      <span className="tag">{idea.provenance?.strategy || 'unknown'}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="results-actions">
                <button className="btn btn--primary" onClick={handleSync} disabled={syncing}>
                  {syncing ? '同步中…' : '同步到 Taskhub'}
                </button>
                <button className="btn btn--ghost" onClick={() => setResults([])}>清空</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default TemplatePicker