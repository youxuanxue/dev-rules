# twin supervisor runbook

每轮 supervisor 在当前 Claude Code 交互会话中调用 Python 子命令；用户面只有 `/twin "<goal>"` / `/twin <workspace>` / `/twin status` / `/twin respond`（见 `commands/twin.md`）。Python 只做结构校验和 artifact 应用，事实判断由 supervisor 完成。

## Bootstrap

`/twin "<one-line goal>"` 不是 workspace 路径时，supervisor 先草拟：

```text
<workspace>/goal.yaml
<workspace>/plan.yaml
```

用 `AskUserQuestion` 展示 goal、AC、non-goals、plan items，并请求确认。确认后写入文件，再进入执行闭环。

## 每轮调用顺序

```text
1. supervisor-context  → 读 goal / plan / state / focus / skeleton
2. supervisor 自写 instruction  （绑定当前 plan gap 与 AC）
3. worker-turn         → 启动或 resume worker，产出 runs/<run_id>/run.json
4. review-context      → 重读上下文 + 该 run artifact
5. supervisor 自写 review JSON
6. review              → 校验并应用，review 内联到 run.json::review
7. status=continue 自动进入下一轮；accepted_done / needs_human / failed 停止
```

state 是 `needs_human` 且无新回答时不启动 worker；state 是 `continue` 且 `next_instruction` 已写入时直接进入第 3 步。`continue` 不是用户停点，supervisor 必须自循环。

## 子命令契约

### `supervisor-context`

```bash
PYTHONPATH=$DEV_RULES python3 -m scripts.twin supervisor-context --workspace <ws> [--run-id <id>]
```

输出 JSON：`goal`、`plan`、`supervisor_persona`、`state`、`next_item`、`remaining_gaps`、`acceptance_evidence`、`acceptance_focus`、`artifact_paths`、`review_skeleton`；可选 `human_response`、`run`。Python 不生成 `next_instruction`。

### `worker-turn`

```bash
PYTHONPATH=$DEV_RULES python3 -m scripts.twin worker-turn --workspace <ws> --instruction "<supervisor-authored>" [--max-budget-usd N] --json
```

默认预算 50 USD，可用 `TWIN_WORKER_MAX_BUDGET_USD` 覆盖；超时由 `TWIN_WORKER_TIMEOUT_SECONDS`（默认 10800，3 小时）控制。返回 `run` 对象，与 `schemas/twin.run.schema.json` 对齐；`status` 一定是 `review_required` 或 `failed`，绝不是 `accepted_done`。

### `review-context`

```bash
PYTHONPATH=$DEV_RULES python3 -m scripts.twin review-context --workspace <ws> --run-id <id> --json
```

等同于 `supervisor-context --run-id <id>`：多出 `run` 字段。supervisor 用它生成 review JSON。

### `review`

```bash
PYTHONPATH=$DEV_RULES python3 -m scripts.twin review --workspace <ws> --run-id <id> --review-file <path>
```

review JSON 必须满足 `schemas/twin.supervisor_review.schema.json`：`status`、`summary`、`next_instruction`、`remaining_gaps`、`acceptance_evidence`、`risk_flags` 必填；可选 `actions: [fix_drift | validate_more | mark_plan_gap]`、`plan_updates`、`human_question`。`mark_plan_gap` 必须搭配 `plan_updates`。

Python 应用语义：

- `accepted_done`：要求 `remaining_gaps` 空、所有 AC 在 plan 都有 `actual_evidence`、plan 没有 open items。
- `continue`：必须给非空 `next_instruction`，写入 state，supervisor 继续下一轮。
- `needs_human`：必须给非空 `human_question`，写入 `state.needs_human`；supervisor 用 `AskUserQuestion` 问一个问题。
- `failed`：terminal。

## State machine

`supervisor_state.json::status` 取值：

| status | 含义 | 允许转入 |
| --- | --- | --- |
| `idle` | 新 workspace | `worker_running` |
| `worker_running` | worker 进行中 | `review_required`、`failed` |
| `review_required` | worker 结束，等 review | `continue` / `needs_human` / `accepted_done` / `failed` |
| `continue` | review 通过，下一轮 | `worker_running` |
| `needs_human` | 等真人回答 | `continue`（`/twin respond` 触发） |
| `accepted_done` | 收敛完成 | terminal |
| `failed` | 不可恢复 | terminal |

`worker-turn` 拒绝 `accepted_done` / `failed` / `review_required` / `needs_human` 状态启动；`worker_running` 但 run artifact 全缺时识别为 stale，自动 reset 后 fresh 启动。

## accepted_done 收尾自验清单

Python 已校验：schema、AC 覆盖、plan 无 open items、`remaining_gaps` 为空。

supervisor 必须自验：

- 宿主仓库 `git status --porcelain` 干净；如必须脏交付，在 review `summary` 写明原因。
- `$DEV_RULES/personas/*` 未被本会话或本轮 worker 写入：扫 `runs/<run_id>/events.jsonl` 的写入工具调用目标。
- worker 信号正常：`run.json::evidence.quality_flags` 无未处理阻断信号。
- 同一 gap 没有连续 3 轮未推进。
- PR / CI 状态绿，或失败原因写进 `risk_flags`。

任一不满足，回 `continue` 或 `needs_human`，别走 `accepted_done`。

## 维护入口

`python3 -m scripts.twin validate --fixtures` 跑 schema + 端到端 contract test；preflight 自动调用。其它阶段名不暴露给用户。
