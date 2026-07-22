---
status: implemented
date: 2026-07-22
scope: twin universal CLI and interactive host supervisor
approved_by: user-chat-2026-07-22
---

# twin 通用命令

## 结论

`twin` 现在是独立、provider-neutral 的 CLI。Claude、Codex、Antigravity 使用同一套 workspace、状态机和 worker backend；当前 Agent 会话只在 instruction 和 review 两个判断点充当 supervisor。

不需要启动本地 server：

- 本机 worker 优先走 `local_cli`，直接调用 `claude`、`codex` 或 `gemini`。
- 远程、多 profile 或长尾 provider 才按需使用 CAO worker backend。
- 本次不实现无人值守 CAO supervisor。

高风险实现基线见 `docs/approved/twin-universal-host-supervisor.md`，live CLI/schema 契约见生成文件 `docs/agent_integration.md`。

## 使用方式

```bash
# Codex 当前会话做 supervisor
twin run <workspace> --supervisor host/codex --json

# Claude 当前会话做 supervisor；/twin <workspace> 是这个入口的薄适配
twin run <workspace> --supervisor host/claude --json

# Antigravity 当前会话做 supervisor
twin run <workspace> --supervisor host/antigravity --json

twin status [workspace]
twin respond <answer>
twin doctor
```

`sync.sh` 把 `global/bin/twin` 分发为 `~/.local/bin/twin`。仓库内开发可直接运行 `global/bin/twin`。

## 工作方式

```text
host 调 twin run
  -> Python 自动执行确定性步骤和 worker turn
  -> 需要判断时返回 self-describing action
  -> host 只生成 instruction 或 review
  -> host 使用 payload 给出的 submit.command 提交
  -> 再次 twin run
  -> watch / needs_human / done / failed 时停下
```

需要宿主判断的 action 包含：

- bounded context；
- expected output；
- workspace state revision；
- 一次性 action token；
- 当前 run ID；
- 可直接执行、从 stdin 接收结果的 submit command。

重复执行 `twin run` 不会发新 token；只要 state 没变，就返回同一个 pending action。提交时 revision、token、action、run、workspace 或 route 任一不一致都会拒绝。

## 责任边界

| Owner | 负责 | 不负责 |
| --- | --- | --- |
| 当前 Agent host | 下一轮 instruction、run evidence review、是否需要真人 | 直接改 state、绕过 token/schema |
| `twin` Python runtime | action 派生、worker 调用、状态迁移、artifact、重入 | 替模型做产品/验收判断 |
| worker backend | 在隔离 worktree 完成本轮交付并产出证据 | 最终验收、高风险批准 |
| 人类 | 高风险审批、架构/业务决策、merge 授权 | 日常状态接力 |

Supervisor route 存在 `supervisor_state.json`；worker route 仍只存在 `plan.yaml.execution`。二者是两个维度，Codex host 可以监督 Claude worker，也可以监督 Codex/Gemini/CAO worker。

## 单一事实来源

- 可执行入口：`global/bin/twin`
- driver 和状态机：`scripts/twin/driver.py`、`scripts/twin/`
- artifact schema：`schemas/twin.*.schema.json`
- host 操作契约：`docs/twin-supervisor-runbook.md`
- live 命令清单：`docs/agent_integration.md`，由 `scripts/export_agent_contract.py` 生成
- Claude 适配：`commands/twin.md`
- Codex/Antigravity 导航：`scripts/gen_codex_agents.py` 生成的项目 `AGENTS.md` managed block

本次为了在 PR #88 端到端交付，不新增第二个 `agent-skills` PR。CLI action 自描述，三端导航只链接同一生成契约；以后若增加 `twin` skill，它也只能做 discoverability，不能复制状态机。

## 兼容和非目标

- 旧 schema-version-1 workspace 缺少 revision/route/token 字段时仍可读取，并在首次 `twin run` 时惰性绑定。
- `python3 -m scripts.twin` 和既有内部子命令继续可用；新的主用户面是 `twin`。
- `/twin status`、`/twin respond` 和 `/twin <workspace>` 继续通过 Claude 薄适配工作。
- 不实现 daemon、Web UI、Agent swarm、跨机器 scheduler 或无人值守 CAO supervisor。
- 不绕过 `needs_human`、高风险审批、provider sandbox、hooks、preflight 或 merge 授权。
