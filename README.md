# dev-rules

数字分身规则的**单一事实来源**。与 `agent-skills` 并列，独立于任何公司项目维护。

## 设计哲学

所有规则与命令围绕两条主线设计：

- **乔布斯产品理念**（约束「做什么」）：聚焦核心场景、删除非核心功能、API 最小面、端到端体验、设计即功能、工艺级打磨。
- **OPC（One-Person Company）哲学**（约束「怎么做」）：人做判断、AI 做执行；流程最小化；自动化优先；规则与记忆代码化（防遗忘、防漂移）。

每个载体承载的哲学（与父项目 `digital-clone-research.md` §10 表保持同步）：


| 载体                                     | Jobs（聚焦/简洁）         | OPC（自动化/杠杆）            |
| -------------------------------------- | ------------------- | ---------------------- |
| `rules/product-dev.mdc`                | 设计哲学节 + 聚焦过滤步       | 自检纪律 + 4 阶段无臃肿         |
| `rules/test-philosophy.mdc`            | 测试聚焦（核心 AC 优先）      | 测试自动化（CI 可跑）           |
| `rules/agent-contract-enforcement.mdc` | API 最小面 + 一意一径      | 契约自动生成（禁手编）            |
| `rules/dev-rules-convention.mdc`       | 单一事实来源（消除规则副本）      | 规则代码化 + 强制提交顺序（防漂移）    |
| `rules/safe-shell-commands.mdc`        | —                   | 破坏性操作的人工门禁（防 Agent 失控） |
| `commands/decompose.md`                | 聚焦决策表（不做什么）         | 引擎路由 + 派发清单            |
| `commands/review.md`                   | 设计质量维度（简洁/最小面/范围蔓延） | 手动操作残留检查 + 流程冗余检查      |
| `commands/calibrate.md`                | —                   | 校准指标自动汇总               |


详细原则说明见父项目的 `digital-clone-research.md` §〇「两个哲学基石」。

## 架构

每个 IT 产品研发项目通过 **git submodule** 引入 dev-rules，在项目内即可编辑和提交规则：

```
项目根目录/
├── dev-rules/              ← git submodule（唯一编辑入口）
│   ├── rules/*.mdc
│   ├── commands/*.md
│   ├── global/CLAUDE.md    ← Claude Code 全局工作宪法
│   └── sync.sh
├── .cursor/rules/*.mdc     ← sync 产物（real copy, git tracked, 云端 Agent 可读）
└── .gitmodules
```

本地全局消费端通过 symlink 自动同步（编辑 dev-rules 后立即生效）：

```
~/Codes/dev-rules/
├── rules/*.mdc        ──→ ~/.cursor/rules/        symlink（Cursor 交互式会话）
├── commands/*.md      ──→ ~/.claude/commands/     symlink（Claude Code 命令）
└── global/CLAUDE.md   ──→ ~/.claude/CLAUDE.md     symlink（Claude Code 起手读的宪法）
```

**与 agent-skills 的关系**：


| 仓库             | 位置                      | 管什么                                                       | 消费端                                                        |
| -------------- | ----------------------- | --------------------------------------------------------- | ---------------------------------------------------------- |
| `agent-skills` | `~/Codes/agent-skills/` | Cursor Skills（怎么做事的技能）                                    | `~/.cursor/skills/`                                        |
| `dev-rules`    | `~/Codes/dev-rules/`    | Cursor Rules + Claude Commands + Claude 全局宪法（`global/`）（做事的规则） | `~/.cursor/rules/` + `~/.claude/commands/` + `~/.claude/CLAUDE.md` + 项目 submodule |


## 包含的规则


| 文件                               | 说明                                              |
| -------------------------------- | ----------------------------------------------- |
| `dev-rules-convention.mdc`       | submodule 约定本身（含先子模块后父仓库提交顺序）                   |
| `agent-contract-enforcement.mdc` | API 契约同步与安全基线（含 Jobs 最小面、OPC 契约自动生成原则）          |
| `test-philosophy.mdc`            | 测试设计方法论（含 Jobs 测试聚焦、OPC 测试自动化原则）                |
| `safe-shell-commands.mdc`        | 破坏性命令使用规范                                       |
| `product-dev.mdc`                | 产品研发工作流（含设计哲学节、原型设计 + 两个审批门禁、提交前完成自检、云端 CLI 配置） |


## 包含的 Claude Code 命令


| 命令          | 用法                       | 说明                                  |
| ----------- | ------------------------ | ----------------------------------- |
| `decompose` | `/user:decompose [需求描述]` | 将需求拆解为含聚焦决策、原型阶段、引擎路由、派发清单的子任务      |
| `review`    | `/user:review [范围]`      | 双维度代码审查（通用质量 + 符合性 + 设计质量），默认最近 24h |
| `calibrate` | `/user:calibrate [日期范围]` | 汇总审查校准指标（Phase 2 准入判定）              |


## 强约束门禁（机械检查）

软规则与硬检查的完整映射定义在父项目 `digital-clone-research.md §六.½`，接入步骤见 `rules/dev-rules-convention.mdc`。本仓库交付的工件清单：


| 工件                            | 退出码语义                                                       |
| ----------------------------- | ----------------------------------------------------------- |
| `sync.sh --check`             | 0 = 一致；1 = drift                                            |
| `verify-rules.sh`             | 0 = 通过；1 = 至少一项失败（<!-- stat:verify-rules-checks -->8<!-- /stat --> 段：含幽灵路径、含 `global/CLAUDE.md`、含 LaunchAgent 实装） |
| `sync.sh --push`              | push submodule + ~/Codes pull --ff-only + fan-out 到所有已注册项目（编辑者主动同步，原子动作） |
| `sync.sh --pull`              | 远端 → ~/Codes → 所有项目 fan-out（LaunchAgent 与手动救场用）             |
| `schemas/review.schema.json`  | 由 ajv / check-jsonschema 消费；calibrate 入口校验                  |
| `templates/preflight.sh`      | 0 = 全部通过；非 0 = 至少一项失败（项目复制后使用）                              |
| `templates/install-hooks.sh`  | 一键将 preflight.sh 接到 `.git/hooks/pre-commit`                 |
| `templates/launchagent.plist` + `install-launchagent.sh` | 渲染 macOS LaunchAgent + launchctl 注册；每 30 min 跑 `sync.sh --pull`，治"跨机器静默落后" |
| `global/CLAUDE.md`            | Claude Code 全局工作宪法（`~/.claude/CLAUDE.md` 是它的 symlink）       |
| `sync-stats.sh` + `.stats.json` | 把散文档中的数值/事实从「叙述」变为「计算」——`--check` 漂移即 exit 1，根治"变更必伴漂移" |


## 新项目接入

```bash
cd 项目根目录
git submodule add git@github.com:youxuanxue/dev-rules.git dev-rules
dev-rules/sync.sh --local
git add .cursor/rules/ .gitmodules dev-rules
git commit -m "chore: add dev-rules submodule and sync rules"
```

## 首次安装（本地全局，每台 dev 机器一次）

```bash
git clone git@github.com:youxuanxue/dev-rules.git ~/Codes/dev-rules
~/Codes/dev-rules/sync.sh                                # 创建 home symlinks
bash ~/Codes/dev-rules/templates/install-launchagent.sh  # 注册 30min 跨机器同步 agent
```

完成后：

1. 规则 → `~/.cursor/rules/`、命令 → `~/.claude/commands/`、全局宪法 → `~/.claude/CLAUDE.md`（symlink）
2. 现存的真实 `~/.claude/CLAUDE.md` 备份为 `CLAUDE.md.bak.<ts>` 后再替换为 symlink
3. macOS LaunchAgent `local.dev-rules.sync` 每 30 min 跑 `sync.sh --pull`
4. 之后 `verify-rules.sh` 段 8 会强制要求 LaunchAgent 在该机器上必须实装，否则 exit 1

## 日常使用（编辑 → 一步全机生效）

```bash
vim dev-rules/{rules,commands,global}/某文件
dev-rules/sync.sh --local                                          # 复制到本项目 + auto-register
dev-rules/verify-rules.sh                                          # 8 段强约束自检
cd dev-rules && git add -A && git commit -m "update rules"
./sync.sh --push                                                   # push + ~/Codes pull + 所有项目 fan-out
cd ..
git add dev-rules .cursor/rules/ && git commit -m "chore: sync dev-rules" && git push

# 克隆含 submodule 的项目
git clone --recurse-submodules <repo-url>
git submodule update --init --recursive   # 已克隆项目补 submodule
```

`sync.sh --push` 把"提交后人工要跑 N 个机器/项目同步"压成一个原子动作。跨机器同步由 LaunchAgent 兜底，不需要任何手工步骤。

## 为什么 home 用 symlink、项目用 real copy？

- **Home 目录 (`~/.cursor/rules/`)**：编辑 dev-rules 后 symlink 立即生效，零延迟。
- **项目目录 (`.cursor/rules/`)**：Cursor 云端 Agent 在 VM 中克隆 repo，拿不到 symlink 的目标文件，必须是真实文件并 commit 到 git。

