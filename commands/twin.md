运行 xuejiao persona supervisor，驱动 Claude Code worker 完成目标。

$ARGUMENTS

## 用户命令面

```text
/twin "<one-line goal>"
/twin <workspace>
/twin status [workspace]
/twin respond <text>
```

## 主路径

`/twin "<one-line goal>"` 是 bootstrap：当参数不是 `status` / `respond` / 已存在 workspace 路径时，当前 Claude Code supervisor 先按 plan mode 的方式调研 repo facts，并亲自草拟 `goal.yaml + plan.yaml`；然后用 `AskUserQuestion` 请求确认。确认后调用 `python3 -m scripts.twin bootstrap --workspace <ws> --goal-file <goal.yaml> --plan-file <plan.yaml>` 写入并校验 workspace，再进入执行闭环。`python3 -m scripts.twin scaffold "<goal>" --json` 只作为最小 scaffold fallback，不代表真实 planning。

`/twin <workspace>` 启动或 resume 已准备好的 workspace。每轮 supervisor 必须自循环：

```text
supervisor-context → 写 next_instruction → worker-turn → review-context → 写 review JSON → review → continue 自动下一轮
```

只在 `accepted_done` / `needs_human` / `failed` 停下；`continue` 必须自动进入下一轮，不能让用户反复说“继续”。worker stop 不是完成。

## workspace 契约

`<workspace>` 必须包含 `goal.yaml` 与 `plan.yaml`。workspace 内禁止出现 `supervisor-persona.md` / `worker-persona.md`；persona 直接读 `$DEV_RULES/personas/*.md`。字段定义见 `docs/twin-design.md`。

## needs_human 展示

state 或 review 落到 `needs_human` 时，优先用 `AskUserQuestion` inline 问一个具体问题，给一段背景和推荐选项。证据路径只作为辅助：`CURRENT.md` / `supervisor_state.json` / `runs/<run_id>/run.json::review`。不要要求用户读 JSON 才能回答。

## 输出风格

一个状态、一条下一步、必要证据路径；不复述 runbook 或 design。supervisor 每轮内部子命令调用顺序见 `docs/twin-supervisor-runbook.md`。
