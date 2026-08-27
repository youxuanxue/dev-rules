---
status: implemented
date: 2026-07-23
scope: twin universal CLI and shared host skill
approved_by: user-chat-2026-07-23
---

# twin 通用命令

## 结论

`twin` 是独立、provider-neutral 的 CLI。Claude、Codex、Antigravity 使用同一套 workspace 和状态机，当前 Agent 会话负责 supervisor 判断，Python 负责执行和记账。

本地运行不需要启动 server。省略 `plan.yaml.execution` 时，worker 唯一默认值是 `claude_headless`；要调用指定的本机 Claude、Codex 或 Gemini，才显式配置 `backend: local_cli`。远程、多 profile 或长尾 provider 才按需使用 CAO。

## 用户旅程

```bash
# 1. 当前 Codex 会话接管一个准备好的 workspace
twin run <workspace> --supervisor host/codex --json

# 2. 按返回 payload 判断 instruction 或 review，执行其中给出的 submit.command
# 3. 再执行返回的 next_command，直到 watch / ask_human / done / failed

# 随时查看状态；遇到真人门禁后记录回答并重入
twin status [workspace]
twin respond <answer>
```

Claude 和 Antigravity 只需把 route 换成 `host/claude`、`host/antigravity`；三端宿主入口统一由 `agent-skills/twin/SKILL.md` 选择当前 route。

## 显式交接

Workspace 首次 `run` 后绑定 supervisor。要换宿主，先完成当前 pending action，再显式交接：

```bash
twin handoff <workspace> --supervisor host/claude --json
twin run <workspace> --supervisor host/claude --json
```

交接会递增 state revision 并写审计事件；旧 route 和旧 token 继续 fail closed。相同 route 的重复交接是幂等操作。

安装检查使用 `twin doctor`。完整 host 操作说明见 `docs/twin-supervisor-runbook.md`；实时命令面以 `twin --help` 为准，schema 以仓库中的 `schemas/twin.*.schema.json` 为准。
