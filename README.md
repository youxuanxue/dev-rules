# dev-rules

数字分身规则的单一事实来源。`README.md` 只做入口索引；长期规则在 `rules/*.mdc`，命令契约在 `commands/*.md`，会话级纪律在 `global/CLAUDE.md`。

## 规则

| 文件 | 作用 |
| --- | --- |
| `rules/dev-rules-convention.mdc` | `dev-rules` submodule 约定、同步顺序、接入方式 |
| `rules/product-dev.mdc` | 默认单 PR、高风险升级路径、PR / commit 形状、自检纪律 |
| `rules/agent-contract-enforcement.mdc` | WebUI / API / CLI / MCP 契约同步、安全基线、跨端对齐 |
| `rules/test-philosophy.mdc` | 按风险匹配 Story 强度、测试设计、Story ↔ Test 对齐 |
| `rules/safe-shell-commands.mdc` | 破坏性命令确认规则 |

## Claude Code 命令

| 命令 | 用法 | 作用 |
| --- | --- | --- |
| `commands/decompose.md` | `/user:decompose [需求描述]` | 先做风险分级，再拆解任务与 PR 形状 |
| `commands/review.md` | `/user:review [范围]` | 默认对话内精简审查；按需写 PR comment / 严格记录 |
| `commands/calibrate.md` | `/user:calibrate [日期范围]` | 从历史 PR review 证据校准审查噪音与漏报 |
| `commands/twin.md` | `/user:twin init|run|validate|replay` | 运行本机 xuejiao 分身 supervisor harness |

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
| `schemas/review.schema.json` | `/user:review` 输出契约 |
| `schemas/skill.schema.json` | 跨项目共享的 Skill manifest 规范 |
| `schemas/xuejiao_twin.*.schema.json` | xuejiao 分身目标、ledger、run 与 agent 输出契约 |
| `scripts/xuejiao_twin/` | 本机 xuejiao supervisor harness CLI 与 fixtures |
| `global/CLAUDE.md` | Claude Code 全局工作宪法 |

## 接入与日常使用

新项目接入、本机安装、同步顺序、hook fallback、项目注册表与禁止事项统一见 `rules/dev-rules-convention.mdc`。<!-- rule-carrier-partition-pointer -->

完整哲学论证见 `digital-clone-research.md`；Harness、记忆分层与 spec delta 取舍见 `docs/dev-rules-agent-context-improvement.md`。
