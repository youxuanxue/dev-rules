# twin host supervisor runbook

## 入口

准备好的 workspace 至少有 `goal.yaml` 和 `plan.yaml`。可用宿主 plan mode 直接起草后执行 `twin bootstrap`，或用 `twin scaffold` 生成最小草稿再完善。

```bash
twin run <workspace> --supervisor host/codex --json
```

Claude 和 Antigravity 分别把 route 换成 `host/claude`、`host/antigravity`。同一 workspace 首次运行后绑定 route；中途静默换宿主会 fail closed。

需要换宿主时，先提交当前 pending action，再显式交接并用新 route 重入：

```bash
twin handoff <workspace> --supervisor host/claude --json
twin run <workspace> --supervisor host/claude --json
```

Pending action 存在时交接会被拒绝。成功交接会递增 state revision 并写 `supervisor_route_handoff` 审计事件；相同 route 重复交接不改状态。

## 宿主循环

1. 调用 `twin run ... --json`。
2. 如果 action 是 `supervisor_instruction` 或 `review_run`，只读取 payload 的 `context`，按 `expected_output` 做一次判断。
3. 把结果写到 payload 给出的 `submit.command` stdin。不要修改命令里的 workspace、route、revision、token 或 run ID。
4. 提交成功后再次调用返回的 `next_command`。
5. 到 `watch_worker`、`ask_human`、`done` 或 `failed` 停下。

| Action | Host 动作 |
| --- | --- |
| `supervisor_instruction` | 形成一个非空 worker instruction，以 text/plain 提交 |
| `review_run` | 对照 goal/plan/evidence 形成 `twin.supervisor_review` JSON |
| `watch_worker` | 报告 worker 尚未可评审；稍后执行 `resume_command` |
| `ask_human` | 呈现 `needs_human.question`，等真人回答 |
| `done` | 报告 `accepted_done` 和 workspace |
| `failed` | 报告失败状态和 evidence 路径，不解释成完成 |

Python 会在一次 `twin run` 内自动执行 `worker_turn` 和 stale recovery；它们不是 host 判断点。

## Review 判断

Review 必须满足 `schemas/twin.supervisor_review.schema.json`。核心状态：

- `continue`：必须给 `next_instruction`，未满足项继续进入下一轮。
- `needs_human`：必须给 `human_question`，只用于真实业务/架构/高风险判断。
- `accepted_done`：plan item 必须全部完成、AC 必须有 plan evidence、`remaining_gaps` 必须为空。
- `failed`：存在无法继续的执行失败，不能伪装成完成。

Worker 输出只是证据，不拥有最终验收权。内部 supervisor review 也不等于高风险 PR 批准或 merge 授权。

## 人类门禁和重入

```bash
twin status [workspace]
twin respond [--workspace <workspace>] "<answer>"
twin run <workspace> --supervisor <bound-route> --json
```

Active workspace 指针按当前项目 cwd 隔离，主路径为 `~/.twin/active-workspaces/<cwd-hash>`；旧 `~/.claude/twin-active-workspaces/<cwd-hash>` 只做兼容读取。回答正文写入 workspace artifact，但 audit event 只记录 artifact 引用和长度，不记录正文。

`watch_worker` 是 bounded stop，不杀 worker、不删除 artifact。稍后从 `resume_command` 重入，Python 会根据当前 artifact 判断继续 watch、review 或 recovery。

## 内部兼容入口

`twin next`、`worker-turn`、`review-context`、`review` 等低层命令只保留给测试和未绑定的旧流程，不出现在公开 help。Workspace 一旦绑定 route，低层 worker/review mutation 会 fail closed；host 只能走 `run` 返回的 token-bound submit command。

## 验证

```bash
twin doctor
python3 -m scripts.twin validate --fixtures
PREFLIGHT_BASE=origin/main bash scripts/preflight.sh
```

Fixtures 覆盖 legacy workspace、stable/duplicate token、stale revision、wrong action/run/workspace/route、三种 host route、Codex→Claude→Antigravity handoff、三种 `local_cli` worker route、Codex host 完整闭环、`needs_human` 重入、provider-neutral active pointer 和真实 launcher。
