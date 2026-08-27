# dev-rules

数字分身规则的单一事实来源。`README.md` 只做入口索引；长期规则在 `rules/*.mdc`，跨宿主工作流在 Agent Skills，会话级纪律在 `global/CLAUDE.md`。

## 规则

| 文件 | 作用 |
| --- | --- |
| `rules/dev-rules-convention.mdc` | `dev-rules` submodule 约定、同步顺序、接入方式 |
| `rules/product-dev.mdc` | 默认单 PR、高风险升级路径、PR / commit 形状、自检纪律 |
| `rules/test-philosophy.mdc` | 按风险匹配 Story 强度、测试设计、Story ↔ Test 对齐 |

## twin 入口

| 入口 | 用法 | 作用 |
| --- | --- | --- |
| `global/bin/twin` | `twin` | Claude、Codex、Antigravity 共用的 xuejiao persona supervisor CLI |
| `agent-skills/twin/SKILL.md` | Claude `/twin`、Codex `$twin`、Antigravity `twin` skill | 三端共用的唯一宿主体验；经 skill symlink 同源分发 |

> `twin` supervisor 不要求本地 server。当前 Claude、Codex 或 Antigravity 会话通过 `host/<provider>` route 做判断；worker 默认使用 Claude headless，也可直接路由到本机 `claude` / `codex` / `gemini` CLI，远程或多 profile 场景才选 CAO。代码审查走三端通用 skill `xj-review`。

## Agent Skills

项目 `.cursor/skills` 是 Skills 的编辑与提交入口；不要在 `.claude/skills` 创建真实副本。家目录 `~/.cursor/skills` 则是 additive consumer registry：dev-rules 只维护指向配置 `agent-skills` checkout 的自有 links，保留其它 owner 的 symlink、文件和目录；`~/.claude/skills` 保持指向该 registry。Cursor、Claude Code、Codex、Antigravity CLI 四端同源消费：Codex 经 `~/.codex/AGENTS.md` + `~/.codex/skills`，Antigravity 经 `~/.gemini/antigravity-cli/AGENTS.md` + `~/.gemini/antigravity-cli/skills`（工作区 `.agents/skills`），两者直接消费 dev-rules-owned agent-skills links，不接管 home registry 的 foreign entries，并共用项目根 `AGENTS.md` 受管块；细则见 `rules/dev-rules-convention.mdc`。

代码审查工作流是 `agent-skills/xj-review/SKILL.md`（三端可调用：Claude Code/Cursor 用 `/xj-review`，Codex 用 `$xj-review` 或描述触发）；输出契约 `schemas/review.schema.json`，机械门禁与熔断复用 `scripts/preflight.sh` / `scripts/review/loop_state.py`。

## 关键入口

| 入口 | 作用 |
| --- | --- |
| `sync.sh --local` | 从当前 submodule 同步到父项目 `.cursor/rules/`，并登记项目 |
| `sync.sh --push` | push submodule 后同步本机镜像与已落地项目 |
| `sync.sh --pull` | 仅从 canonical `main` 拉取 `origin/main`，再 fan-out 到本机已落地项目；非 `main` fail-closed |
| `sync.sh --check` | 检查项目 `.cursor/rules/` 与 submodule 是否 drift |
| `verify-rules.sh` | 验证 dev-rules 仓库自身完整性 |
| `templates/install-hooks.sh` | 安装 pre-commit + commit-msg hook（后者硬拦高风险锚点缺失） |
| `templates/preflight.sh` | 消费项目默认门禁模板 |
| `scripts/preflight.sh` | dev-rules 源仓库自己的提交门禁 |
| `schemas/review.schema.json` | `/user:xj-review` 输出契约 |
| `schemas/skill.schema.json` | 跨项目共享的 Skill manifest 规范 |
| `schemas/twin.*.schema.json` | twin research、goal、plan、state、review、run 与 human response 契约 |
| `scripts/twin/` | twin supervisor support runtime 与 fixtures |
| `docs/twin-design.md` | twin 设计单一事实来源 |
| `docs/twin-supervisor-runbook.md` | supervisor 每轮调用契约 |
| `docs/twin-cao-operator-guide.md` | CAO/Codex worker 的安装、路由、验证和排障指南 |
| `docs/twin-universal-command.md` | twin 三端通用 CLI 与 host supervisor 决策 |
| `templates/twin-workspace/` | 三端共用的 twin workspace 起点模板 |
| `global/CLAUDE.md` | Claude Code 全局工作宪法 |

## 接入与日常使用

新项目接入、本机安装、同步顺序、hook fallback、项目注册表与禁止事项统一见 `rules/dev-rules-convention.mdc`。<!-- rule-carrier-partition-pointer -->

完整哲学论证见 `digital-clone-research.md`。
