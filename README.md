# dev-rules

数字分身规则的单一事实来源。`README.md` 只做入口索引；长期规则在 `rules/*.mdc`，命令契约在 `commands/*.md`，会话级纪律在 `global/CLAUDE.md`。

## 规则

| 文件 | 作用 |
| --- | --- |
| `rules/dev-rules-convention.mdc` | `dev-rules` submodule 约定、同步顺序、接入方式 |
| `rules/product-dev.mdc` | 默认单 PR、高风险升级路径、PR / commit 形状、自检纪律 |
| `rules/agent-contract-enforcement.mdc` | WebUI / API / CLI / MCP 契约同步、安全基线、跨端对齐 |
| `rules/test-philosophy.mdc` | 按风险匹配 Story 强度、测试设计、Story ↔ Test 对齐 |

## Claude Code 命令

| 命令 | 用法 | 作用 |
| --- | --- | --- |
| `commands/twin.md` | `/twin` | 运行 xuejiao persona supervisor；live 命令面见 `docs/agent_integration.md` |

> `/twin` 是 Claude-Code-only supervisor 命令；worker 默认使用 Claude headless，也可通过 CAO 路由到其他 provider。代码审查不再是命令——已重写为三端通用 skill `xj-review`（见下）。

## Agent Skills

`.cursor/skills` 是指向 `$HOME/Codes/agent-skills` 的 symlink，作为 Skills 的编辑与提交入口；不要在 `.claude/skills` 创建真实副本。Cursor、Claude Code、Codex、Antigravity CLI 四端同源消费：Codex 经 `~/.codex/AGENTS.md` + `~/.codex/skills`，Antigravity 经 `~/.gemini/antigravity-cli/AGENTS.md` + `~/.gemini/antigravity-cli/skills`（工作区 `.agents/skills`），两者共用项目根 `AGENTS.md` 受管块；细则见 `rules/dev-rules-convention.mdc`。

代码审查工作流是 `agent-skills/xj-review/SKILL.md`（三端可调用：Claude Code/Cursor 用 `/xj-review`，Codex 用 `$xj-review` 或描述触发）；输出契约 `schemas/review.schema.json`，机械门禁与熔断复用 `scripts/preflight.sh` / `scripts/review/loop_state.py`。

## 关键入口

| 入口 | 作用 |
| --- | --- |
| `sync.sh --local` | 从当前 submodule 同步到父项目 `.cursor/rules/`，并登记项目 |
| `sync.sh --push` | push submodule 后同步本机镜像与已落地项目 |
| `sync.sh --pull` | 从远端拉取并 fan-out 到本机已落地项目 |
| `sync.sh --check` | 检查项目 `.cursor/rules/` 与 submodule 是否 drift |
| `verify-rules.sh` | 验证 dev-rules 仓库自身完整性 |
| `templates/install-hooks.sh` | 安装 pre-commit + commit-msg hook（后者硬拦高风险锚点 / 契约删除公告缺失） |
| `templates/preflight.sh` | 消费项目默认门禁模板 |
| `scripts/preflight.sh` | dev-rules 源仓库自己的提交门禁 |
| `schemas/review.schema.json` | `/user:xj-review` 输出契约 |
| `schemas/skill.schema.json` | 跨项目共享的 Skill manifest 规范 |
| `schemas/twin.*.schema.json` | twin research、goal、plan、state、review、run 与 human response 契约 |
| `scripts/twin/` | twin supervisor support runtime 与 fixtures |
| `docs/twin-design.md` | twin 设计单一事实来源 |
| `docs/twin-supervisor-runbook.md` | supervisor 每轮调用契约 |
| `docs/twin-cao-operator-guide.md` | CAO/Codex worker 的安装、路由、验证和排障指南 |
| `docs/twin-universal-command.md` | twin 三端通用 CLI 与 supervisor backend 提案 |
| `docs/agent-team-playbook.md` | 四层 Agent 团队、七阶段裁剪、能力对比与本轮决策备忘 |
| `docs/agent_integration.md` | 从 live twin CLI、Claude command surface 与 schemas 生成的 Agent 集成契约 |
| `templates/twin-workspace/` | `/twin` workspace 起点模板 |
| `global/CLAUDE.md` | Claude Code 全局工作宪法 |

Agent 契约变更后运行 `python3 scripts/export_agent_contract.py`，并用 `python3 scripts/export_agent_contract.py --check` 检查漂移；`docs/agent_contract.generated.md` 与 `docs/agent_integration.md` 禁止手工编辑。

## 接入与日常使用

新项目接入、本机安装、同步顺序、hook fallback、项目注册表与禁止事项统一见 `rules/dev-rules-convention.mdc`。<!-- rule-carrier-partition-pointer -->

完整哲学论证见 `digital-clone-research.md`。
