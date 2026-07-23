运行 xuejiao persona supervisor；Claude Code 只做 provider-neutral `twin` CLI 的薄宿主适配。

$ARGUMENTS

## 用户命令面

```text
/twin "<one-line goal>"
/twin research "<one-line goal>"
/twin plan "<one-line goal>" [--research <research.yaml>]
/twin <workspace>
/twin status [workspace]
/twin respond <text>
```

## 薄适配契约

- `status [workspace]`：执行 `twin status [workspace]`，逐字转发 stdout；不自行读取或解释 workspace artifact。
- `respond <text>`：执行 `twin respond <text>`，逐字转发 stdout；可用 `--workspace` 显式指定 workspace。
- `<workspace>`：执行 `twin run <workspace> --supervisor host/claude --json`。
- `"<one-line goal>"`：当前 Claude host 按 plan mode 判断是否需要只读 research，亲自形成 `goal.yaml` 和 `plan.yaml`，用 `twin bootstrap` 写入后进入同一个 `twin run` 协议。`research` / `plan` 只是这个 bootstrap 判断面的显式入口，不是另一套运行时。

`twin run` 返回的 action payload 是唯一 supervisor 协议：

1. `supervisor_instruction` 或 `review_run`：只根据 payload 中的 bounded context 做不可机械化判断；按 `expected_output` 形成结果，并通过 payload 的 `submit.command` 原样提交。
2. 提交成功后再次执行 `twin run <workspace> --supervisor host/claude --json`，直到 CLI 返回宿主停点。
3. `watch_worker`：报告 worker 尚未到可评审状态和 `resume_command`，本次调用结束。
4. `ask_human`：逐字呈现 `needs_human.question`；用户回答后执行 `twin respond`，再按 `resume_command` 重入。
5. `done` / `failed`：报告 terminal 结果和 workspace 路径，本次调用结束。

不得自行推断 state transition、拼装 token/revision、直接编辑 `supervisor_state.json`，也不得绕过 `needs_human`、高风险审批或 merge 授权。所有确定性循环、worker 调用、schema 校验和 artifact mutation 由 `twin` CLI 完成；完整 live 契约见 `$DEV_RULES/docs/agent_integration.md`。
