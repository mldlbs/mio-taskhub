# 窄门禁灰度观察记录（单次真实周期）

> 依据：`docs/taskhub/product-decision-20261020.md` §1 验收标准第 4 条
> 「成功后能指着一例：它拦下了一次我本会放过的假完成」—— **这是本条的唯一成功判据**
> 起止：____-__-__ → ____-__-__（一个真实使用周期）

## 观察目标

不是「门禁有没有生效」（那是技术指标，已验证），而是**它有没有在真实使用中拦下过假完成**。

## 观察指标

| # | 指标 | 数据来源 | 判据 | 当前值 |
|---|---|---|---|---|
| M1 | 零改动提交被拦次数 | `run.rejections > 0` 的 run 数 | ≥ 1 才算「拦到过」 | 待观察 |
| M2 | 其中「本会放过的假完成」 | 人工回看每条拦截的上下文 | ≥ 1 例（决策验收第 4 条） | 待观察 |
| M3 | 合法完成被误拒次数 | 误拒 = 后来证明真做了但没留下 diff | **必须 = 0**（G-3 零误伤） | 待观察 |
| M4 | 拦截可见性 | 每次拦截在「动态」tab 可见 + 红色 toast | 4 秒内可见（G-2） | 待观察 |
| M5 | 豁免标注的跳过原因分布 | `run.diff_summary.reason` | 分布是否合理（不该大量是 `collect_failed`） | 待观察 |

## 查询方式

```bash
# M1/M5：拦截次数与原因分布（需 hub 已启动，迁移已应用）
curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats | python -m json.tool
```

或直接查库：

```sql
SELECT
  json_extract(diff_summary, '$.reason') AS reason,
  COUNT(*) AS n,
  SUM(CASE WHEN rejections > 0 THEN 1 ELSE 0 END) AS blocked_runs
FROM run
WHERE diff_summary IS NOT NULL
GROUP BY 1 ORDER BY n DESC;
```

## 回退条件（决策 §1 停止/回退条件）

> 上线后若开始频繁误伤合法完成（说明任务分类没分清）→ **立刻回退为「仅标注」，不要加严**。
> 宁可门是哑的，不能门是堵的。

**立即回退的操作**（不需回滚代码或数据）：

```bash
# 启动 hub 前
export MIO_DIFF_GATE=off     # 完全关闭
# 或
export MIO_DIFF_GATE=warn    # 只标注不拦（默认档）
```

## 观察期内的记录

| 日期 | 事件 | 指标影响 | 备注 |
|---|---|---|---|
| 2026-10-21 | 窄门禁落地（`MIO_DIFF_GATE` 默认 warn） | — | 尚未产生真实拦截 |
| | | | |

## 切 block 的前置条件

需求 FR-9：「切 block 前需 3–5 个真实 run 零误伤记录」。

- [ ] 至少 3 个真实 run 完成
- [ ] M3 误拒 = 0
- [ ] M2 至少 1 例确认拦到过假完成

满足后才可切 `MIO_DIFF_GATE=block`。
