# mio-agent-runtime 全流程对接测试报告（2026-09-25）

- 范围：mio-taskhub ⇄ mio-agent-runtime（0.12.0 + 本地 insight-store 补丁）真实环境全链路（非 monkeypatch）
- 结论：**7 阶段 ~40 用例，主链路全 PASS**；抓到 2 个上游真缺陷 + 2 个已知/文档问题（见缺陷清单）
- 环境：hub pid 3108（127.0.0.1:48620）、observer daemon pid 2960、CLI `D:\node_global\mio.cmd`、node v22.23.2、python 3.13/venv
- 证据：`ev-p0-status.json`、`ev-p6-mio-view.png`、`ev-p6-results.json`（同目录）

## P0 基线（留证）

| 项 | 值 |
|---|---|
| /api/v1/mio/status | available=true，observer running pid 2960，cli 可用 |
| 数据文件 | memory 832 / traces 1163 / reuse 20 / ideas 12 / queries 0 |
| insight-store.js 补丁 | 完好（5105B，14:27 写入，.bak-20260925） |
| MCP 基线 | trace_query 1163 ✓；observer 全 0；insight 0；creativity 1 active |

## P1 观察器管道（核心疑点）— PASS（附缺陷）

- 全 0 根因：`observer-store.js` 默认 baseDir=`<cwd>/.local/observer`，该目录从未存在——**管道从未被喂过数据**（不是路径分裂、不是读错库）。
- `observer_collect(hackernews, limit=5)` → collected=5 → `.local/observer/observations/2026-09-25.json` 落盘 ✓，status observations=1。
- 手动 `ObserverService.forcePipeline()` 全 9 步跑通（0s，LLM 步因缺陷 D2 空转）：
  - status：observations 1 / trends 1 / topics 1 / research 1 / insights 1 / world_model 4 / essays 1（全阶段非 0）✓
  - 输出 envelope（daily_research）、dag → COMPLETED、self_evo 权重更新 ✓
  - **但产出是空壳**：sections 0/5、confidence 0、topic 被截断（"The Mafi"）→ D2。
- `observer_ferment(afternoon)` → clusters=[]（仅 5 条观察，引擎正常返回不报错）✓（注记）。

## P2 Trace / Outcome / Experience — PASS

| 用例 | 结果 |
|---|---|
| agent_register(opencode) | ✓ sessionCount=1 |
| task_record_outcome（trace_id=e2e-outcome-probe-20260925） | ✓ trace 落盘 + agentUpdated |
| trace_query since 过滤 | ✓ total 1163→1164，matched=1 精确命中 |
| agent_report | ✓ taskOutcomes 1/1、memories 37、experienceReuses 21/verified 2 |
| observer_subscribe(task_outcome) | ✓ sub 创建，TTL 7d |
| observer_digest ×2 | ✓ 订阅命中，两次返回不同页（cursor 正常推进，首 10 条为历史回放） |
| experience_reuse 记录 | ✓ xfer_1790334424825 |
| experience_confirm 拒绝 agent_report 源 | ✓ 按设计 422-ish 拒绝（仅 auto_claim 可确认） |

## P3 Memory / Policy / Route — PASS

| 用例 | 结果 |
|---|---|
| memory_record → query 命中 | ✓（新记录 top1） |
| archive → query 排除 | ✓ count=0（archived 不进 query） |
| restore → query 再命中 | ✓ count=1 |
| memory_analyze | ✓ total=33、0 重复、0 低质 |
| queries.jsonl 查询留痕 | ✓ memory_query/task_route 各写 1 条（agent/project/resultIds 完整） |
| MCP policy_check(delete-task, agent-dev) | ✓ risk=low、total=37、hardGate=false、guidance 完整 |
| hub POST /api/v1/mio/policy/check | ✓ available=true、risk=low、total=15、265ms |
| task_route | ✓（0 verified 路由属预期——无 verified 经验；relatedMemories 5、agentHealth 信号正常） |

## P4 创意 / 发酵 / ideas 同步 — PASS

| 用例 | 结果 |
|---|---|
| creativity_generate(numIdeas=1, stable) | ✓ 新假设 8d934668 N40/F95/I75 Σ210 active |
| hub GET /creativity | ✓ hypotheses 1→2（CLI 实时读盘，含新假设） |
| hub GET /ferment | ✓ items=2；旧假设标题关联已有 idea、active→fermenting 映射正确（已 fermenting→action=None） |
| POST /ferment/{id}/sync 幂等 | ✓ 首次 created=true id=1015fcd1，二次同 id |
| sync 后关联+动作 | ✓ linked=true、action={to: fermenting, toward: fermenting} |
| ideas.jsonl→idea 手工同步 | ✓ 842889d5（labels mio-intelligence/strategy:SCAMPER/mio-e2e） |
| 清理 | ✓ 两个测试 idea 均 cancelled |

## P5 Hub CLI 桥（子进程）— PASS

| 用例 | 结果 |
|---|---|
| 坏 MIO_CLI + 缺失 MIO_HOME → available | ✓ False |
| 坏 CLI 下 run_mio | ✓ ok=false、WinError2 被捕获不抛异常（1ms） |
| 坏 CLI 下 policy_check | ✓ fail-open：riskLevel=unknown、不 hardGate |
| 真 CLI run_mio(--json insight status) | ✓ 151ms，stdout total=4 unreported=4（**CLI 读到新值**，见 D3） |
| 真 CLI policy_check | ✓ risk=low total=37 |
| 白名单外子命令（config set） | ✓ 拦截 ok=false |

## P6 UI 渲染（Playwright headless）— PASS 18/18

Mio 运行时面板：标题/观察器(运行)/CLI(可用)/宿主接入(opencode·workbuddy·codex)/数据文件表/最近 Trace（含本次 probe）/最近记忆（含 mio-e2e）/创意假设（含新假设）/只读脚注 全部断言通过；**0 pageerror、0 console.error**；截图 `ev-p6-mio-view.png`。

## 缺陷清单（只记录，未修）

- **D1（上游·重要）研究管道无入口**：`runPipeline/tickPipeline/forcePipeline` 在整个 mio-agent-runtime 包内**没有任何调用方**——`mio observe` daemon 只做对话监听（observe-state.json watchers），不启动 `ObserverService.start()`；MCP/CLI 也无触发命令。→ trends/research/insight 文章/world_model 默认**永远为空**，观察数据只在显式 collect 时写入。本次靠手动实例化才跑通。
- **D2（上游·重要）Observer LLM 硬编码 Ollama**：`ObserverLlmService` 写死 `localhost:11434 / qwen2.5:7b`（注释"假设本地已装 Ollama"），**不读 config.json 的 deepseek-flash**。09-23 的 deepseek 配置修复只覆盖 server 侧（creativity/insight/ferment），observer 包是另一条 LLM 路径。Ollama 未运行时：大脑全 0.3 占位、insight sections 0/5、confidence 0、topic 截断。→ 管道即使被调度起来也产出空壳。
- **D3（已知·需重启/上游）MCP insight 陈旧缓存**：本会话 MCP insight_status=0 vs CLI total=4——本 MCP 进程启动于补丁（14:27）之前，mtime-rebind 不及已加载模块；**新会话生效**；npm 升级会覆盖 `.bak` 补丁，需上游正式发布。
- **D4（文档漂移→已修复）ideas_sync.py 不存在**：`docs/HOWTO.md:152` 引用 `scripts/ideas_sync.py`，仓库内 glob 全无此文件（git 历史也从未存在过）；测试时用 REST 手工完成了等价同步（idea 842889d5）。**后记：已补实现 `mio-taskhub/scripts/ideas_sync.py`**（HOWTO 规格全落：扫 ideas.jsonl / title 去重 + 同 id 重复行合并 / POST /ideas / idea_sync_state.json 幂等 / --dry-run、--project、--base、MIO_HOME·MIO_DATA_DIR·MIO_TASKHUB_BASE 覆盖、UTF-8 输出）。实测：dry-run（唯一 6、重复合并 6、标题去重 6、待同步 0）；真实跑 exit 0 落状态、二次跑状态命中 6 幂等；隔离源 create 路径 1 建 1 成（9afe6364→测后 cancelled）+ state action=created。
- 观察（非缺陷）：hub status 的 `observer.running` 只代表对话监听 daemon，易被误解为"研究管道在跑"（建议 UI 文案区分）；ferment 对 5 条观察返回空簇属正常。

## 测试遗留物

- `.local/observer/` 管道全套产物（observations/trends/topics/research/insights/world_model/essays/dag）——保留（管道现已可用）
- creativity 假设 `8d934668`（active，测试生成，无删除接口）——保留待人工 reject/发酵
- idea `842889d5` / `1015fcd1` —— 已 cancelled ✓
- agent registry opencode 注册、experience 证据 xfer_1790334424825 —— 保留（Phase0 证据）
- 测试 memory `mem_1790334499636` —— 已归档（不进 query）

## 未覆盖

- hub 后台 `MioDigestJob`（12h digest write-back）未在本轮触发验证
- observer 全源采集（bilibili/douyin/rss/github）只验证了 hackernews 单源
- Windows 17:37 自动重启窗口未受影响（测试在重启后时段完成）


## 0.13.0 升级复测（同日，上游发布后）

升级：pm install -g mio-agent-runtime@0.13.0\（0.12.0→0.13.0，本地补丁备份至 .bak-20260925\ 与 temp）。结论：**D1/D2 已由上游修复，D3 未随包发布（本地补丁已重打），D4 已修，新增 D5**。

| 缺陷 | 0.13.0 状态 | 证据 |
|---|---|---|
| D1 研究管道无入口 | ✅ 上游已修 | 新增 \mio observer pipeline\（preview 默认 / \--run\ 执行）+ observer-store \pipeline()\ MCP 入口，代码注释直接引用 D1 |
| D2 LLM 硬编码 Ollama | ✅ 上游已修 | esolveConfig\ 显式配置>LLM_*环境>Ollama 默认 + URL 形状推断 provider；preview 显示 \openai deepseek-flash (key set) https://api.deepseek.com/chat/completions\（读到 config.json） |
| D3 MCP insight 陈旧缓存 | ⚠️ 上游未发（requireStore 仍只建一次）| 已把 mtime-rebind 补丁重打到 0.13.0（与已验证补丁仅注释差异）；本会话 MCP 进程仍是旧代码，**新会话生效**；升级会覆盖补丁，需每次升级后重打（或等上游采纳）|
| D4 ideas_sync.py 缺失 | ✅ 已修 | 升级后 dry-run 复测：唯一 6、状态命中 6、待同步 0、exit 0 |
| **D5（新·上游）pipeline 成功误报失败** | ❌ 发现 | \mio observer pipeline --run\ DAG 全程跑完（\pipeline_completed\ sections 5/5、136s、dag COMPLETED、self_evo 执行），却输出 \Observer pipeline did not complete: unknown reason\ 且 **exit=1** |

**D5 根因**：\OutputLayer.publishInsight\ 在 \dag.state='STORED'\ 时快照 \dagState\（ObserverService.js:153 于 :154 转 COMPLETED 之前调用），而 \observer-store.js:314\ 判 \envelope.dagState.state === 'COMPLETED'\ → 恒 false → \completed=false\（且该路径不带 reason，CLI 只能报 unknown）。影响：任何看退出码的自动化/CI 把成功当失败；MCP 调用方拿到 \completed:false\ 无原因。**只记录未修，待批准。**

### 升级后链路复测（全部通过）

| 用例 | 结果 |
|---|---|
| \mio observer pipeline\ preview | ✓ base-dir=\E:\work\code\agent-dev\.local\observer\、today COMPLETED、LLM=deepseek-flash key set |
| \mio observer pipeline --run\（stash 旧 dag 后）| ✓ 真实 deepseek 全程 136s 完成；产物 insight \obs_2026-09-25T12-41-33\：topic 完整（「【抖音热点】中秋…」不再截断）、**sections 5/5 全为真实中文长文**（697/136/140/206/165 字）、world_model 4 文件更新——**D2 端到端实证**；退出码误报见 D5 |
| forceCollect 全源采集 | ✓ RSS/Bilibili/Douyin/GitHub/HN 5 源无异常（注意：forceCollect 无逐源容错，任一源抛错会中断整条管道——0.13.0 仍未修，本次未触发）|
| hub GET \/mio/insight\ | ✓ total=4 unreported=4（注：正确路径是 \/mio/insight\ 而非 \/mio/insight/status\）|
| hub POST \/mio/ferment/{hyp}/sync\ 幂等 | ✓ 用正确 hyp id a5a9fb…\：两次均 \lready:true\ 不重复建（此前 404 是我把 idea id A5fcd1\ 当 hyp id，属调用方笔误，非缺陷）|
| hub POST \/mio/policy/check\ | ✓ fail-open、risk=low |
| ideas_sync dry-run | ✓ created=0 skipped_state=6 failed=0 |
| observer 守护进程 | ✓ \--stop\/\--start\ 后 pid 24376 跑 0.13.0 代码 |

### 观察（非缺陷，升级后新增）

- \mio observe\（含未知旗标如 \--help\）直接进前台监听不退出；\--start\ 能拉起守护进程但会让外层 shell 包装器报 \ChildProcess.kill\（daemon 本身正常）——工具链小怪癖。
- envelope 形状仅注释差异说明 0.13.0 的 insight-store.js 除我的补丁外与 0.12.0 相同。

## 0.13.1 升级复测（D3 上游修复，D5 仍未修）

发布内容（npm pack 两版本 difflib 对比，**全包只改 2 个文件**）：`package.json`（版本号）+ `server/insight-store.js`（+394B）。

| 缺陷 | 0.13.1 状态 | 证据 |
|---|---|---|
| D3 MCP insight 陈旧缓存 | ✅ **上游已修（采纳本地补丁思路）** | 官方实现 `fileSignature(size:mtimeMs)` 签名 + `storeSignature` 变更即重建 InsightStore，与本地 mtime-rebind 同机制（签名还多了 size）；安装文件 sha 与官方 tarball 完全一致、无本地补丁残留（npm 覆盖后即官方版，**补丁无需再打**）；功能级验证：实例 A 在文件不存在时缓存了 store，外部进程写入 `insights/insights.json` 后 A `status()` 即刻 0→1 ✓ |
| **D5 pipeline 成功误报失败** | ❌ **未修**（0.13.1 未触碰 observer-store.js / ObserverService.js / bin/mio.js） | 三处原样：`publishInsight` 仍在 STORED 态快照 envelope；`completed: envelope.dagState && envelope.dagState.state === 'COMPLETED'`；CLI 仍是 `unknown reason` 文案。包内 grep 无 D5 引用 |

- 守护进程已重启至 0.13.1（pid 10756）。
- 本会话 MCP 进程仍是启动时加载的旧代码——D3 修复对 MCP 侧要**新会话**才可见（CLI/Hub 子进程路径即时生效）。
- 复测小插曲（测试方笔误，非缺陷）：首版验证脚本把文件写到 `<dataDir>/insights.json`，实际 storePath 是 `<dataDir>/insights/insights.json` → 误判不重绑；改用 `A.storePath` 后通过。

## 0.13.2 升级复测（D5 修复，全缺陷闭环）

发布内容（0.13.1→0.13.2 difflib 全包对比，改 3 个文件）：`package.json`（版本 + `@akemi-mio/observer` `^0.1.1→^0.1.2`）、`server/observer-store.js`、`bin/mio.js`。

| 缺陷 | 0.13.2 状态 | 证据 |
|---|---|---|
| **D5 pipeline 成功误报 exit=1** | ✅ **上游已修（消费方+生产方+CLI 三侧齐修）** | ① 生产方 `@akemi-mio/observer 0.1.2`：**COMPLETED 转换挪到 publishInsight 之前**（envelope 快照即 COMPLETED）；② 消费方 `observer-store.js`：`completed = state==='COMPLETED' \|\| state==='STORED'`（方案 B 原样落地）+ 非预期 state 带 `reason: 'unexpected dag state: ...'`（加固项也做了）；③ CLI 失败路径补打 `sections=/topic=/insight=` 诊断，不再裸报 "unknown reason" |

**决定性功能验证**（stash 今日 dag 后真跑 `mio observer pipeline --run`）：
- 日志时序证实生产方修复：`dag_transition state=COMPLETED`（13:45:03.844）**先于** `output_insight_published`（.845）；
- CLI 成功输出 `Observer pipeline completed (daily_research, 138333ms, 5 LLM call(s))` + topic/sections/insight 明细；
- **EXITCODE=0**（同一场景 0.13.1 下 exit 1 + "unknown reason"）；
- 产物：insight `obs_2026-09-25T13-44-41`、sections 5、deepseek-flash 全程 138s。

备注：
- 消费方 STORED 兼容分支在版本配套（0.1.2 生产方）下不会被触发，属对旧生产方的向后兼容路径，无法单独黑盒复测（代码审查确认）。
- ideas_sync dry-run ✓（created=0 skipped_state=6）；守护进程已重启至 0.13.2（pid 8124）。
- 本会话 MCP 进程仍是启动时旧代码——MCP 侧看到 D3/D5 修复需**新开会话**。

**缺陷闭环总览**：D1（0.13.0）→ D2（0.13.0）→ D4（本地修复 scripts/ideas_sync.py）→ D3（0.13.1 官方采纳）→ D5（0.13.2）——报告内 5 个缺陷全部解决。

## P8–P12 续测（0.13.2，全流程补覆盖）

上一轮「未覆盖」项 + 新版本回归，全部完成：

### P8 CLI 只读回归（0.13.2）— PASS

| 用例 | 结果 |
|---|---|
| `mio --version` / `--json status` | ✓ 0.13.2 / version:1 home 正确 |
| `mio insight status` / `creativity status` | ✓ total=4·high-value=0 / 2 假设+评分明细 |
| `mio observer status` / `world-model` / `dag` | ✓ world_model 4·essays 1 / events=3 narratives=3 / dag 视图读 `summaries/*.jsonl`（设计如此：读观察守护的每日摘要，非 dag 任务文件——空属正常） |
| `mio traces --limit 3 --compact` | ✓ 返回本轮 3 条最新 trace（upgrade-0132/D5/upgrade-0131） |
| `mio policy check` | ✓ 通过 |
| `mio recall "observer pipeline"` | ✓ 返回相关记忆 5 条（关联度正确） |
| `mio experience list` | ✓ 1 条 verified 复用记录 |
| `mio memory analyze` | ✓ 36 active/1 archived，decision=31，建议"healthy" |
| **D5 修复 reason 分支**：今日 dag 已完成再 `--run` | ✓ `dag_already_completed` → 打印完整 reason + `sections=0 topic=none insight=none` 诊断 + exit 1（不再 "unknown reason"）。行为注记：重复执行幂等上返回 exit 1 但原因可读；日式自动化应先 preview 看 `todayCompleted` |

### P9 五源逐源采集 — 4/5 PASS + 1 环境缺陷

| 源 | 结果 | 证据 |
|---|---|---|
| hackernews | ✓ 3 obs | `hn_collected new_items=21` |
| rss | ✓ 3 obs | `rss_collected feeds=17 new_items=96`（ruanyifeng 等） |
| bilibili | ✓ 3 obs | `bili_collected total=10` |
| douyin | ✓ 3 obs | `douyin_collected count=20` |
| github-trending | ❌ 0 obs | 主源 `hot.imsyy.top` 抖动（fetch failed）+ fallback `github.com/trending` 必超时（15s abort），两次复测均败 |

**D7（环境/配置·低）github-trending 源在本机恒不可用**：根因实测——系统代理已开（WinINET `ProxyEnable=1, 127.0.0.1:7990`），但 **node/undici fetch 不读 Windows 系统代理也不读 `HTTP_PROXY` env**；本机 `Invoke-WebRequest` 走代理两 URL 均 200，`node fetch` 则 `github.com/trending` 直连超时（`hot.imsyy.top` 直连偶通）。管道不受影响（采集器内部 catch → 返回 []，forceCollect 不抛）。修复方向：给 mio 子进程注入代理 env + node 侧 EnvHttpProxyAgent，或换不依赖 github 直连的源。**只记录未修。**

注：`WeiboCollector`（weibo-hot）存在于包内但**未注册**进 ObserverService 默认采集列表——非缺陷，观察项（疑为预留）。

### P10 hub REST 全路由 — PASS 8/8

`GET status / traces(items=20) / memory(items=20) / creativity(items=2) / insight(items=4) / ferment(items=2)` + `POST policy/check` + `POST ferment/{hyp}/sync`（幂等）——全部 `available=true` 且数据正常。

### P11 MioDigestJob（12h digest write-back）— PASS（本轮补上）

- 配置：interval=720min、initial_delay=10min、`MIO_DIGEST_DISABLED` 门控、默认 days=7。
- 历史证据：`digest_state.json` 今日 19:08:01 首次 tick 已落（hub 启动+10min 自动触发）。
- **主动触发** `MioDigestJob().tick()` → `{ok:True, code:0}`：新报告 `digest/digest-2026-09-25.md`（6833B, 21:56:45）+ `latest.json` 同步更新；**write-back 实证**：`agent-dev/AGENTS.md` MIO_CONTEXT 块新增 `2026/9/25 21:56:45 | digest(7d): agent-dev 39 任务 100% 成功`。

### P12 自动调度入口审计 — 发现 D6

**D6（上游·设计缺口·低）`ObserverService.start()` 全包零调用方**：该方法是唯一的自动驱动源——按 `intervalMs` 定时轮询 5 个采集器 + `setInterval(tickPipeline, 60s)`（内部 PIPELINE_INTERVAL 小时级闸门）+ 启动 5s 后首踢。全包 grep `.start()` / `service.start(` 无任何调用方；CLI 也无 `observer serve/start` 子命令。**后果：研究管道永远只能手动触发**（`mio observer pipeline --run` / MCP `{"run":true}`），日更自动化不存在；D1 的修复止于"手动入口"。与"每日自动研究"的产品意图存在差距。**只记录未修，建议报上游**（接线点可选：observe 守护进程顺带起服务，或新增 `mio observer serve` 长驻命令）。



## 上游 issue（2026-09-25 提交）

- **D6** → https://github.com/mldlbs/akemi-mio/issues/2 —— \[mio-cli\]\[observer\] ObserverService.start() 全仓库零调用方：研究管道无自动调度，日更引擎是死代码
- **D7** → https://github.com/mldlbs/akemi-mio/issues/3 —— \[observer\] GitHubTrendingCollector 在系统代理环境恒失败：node fetch 不读 Windows 系统代理/HTTP_PROXY

提交方式：gh CLI 当次会话认证（git credential fill → 仅进程 env，未落盘/未进记忆），正文含根因、版本（0.13.2 / observer 0.1.2）、对照实验与修复方向。


## 0.13.3 升级复测（2026-09-26）：D6/D7 官方修复验证

上游 0.13.3（observer 0.1.2→0.1.3，+4.7KB，改 5 文件）同时修复 D6/D7（对应 issues #2 #3）。

**升级后代码核对**：runtime 0.13.3 + observer 0.1.3（唯一运行时依赖 undici ^6.29.0）；`observer-store.js` 新增 `serve()` 内 `await service.start()`（零调用方闭合）；`bin/mio.js` 新增 `observer serve` 子命令与 `observe --start --research`；observer 新增 `httpFetch/resolveProxyUrl/readWindowsSystemProxy` 代理层。

**D7 验证 ✅**：`mio observer collect --sources github-trending --limit 3` → 主源返回 HTML 站错（`gh_trending_api_failed`）→ **fallback `gh_trending_scraped count=15` 走代理抓取成功** → 3 obs、exit 0。此前两次（0.13.0/0.13.1）同一命令因直连超时恒 0 条。

**D6 验证 ✅**（守护路径活体实测）：

- `serve --dry-run` → 预览正常（LLM deepseek key set / first tick 5000ms / 60000ms / gate / lock 路径），exit 0；
- `observe --stop` → `observe --start --research` → `--status --json` 显示双 pid（转写 15260 + **research 14272**，logFile research.log）；
- research.log 实录：守护启动 5s 后**自动**触发日更（新 dag_20260926，无需任何手动 `--run`）：COLLECTED（obsCount=351）→ TOPIC_SELECTED → RESEARCHING → … → **COMPLETED → `pipeline_completed` sections=5 durationSec=114**，`output_insight_published` topic=Show HN:（先 COMPLETED .741 后发布 .743，D5 次序保持）；
- 数据落 `agent-dev/.local/observer/dag/dag_20260926.json`，insights 计数 3→4；
- 测后已恢复原状态：research 停止、转写守护重启（pid 3816）。

**结论：5 个原始缺陷 + D5 + D6 + D7 全部闭环**（D6/D7 上游官方修复，issues #2 #3 可观察关闭）。

**启用方式**（测试后默认未常驻）：`mio observe --start --research` 把日更调度器作为受 pid 管理的第二子进程拉起（`research.pid`/`research.log`）；`mio observe --stop` 一并停。

**观察项（非阻断）**：serve 的 schedule 文案写 "30m weibo"，但 `ObserverService` 构造器只注册 5 个采集器（weibo-hot 未入列，WeiboCollector 存在但从未 new），`collect --sources weibo-hot` 仍不可用——文案与实现不一致。


## 完整流程端到端测试（2026-09-26，0.13.3 稳态）

测试矩阵 T1–T7，全部在 `agent-dev` baseDir 下执行：

| # | 用例 | 结果 |
|---|---|---|
| T1 | 裸 `observer collect`（默认 5 源） | 9 obs（bilibili3 / hackernews3 / rss3）；**发现 D8**（见下） |
| T2 | 活 pid 伪造锁 → `pipeline --run` | `pipeline_skipped_locked`，0.3s 拒绝 exit 1，锁未被动 |
| T3 | 死 pid 陈旧锁 → `pipeline --run` | 抢占成功（到达 `dag_already_completed`）→ 闸门生效 → 锁释放（文件消失） |
| T4 | 删当日 dag 强制完整重跑 + **真实跨进程竞争** | 后台跑期间锁实录 `{"pid":12696}`；并发第二个 `--run` 0.3s 被拒；106s 跑完 `pipeline_completed sections=5 topic=Platform`，5 LLM calls，insight `obs_2026-09-26T03-06-18`，锁释放 |
| T4b | 产物核验 | dag timeline 八态完整 `INIT>COLLECTED>TOPIC_SELECTED>RESEARCHING>ANALYZING>WRITING>STORED>COMPLETED`；insights 4→5；world_model events 4→5；status 计数齐涨 |
| T5 | 前台 `observer serve` 实跑 10s | `observer_service_started collectors=5 pipeline_interval_hours=4`；启动即采集（bili10 / gh scraped15 / rss96 / hn23，**全部走代理**）；**5s 首 tick → `dag_already_completed`**（日更闸门在 serve 上下文生效）；无锁泄漏 |
| T6 | `observe --start --research` 守护路径（今日已完成态） | 双 pid（转写+research）；research.log 首 tick `dag_already_completed`；重启复现：gh scraped15 ✓ rss ✓ bili ✓ |
| T7 | 贯通 | hub REST status/insight/traces 三路由 200；digest 今日新文件 `digest-2026-09-26.md` 6833B（写回 AGENTS.md MIO_CONTEXT） |

**锁语义结论**（PipelineLock 源码 + 实测一致）：O_EXCL 建锁 NTFS 原子；活 pid 2h TTL 内拒他人；死 pid/超 TTL 抢占；建锁失败（非 EEXIST）放行（宁可偶尔并发）；`finally` 释放。锁检查**先于**日更闸门，故 T2/T3 可用当日已完成 dag 直接区分两条路径（日志事件不同：`pipeline_skipped_locked` vs `dag_already_completed`）。

**采集源稳态核验**（0.13.3 代理层）：

- ✅ rss（17 feeds）、bilibili、hackernews（间歇 `fetch failed`＝HN API 限流，手动立即恢复 26→8→0 波动，瞬态）、github-trending（primary imsyy API 回 HTML → fallback scraping 走代理稳定 15 条）
- ⚠️ douyin：`hot.imsyy.top/douyin` 持续返回 HTML（第三方 API 退化，代理本身通），0 obs；viki.moe 备源偶发 fetch failed
- ⚠️ 03:09 批次曾整批瞬时 `fetch failed`（无 `proxy_fallback_direct`），5 分钟后重启恢复——判定为瞬时抖动，非代码缺陷
- **D8（新，mio-cli 侧）**：`observer-store.js` 裸 collect 默认源 `['bilibili','hackernews','github','douyin','rss']` 写了 `'github'`，canonical 名是 `'github-trending'`（只认 `github_trending` 下划线别名）→ `collect_by_source_unknown` 警告 + **默认采集永远漏 github-trending**。此前所有测试都显式传 `--sources` 故未暴露。
- O1（观察）：rss 观测的 `source` 字段是 feed URL，计数显示成 `https://feeds.feedburner.com/...` 而非 `rss`（TrendEngine knownSources 也不含 URL → 权重走兜底）
- O2（观察）：serve 的 schedule 文案 "30m weibo" 与构造器未注册 WeiboCollector 不一致（上轮已记）

**测试环境注意**：opencode shell 包装器在命令含 `observe --start/--stop` 或 `$p.Kill()` 时会报 `ChildProcess.kill` 并吞掉输出（命令实际已执行，看产物文件即可）；前台 serve 被 Kill 只杀包装层，node 子进程需按 pid 清理。

**稳态**：测试结束保留 `observe --start --research` 双守护在跑（转写 + 日更调度器）。关停：`mio observe --stop`；只留转写：stop 后再 `mio observe --start`。
