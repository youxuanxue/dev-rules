运行 xuejiao persona supervisor，驱动 Claude Code worker 完成已准备好的目标工作区。

$ARGUMENTS

## 用户命令面

```text
/twin <workspace>
/twin status [workspace]
/twin respond <text>
```

`/twin <workspace>` 启动或 resume worker；每轮 worker 后必须 supervisor review，不能把 worker stop 当完成。`status` 只读，`respond` 把人类回答写入 workspace 后续跑。

## workspace 契约

`<workspace>` 必须包含 `goal.yaml` 与 `feature_ledger.yaml`（plan mode 产出）；缺一拒绝启动。workspace 内禁止出现 `supervisor-persona.md` / `worker-persona.md`；persona 直接读 `$DEV_RULES/personas/*.md`。字段定义见 `docs/twin-design.md`。

## NEEDS_HUMAN 展示

state 或 review 落到 `NEEDS_HUMAN` 时，inline 输出一个具体问题 + 一段背景 + `CURRENT.md` / `supervisor_state.json` / `runs/<run_id>/run.json` / `supervisor_review.json` 路径，最后一行给出 `/twin respond <text>` 用法。只问一个问题。

## 输出风格

一个状态、一条下一步、必要证据路径；不复述 runbook 或 design。supervisor 每轮内部子命令调用顺序见 `docs/twin-supervisor-runbook.md`。
