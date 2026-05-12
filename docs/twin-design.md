# twin supervisor 设计

## 一句话

`twin` 是运行在 Claude Code 交互模式里的 **xuejiao persona supervisor**：它持有目标、指挥 headless worker 多轮推进、验收证据，并只在真正需要真人判断时停下。

worker harness 由 dev-rules、项目 `CLAUDE.md`、hooks、preflight、worktree、Claude Code `-p --permission-mode bypassPermissions` 承担；`twin` 不再造 agent 平台。

## 核心约束

1. **bootstrap or workspace**：`/twin "<one-line goal>"` 由当前 supervisor 调研并草拟 `goal.yaml + plan.yaml`，Python 只写入和校验；`/twin <workspace>` 直接运行已准备好的 workspace。
2. **persona split**：supervisor 使用 `$DEV_RULES/personas/supervisor-persona.md`；worker 只看到 `$DEV_RULES/personas/worker-persona.md`。
3. **interactive supervisor**：`/twin` 本身就是 supervisor，不另起 supervisor `claude -p`。
4. **headless worker**：worker 用 Claude Code `-p --permission-mode bypassPermissions` 执行，并可用 `--resume <worker_session_id>` 续跑。
5. **supervisor 验收**：worker stop 不是完成；完成由 supervisor 对照 goal、plan、run evidence、测试、preflight、PR/commit 状态判断。
6. **single source of truth**：每类事实只有一个权威载体；旧 `feature_ledger.yaml` 是 breaking legacy，不自动迁移。

## workspace 契约

```text
goal.yaml    # 目标、AC、non-goals
plan.yaml    # deliverables、AC 覆盖、状态、证据、下一步
```

`goal.yaml` 只放目标和验收；授权和门禁由 Claude Code permissions / settings / hooks / dev-rules / 项目 `CLAUDE.md` 承担。

`plan.yaml` 只引用 AC ID，不重复 AC statement。

目标工作区禁止复制 persona 文件；发现 `supervisor-persona.md` 或 `worker-persona.md` 直接拒绝。

## 最小模板

### `goal.yaml`

```yaml
schema_version: 1
id: short-slug
one_liner: 一句话说明要交付的结果

core_goal: |
  只写真正要达成的用户/业务结果，不写过程。

acceptance_criteria:
  - id: AC1
    statement: 可被验证的一条验收条件。AC 文字只在这里定义。
    evidence_type: 测试、preflight、页面、API 响应、PR 链接等

non_goals:
  - 明确不做什么，防止 worker 扩 scope
```

### `plan.yaml`

```yaml
schema_version: 1
goal_id: short-slug
items:
  - id: F1
    deliverable: 一个可验收交付物，不写泛泛任务
    scope: 只写本项边界
    covers_ac:
      - AC1
    evidence_plan:
      - 本 deliverable 预计产生的证据
    actual_evidence: []
    depends_on: []
    status: pending
    next_action: 给 worker 的下一步最短动作
```

## 运行闭环

```text
while not terminal:
  1. 读：goal + plan + supervisor persona + run evidence
  2. 干：启动或 resume worker
  3. 验：supervisor 对照 AC、plan、diff、测试、preflight、PR/commit 状态验收
  4. 判：accepted_done / continue / needs_human / failed
```

`continue` 必须自动进入下一轮；只有 `accepted_done` / `needs_human` / `failed` 停下。`needs_human` 用 `AskUserQuestion` 问一个具体问题。

## 单一事实来源

| 事实 | 权威载体 |
| --- | --- |
| 目标、AC、non-goals | `goal.yaml` |
| 计划、deliverables、AC 覆盖、状态、实际证据 | `plan.yaml` |
| supervisor persona | `$DEV_RULES/personas/supervisor-persona.md` |
| worker persona | `$DEV_RULES/personas/worker-persona.md` |
| 当前轮次、next instruction、worker session id、terminal status | `supervisor_state.json` |
| worker 过程、证据、内联 review | `runs/<run_id>/run.json` / `events.jsonl` |
| 人类门禁回答 | `human_response.json` |
| 人类可读状态 | `CURRENT.md` |
| 规则 | dev-rules / project `CLAUDE.md` |

## worker prompt

worker prompt 由四部分组成：

1. `$DEV_RULES/personas/worker-persona.md`
2. `goal.yaml`
3. `plan.yaml`
4. supervisor 本轮生成的 `next_instruction`

worker session memory 只是续跑辅助；事实源始终是 workspace artifacts 和 repo 状态。

## review artifact

supervisor 输出 review JSON，Python 校验后内联写入 `runs/<run_id>/run.json::review`。review 使用统一字段：

```json
{
  "status": "accepted_done|continue|needs_human|failed",
  "summary": "",
  "next_instruction": "",
  "remaining_gaps": [],
  "acceptance_evidence": [],
  "risk_flags": [],
  "actions": [],
  "plan_updates": [],
  "human_question": null
}
```

## 最终原则

`twin` 的目标不是多一个流程，而是让人类不再反复说“继续”。给定目标后，supervisor 应指挥 worker 干到完整闭环；只有真正需要 xuejiao 判断时才停下来。
