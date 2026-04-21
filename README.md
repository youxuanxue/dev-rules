# dev-rules

数字分身规则的单一事实来源。`README.md` 只回答三件事：这里有什么、怎么接入、日常怎么用。长篇原理论证放在 `digital-clone-research.md`。

## 设计哲学

- **Jobs**：聚焦核心场景、删除非核心流程、最小 API 面、端到端体验、简洁。
- **OPC**：人做判断、AI 做执行；默认轻流程；把重复劳动写成脚本和检查。

载体与哲学映射：

| 载体 | Jobs（聚焦/简洁） | OPC（自动化/杠杆） |
| --- | --- | --- |
| `rules/product-dev.mdc` | 默认单 PR，只有高风险才升级流程 | 风险分级、自检纪律 |
| `rules/test-philosophy.mdc` | 测试优先覆盖核心 AC | 测试自动化、Story 对齐 |
| `rules/agent-contract-enforcement.mdc` | API 最小面、一意一径 | 契约生成与 drift gate |
| `rules/dev-rules-convention.mdc` | 单一事实来源，减少副本 | submodule 工作流与同步 |
| `rules/safe-shell-commands.mdc` | 避免“顺手破坏” | 危险命令人工门禁 |
| `commands/decompose.md` | 先判定风险，再决定是否走高风险路径 | 自动路由与派发 |
| `commands/review.md` | 默认短审查，只报真实风险 | 高风险时自动展开证据链 |
| `commands/calibrate.md` | 不把 review 变成散文系统 | 校准指标自动汇总 |

规则正文是操作真相；`digital-clone-research.md` 是 why，不是每天照抄的 SOP。

## 仓库内容

### 规则

| 文件 | 作用 |
| --- | --- |
| `rules/dev-rules-convention.mdc` | `dev-rules` submodule 约定、同步顺序、接入方式 |
| `rules/agent-contract-enforcement.mdc` | agent-facing 契约同步、安全基线、生成脚本要求 |
| `rules/test-philosophy.mdc` | User Story、测试设计、Story ↔ Test 对齐 |
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
| `verify-rules.sh` | 验证仓库自身完整性（<!-- stat:verify-rules-checks -->8<!-- /stat --> 段） |
| `templates/preflight.sh` | 默认门禁 + 条件门禁模板 |
| `templates/install-hooks.sh` | 安装 pre-commit hook |
| `scripts/check_approved_docs.py` | `docs/approved/*.md` frontmatter 不变量检查 |
| `schemas/review.schema.json` | `/user:review` 输出契约 |
| `schemas/skill.schema.json` | 通用 Skill manifest 契约 |
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

