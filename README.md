# dev-rules

数字分身规则的**单一事实来源**。与 `agent-skills` 并列，独立于任何公司项目维护。

## 设计哲学

所有规则与命令围绕两条主线设计：

- **乔布斯产品理念**（约束「做什么」）：聚焦核心场景、删除非核心功能、API 最小面、端到端体验、设计即功能、工艺级打磨。
- **OPC（One-Person Company）哲学**（约束「怎么做」）：人做判断、AI 做执行；流程最小化；自动化优先；规则与记忆代码化（防遗忘、防漂移）。

二者的承载点：

| 哲学 | 主要载体 | 强制时机 |
|------|----------|----------|
| Jobs 聚焦 / 简洁 | `commands/decompose.md`（聚焦决策表）、`commands/review.md`（设计质量维度）、`rules/product-dev.mdc`（设计哲学节）、`rules/agent-contract-enforcement.mdc`（API 最小面） | 需求拆解时、PR review 时 |
| OPC 自动化 / 杠杆 | `rules/test-philosophy.mdc`（测试自动化）、`rules/agent-contract-enforcement.mdc`（契约自动生成）、`commands/calibrate.md`（指标自动汇总）、`commands/review.md`（手动操作残留检查） | 测试编写、契约变更、Phase 准入、PR review |

详细原则说明见父项目的 `digital-clone-research.md` §〇「两个哲学基石」。

## 架构

每个 IT 产品研发项目通过 **git submodule** 引入 dev-rules，在项目内即可编辑和提交规则：

```
项目根目录/
├── dev-rules/              ← git submodule（唯一编辑入口）
│   ├── rules/*.mdc
│   ├── commands/*.md
│   └── sync.sh
├── .cursor/rules/*.mdc     ← sync 产物（real copy, git tracked, 云端 Agent 可读）
└── .gitmodules
```

本地全局消费端通过 symlink 自动同步：

```
~/Codes/dev-rules/rules/*.mdc
     ├──→ ~/.cursor/rules/         symlink（本地 Cursor 交互式会话）
     └──→ ~/.claude/commands/      symlink（本地 Claude Code 命令）
```

**与 agent-skills 的关系**：


| 仓库             | 位置                      | 管什么                                   | 消费端                            |
| -------------- | ----------------------- | ------------------------------------- | ------------------------------ |
| `agent-skills` | `~/Codes/agent-skills/` | Cursor Skills（怎么做事的技能）                | `~/.cursor/skills/`            |
| `dev-rules`    | `~/Codes/dev-rules/`    | Cursor Rules + Claude Commands（做事的规则） | `~/.cursor/rules/` + submodule |


## 包含的规则


| 文件                               | 说明                                                |
| -------------------------------- | ------------------------------------------------- |
| `dev-rules-convention.mdc`       | submodule 约定本身（含先子模块后父仓库提交顺序）                    |
| `agent-contract-enforcement.mdc` | API 契约同步与安全基线（含 Jobs 最小面、OPC 契约自动生成原则）           |
| `test-philosophy.mdc`            | 测试设计方法论（含 Jobs 测试聚焦、OPC 测试自动化原则）                 |
| `safe-shell-commands.mdc`        | 破坏性命令使用规范                                         |
| `product-dev.mdc`                | 产品研发工作流（含设计哲学节、原型设计 + 两个审批门禁、提交前完成自检、云端 CLI 配置） |


## 包含的 Claude Code 命令


| 命令          | 用法                          | 说明                                   |
| ----------- | --------------------------- | ------------------------------------ |
| `decompose` | `/user:decompose [需求描述]`    | 将需求拆解为含聚焦决策、原型阶段、引擎路由、派发清单的子任务       |
| `review`    | `/user:review [范围]`         | 双维度代码审查（通用质量 + 符合性 + 设计质量），默认最近 24h |
| `calibrate` | `/user:calibrate [日期范围]`    | 汇总审查校准指标（Phase 2 准入判定）               |


## 新项目接入

```bash
cd 项目根目录
git submodule add git@github.com:youxuanxue/dev-rules.git dev-rules
dev-rules/sync.sh --local
git add .cursor/rules/ .gitmodules dev-rules
git commit -m "chore: add dev-rules submodule and sync rules"
```

## 首次安装（本地全局）

```bash
cd ~/Codes/dev-rules
./setup_dev_rules_autoupdate_macos.sh load
```

这会：

1. Symlink 规则到 `~/.cursor/rules/`，命令到 `~/.claude/commands/`
2. 注册 macOS LaunchAgent 每小时自动 `git pull`（有更新时自动 re-sync）

## 日常使用

```bash
# 在任意项目内编辑规则
vim dev-rules/rules/product-dev.mdc

# 同步到当前项目的 .cursor/rules/
dev-rules/sync.sh --local

# 提交 submodule 变更
cd dev-rules && git add -A && git commit -m "update rules" && git push && cd ..

# 提交父项目的变更
git add dev-rules .cursor/rules/ && git commit -m "chore: sync dev-rules"

# 克隆含 submodule 的项目（一次性）
git clone --recurse-submodules <repo-url>

# 已克隆项目初始化 submodule
git submodule update --init --recursive
```

## 为什么 home 用 symlink、项目用 real copy？

- **Home 目录 (`~/.cursor/rules/`)**：编辑 dev-rules 后 symlink 立即生效，零延迟。
- **项目目录 (`.cursor/rules/`)**：Cursor 云端 Agent 在 VM 中克隆 repo，拿不到 symlink 的目标文件，必须是真实文件并 commit 到 git。

