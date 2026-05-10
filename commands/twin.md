运行 xuejiao persona supervisor，驱动 Claude Code worker 完成已准备好的目标工作区。

$ARGUMENTS

## 定位

`/twin` 是 Claude Code 交互模式里的 persona supervisor capability。

- supervisor：当前 Claude Code 交互会话，读取事实源、决定继续/验收/`NEEDS_HUMAN`。
- worker：`claude -p --permission-mode bypass` headless worker，可通过 `worker_session_id` resume。
- harness：dev-rules、项目 `CLAUDE.md`、Claude Code permissions/settings/hooks/preflight。

不要另起 headless supervisor。不要把交互上下文当事实源。worker 自述不是验收结论。

## 用户命令面

只暴露三个日常入口：

```text
/twin <workspace>
/twin status [workspace]
/twin respond <text>
```

`init / run / next / replan / replay / validate / loop` 不作为用户心智暴露；必要时只使用 Python 内部 debug 子命令。

## 工作区契约

`/twin <workspace>` 只接受 plan mode 已准备好的目标工作区：

```text
goal.yaml
feature_ledger.yaml
supervisor-persona.md
worker-persona.md
```

缺少 `goal.yaml` 或 `feature_ledger.yaml` 时，拒绝启动 worker，要求回到 Claude Code plan mode 补齐。缺少 persona snapshot 时，也拒绝启动。

`goal.yaml` 只承载 goal / acceptance criteria / non-goals。`feature_ledger.yaml` 只承载 deliverables、依赖、AC 引用、evidence plan/actual evidence、status、next action。授权和 human gate 由 Claude Code permissions/settings/hooks + dev-rules + 项目 `CLAUDE.md` 承担。

## `/twin <workspace>` 行为

1. 读取 `goal.yaml`、`feature_ledger.yaml`、`supervisor-persona.md`、`supervisor_state.json`、`runs/*` 摘要。
2. 若 state 是 `needs_human` 且没有新回答，直接 inline 展示问题和证据路径，不启动 worker。
3. 生成或读取本轮 `next_instruction`：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin next-instruction --workspace <workspace> --json
```

4. 启动或 resume worker：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin worker-turn --workspace <workspace> --json
```

5. 读取 review context，让当前交互会话作为 supervisor 产出 `supervisor_review.json`：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin review-context --workspace <workspace> --run-id <run_id> --json
```

review 必须包含：`decision`、`next_instruction`、`remaining_gaps`、`acceptance_evidence`、`risk_flags`，可用 `actions: [fix_drift|validate_more|mark_ledger_gap]` 辅助纠偏。

6. 应用 review：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin review --workspace <workspace> --run-id <run_id> --review-file <review.json> --json
```

7. 若 decision 是 `CONTINUE`，直接进入下一轮；若是 `ACCEPTED_DONE`、`NEEDS_HUMAN` 或 `FAILED`，停止并汇报。

`ACCEPTED_DONE` 前必须确认 goal、AC、ledger、diff、tests/preflight、contract/docs 证据闭环。连续 3 次同一 gap 无推进会自动转为 `NEEDS_HUMAN`。

## `/twin status`

只读状态，不启动 worker：

```bash
PYTHONPATH=/Users/xuejiao/Codes/dev-rules python3 -m scripts.xuejiao_twin status --workspace <workspace>
```

输出 goal、status、current item、round、remaining gaps、`CURRENT.md` 路径；如果没有明确 workspace，要求用户补 workspace。

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
