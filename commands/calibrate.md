汇总审查校准信号，评估 `/user:review` 是否过严、过松或产生噪音；只有在用户明确要求严格评估时，才判断是否可以进入下一个 Phase：

$ARGUMENTS

## 1. 默认数据源：历史 PR，而不是本地孤儿文件

默认读取当前仓库历史 PR（包含已合并和已关闭），收集 Claude review comments 中的隐藏 marker：

```markdown
<!-- dev-rules-review: {"schema_version":1,"id":"R-001","severity":"high","category":"validation","file":"src/foo.py","line":42,"risk_level":"normal","review_mode":"concise","tool":"claude"} -->
```

建议用 `gh` 获取数据：

```bash
gh pr list --state all --limit 100 --json number,state,mergedAt,closedAt,title,headRefName,baseRefName
gh api repos/:owner/:repo/pulls/<number>/comments
gh api repos/:owner/:repo/issues/<number>/comments
gh pr view <number> --json files,commits,reviews,comments,state,mergedAt,closedAt
```

如果用户传入日期范围，只统计该范围内创建、更新、合并或关闭的 PR。未指定范围时，取最近 100 个 PR 或最近 90 天，二者取覆盖更小者，避免一次校准吞掉过多历史噪音。

## 2. 证据推断，而不是伪自动判真伪

PR 历史只能提供校准证据，不能完全替代人工 verdict。对每条 finding 生成一个推断结果：

- `likely_accepted`：后续 diff 修改了 marker 指向文件/行附近，或回复明确接受，或 PR review thread resolved 后代码变化匹配建议。
- `likely_rejected`：作者明确回复否定，或 maintainer 标记为 not applicable / won't fix。
- `likely_low_value`：无人回复、无人修改，但 PR 直接合并，且 finding 为 medium/low。
- `inconclusive`：证据不足、PR 被关闭但原因不明、代码变化无法关联。
- `possible_miss`：PR 合并后短期内出现 revert/fix follow-up，且原 review 没覆盖相关 category。

禁止把“未修改”直接等同于误报，也禁止把“已修改”直接等同于严重程度正确。输出必须带 `confidence`：`high` / `medium` / `low`。

## 3. 离线 fallback：`.reviews/*.json`

只有以下情况才读取 `.reviews/review-*.json`：

- 仓库没有 GitHub PR 历史或无法使用 `gh`。
- 用户明确要求离线校准。
- 需要兼容旧数据。

读取本地 JSON 前，用 `dev-rules/schemas/review.schema.json` 做 schema 校验；校验失败的文件排除，并在数据完整性中列出。只统计 `human_verdict.accurate` 已填写的 findings。

## 4. 计算指标

基于 PR 证据与可用人工 verdict 计算：

| 指标 | 含义 | 参考线 |
| --- | --- | --- |
| 接受率 | `likely_accepted` / 有结论 findings | 观察项 |
| 明确拒绝率 | `likely_rejected` / 有结论 findings | 越低越好 |
| 低价值率 | `likely_low_value` / 有结论 findings | 越低越好 |
| 不确定率 | `inconclusive` / 全部 findings | 用于判断是否需要人工抽样 |
| 严重问题接受率 | critical/high 中 `likely_accepted` 占比 | ≥ 80% 作为参考 |
| medium 噪音率 | medium 中 `likely_low_value` + `likely_rejected` 占比 | ≤ 30% 作为参考 |
| possible_miss 数 | 合并后疑似漏报的 PR 数 | 逐条分析 |

以上门槛默认是校准信号，不是硬阻断；只有用户明确说“严格校准 / Phase 准入判断”时，才给通过/未通过。

样本量不足时必须降级结论：

- findings < 20：只输出观察，不给趋势判断。
- critical/high findings < 5：不评价严重问题准确性。
- `inconclusive` > 50%：建议抽样人工判定，不调整规则。

## 5. 输出

默认只在对话中输出短报告，不创建文件：

```markdown
# Review Calibration

> 数据源：PR history / local JSON fallback
> 范围：...
> 样本：N PRs, M findings

## 信号
- 严重问题接受率：...
- medium 噪音率：...
- 不确定率：...
- possible_miss：...

## 建议
- 收紧 / 放松哪些 category 或 severity
- 哪些 finding 类型应继续只在 high risk 输出
- 是否需要人工抽样
```

只有用户明确要求留档、趋势追踪或严格评估时，才输出：

- `.reviews/calibration-$(date +%Y%m%d).md`
- `.reviews/calibration-$(date +%Y%m%d).json`

## 6. 调整建议原则

- 如果 high/critical 接受率低：先检查 severity 是否过高，不要立刻删规则。
- 如果 medium 噪音率高：默认从日常 review 输出中降噪，只保留 high-risk 或显式 strict 模式。
- 如果 possible_miss 集中在某 category：补 review checklist 或机械 preflight，而不是增加泛化散文。
- 如果不确定率高：抽样人工校准，不要伪装成自动闭环。

校准的目标不是制造报告，而是让 `/user:review` 更像锋利的小刀：少报、准报、只在必要时留痕。
