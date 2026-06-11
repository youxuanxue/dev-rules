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
| `commands/twin.md` | `/twin "<goal>"\|<workspace>\|status [workspace]\|respond <text>` | 运行 xuejiao persona supervisor，驱动 Claude Code worker 完成目标 |

> `/twin` 是 Claude-Code-only 命令（驱动 `claude` CLI worker）。代码审查不再是命令——已重写为三端通用 skill `xj-review`（见下）。

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
| `templates/install-hooks.sh` | 安装 pre-commit hook |
| `templates/preflight.sh` | 消费项目默认门禁模板 |
| `scripts/preflight.sh` | dev-rules 源仓库自己的提交门禁 |
| `schemas/review.schema.json` | `/user:xj-review` 输出契约 |
| `schemas/skill.schema.json` | 跨项目共享的 Skill manifest 规范 |
| `schemas/twin.*.schema.json` | twin goal、plan、state、review、run 与 human response 契约 |
| `scripts/twin/` | twin supervisor support runtime 与 fixtures |
| `docs/twin-design.md` | twin 设计单一事实来源 |
| `docs/twin-supervisor-runbook.md` | supervisor 每轮调用契约 |
| `templates/twin-workspace/` | `/twin` workspace 起点模板 |
| `global/CLAUDE.md` | Claude Code 全局工作宪法 |

## 接入与日常使用

新项目接入、本机安装、同步顺序、hook fallback、项目注册表与禁止事项统一见 `rules/dev-rules-convention.mdc`。<!-- rule-carrier-partition-pointer -->

完整哲学论证见 `digital-clone-research.md`。
