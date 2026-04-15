# dev-rules

数字分身规则的**单一事实来源**。与 `agent-skills` 并列，独立于任何公司项目维护。

## 架构

```
~/Codes/dev-rules/rules/*.mdc     ← 唯一编辑入口
     │
     ├──→ ~/.cursor/rules/         symlink（本地 Cursor 交互式会话）
     ├──→ ~/.claude/commands/      symlink（本地 Claude Code 命令）
     └──→ 各项目/.cursor/rules/    real copy（云端 Agent，committed to git）
```

**与 agent-skills 的关系**：

| 仓库 | 位置 | 管什么 | 消费端 |
|------|------|--------|--------|
| `agent-skills` | `~/Codes/agent-skills/` | Cursor Skills（怎么做事的技能） | `~/.cursor/skills/` |
| `dev-rules` | `~/Codes/dev-rules/` | Cursor Rules + Claude Commands（做事的规则） | `~/.cursor/rules/` + `~/.claude/commands/` |

## 包含的规则

| 文件 | 说明 |
|------|------|
| `agent-contract-enforcement.mdc` | API 契约同步与安全基线 |
| `test-philosophy.mdc` | 测试设计方法论（User Story → 测试 → 对齐门禁） |
| `safe-shell-commands.mdc` | 破坏性命令使用规范 |
| `product-dev.mdc` | 产品研发工作流（含原型设计 + 两个审批门禁） |

## 包含的 Claude Code 命令

| 命令 | 用法 | 说明 |
|------|------|------|
| `decompose` | `/user:decompose [需求描述]` | 将需求拆解为含原型阶段和审批门禁的子任务 |
| `review` | `/user:review [范围]` | 代码审查（默认最近 24h） |

## 安装（首次）

```bash
cd ~/Codes/dev-rules
./setup_dev_rules_autoupdate_macos.sh load
```

这会：
1. Symlink 规则到 `~/.cursor/rules/`，命令到 `~/.claude/commands/`
2. 注册 macOS LaunchAgent 每小时自动 `git pull`（有更新时自动 re-sync）

## 日常使用

```bash
# 编辑规则（唯一入口）
vim ~/Codes/dev-rules/rules/product-dev.mdc

# 同步到本地（通常不需要，symlink 自动生效）
./sync.sh

# 注册一个项目（之后 --all 会包含它）
./sync.sh --register /path/to/project

# 同步到所有已注册项目（real copy for 云端 Agent）
./sync.sh --all

# 查看状态
./sync.sh --status
```

## 为什么 home 用 symlink、项目用 real copy？

- **Home 目录 (`~/.cursor/rules/`)**：编辑 dev-rules 后 symlink 立即生效，零延迟。
- **项目目录 (`.cursor/rules/`)**：Cursor 云端 Agent 在 VM 中克隆 repo，拿不到 symlink 的目标文件，必须是真实文件并 commit 到 git。
