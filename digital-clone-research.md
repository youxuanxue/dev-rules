# 长时运行的个人 IT 产品研发数字分身

> 调研日期：2026-04-15
> 现状：已安装 Cursor + Claude Code，全部规则/命令/全局宪法由 `dev-rules/` 单一仓库管理（详见 §4）
> 目标：构建一个 7×24 不间断工作的数字分身，独立承接 IT 产品研发长周期任务

> 说明：本文档是研究、论证与设计背景，不是日常操作手册。当前操作真相以 `rules/product-dev.mdc`、`commands/decompose.md`、`commands/review.md`、`global/CLAUDE.md` 和 `README.md` 为准。若本文示例与规则正文冲突，以规则正文为准。

---

## 〇、两个哲学基石

本系统的一切设计决策建立在两个互为支撑的哲学之上：

### 乔布斯产品设计哲学 — 决定「做什么」和「怎么做」

> "People think focus means saying yes to the thing you've got to focus on. But that's not what it means at all. It means saying no to the hundred other good features that there are." — Steve Jobs

**核心原则**：

1. **聚焦**：对一千件事说不，只做一件做到极致的事。需求不是越多越好——每多一个功能，复杂度的增长是指数级的。
2. **简洁**：简洁是复杂的最终形态。代码、API、UI 都应追求「看起来显然没有多余的东西」。
3. **端到端体验**：不是把各模块做好再拼凑。用户感知的是完整旅程，从第一次接触到最终完成任务。
4. **设计即工作方式**：设计不是表面的美观，而是产品如何工作。一个好 API 应该让调用者觉得「就该是这样」。
5. **精品意识**：宁可少做，做出来的每一个功能都必须达到「可以自豪地展示」的水准。

**对数字分身系统的约束**：

- 高风险变更在原型阶段必须先回答「这个功能应不应该存在」，再回答「怎么实现」
- 需求拆解时，砍掉的功能和保留的功能同样重要——砍掉什么必须在报告中说明
- 每个 PR 只解决一个问题（已在 `product-dev.mdc` 中执行）；默认单 PR，只有高风险或真实决策边界才拆分

### OPC（One-Person Company）哲学 — 决定「谁来做」和「做多少」

> 一个人 + AI 数字分身 = 一个精干团队的产出。不是缩小版的大公司，而是完全不同的运转方式。

**核心原则**：

1. **杠杆最大化**：你的时间是唯一不可扩展的资源。每一件事都应问「这必须由人做吗」——如果不是，交给 Agent。
2. **流程极简**：每个流程步骤必须挣得自己的位置。不能证明价值的步骤就是损耗，直接去掉。
3. **自动化优先**：能自动化的绝不手工做。手工操作只在需要人类判断力的关键节点存在（审批门禁）。
4. **深度 > 广度**：不做十个平庸的产品，做一个深入骨髓的产品。OPC 的竞争力不是人多，而是在一个点上做得比任何大团队都深。
5. **反脆弱**：规则、记忆、技能全部代码化、版本化（`dev-rules/`），不依赖任何一个人的记忆或任何一台机器。

**对数字分身系统的约束**：

- 你（人类）每天只投入 1-2 小时（见 §五 工作节奏），其余时间 Agent 自主运转
- 人工介入只出现在高风险审批门禁和架构决策——其他一切可自动化
- 不招人，不组团队。产能瓶颈靠增加 Agent 并行数解决（最多 8 个），不靠增加人头
- 成本预算严格控制在 $250-350/月（≈ ¥2,500，不到初级工程师月薪的 1/10）

### 两个哲学的交汇

```
乔布斯哲学                              OPC 哲学
  │                                       │
  │ 做什么：聚焦一个产品                     │ 谁来做：1 人 + N 个 Agent
  │ 怎么做：简洁、端到端、精品              │ 做多少：杠杆最大化、流程极简
  │                                       │
  └──────────┬────────────────────────────┘
             │
    数字分身系统的设计原则：
    · 产品方向你来定（聚焦），执行 Agent 来做（杠杆）
    · 做少做精（乔布斯），自动化一切非判断性工作（OPC）
    · 高风险审批门禁 = 人类判断力的最小必要介入
    · Agent 的 Rules/Skills/Memory 就是 OPC 的「制度资产」
```

### 哲学落到实处的强约束机制

哲学不是用来挂在墙上的——本系统的关键纪律是**每条软规则必须配套一个机械检查脚本**，违反时构建/提交/CI 真的会失败：

- **「自动化优先」的元约束**：当某个"靠自觉"的问题反复出现，必须新增一段检查到 `scripts/preflight.sh` 或 `dev-rules/verify-rules.sh`
- **「变更必伴漂移」的元解法**：散文中的数值/事实改用 `<!-- stat:NAME -->` 占位符 + `dev-rules/sync-stats.sh --check` 机械拦截（详见 §六.¾）
- **「承诺必须等于事实」的元约束**：凡是文档/规则里声称"自动跑"的进程（如跨机器同步 LaunchAgent），必须有一段检查验证它真的在运行，否则就是软承诺；`verify-rules.sh` 的 **LaunchAgent 实装段**是这条原则的样板
- **完整的软→硬映射表见 §六.½**，覆盖 dev-rules 同步、契约不漂移、分支命名、approved 改动门禁、文档数值漂移、LaunchAgent 实装等
- **核心脚本入口**：`dev-rules/sync.sh --check`、`dev-rules/sync.sh --push`、`dev-rules/verify-rules.sh`、`dev-rules/sync-stats.sh --check`、`scripts/preflight.sh`、`dev-rules/schemas/review.schema.json`

这一条本身是对 OPC「自动化优先」原则的硬约束实现——不允许这个原则停留在描述层面。

---

## 一、什么是"长时运行"

**长时运行 ≠ 一次对话回答一个问题。** 它意味着：

- Agent 在你睡觉/开会/做别的事时，**持续数小时甚至数天**自主工作
- Agent 维护**跨会话持久记忆**，不会每次重新"认识"你的项目
- Agent 产出的是**可合并的代码 PR**，不是聊天建议
- Agent 在遇到阻塞时**等你回来决策**，而不是瞎猜或放弃

### 这已经不是理论


| 实际案例                         | 运行时长  | 产出                    |
| ---------------------------- | ----- | --------------------- |
| Cursor 内部：从零构建 Web 浏览器       | ~7 天  | 100 万+ 行代码，1000+ 文件   |
| Cursor 内部：Solid → React 迁移   | 3+ 周  | +266K/-193K 行编辑，通过 CI |
| 外部用户：构建全新聊天平台                | 36 小时 | 集成开源工具的完整平台           |
| 外部用户：Web App → Mobile App 迁移 | 30 小时 | 完整移动端应用               |
| 外部用户：认证与 RBAC 系统重构           | 25 小时 | 生产级重构                 |
| 外部用户：原计划一个季度的项目              | 52 小时 | 151K 行代码的大型 PR        |


---

## 二、你已经有的基础设施

你的环境已经具备构建数字分身的核心骨架：

### 2.1 Cursor 端

```
~/.cursor/
├── rules/                              ← symlink → ~/Codes/dev-rules/rules/
│   ├── dev-rules-convention.mdc        ← submodule 约定本身（alwaysApply）
│   ├── agent-contract-enforcement.mdc  ← API 契约同步，Agent 变更后自动校验
│   ├── test-philosophy.mdc             ← 分层测试方法论（默认聚焦测试；高风险才启用完整 Story 闭环）
│   ├── safe-shell-commands.mdc         ← 危险命令拦截
│   └── product-dev.mdc                 ← 产品研发工作流（默认单 PR；高风险时启用原型与审批门禁）
├── skills/                             ← 14 个领域技能（PPT/视频/策展/发布...）
└── skills-cursor/                      ← 8 个 Cursor 原生技能（babysit/hook/rule...）
```

**关键优势**：你的 `test-philosophy.mdc` 现在是一份分层测试规范——默认先补聚焦测试，高风险再启用完整 Story 与对齐门禁。这让 Agent 既不会漏掉关键验证，也不会把低风险工作默认推成重文档流程。

### 2.2 Claude Code 端

```
~/.claude/
├── CLAUDE.md                           ← 全局数字分身工作模式（不再 @include 外部规则）
└── commands/                           ← symlink → ~/Codes/dev-rules/commands/
    ├── decompose.md                    ← 任务拆解命令（先做风险分级，再决定是否启用原型审批）
    ├── review.md                       ← 代码审查命令（双维度 + human_verdict 标注）
    └── calibrate.md                    ← 审查校准汇总（Phase 2 准入判定）
```

全局 `CLAUDE.md` 定义数字分身工作模式（研发流程、长时运行原则、Headless 模式约束），不再通过 `@include` 引用外部规则路径——因为云端 Agent 无法访问家目录。

### 2.3 规则独立仓库

```
~/Codes/
├── agent-skills/                       ← Cursor Skills（怎么做事的技能） → ~/.cursor/skills/
└── dev-rules/                          ← Cursor Rules + Claude Commands（做事的规则）
    ├── rules/*.mdc                     ← 唯一编辑入口（<!-- stat:rules-count -->5<!-- /stat --> 条规则）
    ├── commands/*.md                   ← Claude Code 自定义命令（<!-- stat:commands-count -->3<!-- /stat --> 条）
    ├── global/CLAUDE.md                ← Claude Code 全局工作宪法
    ├── schemas/                        ← review.schema.json + stats.schema.json
    ├── templates/                      ← preflight.sh + install-hooks.sh + launchagent.plist + install-launchagent.sh
    ├── scripts/                        ← 跨项目共享检查脚本（如 check_approved_docs.py）
    ├── .stats.json                     ← 数值事实注册表（治"变更必伴漂移"，见 §六.¾）
    ├── .registered-projects            ← 跨机器项目注册表（git-tracked，TSV: name\tgit_url）
    ├── .local-projects                 ← 本机落地映射（gitignored，TSV: git_url\tabs_path；auto-register 维护）
    ├── sync.sh                         ← 分发脚本（--local / --push / --pull / --check / --status）
    ├── sync-stats.sh                   ← stat 块漂移机械检查
    └── verify-rules.sh                 ← dev-rules 仓库自身完整性自检（<!-- stat:verify-rules-checks -->8<!-- /stat --> 段）

# 系统侧产物（由 dev-rules/templates/install-launchagent.sh 注册，独立于 dev-rules 目录）
~/Library/LaunchAgents/local.dev-rules.sync.plist
   ↑ 跨机器同步 LaunchAgent，每 30 min 跑 `sync.sh --pull`
   ↑ verify-rules.sh 的 LaunchAgent 实装段校验它真的装了（macOS dev 机器强制）
```

规则作为**个人资产**独立维护，不随公司项目删除。GitHub 仓库：`git@github.com:youxuanxue/dev-rules.git`。

### 2.4 已补齐的能力


| 之前缺失       | 现在补齐方案                                               | 状态  |
| ---------- | ---------------------------------------------------- | --- |
| 项目上下文（做什么） | 每个项目的 `CLAUDE.md`（自包含）                               | ✅   |
| 任务拆解模板     | `/user:decompose` 命令（先做风险分级，再输出对应路径）              | ✅   |
| 长时运行编排配置   | Cursor Long-running Agent + Claude Code Headless 双引擎 | ✅   |
| 产品研发专用工作流  | `product-dev.mdc` 规则（默认单 PR；高风险才升级门禁）             | ✅   |
| 规则云端可达     | submodule + sync --local（real copy, git tracked）     | ✅   |


---

## 三、长时运行的两个引擎

你已安装的两个工具恰好覆盖了长时运行的两种模式：

### 3.1 Cursor Long-running Agent — "云端永动机"

**工作原理**（Cursor 官方架构）：

```
你下达任务（自然语言）
     ↓
┌─── Planner Agent ──────────────────────────────┐
│  探索代码库 → 制定计划 → 等你批准                    │
│  批准后 → 拆分为子任务                              │
│  可递归生成 Sub-Planner 处理特定领域                  │
└───────┬─────────┬─────────┬────────────────────┘
        │         │         │
   ┌────▼───┐ ┌───▼────┐ ┌──▼─────┐
   │Worker 1│ │Worker 2│ │Worker N│   ← 并发执行，乐观并发控制
   │专注单一 │ │专注单一 │ │专注单一  │   ← 不关心全局，只管完成分配的任务
   │任务    │ │任务    │ │任务     │   ← 完成后提交变更
   └────┬───┘ └───┬────┘ └──┬─────┘
        │         │         │
        └─────────▼─────────┘
              Judge Agent
         审查产出 → 决定是否继续
         ↓ 下一轮迭代
```

**关键特性**：

- **你可以关掉电脑**：Agent 在 Cursor 云端 VM 中运行，不依赖你的本地机器
- **手机可监控**：通过 Web 界面随时查看进度、回答 Agent 问题
- **产出 PR**：工作结果是 merge-ready 的 Pull Request
- **最多 8 个并行**：同时跑 8 个独立任务
- **需要 Ultra 计划**：$200/月

**你晚上下班时的操作**：

1. 在 Cursor 中描述任务（或用 `&` 前缀直接发送到云端）
2. 审批 Agent 提出的执行计划
3. 关机走人
4. 第二天早上回来 Review PR

### 3.2 Claude Code Headless Mode — "无人值守守护进程"

**工作原理**：

```bash
# 基础用法：一次性无人值守执行
claude -p "重构 auth 模块，添加 JWT 支持" \
  --allowedTools "Read" "Write" "Bash(npm test)" \
  --output-format json

# 定时任务：每天凌晨 2 点执行结构化审查（实际 prompt 见 §七 Phase 1）
0 2 * * * cd /path/to/project && claude -p "审查最近24小时commit，输出到 docs/review-*.json" \
  --allowedTools "Read" "Write" "Bash(git *)" --max-budget-usd 5

# 循环监控：持续监控 CI 状态
claude  # 进入交互模式
/loop 5m "检查 CI 状态，如果有失败的测试，分析原因并提交修复 PR"
```

**关键特性**：

- `**-p` 模式**：非交互式执行，适合脚本/CI/cron
- `**/loop` 命令**：会话内定时轮询（最长 3 天，50 任务上限）
- **权限控制**：`--allowedTools` 精确限制 Agent 可以做什么
- **成本控制**：`--max-budget-usd 10` 设置单次预算上限
- **JSON 输出**：`--output-format json` 便于下游自动化消费

### 3.3 两个引擎的分工


| 维度          | Cursor Long-running Agent | Claude Code Headless         |
| ----------- | ------------------------- | ---------------------------- |
| **最擅长**     | 大型功能开发、跨文件重构              | 定时巡检、批量处理、CI 集成              |
| **运行时长**    | 小时 → 天                    | 分钟 → 小时（可 cron 循环）           |
| **运行环境**    | 云端 VM（不占本地资源）             | 本地终端 / 远程服务器 / 云端 VM（见 §3.4） |
| **并行能力**    | 最多 8 个                    | 无限（取决于 API 配额）               |
| **人工介入**    | 计划审批 + PR Review          | 仅 PR Review                  |
| **适合的任务规模** | 中→大（数天的功能开发）              | 小→中（数小时的定向任务）                |
| **费用**      | $200/月（Ultra 包含）          | 按 token 计费（~$0.01-0.1/任务）    |


### 3.4 云端 Agent 调用 Claude Code CLI

Cursor 云端 VM 是临时环境，默认没有 Claude Code CLI。当云端 Agent 需要执行 `claude -p`（如审查、拆解、校准）时，需要先完成安装和配置。

**一次性配置（人工操作，所有项目共享）**：

1. 打开 [Cursor Dashboard → Cloud Agents](https://cursor.com/dashboard/cloud-agents)
2. Secrets → Add Secret
3. Name: `ANTHROPIC_API_KEY`，Value: `sk-ant-api03-...`
4. 保存后对所有新启动的 Cloud Agent 生效

> Cursor Cloud Secrets 在 shell 的 `printenv` 中可能不可见，但对 `claude` 进程可用。

**每次 Agent 启动时（项目自管脚本，不由 dev-rules 提供）**：

```bash
bash scripts/setup-claude-code.sh
```

脚本职责（每个需要云端调 Claude Code 的项目自行实现）：检查 Node.js → 安装 `claude` CLI → 验证 API Key → 检查项目上下文。参考实现见 `zw-brain/scripts/setup-claude-code.sh`。安装完成后即可执行：

```bash
claude -p "审查最近 24 小时的 commit..." --max-budget-usd 5
```

`product-dev.mdc` 规则只在云端环境提示"先跑 setup 脚本再 `claude -p`"；具体的安装与鉴权流程由各项目按上面的职责自行落地。

---

## 四、数字分身系统架构

### 4.1 规则分发：submodule + 单一事实来源

**核心问题**：Cursor Long-running Agent 运行在云端 VM，无法访问你的 `~/.cursor/rules/`。如果规则只存在家目录，云端 Agent 就是"裸奔"。

**解决方案**：独立仓库 `~/Codes/dev-rules/`（GitHub: `youxuanxue/dev-rules`）作为唯一编辑入口。每个项目通过 **git submodule** 引入，在项目内即可编辑、提交、推送规则变更。

```
项目根目录/
├── dev-rules/                 ← git submodule → git@github.com:youxuanxue/dev-rules.git
│   ├── rules/*.mdc            ← 唯一编辑入口（SINGLE SOURCE OF TRUTH）
│   ├── commands/*.md           ← Claude Code 自定义命令
│   └── sync.sh                 ← 分发脚本
├── .cursor/rules/*.mdc        ← sync 产物（real copy, git tracked, 云端 Agent 可读）
└── .gitmodules
```

本地全局消费端通过 symlink 自动同步：

```
~/Codes/dev-rules/                      ← 本机规范副本（local canonical mirror）
     ├──→ ~/.cursor/rules/              symlink（本地 Cursor 交互式会话，改即生效）
     ├──→ ~/.claude/commands/           symlink（本地 Claude Code 命令，改即生效）
     └──→ ~/.claude/CLAUDE.md           symlink（全局工作宪法）
```

**变更传播：双保险机制（治"push 后全机生效"承诺）**

每个 dev-rules 改动需要扇出到 N 个消费端（home 3 处 + 各项目 .cursor/rules/）。两个失效模式各有兜底：


| 失效模式        | 主路径（编辑者主动）                                                                                                  | 兜底路径（被动轮询）                                                                                                    |
| ----------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| 本机修改 + push | `dev-rules/sync.sh --push`：原子完成 `git push` → `~/Codes/dev-rules` `git pull --ff-only` → fan-out 到本机已落地的注册项目 | LaunchAgent（30 min 后下一轮 `--pull` 也会拉到，但通常用不上）                                                                 |
| 跨机器有人 push  | （无主路径——别的机器不知道该通知谁）                                                                                         | macOS LaunchAgent `local.dev-rules.sync` 每 30 min 跑 `sync.sh --pull`；`verify-rules.sh` 的 LaunchAgent 实装段未装即报错 |


**注册表拆分为两层（治"项目集合 vs 本机落地"概念混淆）**：`.registered-projects` 用 `name<TAB>git_remote_url` 记录跨机器项目集合（git-tracked 进 dev-rules 仓库），`.local-projects` 用 `git_remote_url<TAB>absolute_local_path` 记录本机落地（gitignored，每台机器自有一份）。fan-out 工作集 = (注册项目 ∩ 本机已落地)；未落地项目静默跳过。换机器时各项目跑一次 `sync.sh --local` 即按 git remote 自动重建本机映射。

**为什么不在项目里把 `.cursor/rules/*.mdc` 改成 symlink → `~/Codes/dev-rules/`？** 看似省事，实则把"项目通过 submodule SHA 锁定的规则版本"与"本机 mirror 的浮动 HEAD"混淆为同一上游。一旦 mirror 落后或超前，项目跑的逻辑就与它声称遵循的版本不一致。submodule + real-copy 的代价是多一个 `sync.sh --local`，换来的是每个项目的规则版本与提交历史强绑定，可审计、可回滚、云端可读。

**为什么用 submodule？**


| 对比          | 外部仓库 + sync --all        | submodule + sync --local                    |
| ----------- | ------------------------ | ------------------------------------------- |
| 在项目内编辑规则    | ❌ 需切换到 ~/Codes/dev-rules | ✅ 直接编辑 dev-rules/                           |
| 提交规则变更      | ❌ 需单独操作外部仓库              | ✅ 在项目内 cd dev-rules && git push             |
| 云端 Agent 可达 | ✅ 需预先 sync + commit      | ✅ clone --recurse-submodules + sync --local |
| 新项目接入       | 手动 sync.sh --register    | git submodule add + sync --local            |
| 离职风险        | ✅ 独立于公司项目                | ✅ 独立于公司项目（submodule 只是引用）                   |


**为什么项目内用 real copy？** 云端 VM 克隆 repo 时拿不到 symlink 的目标文件，`.cursor/rules/` 必须是真实文件并 commit 到 git。

**修改规则的标准流程（推荐 wrapper 路径）**：

```bash
vim dev-rules/{rules,commands,global}/某文件
dev-rules/sync.sh --local                                          # 复制到本项目 + auto-register
dev-rules/verify-rules.sh                                          # <!-- stat:verify-rules-checks -->8<!-- /stat --> 段完整性检查（含 LaunchAgent 实装）
cd dev-rules && git add -A && git commit -m "update rules"
./sync.sh --push                                                   # push + ~/Codes pull + 所有项目 fan-out
cd ..
git add dev-rules .cursor/rules/ && git commit -m "chore: sync dev-rules" && git push
```

`--push` 是关键节点：上一步的 commit 会**原子地**走到 `~/Codes/dev-rules` 与每个本机已落地的注册项目的 `.cursor/rules/`。本机所有 Cursor 与 Claude Code 会话立即用新规则；被 fan-out 影响的其他项目会显示 `.cursor/rules/` 改动待提交。在别的机器上，被动路径会在下一个 LaunchAgent 周期把变更拉到该机器的 `~/Codes/dev-rules` 并 fan-out 到该机器自己的落地项目。

**首次本机准备（每台 dev 机器一次）**：

```bash
git clone git@github.com:youxuanxue/dev-rules.git ~/Codes/dev-rules
~/Codes/dev-rules/sync.sh                                          # 创建 home symlinks
bash ~/Codes/dev-rules/templates/install-launchagent.sh            # 注册 30min LaunchAgent
```

`verify-rules.sh` 的 LaunchAgent 实装段会强制要求 LaunchAgent 在 macOS dev 机器（即 `~/Codes/dev-rules` 存在的机器）上必须实装。CI 与纯消费机器自动 skip。

**新项目接入（最小 3 步 + 按需扩展）**：

```bash
# 必做
git submodule add git@github.com:youxuanxue/dev-rules.git dev-rules
dev-rules/sync.sh --local                                          # 复制规则 + auto-register
git add .cursor/rules/ .gitmodules dev-rules \
  && git commit -m "chore: add dev-rules submodule and sync rules"

# 启用强约束门禁（推荐，但不强制）
bash dev-rules/templates/install-hooks.sh                          # git pre-commit → preflight

# 仅当项目有 dev-rules 模板未覆盖的特异检查时，才需要 wrapper：
cp dev-rules/templates/preflight.sh scripts/preflight.sh           # 然后在文件末尾追加项目段
git add scripts/preflight.sh && git commit -m "chore: add project-level preflight wrapper"
```

`install-hooks.sh` 安装的 hook 会按 `scripts/preflight.sh` → `dev-rules/templates/preflight.sh` 顺序 fallback，所以无 wrapper 时模板直接生效，不需要复制。按需创建项目级机械检查脚本（`scripts/export_agent_contract.py`、`.testing/user-stories/verify_quality.py`）；这些脚本一旦存在，preflight 对应段自动启用。

**这个约定本身也是一条规则**：`dev-rules-convention.mdc`（alwaysApply: true），通过 submodule 分发到所有项目，确保 Agent 知道如何正确处理规则。

### 4.2 研发阶段流程（高风险路径示意）

日常默认路径已经收敛为：

```text
单 PR → 实现/测试 → preflight → review → 人工确认
```

下图只说明**高风险变更**为什么需要原型与审批门禁，而不是默认要求所有任务都走这条路径。

```
需求分析          原型设计          功能实现          测试验证
┌──────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
│任务拆解│ ──→ │技术方案文档│ ──→ │生产级代码  │ ──→ │测试套件   │
│依赖分析│      │最小可运行  │      │边界处理   │      │验收确认   │
│优先排序│      │原型实现   │      │完整测试   │      │Story对齐  │
└──────┘      └────┬─────┘      └────┬─────┘      └────┬─────┘
                   │                  │                  │
              ▶ 原型审批门禁 ◀     ▶ （Agent 继续） ◀    ▶ 合并审批门禁 ◀
              Agent 暂停            无需暂停             Agent 暂停
              等待你审批：           你的规则已经           等待你确认：
              · 数据模型             通过 Rules 和          · 代码质量
              · API 设计             测试门禁自动           · 测试覆盖
              · 技术选型             保障质量               · 可否合并
              · 原型演示
```

**关键设计**：高风险路径上的两个审批门禁确保你对方向有完全控制权，同时 Agent 在门禁之间可以完全自主工作。低风险与常规风险不应被这条路径拖重。

### 4.3 面向 IT 产品研发的完整架构

```
═══════════════════════════════════════════════════════════════
  老板 / PM：在 GitHub Issues 或任务系统中创建需求
═══════════════════════════════════════════════════════════════
                          ↓ 触发
┌─────────────────────────────────────────────────────────────┐
│                    你（人类 Supervisor）                      │
│                                                             │
│  日常：每天 1-2 小时                                          │
│  ① 早上：Review 昨晚 Agent 产出的 PR（含原型 PR）              │
│  ② 中午：审批原型设计 / 回答 Agent 的阻塞问题                   │
│  ③ 下班前：下达新任务 + 审批 Agent 计划                         │
│  ④ 关键节点：架构决策、产品方向、优先级排序                       │
└─────────────────┬───────────────────────────────────────────┘
                  │
    ┌─────────────▼─────────────────────────┐
    │        持 久 化 记 忆 层                │
    │                                        │
    │  dev-rules/ (git submodule)            │
    │  ├─ rules/*.mdc  唯一编辑入口            │
    │  └─ sync.sh --local                    │
    │       ↓                                │
    │  项目/.cursor/rules/ (real copy,云端可读)│
    │  ~/.cursor/rules/    (symlink,本地可读) │
    │                                        │
    │  项目/CLAUDE.md  (自包含，不依赖 @~)     │
    │  ~/.claude/CLAUDE.md → dev-rules/global/ │
    │  (symlink，自动同步，非手维护文件)        │
    └─────────────┬─────────────────────────┘
                  │
    ══════════════▼══════════════════════════
    ║         任 务 编 排 层                  ║
    ║                                       ║
    ║  ┌──────────────┐ ┌────────────┐ ┌───────────┐ ║
    ║  │ Cursor L-R   │ │ Cursor     │ │ Claude    │ ║
    ║  │ Agent        │ │ Background │ │ Code      │ ║
    ║  │              │ │            │ │ Headless  │ ║
    ║  │ 原型实现     │ │ Bug 修复   │ │ 定时巡检  │ ║
    ║  │ 功能开发     │ │ Migration  │ │ 代码审查  │ ║
    ║  │ 系统重构     │ │ 小型功能   │ │ 测试补充  │ ║
    ║  │ 新模块实现   │ │            │ │ 文档更新  │ ║
    ║  │              │ │ 云端 VM    │ │ 依赖检查  │ ║
    ║  │ 云端 VM ×8   │ │ 快速定向   │ │ 本地/云端 │ ║
    ║  └──────┬───────┘ └─────┬──────┘ └────┬──────┘ ║
    ║         │               │             │         ║
    ══════════▼═════════▼═════════▼═══════════
              │         │         │
              └─────────┼─────────┘
                        │
                       ↓
         ┌──────────────────────────┐
         │   Git + PR               │
         │   prototype: → 原型审批   │
         │   feat:      → 合并审批   │
         │   CI/CD 门禁              │
         └──────────┬───────────────┘
                    ↓
         ┌──────────────────────────┐
         │  你 Review + 审批         │
         │  合并上线                  │
         └──────────────────────────┘
```

### 4.4 持久化记忆层详解

#### 项目目录布局

dev-rules 仓库结构见 §2.3。每个项目的完整目录布局如下：

```
项目根目录/
├── dev-rules/                  # ← git submodule（在项目内编辑 + 提交规则）
├── .gitmodules                 # submodule 配置
├── CLAUDE.md                   # 自包含（不依赖 @~ 引用），Claude Code 读取
├── .cursor/rules/              # Cursor Agent 读取（本地 + 云端）
│   ├── dev-rules-convention.mdc         ← sync 产物，禁止直接编辑
│   ├── agent-contract-enforcement.mdc   ← sync 产物
│   ├── test-philosophy.mdc              ← sync 产物
│   ├── safe-shell-commands.mdc          ← sync 产物
│   └── product-dev.mdc                  ← sync 产物（含原型审批流程）
├── docs/
│   ├── approved/               # 人类审批通过的产物（merge = 审批，审查基线）
│   │   ├── design-{feature}.md #   设计文档（实体化命名，不带版本号）
│   │   ├── api-contract.yaml   #   API 契约（新审批覆盖旧版本，git history 保留演进）
│   │   ├── data-model.md       #   数据模型
│   │   └── tech-decisions.md   #   技术选型决策
│   ├── review-*.json           #   结构化审查报告（review 命令产出，机器消费）
│   ├── review-*.md             #   [可选] 审查摘要（高风险或明确要求人类阅读时产出）
│   ├── calibration-*.json      #   校准指标原始数据（calibrate 命令产出）
│   ├── calibration-*.md        #   校准报告（含 Phase 准入判定）
│   └── task-breakdown-*.md     #   任务拆解文档（decompose 命令产出）
├── scripts/                    # 项目自管脚本（dev-rules 不直接提供，按需创建）
│   ├── setup-claude-code.sh    #   云端 Agent 安装 Claude Code CLI（见 §3.4，每个项目自管）
│   ├── preflight.sh            #   [可选] 项目级 wrapper；不存在时 hook 直接调 dev-rules/templates/preflight.sh
│   └── export_agent_contract.py #  [按需] API/CLI/MCP 契约生成与 --check（agent-contract-enforcement.mdc）
└── .testing/user-stories/      # User Story + AC（test-philosophy 规范要求）
    └── verify_quality.py       # [按需] Story ↔ Test 对齐校验（preflight 段 5 调用，不存在则跳过）
```

**为什么自包含**：云端 Agent 和 CI 无法访问 `~/.cursor/rules/` 或 `@~` 路径（见 §4.1）。所有引用必须是项目内相对路径。

### 4.5 任务拆解（风险分级后再决定是否启用原型审批）

使用 Claude Code 自定义命令时，先做风险分级，再决定输出默认单 PR 路径还是高风险路径：

```bash
claude
/user:decompose 老板说要给政务系统加一个审批流程引擎，支持多级审批、会签、加签，需要可视化流程设计器
```

高风险需求的输出会包含按阶段组织的子任务和审批门禁；低风险与常规风险则应收敛成默认单 PR 任务清单：

```
一、原型设计阶段
  T-001: 数据模型设计文档        [PARALLEL]
  T-002: API 接口设计文档        [PARALLEL]
  T-003: 最小可运行原型实现       [SEQUENTIAL: T-001, T-002]
  ▶ GATE-1: 原型审批            [GATE: 需人工审批]

二、功能实现阶段
  T-004: 审批流程引擎核心         [PARALLEL, 依赖 GATE-1]
  T-005: 会签/加签逻辑           [PARALLEL, 依赖 GATE-1]
  T-006: 流程设计器前端           [PARALLEL, 依赖 GATE-1]

三、测试验证阶段
  T-007: 测试套件                [SEQUENTIAL: T-004-T-006]
  ▶ GATE-2: 合并审批            [GATE: 需人工审批]
```

---

## 五、一天的工作节奏（目标状态）

本节示例故意使用“高风险审批流程引擎”场景来展示原型审批、符合性审查与校准如何协作；它不是“所有任务默认都会产生多个 PR”的操作建议。默认任务应优先收敛为单 PR。

### 07:30 — 早起 Review（30 分钟）

```
你打开电脑，看到：
├── 1 个原型 PR 等待审批
│   └── PR #141: prototype(approval): 审批流程引擎原型
│       ├── docs/approved/design-approval-engine.md（数据模型 + API 设计，approved_by: pending）
│       └── 可运行的最小原型（核心路径可演示）
├── 2 个功能 PR（昨天已审批原型后 Agent 自动进入的功能实现阶段）
│   ├── PR #142: feat(approval): 审批流程引擎核心模型  ✅ CI 通过
│   │   └── 含 3 个 autofix: commits（Claude Code 凌晨自动修复的 lint 问题）
│   └── PR #143: feat(approval): 多级审批状态机实现     ✅ CI 通过
├── 2 个 GitHub Issues（Claude Code 审查发现的 🟡🔴 问题）
│   ├── Issue #201 [auto-review] 🟡 审批 API 缺少分页参数校验
│   └── Issue #202 [auto-review] 🔴 审批状态机缺少并发控制
├── 1 份结构化审查报告
│   └── docs/review-20260415.json（JSON，含 findings + summary + circuit_breaker）
└── 1 个 Agent 问题等待你决策
    └── "加签功能是否允许跨部门？需要确认业务规则"
```

你的操作：

1. 审批原型 PR #141 → 校准 `docs/approved/` 中的设计文档（直接在 PR 中编辑）+ 原型演示 → merge = 审批
2. 快速浏览 CI 通过的功能 PR → 合并（autofix commits 已包含在内）
3. 处理 Issues：#201 标记 accept → 下个迭代修复；#202 标记 critical → 今天让 Agent 处理
4. 回答 Agent 的问题 → "加签允许跨部门"
5. **校准标注**：打开 `docs/review-*.json`，为每个 finding 的 `human_verdict` 填写 accurate/severity_correct/autofix_safe（约 5-10 分钟）

### 09:00 — 上班，处理需要人工主导的事

- 参加需求评审会
- 做架构决策
- 和其他团队协调接口
- Agent 在后台继续干活

### 12:00 — 午间快速检查（10 分钟）

打开手机，通过 Cursor Web 界面：

- 确认上午启动的 Agent 在正常运行
- 看看是否有新的阻塞问题
- 必要时回答一两个问题

### 17:30 — 下班前部署新任务（30 分钟）

1. 打开今天 PM 在 GitHub Issues 里新建的 3 个需求
2. 用 Claude Code 拆解任务（自动路由引擎 + 生成派发清单）：
  ```bash
   /user:decompose [需求描述]
  ```
3. 按派发清单复制粘贴到对应引擎（路由已固化，无需判断）：
  - Cursor Long-running/Background 任务 → 复制 prompt 到 Cursor Agent 对话框
  - Claude Code Headless 任务 → 复制 `claude -p` 命令到终端执行
4. 审批 Agent 提出的执行计划
5. 设置 Claude Code 定时任务：
  ```bash
   /loop 2h 检查所有运行中的 Agent 进度，如果有完成的任务则运行测试套件并生成报告
  ```
6. 关机回家

### 22:00 — 你在睡觉，双引擎闭环运转

```
[22:15] Cursor Agent #1: 完成审批流程 API 层，提交 commits 到 PR #145
[23:30] Cursor Agent #2: 完成流程设计器基础 UI，提交 commits 到 PR #146
[01:00] Cursor Agent #3: 完成数据库迁移脚本，提交 PR #147
[02:00] Claude Code: 定时审查 → 输出 review-20260416.json
        ├── 发现 3 个 🟢 问题（未使用导入、格式不一致、拼写错误）
        │   └── 自动修复 → 3 个 autofix: commits 推到 PR #145、#146
        ├── 发现 1 个 🟡 问题（API 响应缺少 total_count 字段）
        │   └── 创建 Issue #205 [auto-review]
        └── 发现 1 个 🔴 问题（迁移脚本缺少 down migration）
            └── 创建 Issue #206 [auto-review][critical] + PR #147 评论
[02:15] Claude Code: 自动修复完成，重新运行受影响文件的测试 → 全部通过
[02:20] Claude Code: 审查完毕，已生成 docs/review-*.json + 自动修复 commits + GitHub Issues
[05:30] 所有 Agent 已完成或等待中
```

**与开环的区别**：凌晨 2 点发现的 3 个 🟢 问题已被自动修复，你早上直接看到干净的 PR。只需处理 🔴 Issue #206（迁移脚本）。

---

## 六、IT 产品研发任务的自主能力矩阵

### 6.1 Agent 可以独立完成（你只需 Review）


| 任务类型          | 引擎                   | 路由规则 | 典型时长       | 产出               |
| ------------- | -------------------- | ---- | ---------- | ---------------- |
| 功能模块实现        | Cursor Long-running  | #2   | 4-36 小时    | 代码 + 测试 + PR     |
| Bug 修复        | Cursor Background    | #3   | 15 分钟-2 小时 | 修复代码 + 回归测试      |
| 单元/集成测试补充     | Claude Code Headless | #4   | 30 分钟-3 小时 | 测试文件 + 覆盖率报告     |
| 代码重构          | Cursor Long-running  | #1   | 2-25 小时    | 重构代码 + 不破坏已有测试   |
| API 文档生成/更新   | Claude Code Headless | #5   | 10-30 分钟   | Markdown/OpenAPI |
| 技术债务清理        | Claude Code Headless | #4   | 1-4 小时     | 清理代码 + PR        |
| 依赖升级          | Claude Code Headless | #4   | 30 分钟-2 小时 | 升级 + 兼容性修复       |
| CI/CD 配置      | Claude Code Headless | #4   | 30 分钟-2 小时 | 配置文件 + 验证        |
| 数据库 Migration | Cursor Background    | #3   | 30 分钟-2 小时 | 迁移脚本 + 回滚脚本      |


**引擎路由规则**（在 `/user:decompose` 中强制执行，不是建议）：


| #   | 条件                                 | 引擎                   |
| --- | ---------------------------------- | -------------------- |
| 1   | 复杂度 ≥ L 且涉及 ≥ 3 文件的代码变更            | Cursor Long-running  |
| 2   | 功能模块实现 / 系统重构 / 新模块开发              | Cursor Long-running  |
| 3   | Bug 修复 / Migration / 小型功能，且复杂度 ≤ M | Cursor Background    |
| 4   | 测试补充 / 文档生成 / 技术债务 / 依赖升级 / CI 配置  | Claude Code Headless |
| 5   | 产出为纯文档（设计文档 / 技术方案 / API 契约）       | Claude Code Headless |
| 6   | 代码审查 / 巡检 / 报告生成                   | Claude Code Headless |
| 7   | 以上均不匹配                             | Cursor Background    |


每个子任务的 `引擎` 字段标注为 `引擎名称（规则 #N）`，路由决策可追溯。

> **与 §3 "两个引擎" 的关系**：§3 介绍的是两个**平台**（Cursor 和 Claude Code）。Cursor Background 是 Cursor 的轻量模式（快速启动、无需 Planner/Judge），与 Long-running 共享同一平台但适合不同规模的任务。路由规则在此基础上区分三种运行模式。

### 6.2 Agent 需要你中间介入


| 任务类型     | 你需要做什么     | Agent 做什么                 |
| -------- | ---------- | ------------------------- |
| 需求分析     | 确认业务规则、优先级 | 输出 User Story + AC + 技术方案 |
| 架构设计     | 做最终架构决策    | 生成候选方案 + 利弊分析             |
| API 接口设计 | 审批接口契约     | 生成 OpenAPI 规范 + Mock      |
| 数据库模型设计  | 审批 ER 图    | 生成模型 + Migration + ORM 代码 |
| 性能优化     | 确认优化方向     | 性能分析 + 实施优化 + 基准测试        |


### 6.3 Agent 无法替代你做的事

- 产品方向决策与优先级排序
- 跨团队沟通与利益协调
- 向老板汇报进展（但 Agent 可以帮你生成周报）
- 生产环境的关键部署决策
- 安全事件的应急响应

---

## 六.½ 强约束实现层（软规则 → 硬检查）

OPC 哲学的核心一条是「自动化优先」——能写成脚本/CI 检查/Rule 的，绝不依赖 Agent 每次自觉遵守。本节明示每条软规则对应的机械检查点（共 16 行），没有对应硬约束的规则不允许停留在"建议"状态。


| 软规则（描述于）                                                                                                          | 硬约束实现                                                                                             | 触发时机                 | 失败后果                         |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------- | ---------------------------- |
| `.cursor/rules/` 与 submodule 同步 (`dev-rules-convention.mdc`)                                                      | `dev-rules/sync.sh --check`                                                                       | pre-commit / CI      | exit 1，阻断提交                  |
| dev-rules 仓库自身完整性（README / frontmatter / 哲学映射 / 幽灵路径 / global 关键文件 / LaunchAgent 实装） (`dev-rules-convention.mdc`) | `dev-rules/verify-rules.sh`（8 段检查）                                                                | 子模块提交前               | exit 1                       |
| 先子模块后父仓库（submodule SHA 必须在 dev-rules 中真实存在） (`dev-rules-convention.mdc`)                                          | `scripts/preflight.sh` 段 2                                                                        | pre-commit / CI      | exit 1                       |
| review JSON 输出格式合规 (`review.md`)                                                                                  | `dev-rules/schemas/review.schema.json` + ajv/check-jsonschema                                     | `/user:calibrate` 入口 | 该 JSON 排除出校准                 |
| 契约文档不漂移 (`agent-contract-enforcement.mdc`)                                                                        | `python scripts/export_agent_contract.py --check`                                                 | preflight 段 4 / CI   | exit 1                       |
| 分支命名前缀（`prototype/` `feature/` `fix/` `chore/` `docs/`） (`product-dev.mdc`)                                       | `scripts/preflight.sh` 段 1                                                                        | pre-commit           | exit 1                       |
| User Story ↔ Test 不漂移 (`test-philosophy.mdc`)                                                                     | `python .testing/user-stories/verify_quality.py`                                                  | preflight 段 5 / CI   | exit 1                       |
| `docs/approved/` 在非高风险 / 非原型路径中被修改时必须触发 reviewer 明确确认 (`product-dev.mdc`)                              | `scripts/preflight.sh` 段 6                                                                        | PR 检查                | warn → reviewer 必须确认         |
| `approved_by: pending` 不进 main (`product-dev.mdc`)                                                                | `scripts/preflight.sh` 段 7                                                                        | main 分支 commit / CI  | exit 1                       |
| 提交前完成自检 (`product-dev.mdc`)                                                                                       | 整个 `scripts/preflight.sh`（接到 git pre-commit hook）                                                 | pre-commit / CI      | exit 1                       |
| 文档/规则中引用的 `dev-rules/...` 路径必须真实存在（防"幽灵引用"） (`dev-rules-convention.mdc`)                                          | `dev-rules/verify-rules.sh` 幽灵路径段                                                                 | 子模块提交前               | exit 1                       |
| preflight 真的会被自动触发，不依赖人记得跑 (`dev-rules-convention.mdc`)                                                           | `dev-rules/templates/install-hooks.sh` 安装 git pre-commit hook                                     | 项目接入时一次              | hook 缺失则 commit 不会触发检查       |
| Claude Code 全局宪法（`~/.claude/CLAUDE.md`）不是手维护的孤儿文件 (`dev-rules-convention.mdc`)                                    | `dev-rules/global/CLAUDE.md` + `sync.sh` 自动 symlink                                               | 跨会话持续                | 链接断裂时 sync.sh `--status` 标 ⚠ |
| 本机 dev-rules mirror 不静默落后远端（"承诺有 LaunchAgent" ≠ "实际装了"） (`dev-rules-convention.mdc`)                              | `dev-rules/templates/install-launchagent.sh` 注册 + `dev-rules/verify-rules.sh` 的 LaunchAgent 实装段检查 | 子模块提交前 + 30 min 自动跑  | 未装 → verify-rules.sh exit 1  |
| dev-rules 编辑后所有消费端及时更新（不依赖人记住 N 个 sync 命令）                                                                        | `dev-rules/sync.sh --push`（push + ~/Codes pull + 所有项目 fan-out 原子动作）                               | 编辑者主动                | 不跑则下一次 LaunchAgent 兜底        |
| 散文档中的数值/事实声明（"verify-rules X 段"等）不漂移 (`digital-clone-research.md §六.¾`)                                           | `dev-rules/sync-stats.sh --check`（基于 `.stats.json` 注册表 + 文档内 `<!-- stat:NAME -->` 占位符）            | preflight 段 8 / CI   | exit 1                       |


**升级原则**：当 review 中反复发现某个"靠自觉"的问题时，**必须**新增一段检查到 `preflight.sh` 或 `verify-rules.sh`，把软约束硬化。这条是元规则，写入 `product-dev.mdc` 的「完成自检」节末尾。

**新项目接入硬约束**：复制 `dev-rules/templates/preflight.sh` → `项目/scripts/preflight.sh`，在 `.git/hooks/pre-commit` 与 `.github/workflows/preflight.yml` 中调用。如果某项检查段在该项目尚不可自动化，必须记入 `docs/preflight-debt.md` 并设定截止日期，禁止悄悄降级为"靠自觉"。

---

## 六.¾ 反漂移机制（治"变更必伴漂移"）

### 问题：散文档中的事实必然滞后于实现

每次实现侧的小改动（verify-rules 加一段、preflight 加一类、规则文件加一个），文档中所有提到"X 段/X 类/X 个"的句子都需要同步更新。靠人记忆 → 总会漏；靠 review 兜底 → 七轮回顾里我自己漏过六次。

这是元规律，不是态度问题。

### 解决：把数值从「叙述」变成「计算」

**两件工件**：

1. `dev-rules/.stats.json` —— 数值事实注册表，每条声明计算公式：
  ```json
   "verify-rules-checks": {
     "compute": "grep -cE '^log \"\\[[0-9]+/[0-9]+\\]' dev-rules/verify-rules.sh"
   }
  ```
2. `dev-rules/sync-stats.sh` —— 三种模式：
  - `--list`  列出所有 stat 当前 live 值
  - `--update`  扫描所有 `*.md` `*.mdc`，把 `<!-- stat:NAME -->...<!-- /stat -->` 的内部值替换为 live 值
  - `--check`  对比，发现漂移即 exit 1（接 preflight）

### 文档侧用法

写作时把容易过期的数值放进 stat 块：

```markdown
verify-rules.sh 现在 <!-- stat:verify-rules-checks -->8<!-- /stat --> 段
```

人读到的还是"8 段"，但**这个 8 不是手敲的**——任何一次 `verify-rules.sh` 加段，下一次 `git commit` 的 preflight（sync-stats 检查段）就会爆 drift，直到运行 `sync-stats.sh --update` 把所有文档刷成最新值才能提交。

### Jobs vs OPC 权衡


| 视角             | 评价                                                                                  |
| -------------- | ----------------------------------------------------------------------------------- |
| **Jobs（简洁）**   | 一个 50 行脚本 + 一个 JSON 注册表，零额外依赖（仅 python3 + perl）。占位符语法即「这个数字由机器维护」的视觉信号，无需阅读任何文档就能理解 |
| **OPC（自动化优先）** | 把"事后人工检查"前移为"提交时机械拦截"，单次违规 = 立刻可见，无需等到下一轮全量 review                                  |
| **不做的取舍**      | 不做 LLM 文档一致性检查（不可验证、贵、慢）；不做全文档生成（散文叙述本身有价值，且过度模板化反损可读性）；不做跨文件交叉引用 lint（rabbit hole） |


### 增量采纳路径

旧文档不强制立刻迁移：未标 stat 块的数字继续工作，按以下优先级逐步加注：

1. **必须立刻加**：`verify-rules.sh` / `preflight.sh` / `decompose.md` 命令路由规则等结构化产物的段数/规则数
2. **下次改动时加**：当前还没漂移过、但同一数字出现 ≥ 2 次的事实
3. **不必加**：单次出现的散文性数字（如"3 周内交付"）

凡新增到 `.stats.json` 的 stat，必须保证：(a) compute 命令在干净 checkout 上稳定可重现；(b) 至少有一处文档使用它（否则等于死规则）。

---

## 七、实施路线图（基于你的现状）

### Phase 0：补齐配置 — ✅ 已完成

已创建的基础设施：


| 基础设施                                                     | 状态  | 作用                                                                                        |
| -------------------------------------------------------- | --- | ----------------------------------------------------------------------------------------- |
| `~/Codes/dev-rules/`                                     | ✅   | **独立 GitHub 仓库**（`youxuanxue/dev-rules`），规则单一事实来源                                         |
| `dev-rules-convention.mdc`                               | ✅   | submodule 约定本身作为规则（alwaysApply），Agent 自动遵守                                                |
| `项目/dev-rules/`                                          | ✅   | **git submodule**，在项目内编辑 + 提交规则变更（项目集合见 `.registered-projects`，本机落地映射见 `.local-projects`） |
| `项目/.cursor/rules/*.mdc`                                 | ✅   | sync 产物（5 条规则，real copy），云端 Agent 可读                                                      |
| `项目/CLAUDE.md`                                           | ✅   | 项目上下文（自包含，不依赖 @~）                                                                         |
| `~/.cursor/rules/*.mdc`                                  | ✅   | symlink → dev-rules/rules/（本地 Cursor）                                                     |
| `~/.claude/CLAUDE.md`                                    | ✅   | symlink → dev-rules/global/CLAUDE.md（Claude Code 全局宪法，自动同步）                               |
| `~/.claude/commands/*.md`                                | ✅   | symlink → dev-rules/commands/（本地 Claude Code）                                             |
| `dev-rules/global/CLAUDE.md`                             | ✅   | 全局宪法唯一编辑入口（与 rules/ commands/ 同等地位）                                                       |
| `sync.sh --local`                                        | ✅   | submodule → 父项目 .cursor/rules/ 分发 + auto-register（同时写跨机器表与本机映射）                           |
| `sync.sh --push`                                         | ✅   | 编辑→ push→ ~/Codes pull→ 本机已落地项目 fan-out 原子动作（编辑者主动路径）                                     |
| `sync.sh --pull`                                         | ✅   | 远端 → ~/Codes → 本机已落地项目 fan-out（LaunchAgent 与手动救场用）                                        |
| `sync.sh --check`                                        | ✅   | drift 检测（CI/preflight 用，见 §六.½）                                                           |
| `verify-rules.sh`                                        | ✅   | dev-rules 仓库完整性检查（含幽灵路径检测、global 关键文件、LaunchAgent 实装，8 段）                                 |
| `schemas/review.schema.json`                             | ✅   | review JSON 格式 Schema（calibrate 入口前置校验）                                                   |
| `templates/preflight.sh`                                 | ✅   | 项目级提交前/CI 门禁模板（覆盖 8 类硬约束，见 §六.½）                                                          |
| `templates/install-hooks.sh`                             | ✅   | 一键安装 git pre-commit hook，让 preflight 自动触发                                                 |
| `templates/launchagent.plist` + `install-launchagent.sh` | ✅   | 跨机器同步 LaunchAgent 模板 + 一键 launchctl 注册                                                    |
| `scripts/preflight.sh`                                   | ✅   | 本 workspace 自身的 preflight 入口（吃狗粮，触发 hook）                                                 |
| `sync-stats.sh` + `.stats.json`                          | ✅   | 文档内数值漂移机械拦截（治"变更必伴漂移"，见 §六.¾）                                                             |
| `scripts/setup-claude-code.sh`                           | ✅   | 云端 Agent 自动安装 Claude Code CLI + 验证 API Key（见 §3.4）                                        |
| Cursor Cloud Secrets                                     | ⬜   | 在 Dashboard 配置 `ANTHROPIC_API_KEY`（一次性，见 §3.4）                                            |


修改规则/命令/全局宪法：`编辑 dev-rules/{rules,commands,global}/ → sync.sh --local → verify-rules.sh → cd dev-rules && git commit → ./sync.sh --push → cd .. && git commit && git push`

新项目接入：`git submodule add ... dev-rules → sync --local → git commit`

### 贯穿所有 Phase 的前置设计：双引擎反馈闭环

**核心问题**：Claude Code 的审查报告如何回流驱动 Cursor Agent 行动？

#### 问题：开环 vs 闭环

```
【开环（有缺陷）】
Cursor Agent → PR                     ┐
Claude Code (凌晨) → review-*.json     ┤→ 全部堆到你的早上 → 你是瓶颈
                                       ┘

【全闭环（有风险）】
Cursor Agent → PR → Claude Code 审查 → 发现问题 → 自动修复 →
  → Claude Code 再审查 → 又发现问题 → 又自动修复 → ...（无限循环 + 成本爆炸）

【分级闭环（推荐）】
Cursor Agent → PR → Claude Code 审查 → 结构化输出 →
  ├── 🟢 auto-fix:  lint/格式/简单类型  → Agent 自动修复，不等人
  ├── 🟡 issue:     中等问题           → 创建 GitHub Issue，你决定是否修
  └── 🔴 block:     架构/安全/方向性    → 标记 PR，通知你，Agent 停止
```

#### 闭环的关键约束

1. **结构化输出**：Claude Code 审查必须输出 JSON（不是 Markdown 散文），才能被下游自动消费
2. **对照审批产物**：审查时必须读取人类已确认的设计文档、API 契约、验收标准，做**符合性检查**（不是凭空判断）
3. **分级分类**：每个发现必须标注 severity（🟢🟡🔴）和 automability（可自动修/需人工）
4. **熔断器**：自动修复最多 2 轮；超过则停止并通知人类
5. **预算上限**：每个审查+修复循环有独立预算上限
6. **审计日志**：所有自动修复必须有记录（谁发现的、为什么改、改了什么）

#### 审查的两个维度

```
维度一：通用代码质量检查
  安全漏洞 / lint / 未处理异常 / 架构分层 / 命名一致性
  → 不需要审批产物，任何代码都适用

维度二：符合性检查（对照审批产物）
  代码 ↔ 设计文档   → 数据模型是否与审批的 ER 图一致？
  代码 ↔ API 契约   → 接口路径、参数、返回结构是否与审批的契约一致？
  代码 ↔ 验收标准   → User Story 的 AC 是否被代码覆盖？
  代码 ↔ 技术选型   → 是否使用了审批确认的技术栈和依赖？
  代码 ↔ 任务拆解   → 实现范围是否超出或遗漏了拆解的任务边界？
  → 必须读取审批产物，否则审查无意义
```

**如果缺少审批产物**：Claude Code 应在报告中标注"无法执行符合性检查：缺少审批产物"，而不是跳过不提。

#### 审批产物的存放约定

审批产物存放在 `docs/approved/` 目录中（完整目录结构见 §4.4 文件布局）。

**生命周期**：

1. **Agent 直接写入**：原型阶段，Agent 在 GATE-1 PR 中直接创建/修改 `docs/approved/` 下的文件
2. **人在 PR 中校准**：审批时直接在 PR 中编辑、修正设计文档（GitHub suggest changes 或 push commits）
3. **merge = 审批**：PR 合并到 main 后，`docs/approved/` 中的文件即为人类审批通过的版本
4. **非 GATE PR 禁止修改**：日常开发 PR 不得修改 `docs/approved/` 下的文件（CI check 或 CODEOWNERS 约束）

**命名规则**：实体化命名，不带版本号。新审批直接覆盖旧版本，git history 保留完整演进。例如 `data-model.md`（不是 `data-model-v1.md`）。

**冲突处理**：当 Feature B 需要修改 Feature A 已审批的数据模型时，Feature B 的 GATE-1 PR 直接修改 `docs/approved/data-model.md`。PR diff 清晰显示变更内容，人在审批时自然看到"正在修改已审批的产物"，做出有意识的决策。

**审批元数据头**：每个 `docs/approved/` 文件包含 YAML frontmatter，Agent 生成时填 `approved_by: pending`，人 merge 前校准为实际信息：

```yaml
---
approved_by: xuejiao
approved_at: 2026-04-15T08:30:00+08:00
source_pr: "#141"
gate: GATE-1
---
```

Claude Code 审查时：读取 `docs/approved/` 作为符合性检查基线，检查元数据头确认审批状态（`approved_by: pending` 视为未审批）。

#### 结构化审查输出格式

```json
{
  "review_date": "2026-04-16T02:00:00Z",
  "commits_reviewed": ["abc1234", "def5678"],
  "approved_artifacts_referenced": [
    "docs/approved/design-approval-engine.md",
    "docs/approved/api-contract.yaml"
  ],
  "findings": [
    {
      "id": "F-001",
      "severity": "low",
      "category": "style",
      "automatable": true,
      "file": "src/approval/engine.py",
      "line": 42,
      "description": "未使用的 import: os",
      "suggested_fix": "删除 import os",
      "human_verdict": { "accurate": true, "severity_correct": true, "autofix_safe": true }
    },
    {
      "id": "F-002",
      "severity": "high",
      "category": "conformance",
      "automatable": false,
      "file": "src/approval/api.py",
      "line": 15,
      "description": "审批接口 POST /approvals 缺少 priority 参数，与 docs/approved/api-contract.yaml 中定义的契约不一致",
      "reference": "docs/approved/api-contract.yaml#/paths/~1approvals/post",
      "suggested_fix": "按契约补充 priority 参数（enum: low/medium/high/urgent）",
      "human_verdict": { "accurate": true, "severity_correct": false, "notes": "应为 medium，不影响现有功能" }
    },
    {
      "id": "F-003",
      "severity": "critical",
      "category": "conformance",
      "automatable": false,
      "file": "src/approval/models.py",
      "line": 30,
      "description": "ApprovalStep 模型缺少 counter_sign 字段，与 docs/approved/data-model.md 中审批通过的数据模型不一致",
      "reference": "docs/approved/data-model.md#审批步骤表",
      "suggested_fix": "按数据模型补充 counter_sign: bool 字段及相关逻辑",
      "human_verdict": { "accurate": false, "notes": "counter_sign 在 v2 才需要，v1 审批产物中标注为 optional" }
    }
  ],
  "conformance_summary": {
    "artifacts_checked": 2,
    "artifacts_missing": 0,
    "conformance_issues": 2,
    "full_conformance": false
  },
  "summary": {
    "total": 6,
    "auto_fixable": 2,
    "needs_human": 3,
    "critical_blockers": 1
  },
  "circuit_breaker": {
    "fix_rounds_used": 0,
    "max_fix_rounds": 2,
    "budget_used_usd": 1.5,
    "budget_limit_usd": 5
  }
}
```

#### 各 Phase 的闭环程度（逐阶段收紧）

```
Phase 1  ──  观察为主    ──  只自动修 🟢，其余全部留给人类
Phase 2  ──  辅助闭环    ──  自动修 🟢🟡，🔴 留给人类
Phase 3  ──  自动闭环    ──  自动修 🟢🟡，🔴 通知并等待
Phase 4  ──  自治闭环    ──  全自动 + 熔断器 + 周报人工复盘
```

### Phase 1：单任务长时运行（第 1-2 周）

**目标**：验证 Agent 可以在你不在时独立完成一个完整功能，**同时校准审查闭环的准确率**

**闭环级别：观察为主**——只自动修 🟢（lint、格式、未使用导入），其余生成 Issue 等你处理

1. 选一个**中等复杂度、边界清晰**的任务（如：给现有系统加一个 CRUD 模块）
2. 用 Cursor Long-running Agent 执行
3. 关机，第二天 Review PR
4. 记录：Agent 做对了什么、做错了什么、卡在哪里
5. **重点记录**：Claude Code 审查的准确率（误报率、漏报率）——这决定 Phase 2 能放开多少
6. 将经验补充到 Rules 和 CLAUDE.md 中

**夜间闭环流程**：

```
22:00  你关机
        ↓
22:xx  Cursor Agent 持续工作，提交 commits
        ↓
02:00  Claude Code 定时审查（结构化 JSON 输出）
        ↓
       ┌─ 🟢 severity=low + automatable=true → 自动提交修复到同一分支
       ├─ 🟡 severity=medium → 创建 GitHub Issue（label: auto-review）
       └─ 🔴 severity=high/critical → 创建 GitHub Issue + 在 PR 中评论 + 不自动修
        ↓
07:30  你早上看到：
       ├── PR（含 Agent 的代码 + 🟢 自动修复的 commit）
       ├── N 个 GitHub Issues（Claude Code 发现的 🟡🔴 问题）
       └── 审查报告 JSON（docs/review-*.json）
        ↓
07:45  标注 human_verdict（每个 finding 30 秒，通常 5-10 分钟完成）
        ↓
       每周运行 claude /user:calibrate → 自动产出校准报告
```

**Claude Code 配合**：审查逻辑（双维度、分级、JSON 输出格式）的**唯一定义**在 `dev-rules/commands/review.md`。cron 只负责调度 + 在审查结果上执行 Phase 1 的分级处理动作（不重复审查规则）。

```bash
# 凌晨审查（Phase 1 版本：保守模式）
0 2 * * * cd /path/to/project && claude -p "$(cat <<'PROMPT'
执行 /user:review 审查最近 24 小时的所有 commit，按 review.md 的规范输出 JSON。

完成后执行 Phase 1 分级处理（基于生成的 review-*.json）：
1. severity=low 且 automatable=true → 直接修复，commit message 前缀 'autofix:'，最多 5 个
2. severity=medium → gh issue create，label 'auto-review'
3. severity=high/critical → gh issue create + gh pr comment（不自动修）
4. category=conformance 且 severity≥medium → 额外 label 'conformance-drift'

严禁：修改业务逻辑 / API 契约 / 功能代码 / 数据库 schema / docs/approved/ 中任何文件。
PROMPT
)" \
  --allowedTools "Read" "Write" "Bash(git *)" "Bash(gh issue create *)" "Bash(gh pr comment *)" \
  --max-budget-usd 5
```

> 单一事实来源原则：审查的"测什么、怎么分级、JSON schema"全部在 `review.md`。修改 review 行为只改 review.md，cron prompt 自动跟随，避免双写漂移。

**Phase 1 校准工作流**（决定 Phase 2 放开程度）：

校准不是 Phase 1 结束时一次性做的事，而是贯穿整个阶段的持续过程：

```
每天早上 Review 审查报告时：
  1. 打开 docs/review-*.json
  2. 为每个 finding 的 human_verdict 填写：
     - accurate: true/false       （这个发现是否确实是问题）
     - severity_correct: true/false（严重程度分级是否合理）
     - autofix_safe: true/false    （自动修复是否安全，仅限 automatable=true）
     - notes: "可选备注"           （漏报、误判原因等）
  3. 保存 JSON

每周（或样本量 ≥ 20 时）运行校准汇总：
  claude /user:calibrate
  → 自动读取所有已标注的 review JSON
  → 计算 6 项指标
  → 输出 docs/calibration-*.md（含达标/未达标判定）
```


| 校准指标     | 计算方式                                               | 达标门槛  |
| -------- | -------------------------------------------------- | ----- |
| 审查准确率    | `accurate=true` 的 🟡🔴 / 已标注的 🟡🔴 总数              | ≥ 80% |
| 自动修复安全率  | `autofix_safe=true` 的 / 已标注的 `automatable=true` 总数 | ≥ 95% |
| 分级准确率    | `severity_correct=true` 的 / 已标注总数                  | ≥ 75% |
| 误报率      | `accurate=false` 的 / 已标注总数                         | ≤ 20% |
| 符合性检查准确率 | conformance findings 中 `accurate=true` 的比例         | ≥ 85% |
| 符合性检查覆盖率 | 审批产物中的关键约束被检查到的比例                                  | ≥ 70% |


**Phase 2 准入判定**：6 项指标全部达标 + 样本量 ≥ 20 → 可以进入 Phase 2。
校准报告自动产出结论（`✅ 已就绪` 或 `❌ 未就绪 + 改进建议`），无需人工计算。

### Phase 2：多任务并行 + 辅助闭环（第 3-4 周）

**目标**：同时跑多个 Agent，闭环从 🟢 扩展到 🟡

**闭环级别：辅助闭环**——基于 Phase 1 的校准结果，允许自动修复 🟡 级别问题

1. 将一个需求拆解为 3-5 个并行子任务（`/user:decompose`，自动路由引擎）
2. 按派发清单将子任务分配到对应引擎（Cursor Long-running / Background / Claude Code Headless）
3. Claude Code 作为"监工"：
  - 定时检查各 Agent 进度
  - 审查已提交的 PR（结构化 JSON）
  - **自动修复 🟢 + 🟡 问题**（Phase 1 校准达标后开放）
  - 🔴 问题仍然等你
4. 你只做计划审批 + 🔴 问题决策 + 最终 PR Review

**新增能力**：

```bash
# Claude Code 监工：每 2 小时巡检一次
claude -p "$(cat <<'PROMPT'
巡检所有活跃的 PR 和最近 2 小时的 commit：
1. 结构化审查 → JSON
2. auto-fix: severity ∈ {low, medium} 且 automatable=true → 直接修复并 commit
3. 修复后重新运行相关测试，确认没有回归
4. 如果修复引入了新的测试失败 → 回滚修复，降级为 GitHub Issue
5. 熔断器：同一文件在同一轮被修复超过 3 次 → 停止，创建 Issue

将巡检摘要写入 docs/patrol-$(date +%Y%m%d%H%M).json
PROMPT
)" --max-budget-usd 8
```

**Phase 2 新增的安全机制**：

- **回滚保护**：自动修复后跑测试，失败则 `git revert`
- **文件级熔断**：同一文件被修复 > 3 次 → 停止，说明问题不是局部的
- **跨 Agent 冲突检测**：如果两个 Agent 改了同一文件，Claude Code 标记冲突而非自动解决

### Phase 3：自动化流水线 + 全闭环（第 2-3 月）

**目标**：事件驱动的自动化研发流水线，Claude Code 和 Cursor Agent 形成完整反馈环

**闭环级别：自动闭环**——所有可自动修复的问题都自动处理，🔴 问题通知并等待

```
GitHub Issue 创建
  → Claude Code 自动拆解任务（/user:decompose）
  → 创建子 Issue + 分支
  → Cursor Agent 自动认领并执行
  → PR 提交后自动触发闭环：
     │
     ▼
  ┌──────────────────────────────────────────────────┐
  │              反馈闭环（最多 2 轮）                  │
  │                                                   │
  │  Claude Code 审查（JSON）                         │
  │    ├── 🟢🟡 auto-fix → commit → re-review         │
  │    ├── 🔴 → 通知你 + 暂停闭环                     │
  │    └── 全部通过 → 跳出循环                         │
  │                                                   │
  │  熔断条件（任一触发则停止）：                        │
  │    · 已完成 2 轮 fix-review 循环                   │
  │    · 累计修改超过 20 个文件                        │
  │    · 预算耗尽                                     │
  │    · 测试 pass rate 下降                          │
  └──────────────────────────────────────────────────┘
     │
     ▼
  全部通过 → 通知你 Review
```

所需配置：

- GitHub Actions 中集成 Claude Code Headless（PR 事件触发审查）
- Cursor Automations（Issue 创建触发 Agent）
- 通知机制（Slack/邮件/手机推送）
- 共享任务状态文件（`.tasks/active.json`）供两个引擎协调

### Phase 4：7×24 自治 + 自愈闭环（第 3-4 月）

**目标**：数字分身可以独立"接活"，双引擎形成自我改进的正反馈

**闭环级别：自治闭环**——全自动 + 熔断器 + 每周人工复盘

- 老板在任务系统中创建需求 → 数字分身自动开始工作
- 你每天花 1-2 小时 Review + 决策
- Agent 自动维护技术债务、更新文档、补充测试
- 每周五 Agent 自动生成本周工作报告

**Phase 4 新增：自我改进环路**

```
每周复盘 → 分析本周所有自动修复的记录 →
  ├── 误报率高的 category → 调整审查 prompt，提高阈值
  ├── 反复出现的问题 → 转化为新的 Rule（写入 dev-rules/rules/）
  └── Agent 常犯的错误 → 写入 CLAUDE.md 的"已知陷阱"列表
```

---

## 八、成本与 ROI（OPC 视角）

### 8.1 月度成本


| 项目                      | 费用             | 用途                                       |
| ----------------------- | -------------- | ---------------------------------------- |
| Cursor Ultra            | $200/月         | Long-running Agent + Background Agent ×8 |
| Claude API（Headless 用量） | $50-150/月      | 定时任务 + 代码审查 + 任务拆解                       |
| **合计**                  | **$250-350/月** | ≈ ¥1,800-2,500/月                         |


### 8.2 OPC 杠杆率

传统模式下，同等产出需要 3-5 人的团队；OPC 模式下，1 人 + AI 数字分身即可覆盖：


| 指标       | 传统小团队（3-5 人）         | OPC（1 人 + Agent）                  |
| -------- | -------------------- | --------------------------------- |
| 每天有效产出时间 | 12-20 小时（人均 4h × 人数） | 4 小时（你）+ 16 小时（Agent）             |
| 月人力成本    | ¥90,000-150,000      | ¥2,500（Agent）+ 你自己的时间             |
| 沟通协调开销   | 每天 1-3 小时（会议/对齐/冲突）  | ≈ 0（Agent 通过 Rules 对齐，无需开会）       |
| 知识传递成本   | 新人入职 1-3 个月爬坡        | 0（规则即知识，`dev-rules/` 即培训手册）       |
| 单人被替代风险  | 高（关键人离职 = 知识流失）      | 低（规则/技能/记忆全部版本化，可迁移）              |
| 产品一致性    | 随人数增加而下降（风格差异）       | 高（同一套 Rules 约束所有 Agent，乔布斯式端到端控制） |


**OPC 核心等式**：`¥2,500/月的 Agent 成本` ÷ `等效 ¥20,000-60,000/月的人力产出` = **8-24 倍杠杆率**

**关键认知**：Agent 不是廉价劳动力的替代品。OPC 的价值不在于「省钱雇了一个便宜的程序员」，而在于**消除了团队协作中的一切非生产性开销**——沟通、对齐、等待、入职培训、风格冲突。一个人的判断力 + Agent 的执行力 = 比小团队更快、更一致的产出。

---

## 九、关键风险与应对

### 9.1 长时运行特有风险


| 风险        | 为什么长时运行更严重                     | 应对                                |
| --------- | ------------------------------ | --------------------------------- |
| **方向漂移**  | 运行越久，偏离初始意图的概率越大               | Planner 先提计划等你审批；Judge Agent 每轮检查 |
| **错误放大**  | 一个错误假设被后续代码不断巩固                | 频繁提交 + CI 门禁 + 每日自动 Review        |
| **上下文膨胀** | 长时间运行积累大量上下文，超出窗口              | 模块化拆分任务；Agent 之间不共享上下文            |
| **成本失控**  | 夜间无人监管时 token 消耗               | `--max-budget-usd` 硬限 + 成本告警      |
| **合并冲突**  | 多个 Agent 改同一文件                 | 乐观并发控制 + Worker 互不干扰              |
| **闭环震荡**  | 自动修复引入新问题→再修复→再引入              | 熔断器（最多 2 轮）+ 回滚保护 + 文件级熔断         |
| **误报级联**  | Claude Code 审查误报→Agent "修"正确代码 | 分级策略：Phase 1 只自动修 🟢；逐阶段校准后放开     |


### 9.2 IT 产品研发特有风险


| 风险               | 应对                                  |
| ---------------- | ----------------------------------- |
| Agent 不了解业务领域    | 在 CLAUDE.md 中写入业务领域术语表 + 核心业务规则     |
| Agent 选择了错误的技术方案 | 架构决策必须人工审批；Agent 只在已定方案内执行          |
| 生产数据安全           | Agent 只能访问开发/测试环境；生产部署必须人工触发        |
| 代码质量不一致          | 你已有的 `test-philosophy.mdc` 强制执行测试规范 |
| 团队成员不信任 Agent 代码 | 所有 Agent 代码必须通过与人类代码相同的 Review 流程   |


---

## 十、与现有 Skills 体系的扩展

你已有 14+ 个 Skills。以下是产品研发专用能力的对照表：


| 能力              | 触发场景         | 实现方式                             | 承载哲学                       | 状态         |
| --------------- | ------------ | -------------------------------- | -------------------------- | ---------- |
| 产品研发工作流         | 需求分析/功能开发/发布 | `product-dev.mdc` 规则             | Jobs（设计哲学节）+ OPC（自检纪律）     | ✅ 已实现      |
| 任务拆解 + Agent 分配 | 收到大型需求时      | `/user:decompose` 命令             | Jobs（聚焦决策表）+ OPC（引擎路由+派发）  | ✅ 已实现      |
| 自动化代码审查         | PR 提交 / 定时巡检 | `/user:review` 命令 + cron         | Jobs（设计质量维度）+ OPC（手动操作残留）  | ✅ 已实现      |
| 审查校准 + Phase 准入 | 每周汇总         | `/user:calibrate` 命令             | OPC（指标自动汇总）                | ✅ 已实现      |
| 测试方法论           | 功能性变更        | `test-philosophy.mdc` 规则         | Jobs（测试聚焦）+ OPC（测试自动化）     | ✅ 已实现      |
| API 契约执行        | API/CLI 变更   | `agent-contract-enforcement.mdc` | Jobs（API 最小面）+ OPC（契约自动生成） | ✅ 已实现      |
| 迭代报告            | 周五/迭代结束      | 待建设                              | OPC（人工只看摘要）                | 📋 Phase 4 |


---

## 十一、参考资源

### Cursor 长时运行 Agent

- [Scaling long-running autonomous coding](https://cursor.com/blog/scaling-agents) — Planner/Worker/Judge 架构深度解析
- [Expanding long-running agents](https://cursor.com/blog/long-running-agents) — 使用案例与最佳实践
- [Towards self-driving codebases](https://cursor.com/blog/self-driving-codebases) — 自动驾驶代码库的愿景
- [Agent best practices](https://cursor.com/blog/agent-best-practices) — 规则与技能配置指南

### Claude Code 无人值守

- [Claude Code Headless Mode 指南](https://www.mindstudio.ai/blog/claude-code-headless-mode-autonomous-agents/) — `-p` 模式完整用法
- [Claude Code 定时任务配置](https://claudefa.st/blog/guide/development/scheduled-tasks) — `/loop` 与 cron 集成
- [Claude Code 工作流最佳实践 2026](https://smart-webtech.com/blog/claude-code-workflows-and-best-practices/) — 上下文管理与模块化规则

### 双引擎协作

- [Cursor + Claude 管理 8 个项目的实践](https://medium.com/@chace.medeiros/my-vibe-coding-setup-using-cursor-claude-to-manage-8-projects-7dd9a1216597) — 多项目工作流
- [Claude Code 多 Agent 编排](https://www.mintlify.com/saurav-shakya/Claude_Code-_Source_Code/advanced/multi-agent) — Sub-agent 与 Agent Teams

### 中文资源

- [阿里云 - 2026 数字分身深度解析](https://developer.aliyun.com/article/1708947)
- [个人 Agent 完美架构推演](https://www.gankinterview.cn/zh-CN/blog/if-openclaw-is-merely-a-transitional-phase-what-exactly-will-the-perfect-archite) — 微内核 + 插件化架构

