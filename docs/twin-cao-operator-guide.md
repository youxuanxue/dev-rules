# twin worker 路由与 CAO 操作指南

## 当前边界

Supervisor 与 worker 是两个独立路由。当前 Claude、Codex 或 Antigravity 会话都可以做 host supervisor；worker 默认使用 Claude headless，也可以直接调用本机 `claude` / `codex` / `gemini` CLI。只有远程、多 profile 或其他 provider 才需要 CAO：

```text
Claude / Codex / Antigravity host supervisor
  -> twin workspace/state engine
  -> local_cli provider CLI 或 CAO POST /terminals/run-step
  -> provider worker
  -> twin 隔离 worktree
```

CAO 启动的是一个新的 worker CLI 进程，不会接管或复用当前 host 会话。跨轮事实来自 twin workspace artifacts，不来自 provider transcript。Host supervisor 和 `local_cli` worker 都不要求 CAO server。

## 前置条件

1. 直接 CLI 路由：目标 provider CLI 在当前 `PATH` 中，可运行 `twin doctor --json` 检查。
2. CAO 路由：CAO server 可从 twin 所在机器访问，目标 provider CLI 在 CAO server 的 `PATH` 中。
3. provider 进程可以使用同一用户凭据或 server 环境完成认证。
4. CAO terminal backend 的依赖已安装；默认 tmux backend 需要可用的 `tmux`。
5. `cao profile list` 能看到 plan 将使用的 agent profile。

“当前 Codex 会话能工作”不能单独证明 CAO 子进程也能工作。若当前会话依赖 launcher、自定义 provider 或父进程注入 token，启动 CAO 时必须复现同一环境。优先用一个 fresh、只读的 provider 命令做 smoke test。

## 启动本地 CAO

使用本地 CAO 仓库时：

```bash
cd /path/to/cli-agent-orchestrator
uv run cao init
uv run cao profile list
uv run cao-server --host 127.0.0.1 --port 9889
```

另一个终端检查：

```bash
curl -fsS http://127.0.0.1:9889/health
```

预期返回 service status 为 `ok`。若 `cao-server` 已作为 uv tool 安装，也可以直接运行全局命令；twin 只依赖 HTTP contract，不依赖 CAO 的安装位置。

## twin 连接配置

默认地址是 `http://127.0.0.1:9889`。连接其他 CAO 时，在启动 host supervisor 前设置：

```bash
export TWIN_CAO_BASE_URL=http://127.0.0.1:9889
```

只有 CAO 开启认证时才设置：

```bash
export CAO_AUTH_LOCAL_TOKEN='<local-token>'
```

`CAO_AUTH_LOCAL_TOKEN` 只作为 HTTP bearer header 使用，不写入 goal、plan、state、run 或 events。未启用认证时保持该变量未设置。带 bearer 的 loopback 地址可使用本地 HTTP；非 loopback CAO 必须配置 HTTPS，否则 twin 在发送请求前 fail closed。CAO `run-step` 不跟随 HTTP redirect；迁移 endpoint 时直接更新 `TWIN_CAO_BASE_URL`，不要依赖 30x。

## 选择 provider 和 profile

在 workspace 的 `plan.yaml` 中声明直接 CLI 路由：

```yaml
execution:
  backend: local_cli
  provider: codex
```

本机 CLI provider 为 `claude`、`codex`、`gemini`。Codex fresh/resume 每轮都显式固定 `workspace-write` + `approval_policy=never`；Gemini 固定 OS sandbox + `approval-mode=yolo`。这两层硬边界不依赖用户全局 CLI 配置，provider timeout 也会终止同一进程组中的 tool 子进程。需要 CAO profile 时使用：

```yaml
execution:
  backend: cao
  provider: codex
  agent: developer
```

字段含义：

- `backend`：twin worker backend；`cao` 表示通过 CAO HTTP control plane。
- `provider`：CAO provider 名称，例如 `codex`。
- `agent`：CAO agent profile 名称，必须能由 `cao profile list` 查到。

`developer` 是 CAO 内置 profile，适合首次连通性验证。`codex_developer` 不是通用内置名称，不应在未安装该 profile 时直接使用。

正式运行建议使用专门的 twin worker profile，而不是在 plan 中塞 provider 权限细节。CAO profile 负责 role、allowed tools、model、MCP 和 provider-specific config；twin 只选择 profile。

CAO `run-step` 当前没有费用预算字段。`--max-budget-usd` 和 `TWIN_WORKER_MAX_BUDGET_USD` 只约束 Claude headless；CAO backend 显式设置它们会 fail closed。Codex 等 provider 的成本限制需要在 twin 之外用 provider/account policy 管理。`TWIN_WORKER_TIMEOUT_SECONDS` 仍会作为单轮 timeout 传给 CAO。

## Codex 的两个 profile 概念

CAO agent profile 与 Codex 原生 config profile 不是同一件事：

- `plan.yaml.execution.agent` 选择 CAO agent profile。
- CAO agent profile 中的 `codexProfile` 再选择 Codex 原生 profile。

CAO 的 Codex provider 在没有可用 `codexProfile` 时可能走非交互 unrestricted 路径。生产 profile 应明确配置非交互 sandbox/approval 策略，并用当前安装的 Codex CLI 单独验证：

```bash
codex exec --sandbox workspace-write -c 'approval_policy="never"' "Reply exactly CODEX_PROFILE_OK. Do not edit files."
```

Codex profile 的存储格式随 CLI 版本演进，必须以本机 `codex --help` 和实际 smoke test 为准，不要只凭旧 CAO 示例假设配置位置。CAO 当前对 Codex 的 allowed-tools 限制主要是 prompt-level；真正的硬边界仍是 Codex sandbox、外部 worktree 和审批门禁。

## 直接验证 CAO/Codex 链路

在运行 twin 前，可以直接调用同一 endpoint 排除 twin 状态机因素：

```bash
curl -fsS -X POST http://127.0.0.1:9889/terminals/run-step \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "codex",
    "agent": "developer",
    "prompt": "Reply exactly CODEX_WORKER_OK. Do not edit files.",
    "teardown": true,
    "timeout": 120,
    "working_directory": "/absolute/path/to/repo"
  }'
```

CAO 开启认证时额外添加：

```bash
-H "Authorization: Bearer ${CAO_AUTH_LOCAL_TOKEN}"
```

成功响应应包含 `terminal_id`、`last_message` 和完成状态。smoke test 使用只读任务；实际 twin turn 会把隔离 worktree 路径作为 `working_directory`。

## 从 twin 运行

准备好带 `execution` 的 workspace 后，从当前宿主运行，例如 Codex：

```bash
twin run <workspace> --supervisor host/codex --json
```

twin 每轮创建 fresh CAO terminal，使用 `teardown=true`，并在 `run.json::worker` 记录 backend、provider、agent 和 terminal hash。CAO backend 不 resume provider session；下一轮由 goal、plan、state 和 run evidence 重建上下文。

## Git 隔离

- 人类交互式创建 worktree 使用 `wts`。
- 普通 Agent 会话加载 `git-worktree-submodule` skill，并调用共享 `wtree.py`。
- twin worker 内部只调用同一个 `wtree.py` JSON contract，不调用 `wts` shell wrapper。
- worktree 创建或 session check 失败时 fail closed，不回退共享 checkout。
- terminal cleanup 只移除没有未保存业务改动的 twin worktree，并把 removed / preserved / failed 结果写入 `workspace_events.jsonl`。

`.wtree-session.json` 是本机 session binding metadata，不是业务改动，也不进入 git。

## 常见故障

| 现象 | 检查 |
| --- | --- |
| connection refused | CAO server 是否运行，`TWIN_CAO_BASE_URL` 是否正确 |
| 401/403 | CAO 是否启用 auth，server 与 twin 是否使用正确的 `CAO_AUTH_LOCAL_TOKEN` |
| bearer auth requires HTTPS | 非 loopback `TWIN_CAO_BASE_URL` 是否使用 HTTPS；本地开发改用 `127.0.0.1` / `localhost` |
| provider not installed | 从 CAO server 环境运行 `command -v <provider-cli>` |
| profile not found | 运行 `cao profile list`，修正 `plan.yaml.execution.agent` |
| provider 启动后超时 | 检查认证、approval 是否等待人工输入、terminal backend 和 profile init timeout |
| 当前会话能用但 CAO worker 不能用 | CAO server 是否继承 launcher、自定义 provider、proxy 或 credential 环境 |
| worktree isolation failed closed | 运行 `wtree.py status` / `session-check`，检查 dirty submodule 和 gitlink SHA |

## 验证命令

仓库内的确定性验证：

```bash
twin validate --fixtures
python3 scripts/twin/worktree.py
python3 scripts/export_agent_contract.py --check
./scripts/preflight.sh
```

真实 CAO/provider smoke test 依赖本机服务、认证和 terminal backend，不由离线 fixture 冒充。
