# twin supervisor 设计

## 一句话

`twin` 是 provider-neutral 的 **xuejiao persona supervisor CLI**：当前 Claude、Codex 或 Antigravity 会话通过 host action protocol 做判断；Python 持有目标和状态，通过可插拔 backend 指挥 worker、验收证据，并只在真正需要真人判断时停下。

worker harness 由 dev-rules、项目 `CLAUDE.md`、hooks、preflight、`wtree.py` 与 worker backend 承担；`twin` 不再造 agent 平台。普通 Agent 会话走 `$git-worktree-submodule`，人类走 `wts`；twin 作为程序化调用方也只消费同一个 `wtree.py` 引擎。

## 核心约束

1. **bootstrap or workspace**：当前 host 判断是否需要可选只读 research，再草拟 `goal.yaml + plan.yaml`；Python 只写入和校验，`twin run <workspace> --supervisor host/<provider>` 运行已准备好的 workspace。
2. **persona split**：supervisor 使用 `$DEV_RULES/personas/supervisor-persona.md`；worker 只看到 `$DEV_RULES/personas/worker-persona.md`。
3. **interactive supervisor**：当前 Agent 会话就是 supervisor，不另起 supervisor server 或 headless Agent；Claude、Codex 与 Antigravity 复用同一个 `twin` skill 作为宿主入口。
4. **worker backend**：默认 Claude Code headless；`local_cli` 直接调用本机 Claude、Codex 或 Gemini；CAO `run-step` 是远程、多 profile 和并发场景的可选 backend。
5. **supervisor 验收**：worker stop 不是完成；完成由 supervisor 对照 goal、plan、run evidence、测试、preflight、PR/commit 状态判断。
6. **single source of truth**：每类事实只有一个权威载体；旧 `feature_ledger.yaml` 是 breaking legacy，不自动迁移。

Supervisor route 首次 `run` 时绑定；不同 host 只能在无 pending action 时通过 `twin handoff` 显式交接。交接持 workspace driver lock、递增 revision 并写审计事件，旧 route/token 继续拒绝。Route-bound workspace 的 worker/review mutation 只能由 driver 的 token-bound 协议触发。

## workspace 契约

```text
research.yaml # 可选，只读调研事实、来源、置信度与选项
goal.yaml    # 目标、AC、non-goals
plan.yaml    # deliverables、AC 覆盖、状态、证据、下一步
```

`research.yaml` 不是决策文件。Dynamic Workflow 可以生成它，但最终目标、non-goals、AC 与交付拆分必须由 supervisor 判断。

`goal.yaml` 只放目标和验收；授权和门禁由当前 provider sandbox/permissions、hooks、dev-rules 和项目指令承担。

`plan.yaml` 只引用 AC ID，不重复 AC statement。bootstrap 阶段必须把复杂目标拆成短交付：多 AC 不得只生成单个 item；每个 item 要写清 scope 边界、证据预算、停止/转 review 条件；已知 gate gap 用 `blocked` / `deferred` + `blocked_reason` 表达；最终验收、summary、preflight 类 item 依赖前置交付项。防止长跑优先靠入口 plan 质量，不靠运行期限制 worker 自主性。

`plan.yaml.execution` 是可选的 workspace 级执行路由。缺省为 `claude_headless`；`local_cli` 必须声明 `provider: claude|codex|gemini`，`cao` 必须同时声明 `provider` 与 `agent`。目标与 AC 不得依赖具体 provider 才成立。

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
execution:
  backend: cao
  provider: codex
  agent: developer
items:
  - id: F1
    deliverable: 一个可验收交付物，不写泛泛任务
    scope: 只写本项边界；明确不做相邻需求或最终验收大包
    covers_ac:
      - AC1
    evidence_plan:
      - 证据预算：只收集本项需要的一组证据
      - 停止条件：证据产出后转 review；范围不清时 needs_human
    actual_evidence: []
    depends_on: []
    status: pending
    next_action: 给 worker 的下一步最短动作；完成后转 review
    blocked_reason: null
```

## 运行闭环

```text
while not terminal:
  1. twin 派生确定性 action
  2. host 只在 instruction/review action 做判断并带 token 提交
  3. twin 启动或 resume worker，写入 evidence
  4. watch / needs_human / accepted_done / failed 时返回宿主
```

`continue` 在一次 host 调用中自动进入下一轮；`watch_worker` 是 bounded stop，稍后从 artifact state 重入。`needs_human` 向用户问一个具体问题。每轮 `next_instruction` 只能绑定当前 plan item 的证据预算和停止条件；如果发现 plan item 过宽，supervisor 应回到 plan 约束修正或 `needs_human`，不要把过宽目标交给 worker 长跑。

## 单一事实来源

| 事实 | 权威载体 |
| --- | --- |
| 调研事实、来源、置信度与候选方案 | 可选 `research.yaml` |
| 目标、AC、non-goals | `goal.yaml` |
| 计划、执行路由、deliverables、AC 覆盖、状态、实际证据 | `plan.yaml` |
| supervisor persona | `$DEV_RULES/personas/supervisor-persona.md` |
| worker persona | `$DEV_RULES/personas/worker-persona.md` |
| 当前轮次、host route、state revision、pending action、next instruction、可续跑 backend session handle、terminal status | `supervisor_state.json` |
| worker 过程、证据、内联 review | `runs/<run_id>/run.json` / `events.jsonl` |
| 人类门禁回答 | `human_response.json` |
| 人类门禁审计 | `workspace_events.jsonl` |
| 人类可读状态 | `CURRENT.md` |
| 规则 | dev-rules / project `CLAUDE.md` |

## 审批权威分层

supervisor review 是 twin 内部的轮次裁决（`continue` / `needs_human` / `accepted_done` / `failed`），**不是**高风险变更的最终审批。高风险变更的最终审批以 `rules/product-dev.mdc` 为准——人类在 PR 中编辑/确认 `docs/approved/` 下文件并 merge 才是终态。supervisor 命中高风险信号时必须落到 `needs_human`，把决策让回 PR 通道，绝不在 twin 闭环内自行 `accepted_done`。

## worker prompt

worker prompt 由四部分组成：

1. `$DEV_RULES/personas/worker-persona.md`
2. `goal.yaml`
3. `plan.yaml`
4. supervisor 本轮生成的 `next_instruction`

worker session memory 只是续跑辅助；事实源始终是 workspace artifacts 和 repo 状态。跨会话重入先由 `next --json` 读取 artifact state 决定下一步，不能依赖上一段交互记忆。

## status artifact

`CURRENT.md` 和 `twin status` 是人类状态面：从 `goal.yaml`、`plan.yaml`、`supervisor_state.json` 与 run artifact 派生，不驱动状态变化。它们展示可读状态、当前 item、轮次、下一条命令和必要证据路径；`worker_running` 时只用 artifact metadata 派生 compact worker 诊断，不展开日志。机器消费继续使用 `status --json` 的原始字段。

`workspace_events.jsonl` 记录 workspace 级 mutation 审计。`twin respond` 不写回答正文，只记录状态迁移、artifact 引用和回答长度；host instruction submission 也只记录 route、revision、长度和 hash，不记录 instruction 正文；`twin handoff` 记录新旧 route 和新旧 revision。

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
