# 新 exe 部署记录（2026-10-29）

> 用户批准“新 exe 部署”。本次只刷新本机运行目录，不做 GitHub Release、不发布 zip/安装包。

## 1 构建

| 项 | 值 |
|---|---|
| 代码 | `f867c2b`（干净工作区） |
| 命令 | `packaging/build.ps1 -Quick` |
| 版本 | `0.6.1`（未改版本号，未打 tag） |
| 产物 | `dist/mio-taskhub-v3/TaskHub.exe` |
| 新 exe sha256 | `abdf1d735ebeee80…` |
| 大小 | 16,142,656 字节 |
| 构建结果 | 退出码 0；安装器也重建，但未分发 |

## 2 变更前备份

| 项 | 路径 / 值 |
|---|---|
| 旧运行目录 | `~/.mio_taskhub/deploy-20261029/old-dist/` |
| 旧 exe sha256 | `5c0e4acd44929c455a44f219c0cb0b2b4b32e099bc03478a99aafdd3163d893e` |
| 旧 exe 大小 | 16,083,824 字节 |
| 生产 DB 备份 | `~/.mio_taskhub/deploy-20261029/taskhub-pre-deploy.db` |
| DB 备份 sha256 | `eba74313a32f61fb79079c1b1861a1a2a9dcc130caa2268d2ee16e7262c90f9b` |
| DB 备份完整性 | `integrity_check=ok` |

## 3 部署后状态

| 检查 | 结果 |
|---|---|
| TaskHub 进程 | supervisor + worker（PID 37032 / 39580） |
| 端口 48620 | 监听 |
| `GET /api/v1/tasks` | HTTP 200 |
| `GET /api/v1/materials/sources` | HTTP 200（10 个来源；旧 exe 为 404） |
| `task` 行数 | 1,948 |
| `updated_at` NULL | 0 |
| `integrity_check` | `ok` |
| 素材库 | 2,351 active / 38 quarantine |
| runtime.log | supervisor spawn worker、worker 启动，无报错 |

## 4 回滚

1. 停止 TaskHub 进程；
2. 用 `~/.mio_taskhub/deploy-20261029/old-dist` 覆盖 `dist/mio-taskhub-v3`；
3. 启动 `TaskHub.exe hub --worker`；
4. 验证 `/api/v1/tasks` 200、任务数、素材数。

数据库无需回滚：旧 exe 不识别 `updated_at`，会忽略该列；迁移触发器保留也不影响旧代码。只有在确认新 exe 写坏数据时，才按迁移备份恢复 DB，并需单独批准。

## 5 边界

- 未上传 GitHub Release、未发 zip、未分发安装器；
- 版本仍为 `0.6.1`，未打 tag；
- 素材回填、阈值、日志来源（`trial`）未变；
- 素材业务效用仍未测量。

## 6 追加部署：看板默认创建时间倒序（2026-10-29）

| 项 | 值 |
|---|---|
| 变更 | 看板泳道默认按 `created_at` 倒序，保留“优先级”切换；列表默认改为创建时间倒序 |
| 前端构建 | `npm run build` 通过，asset `index-B4blXezh.js` |
| 新 exe sha256 | `530b074728b44b4b4892f3da2d36eeb2003cba86729fbd99456357aa6cb95f86` |
| 部署前运行目录 | `~/.mio_taskhub/deploy-20261029/pre-sort-dist/` |
| 进程 | PID 5468 / 28440（supervisor + worker） |
| API | `/api/v1/tasks` 200；`/api/v1/materials/sources` 200 |
| 页面 bundle | 已包含“创建时间（新→旧）”“优先级”两个排序选项 |

回滚：恢复 `pre-sort-dist` 并重启 `TaskHub.exe hub --worker`；数据库无需变更。

## 7 工作流面板 MVP（2026-10-30）

| 项 | 内容 |
|---|---|
| 新接口 | `GET /api/v1/tasks/{id}/workstream` |
| 分组 | `labels` 含 `part-of:<owner>` 的平级任务；成员打开时自动解析 owner |
| 依赖 | 复用 `depends_on`；返回 `edges`、`layers`、`external`、`missing`、`blocked`、`cycle` |
| 统计 | total / done / blocked / estimate_min / critical_path |
| UI | 任务详情新增“工作流”标签：按拓扑层横排卡片、阻塞标记、依赖 chip、外部前置/缺失依赖 |
| 默认 | 有 `part-of` 标签或存在工作流成员时自动选中“工作流” |
| 旧“子任务” | 改名“清单”，仍只表示 `add_subtask` 勾选项 |
| 边界 | 不改 `parent_id`，不改 `MIO_DECOMPOSE_STAGE`，不新增层级字段 |

生产实测：`976be80e` 组返回 4 个任务、3 层、2 个阻塞；页面 bundle 含“工作流”标签，看板排序默认 `created`。回滚目录：`~/.mio_taskhub/deploy-20261029/pre-autotab-dist/`。

最终 exe：sha256 `958297b0…`，16,146,391 字节；进程 PID 32524 / 35560。

看板隐藏成员与依赖全量显示（2026-10-30 追加）：新 exe sha256 `71e88d20…`，16,146,428 字节；PID 9420 / 11292。看板已隐藏 15 个工作流成员，并显示“已隐藏工作流成员 15 个”。

工作流隐藏已取消成员（2026-10-30 追加）：exe sha256 `7c8c1e0c…`，16,146,713 字节；PID 17240 / 39988。`976be80e` 组：15 个进行中、4 个已取消（已隐藏）。

工作流节点点击打开详情（2026-10-30 追加）：修正 `onOpen(id)` 与 `openTask({id})` 的参数不一致。exe sha256 `d10cec87…`，16,146,713 字节；PID 20120 / 35376。

子任务删除与依赖缺失错误（2026-10-30 追加）：新增 `DELETE /tasks/{id}/subtasks/{sid}` 与 MCP `taskhub_delete_subtask`；缺少依赖返回结构化 422 `dependency.not_found`（含 `refs`）。exe sha256 `30f1dd48…`，16,147,323 字节；PID 18908 / 28188。
