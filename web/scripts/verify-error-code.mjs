// node scripts/verify-error-code.mjs
//
// 窄门禁（d468e633 FR-8）前端错误码透传的**行为层**取证。
//
// 背景：后端 422 run.empty_diff 的 detail 结构是
//   {code, message, hint, files_changed, rejections, max_rejections}
// 前端必须能拿到：
//   ① code   → 供调用方按业务码分支渲染（api.md FR-8 明文要求）
//   ② message→ 人话（三段式第一段），不能是 JSON 化的一行乱码
//   ③ hint   → 下一步提示
// 同时**不能**打断 policy_guard 走 detail.error 的 destructive 门控链路
// （api.js 的 gated() 依赖 error_code === 'policy_risk_high'）。
//
// ⚠️ **零依赖**（2026-11-19 修正）：本脚本**不依赖 esbuild / npm install**。
// 最初用 esbuild 把 api.js 打成 cjs 再 require，但 CI（windows-latest）不装
// web/node_modules → ERR_MODULE_NOT_FOUND → CI 红。
// 改为从 api.js 源码里**原样抽取** ERROR_MESSAGES 与 ApiError 类，用 new Function
// 构造后断言 —— 测的仍是仓库里的真实源码，且任何环境都能跑。
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'url'

const here = path.dirname(fileURLToPath(import.meta.url))
const apiPath = path.join(here, '..', 'src', 'api.js')

let failed = 0
function assert(cond, msg) {
  if (cond) return
  failed += 1
  console.error('FAIL: ' + msg)
}

const src = fs.readFileSync(apiPath, 'utf8')

// 抽取 ERROR_MESSAGES 常量表
const emMatch = src.match(/const ERROR_MESSAGES = \{[\s\S]*?\n\}/)
if (!emMatch) {
  console.error('FAIL: 未能在 api.js 中定位 ERROR_MESSAGES')
  process.exit(1)
}
// 抽取 ApiError 类（类体末尾的 } 位于行首，方法体内的 } 都有缩进，不会误匹配）
const clsMatch = src.match(/export class ApiError extends Error \{[\s\S]*?\n\}/)
if (!clsMatch) {
  console.error('FAIL: 未能在 api.js 中定位 ApiError 类')
  process.exit(1)
}

let ApiError
try {
  const factory = new Function(
    `${emMatch[0]}\n${clsMatch[0].replace('export ', '')}\nreturn ApiError`
  )
  ApiError = factory()
} catch (e) {
  console.error('FAIL: 抽取出的源码无法构造 —— ' + (e && e.message))
  process.exit(1)
}

// ① 窄门禁 422：detail.code / message / hint 三件套
const emptyDiff = new ApiError('raw', 422, {
  code: 'run.empty_diff',
  message: '拒绝提交：声明 success=true，但本次 run 期间工作区零改动（0 文件 / 0 行）',
  hint: '先在工作区实际完成改动再提交；若确无代码产出，置 diff_gate_exempt=true。',
  files_changed: 0,
  rejections: 1,
  max_rejections: 2,
}, '/api/v1/runs/x/result')

assert(emptyDiff.error_code === 'run.empty_diff',
  '422 detail.code 未透传为 error_code（实际：' + emptyDiff.error_code + '）')
assert(emptyDiff.userMessage.indexOf('零改动') >= 0,
  'userMessage 未取 detail.message（实际：' + emptyDiff.userMessage + '）')
assert(emptyDiff.userMessage !== '数据校验失败，请检查必填项',
  'userMessage 仍被 422 通用文案覆盖 —— api.md 明确 run.empty_diff 不复用通用文案')
assert(typeof emptyDiff.hint === 'string' && emptyDiff.hint.indexOf('diff_gate_exempt') >= 0,
  'hint 未透传')
assert(emptyDiff.status === 422 && emptyDiff.detail.files_changed === 0,
  '原始 detail 未保留')

// ② destructive 门控链路不能断：后端 policy_guard 用的是 detail.error
const risk = new ApiError('raw', 409, { error: 'policy_risk_high', risk: 'high' }, '/api/v1/x')
assert(risk.error_code === 'policy_risk_high',
  'detail.error 约定被丢掉 —— gated() 的确认重试链路会失效（实际：' + risk.error_code + '）')

// ③ code 与 error 同时存在 → code 优先（api.md 的统一错误码表以 code 为准）
const both = new ApiError('raw', 422, { code: 'run.empty_diff', error: 'policy_risk_high' }, '/x')
assert(both.error_code === 'run.empty_diff', 'detail.code 未优先于 detail.error')

// ④ 无 detail.message → 回落到按状态码的通用文案（不能变成 undefined）
const bare = new ApiError('raw', 404, { code: 'x.not_found' }, '/x')
assert(bare.userMessage === '资源不存在',
  '无 detail.message 时未回落到状态码通用文案（实际：' + bare.userMessage + '）')

// ⑤ detail 是字符串（后端偶发纯文本 detail）→ 不崩，error_code 为空
const strDetail = new ApiError('raw', 400, '参数不合法', '/x')
assert(strDetail.userMessage === '请求参数有误，请检查后重试',
  '字符串 detail 时的 userMessage 不对（实际：' + strDetail.userMessage + '）')
assert(strDetail.error_code === undefined, '字符串 detail 不应产出 error_code')

// ⑥ 完全无 detail → 兜底文案，不抛
const none = new ApiError('raw', 500, null, '/x')
assert(none.userMessage === '服务器内部错误，请稍后重试',
  '无 detail 时的兜底文案不对（实际：' + none.userMessage + '）')
assert(none.error_code === undefined, '无 detail 时不应产出 error_code')

if (failed) {
  console.error('verify-error-code: ' + failed + ' 条断言失败')
  process.exit(1)
}
console.log('verify-error-code: 全部断言通过')
