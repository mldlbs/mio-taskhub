// 阶段产出物要求。
//
// 与后端 mio_taskhub/api/task_stages.py 的 STAGE_ARTIFACT_REQUIREMENTS 保持一致
// （可经 GET /api/v1/tasks/stages/requirements 动态获取）。此处保留静态副本，
// 让阶段推进弹窗无需等待网络即可提示；若后端表有变更，请同步本文件。
//
//   kind          该阶段需要的文档类型（写入 task.doc_paths）
//   acceptsText   未提供文档时是否接受纯文本（done 的审查结论）
//   textField     纯文本对应的字段名
export const STAGE_ARTIFACT = {
  brainstorming: {
    kind: 'requirement',
    acceptsText: false,
    requiresDiscussion: false,
    label: 'Requirement 需求文档路径',
    ph: 'docs/requirement-xxx.md',
  },
  design: {
    kind: 'spec',
    acceptsText: false,
    requiresDiscussion: true,
    label: 'Spec 设计文档路径',
    ph: 'docs/spec-xxx.md',
  },
  planning: {
    kind: 'plan',
    acceptsText: false,
    requiresDiscussion: false,
    label: 'Plan 实现计划路径',
    ph: 'docs/plan-xxx.md',
  },
  implementing: {
    kind: 'changelog',
    acceptsText: false,
    requiresDiscussion: false,
    label: 'Changelog 变更日志路径',
    ph: 'docs/changelog-xxx.md',
  },
  done: {
    kind: 'review',
    acceptsText: true,
    textField: 'review_result',
    requiresDiscussion: false,
    label: '审查报告路径（或直接填写审查结论）',
    ph: 'docs/review-xxx.md',
  },
}

export const artifactOf = (stage) => STAGE_ARTIFACT[stage] || null

const looksLikePath = (v) => /[\\/]/.test(v) || /\.(md|markdown|txt|pdf)$/i.test(v)

// 把用户输入转成阶段推进请求体：done 阶段允许纯文本结论，其余写进 doc_paths。
// 兼容旧的 spec_path / plan_path 由后端负责，这里统一走 doc_paths。
export function artifactBody(stage, value) {
  const spec = artifactOf(stage)
  const v = (value || '').trim()
  if (!spec || !v) return {}
  if (spec.acceptsText && !looksLikePath(v)) {
    return { [spec.textField || 'review_result']: v }
  }
  return { doc_paths: { [spec.kind]: v } }
}

// 该阶段产出物的当前值（用于弹窗预填）
export function currentArtifact(task, stage) {
  const spec = artifactOf(stage)
  if (!task || !spec) return ''
  const dp = task.doc_paths || {}
  if (dp[spec.kind]) return dp[spec.kind]
  if (spec.kind === 'spec') return task.spec_path || ''
  if (spec.kind === 'plan') return task.plan_path || ''
  if (spec.acceptsText) return task[spec.textField || 'review_result'] || ''
  return ''
}
