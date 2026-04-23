# dev-rules

数字分身规则的单一事实来源。`README.md` 只回答三件事：这里有什么、怎么接入、日常怎么用。

哲学（Jobs 聚焦/简洁 + OPC 杠杆/自动化）的完整论证见 `digital-clone-research.md`；规则正文是它们落到日常工作的可执行形式。

## 仓库内容

### 规则

| 文件 | 作用 |
| --- | --- |
| `rules/dev-rules-convention.mdc` | `dev-rules` submodule 约定、同步顺序、接入方式 |
| `rules/agent-contract-enforcement.mdc` | agent-facing 契约同步、安全基线、生成脚本要求 |
| `rules/test-philosophy.mdc` | 按风险匹配 Story 强度、测试设计、Story ↔ Test 对齐 |
| `rules/safe-shell-commands.mdc` | 破坏性命令确认规则 |
| `rules/product-dev.mdc` | 默认单 PR、高风险升级路径、PR/commit 形状、自检纪律 |

### Claude Code 命令

| 命令 | 用法 | 作用 |
| --- | --- | --- |
| `commands/decompose.md` | `/user:decompose [需求描述]` | 先做风险分级，再拆解任务与 PR 形状 |
| `commands/review.md` | `/user:review [范围]` | 默认精简审查；高风险时做完整符合性检查 |
| `commands/calibrate.md` | `/user:calibrate [日期范围]` | 汇总 review JSON 的人工校准结果 |

### 关键工件

| 工件 | 作用 |
| --- | --- |
| `sync.sh --check` | 检查项目 `.cursor/rules/` 与 submodule 是否 drift |
| `sync.sh --push` | push submodule 后同步到本机镜像与已落地项目 |
| `sync.sh --pull` | 从远端拉取并 fan-out 到本机已落地项目 |
| `verify-rules.sh` | 验证仓库自身完整性（frontmatter / 哲学映射 / 幽灵路径 / global 关键文件 / LaunchAgent 实装） |
| `templates/preflight.sh` | 默认门禁 + 条件门禁模板 |
| `templates/install-hooks.sh` | 安装 pre-commit hook |
| `templates/cloud-agent-bootstrap.sh` | 安装 + `--check` 云端/本地 agent 运行环境（claude/gh/jq、secrets） |
| `templates/cloud-agent.env.example` | 项目声明 agent 运行契约（工具、必需/可选 secrets、Claude gateway） |
| `scripts/check_approved_docs.py` | `docs/approved/*.md` frontmatter 不变量检查 |
| `schemas/review.schema.json` | `/user:review` 输出契约（`/user:calibrate` 入口校验） |
| `schemas/skill.schema.json` | 跨项目共享的 Skill manifest 规范（消费侧自行 validate；本仓库不内置 check） |
| `sync-stats.sh` + `.stats.json` | 文档数字/事实 drift 机械检查 |
| `global/CLAUDE.md` | Claude Code 全局工作宪法 |
| `.registered-projects` + `.local-projects` | 跨机器项目注册表 + 本机落地映射 |

## 接入

### 新项目接入

```bash
cd 项目根目录
git submodule add git@github.com:youxuanxue/dev-rules.git dev-rules
dev-rules/sync.sh --local
git add .cursor/rules/ .gitmodules dev-rules
git commit -m "chore: add dev-rules submodule and sync rules"
```

### 本机首次安装

```bash
git clone git@github.com:youxuanxue/dev-rules.git ~/Codes/dev-rules
~/Codes/dev-rules/sync.sh
bash ~/Codes/dev-rules/templates/install-launchagent.sh
```

安装后：

- `~/.cursor/rules/` → `rules/`
- `~/.claude/commands/` → `commands/`
- `~/.claude/CLAUDE.md` → `global/CLAUDE.md`
- macOS LaunchAgent 每 30 分钟执行一次 `sync.sh --pull`

## 日常使用

```bash
vim dev-rules/{rules,commands,global}/某文件
dev-rules/sync.sh --local
dev-rules/verify-rules.sh
cd dev-rules && git add -A && git commit -m "update rules"
./sync.sh --push
cd ..
git add dev-rules .cursor/rules/ && git commit -m "chore: sync dev-rules" && git push
```

`sync.sh --push` 是推荐的一步同步入口。它把“push submodule + 更新本机镜像 + fan-out 到本机项目”压成一个原子动作。

## 结构约定

- `~/Codes/dev-rules` 与 `~/.cursor/rules/` / `~/.claude/*` 使用 symlink，保证本机即时生效。
- 项目内 `.cursor/rules/` 使用 real copy，保证云端 Agent 可读、可随项目版本化。
- 需要完整背景时读 `digital-clone-research.md`；需要执行规则时读 `rules/*.mdc`、`commands/*.md`、`global/CLAUDE.md`。

