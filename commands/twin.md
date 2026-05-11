运行 xuejiao persona supervisor，驱动 Claude Code worker 完成已准备好的目标工作区。

$ARGUMENTS

## 定位

`/twin` 是 Claude Code 交互模式里的 persona supervisor capability。

- supervisor：当前 Claude Code 交互会话，读取事实源、决定继续/验收/`NEEDS_HUMAN`。
- worker：`claude -p --permission-mode bypassPermissions` headless worker，可通过 `worker_session_id` resume。
- harness：dev-rules、项目 `CLAUDE.md`、Claude Code permissions/settings/hooks/preflight。

不要另起 headless supervisor。不要把交互上下文当事实源。worker 自述不是验收结论。

## 用户命令面

只暴露三个日常入口：

```text
/twin <workspace>
/twin status [workspace]
/twin respond <text>
```

`init / run / next / replan / replay / validate / loop / health` 不作为用户心智暴露；必要时只使用 Python 内部 debug 子命令。

## 工作区契约

`/twin <workspace>` 只接受 plan mode 已准备好的目标工作区：

```text
goal.yaml
feature_ledger.yaml
```

缺少 `goal.yaml` 或 `feature_ledger.yaml` 时，拒绝启动 worker，要求回到 Claude Code plan mode 补齐。persona 不属于目标工作区；supervisor 和 worker 直接使用 `$DEV_RULES/personas/supervisor-persona.md` 与 `$DEV_RULES/personas/worker-persona.md`。工作区内出现 `supervisor-persona.md` 或 `worker-persona.md` 时直接拒绝，防止按具体 goal 改 persona。supervisor 和 worker 禁止写 `$DEV_RULES/personas/*`；health/review 发现 `PERSONA_SOURCE_WRITE` 时不能验收。

`goal.yaml` 只承载 goal / acceptance criteria / non-goals。`feature_ledger.yaml` 只承载 deliverables、依赖、AC 引用、evidence plan/actual evidence、status、next action。授权和 human gate 由 Claude Code permissions/settings/hooks + dev-rules + 项目 `CLAUDE.md` 承担。

## `/twin <workspace>` 行为

1. 读取 `goal.yaml`、`feature_ledger.yaml`、`$DEV_RULES/personas/supervisor-persona.md`、`supervisor_state.json`、`runs/*` 摘要。
2. 若 state 是 `needs_human` 且没有新回答，直接 inline 展示问题和证据路径，不启动 worker。
3. 若 state 是 `continue` 且已有 `next_instruction`，禁止停下汇报；直接用该 instruction 启动/resume worker。
4. 读取 supervisor context；当前 Claude Code 交互会话作为 persona supervisor，基于事实源生成本轮 `next_instruction`：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin supervisor-context --workspace <workspace>
```

Python 只输出 goal、ledger、state、gaps、next item、artifact paths 和空 review skeleton；不要让 Python 生成 instruction 或 decision。

5. 用当前 supervisor 生成的 instruction 启动或 resume worker；默认 worker 预算为 20 USD，可用 `XUEJIAO_TWIN_WORKER_MAX_BUDGET_USD` 或 `--max-budget-usd` 覆盖：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin worker-turn --workspace <workspace> --instruction "<supervisor-authored instruction>" --json
```

6. 读取 review context，让当前交互会话作为 supervisor 产出 `supervisor_review.json`：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin review-context --workspace <workspace> --run-id <run_id> --json
```

review 必须由当前 Claude Code supervisor 生成，并包含：`decision`、`next_instruction`、`remaining_gaps`、`acceptance_evidence`、`risk_flags`，可用 `actions: [fix_drift|validate_more|mark_ledger_gap]` 标记纠偏意图。Python 只校验并应用 review。

7. 应用 review：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin review --workspace <workspace> --run-id <run_id> --review-file <review.json> --json
```

8. 若 decision 是 `CONTINUE`，禁止停下汇报，必须直接进入下一轮 worker-turn；若是 `ACCEPTED_DONE`、`NEEDS_HUMAN` 或 `FAILED`，停止并汇报。

`ACCEPTED_DONE` 前必须确认 goal、AC、ledger、diff、tests/preflight、contract/docs 证据闭环。连续 3 次同一 gap 无推进会自动转为 `NEEDS_HUMAN`。

## ACCEPTED_DONE 收尾契约

`ACCEPTED_DONE` 不是一次 worker 完成的代名词，是"宿主仓库已经收敛到可交付状态"。supervisor 在落 `ACCEPTED_DONE` 前必须自验：

1. 宿主仓库 `git status` 干净；如有未提交改动，supervisor 必须先驱动 worker 完成提交，或显式在 review 里写 `actions: ["allow_uncommitted_evidence"]` 并在 `summary` 里记录原因。
2. 当前分支与远端 PR 状态一致；存在 PR 时，PR checks 已绿或失败原因已在 review `risk_flags` 中点名。
3. 一次独立的轻量 review pass（默认 `/user:review concise`）已经跑过，且没有新发现的 blocker；如果有，回到 `CONTINUE` 而不是 `ACCEPTED_DONE`。

`supervisor_review.py::_validate_git_state_for_accepted_done` 会硬阻断条件 1 失败的情况；条件 2、3 由 supervisor persona 在生成 review 前自查，不靠 Python。

## `/twin status`

只读状态，不启动 worker：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin status --workspace <workspace>
```

输出 goal、status、current item、round、remaining gaps、`CURRENT.md` 路径；如果没有明确 workspace，要求用户补 workspace。

## Python 内部 debug：health

`health` 是 supervisor 维护用只读审计入口，不属于 `/twin` 日常用户命令面；它只读取 state、ledger、current run、review 与 events tail，不启动 worker、不写 workspace、不替 supervisor 做验收决策。

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin health --workspace <workspace> --json
```

用于快速查看 terminal state、run_health、events tail 和历史退化 warning；最终继续/验收/`NEEDS_HUMAN` 仍由当前 Claude Code supervisor 按事实源判断。

## `/twin respond <text>`

把人类回答写入当前 workspace，然后继续 `/twin <workspace>`：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin respond --workspace <workspace> --text "<text>"
```

回答会写入 `human_response.json`，下一轮 worker prompt 会包含并消费它。不使用旧的 `--action approve_and_continue` 等动作枚举。

## `NEEDS_HUMAN` 展示契约

当 state 或 review 为 `NEEDS_HUMAN`，必须 inline 输出：

1. 一个具体问题。
2. 简短背景。
3. 相关 `CURRENT.md`、`supervisor_state.json`、`runs/<run_id>/run.json`、`supervisor_review.json` 路径。
4. 下一条 `/twin respond <text>` 用法。

只问一个问题。不要让用户去翻 `CURRENT.md` 才知道该回答什么。

## 输出风格

默认保持乔布斯/OPC：一个状态、一条下一步、必要证据路径。不要重复 runbook 大段内容。
