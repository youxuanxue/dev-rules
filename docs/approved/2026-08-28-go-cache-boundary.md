---
approved_by: user-chat-2026-08-28
status: approved
risk_level: high
related_prs:
  - https://github.com/youxuanxue/dev-rules/pull/102
  - https://github.com/youxuanxue/sub2api/pull/1877
related_commits:
  - cf84333
  - 4c49852a2
---

# Go cache boundary

## Decision

Go 的可再生缓存必须同时具备两个硬边界：

- 本机使用一个加密 APFS `DevCache` Volume，`quota = 64 GiB`、不设置 `reserve`；
- GitHub Actions 的 Go 缓存由默认分支上的唯一写入者维护，活动工作集不超过 `6 GiB`，每类最多保留两代。

这两个边界解决同一个问题：缓存是加速器，不得成为机器或 CI 的无界持久状态。研发者继续并发使用 Agent、IDE 和多个 worktree，不承担观察、清理或选择缓存参数的日常操作。

本设计只治理 Go 的 build、test、module 和 temporary cache。pnpm、Docker、IDE、Agent session 等缓存没有证据表明属于本次根因，不纳入同一系统。

## Evidence and rejected designs

2026-08-28 的本机排查确认：

- Data Volume 一度只剩约 `2.9 GiB`；
- `~/Library/Caches/go-build` 约 `509.7 GiB`；
- `go clean -cache` 释放约 `513 GiB`；
- 多个 worktree、未启用 `-trimpath`、不同 Go toolchain 和构建参数、Ent 的时间戳 loader 共同放大了 cache key 基数；
- Go build cache 没有字节配额，按最近使用时间回收不足以保护研发机器。

2026-08-28 的 GitHub Actions API 观测确认 `youxuanxue/sub2api` 有 55 份活动缓存，共 `9,979,569,219` bytes，接近仓库 `10 GB` 上限。主要占用来自每次 main 构建都以 `github.run_id` 生成新 key 的 Release Go cache，以及分支 benchmark cache。GitHub-hosted Runner 的临时磁盘会随 Job 销毁，但上传到 Actions Cache 的归档会持续占用仓库配额并触发淘汰抖动。

2026-08-29 再次枚举同一仓库，活动缓存已到 `11.12 GiB` / 78 份，平台淘汰已经在发生。按 prefix 看，超标来自代数而不是单份归档：

| Prefix | Latest on `main` | Copies on `main` | `main` total |
| --- | --- | --- | --- |
| `Linux-go-release-*` | `1.41 GiB`（内嵌 `GOMODCACHE`） | 4 | `5.06 GiB` |
| `Linux-gobuild-unit-*` | `0.60 GiB` | 4 | `1.96 GiB` |
| `Linux-gomod-*` | `0.48 GiB` | 1 | `0.48 GiB` |
| `Linux-gobuild-integration-*` | `0.31 GiB` | 2 | `0.54 GiB` |
| `Linux-gobuild-lint-*` | `0.28 GiB` | 1 | `0.28 GiB` |
| `Linux-gobuild-preflight-*` | `0.25 GiB` | 1 | `0.25 GiB` |
| `Linux-gobuild-security-*` | `0.03 GiB` | 1 | `0.03 GiB` |
| `Linux-golangci-*` | `0.002 GiB` | 12 | `0.02 GiB` |
| leftover `setup-go-*` | `0.48 GiB` | 1 | `0.48 GiB` |

把 `release` 从模块缓存中拆开、把 unit/preflight 收进同一 `test` family、把 lint GOCACHE + `~/.cache/golangci-lint` + security 收进同一 `analysis` family 之后，五类最新代合计约 `2.6 GiB`。每类保留两代仍低于 `6 GiB`。因此 `6 GiB` 保持为固定工作预算；warm 失败条件仍然是「五个最新代本身超预算」，不根据这次快照自动抬额。

以下方案不采用：

- **仅靠 `go clean` 或定时扫描**：仍存在清理间隔，机器可以先被撑爆，并新增后台状态和阈值维护；
- **仅靠 `dev-go` wrapper**：IDE、Agent 或绝对路径调用可以绕过 wrapper，不能形成安全边界；
- **每个 worktree 独立缓存**：复制相同构建产物，直接放大已确认的根因；
- **固定磁盘分区或 APFS Reserve**：提前占用容量，牺牲系统可用空间；
- **扩大 GitHub 缓存配额**：只推迟淘汰抖动，不改善 key 质量或写入所有权；
- **GOCACHEPROG 或常驻缓存服务**：当前需求只需要容量边界与确定性 key，增加服务生命周期没有收益。

## Architecture

| Layer | Owner | Hard boundary | Read/write model |
| --- | --- | --- | --- |
| Local macOS | `dev-go` plus native Go environment | APFS quota `64 GiB`, no Reserve | Every managed Go invocation shares one bounded Volume |
| GitHub Actions | existing repository-owned Go cache action plus `warm-release-cache-main` | Active Go cache working set `<= 6 GiB` | PR/tag/required jobs restore-only; main warm workflow is the only writer |

本机与 CI 使用相同的 key 维度原则：精确 Go 版本、构建 profile、依赖内容和源码内容决定复用；绝对 checkout 路径、worktree 名、branch、日期和 run ID 不得成为大型持久缓存的代际维度。

## Local macOS boundary

### APFS Volume

在当前系统 APFS Container 中新增逻辑 Volume `DevCache`：

- maximum file data usage：`64 GiB`；
- minimum guaranteed capacity：不设置；
- 空间与现有 Volume 动态共享，只按实际缓存字节消耗物理容量；
- 必须加密，静态数据保护不得弱于当前已启用 FileVault 的 Data Volume；
- secret 只存本机 Keychain，不进入仓库、日志或 shell profile；
- 使用 Volume UUID 验证身份，不仅依赖显示名 `/Volumes/DevCache`；
- 登录后必须可自动解锁和挂载；挂载或身份验证失败时 fail closed；
- 禁止 Spotlight 索引和 Time Machine 备份该 Volume，避免缓存派生出新的系统数据或快照占用。

自动挂载由一个 `dev-rules` 管理的 one-shot login LaunchAgent 完成，不引入常驻 daemon。helper 使用 manifest 中的 Volume UUID，从 login Keychain 读取 Disk User passphrase，经 stdin 传给 `diskutil apfs unlockVolume`，验证实际 UUID 和 mount path 后退出；passphrase 不得出现在 argv、stdout、stderr 或 plist。Volume 已正确挂载时 helper 为 no-op。`dev-go` 在执行前也调用同一幂等 helper，消除登录启动顺序的竞争。

创建、加密、Keychain 写入、Time Machine exclusion 和首次迁移属于一次性高风险外部状态变更。实现代码可以先合并，但执行这些动作前必须再次取得人工确认。

### Go-native enforcement

安装器在 Volume 内创建包含 Volume UUID 的 identity directory，再创建三个 guard symlink：

```text
<home>/Library/Caches/dev-go/build -> /Volumes/DevCache/.dev-go-<volume-uuid>/build
<home>/Library/Caches/dev-go/mod   -> /Volumes/DevCache/.dev-go-<volume-uuid>/mod
<home>/Library/Caches/dev-go/tmp   -> /Volumes/DevCache/.dev-go-<volume-uuid>/tmp
```

Go 环境只引用 home 下的 guard path，不直接引用 mount path。Volume 未挂载或同名错误 Volume 被挂载时，这些 symlink 变为 broken link；Go 初始化 cache 会因目标不可用而失败，不会在 `/Volumes/DevCache` 创建一个普通 Data-Volume 目录并继续写爆系统盘。

安装器随后通过 `go env -w` 设置以下持久值：

```text
GOCACHE=<absolute-home>/Library/Caches/dev-go/build
GOMODCACHE=<absolute-home>/Library/Caches/dev-go/mod
GOTMPDIR=<absolute-home>/Library/Caches/dev-go/tmp
GOFLAGS=<preserve-existing-flags-and-add-exactly-one--trimpath>
```

安装器写入解析后的绝对 home path；`~` 和 `$HOME` 不作为字面量进入 Go environment file。使用 Go 自己的环境存储和 broken-link guard 是默认安全边界。只要调用方没有显式覆盖这些变量，shell、IDE、Agent、Makefile 和真实 Go binary 的直接调用都会进入同一 Volume；wrapper 不再是容量安全的唯一前提。

任意进程仍然可以显式设置 `GOCACHE=/private/tmp/...` 绕过 Go-native 默认值，正如任意进程可以直接向系统盘写文件。实现因此还提供 `~/.local/bin/go -> dev-go` shim。`sync.sh` 只分发 `dev-go`；installer 仅在本机边界验证完成后创建 `go` shim，未安装机器不受影响。安装器必须验证 `~/.local/bin` 在解析后的 PATH 中位于真实 Go binary 之前；普通 shell、Agent 和 PATH-based IDE 调用会由 shim 强制覆盖三个 cache path，并确定性合并 `-trimpath`。shim 从本机 manifest 读取并验证真实 Go binary，禁止递归解析自身。

所有 repository-owned override 收敛到唯一入口 `dev-go cold`：它只能在 `DevCache` 内创建 task-owned directory，并在退出时删除。项目 sentinel 扫描受管脚本、Makefile、workflow 和 Agent launcher，拒绝本机路径指向 `/private/tmp`、`/tmp` 或 worktree-local persistent cache；CI 的 Runner-local 路径按独立 CI contract 校验。绝对路径执行真实 Go binary且同时显式覆盖 cache 的未受管恶意进程，不属于本设计能够提供的 OS sandbox 保证。

安装器必须保留现有 `GOFLAGS`，只确定性去重并加入 `-trimpath`。验证读取 `go env` 的最终解析值，而不是只检查配置文件文本。仓库脚本或 workflow 若重新定义 `GOFLAGS`，必须显式保留 `-trimpath`。

不写入全局固定 `GOTOOLCHAIN`。Go 版本属于项目契约：`sub2api/backend/go.mod` 是单一事实来源，现有 Makefile 从中推导精确版本，CI 使用 `actions/setup-go` 安装并验证同一版本。这样不会用一个项目的 toolchain 决策污染其他 Go 项目。

### `dev-go` experience layer

`dev-rules/global/bin/dev-go` 是受支持的人类和 Agent 入口，经 `sync.sh` 发布到 `~/.local/bin/`。它只承担 native enforcement 之外的体验职责：

1. 验证 Volume UUID、挂载点、加密状态、quota 和 Go 环境；
2. 从最近的 `go.mod` 推导本次命令的精确 `GOTOOLCHAIN`；
3. `dev-go cold` 为冷缓存实验在 `DevCache` 内创建 task-owned build/module/temp directory，并在退出时回收；普通 `dev-go` 直接使用共享 cache；
4. 在容量不足时取得全局恢复锁，先删除 task-owned 临时内容；
5. 只有能够证明没有其他 Go 构建活动时，才重置共享 build cache；
6. 自动重试原命令一次，第二次失败原样返回非零退出码。

`dev-go doctor` 是唯一诊断命令；`dev-go cold <go-args...>` 是唯一冷缓存入口；其他参数透明传给真实 Go binary。以文件名 `go` 调用时始终进入透明模式，不增加另一套 Go CLI。

每个 shim 管理的 Go 命令在真实 binary 生命周期内持有 activity shared lock。cache-full recovery 必须在原命令退出并释放 shared lock 后取得 exclusive lock，再确认没有未受 wrapper 管理的 Go、compile 或 link 进程；等待 exclusive lock 最多 60 秒。任一步骤不确定或超时都停止恢复，不清理共享 cache。该锁不声称约束绝对路径启动的恶意进程，而是让受管开发路径拥有可机械验证的并发语义。

任何不确定的并发状态都禁止删除共享 cache。失败时不切回 `~/Library/Caches/go-build`，因为“继续构建但再次威胁系统盘”不是允许的降级路径。没有显式恶意 override 的直接真实 Go binary 调用仍受 APFS quota 保护，只是不获得自动诊断和重试体验。

### Local ownership and checks

本机能力只由 `dev-rules` 管理：

- `global/bin/dev-go`：runtime owner；
- 一个 repository-owned installer：执行一次性 Volume 创建与 Go 环境迁移；
- 一个 one-shot mount helper 和对应 LaunchAgent：只负责登录解锁、挂载和验证；
- 一个确定性 checker：输出 mount、UUID、encryption、quota、reserve、guard symlink、resolved Go env 和实际占用；
- `sync.sh --check`：只检查已安装机器的漂移，不静默创建、重建或删除 Volume。

安装成功后写入固定的非 secret manifest：`<home>/Library/Application Support/dev-rules/go-cache-boundary.json`，记录 Container UUID、Volume UUID、mount path、quota bytes、guard paths 和真实 Go binary。`sync.sh --check` 仅在该 manifest 存在时启用本机检查，因此未安装该能力的机器不会被误判漂移。

安装器必须幂等。发现同名但 UUID 不匹配、Volume 未加密、存在 Reserve、quota 不等于 `64 GiB`、guard symlink 指向未知位置、真实 Go binary 漂移或目标路径不是挂载 Volume 时立即停止，不接管未知状态。已存在的 foreign `~/.local/bin/go` 也必须 fail closed，不覆盖用户文件；卸载时只删除仍精确指向 `dev-go` 的 installer-owned shim。

## GitHub Actions boundary

### One writer

复用并收敛 `sub2api` 当前的 `.github/actions/go-rolling-cache` 与 `.github/workflows/warm-release-cache-main.yml`，不新增第二套缓存框架：

- 所有 `actions/setup-go` 显式使用 `cache: false`；
- pull request、tag、release 和 required CI job 只 restore，不 save；
- 只有 default branch 上的 warm workflow 可以保存大型 Go cache；
- warm workflow 使用 concurrency cancellation，过期 main run 不竞争写入；
- warm workflow 仅在 trusted default-branch context 获得最小 `actions: write`、`contents: read` 权限；
- release tag 只消费 default branch 生成的 cache，不创建 tag-scoped cache；
- cache 失败只影响加速，required CI 必须能够冷构建并保持正确性。

零散、低频的 Go workflow 要么消费同一 restore-only action，要么不使用 build cache；不得重新启用 setup-go 的隐式 cache。现网必须收敛的写入面：

| Surface | Current write behavior | Target |
| --- | --- | --- |
| `backend-ci.yml` `test-unit` | `save_caches: true` on main | restore-only；保留 `outputs.build_cache_hit`（#1873，仅 primary exact-hit） |
| `backend-ci.yml` `preflight` / `golangci-lint` / `backend-security` | composite 默认 `save_caches: true` | restore-only |
| `warm-release-cache-main.yml` | save `release`（key 含 `run_id`，内嵌 `GOMODCACHE`）和 daily `integration` | 唯一写入者；五类都写；key 去掉 `run_id` / 日期 |
| `test-integration` | 已是 restore-only | 保持；改为消费无日期的 `integration` key |
| `new-api-bump-smoke` / `compile-smoke` / watchdog / `client-fidelity-watch` / `ops-repair-draft` / `pr-repair-agent` / `security-scan` | 部分 `setup-go` `cache: true` | `cache: false`，或 restore-only 消费同一 action |

### Cache families and keys

远端只保留五类 Go cache：

| Family | Contents | Consumers |
| --- | --- | --- |
| `gomod` | `GOMODCACHE` | every Go job |
| `test` | unit and preflight build/test objects | unit, preflight |
| `integration` | integration-tag build/test objects | integration |
| `analysis` | lint and security analysis build objects | golangci-lint, govulncheck/gosec |
| `release` | linux amd64 and arm64 release objects | release |

`release` 不再内嵌一份 `GOMODCACHE`，模块只由 `gomod` family 持有。每个 build family 使用独立 `GOCACHE` 路径，避免为缩小归档而依赖共享目录的偶然内容。

`analysis` 必须同时覆盖两份 blob，不能把 `~/.cache/golangci-lint` 留成第六个无界 writer：

- lint / govulncheck / gosec 的 `GOCACHE`；
- `~/.cache/golangci-lint`（现网单份约 `2 MiB`，但 key 带 `run_id`，main 上已有十余代）。

`preflight` 不再单独占一个 family；它与 unit 共用 `test` 的 `GOCACHE`（相同 `-trimpath` 与 `-dwarf=false`）。`refresh_daily`、默认 week epoch 以及任何日期/周 key 输入必须从 composite 中删除，而不是停用后留在接口上。

大型 cache key 只能包含：

```text
schema-version / runner-os / exact-go-version / family / dependency-fingerprint / relevant-source-fingerprint
```

fingerprint 的文件集固定为：

- dependency：`backend/go.mod`、`backend/go.sum`、`.new-api-ref`；
- source：`backend/**/*.go`；
- family 附加配置：`test` / `analysis` 加 `backend/.golangci.yml`，`release` 加 `.goreleaser.yaml`。

禁止 `hashFiles('backend/**')` 这种厨房水槽，也禁止包含 `github.run_id`、日期、branch 或绝对 checkout path。内容未变化时 primary exact-hit，**跳过 save**，不重新上传；内容变化时 restore 上一代、编译 delta、保存新内容 key。所有 CI `GOFLAGS` 必须包含 `-trimpath`，profile 特异 flag 继续作为 Go 自身 action ID 的一部分。

现网重 Go job 用进程环境变量整段覆盖 `GOFLAGS`（例如 `GOFLAGS: -gcflags=all=-dwarf=false`），这会丢掉 `go env -w` 写入的 `-trimpath`。Phase 1 必须把这些 job 改成显式保留 `-trimpath`；机械测试拒绝缺失。

唯一写入者必须显式按 `actions/cache/restore -> build -> actions/cache/save -> prune` 排序，不能使用在 Job 尾部才保存的 combined action；否则 prune 无法观察刚保存的新代。primary exact-hit 时 save step 必须 no-op。restore consumer 永远使用 restore-only action，并继续导出 `build_cache_hit`（仅 primary exact-hit 为 true），供 Unit 冷/热调度（#1873）使用。

### Retention and budget

warm workflow 保存成功后调用 repository-owned deterministic prune script：

1. 只枚举 default branch 且匹配五个受管 prefix 的 cache；
2. 每个 family 始终保留最新一代；
3. 收集每个 family 的上一代作为候选；若最新代加全部候选超过 `6 GiB`，按候选 size descending、key ascending 的固定顺序删除，直到回到预算内；
4. 删除其他受管代；若五个最新代本身已超过预算，保留最新代但让 warm workflow 失败并输出分 family 证据；
5. 不删除非 Go cache，不使用 broad `--all`；
6. prune 或 budget check 失败时让非 required 的 warm workflow 报红。

Benchmark 默认只使用 Runner 内的 task-owned empty directory，不上传远端 cache。确需跨 Job 测量时，benchmark workflow 必须显式 save、restore，并在 `always()` cleanup 中按精确 ID 删除；未知或删除失败的实验缓存使 benchmark 失败。

`6 GiB` 是 repository-owned working budget，GitHub 的 `10 GB` 限额只是最后一道平台保护，不是正常运行目标。预算与“每类最多两代”均为固定产品决策，不暴露为日常用户参数。

## Failure semantics

| Failure | Required behavior |
| --- | --- |
| `DevCache` absent, locked or UUID mismatch | Stop before build; never recreate a normal directory at the mount path |
| Encryption, quota or Reserve drift | Checker fails; installer does not auto-adopt or mutate unknown Volume |
| Local quota reached | Recover only task-owned temp first; reset shared build cache only under proven idle state; retry once |
| Concurrent build makes cleanup unsafe | Return a clear bounded failure; do not delete shared state or spill to Data Volume |
| CI cache miss/corruption | Delete local restored directory if needed and cold-build; correctness remains independent of cache |
| CI save/prune/API failure | Warm workflow fails and reports evidence; required CI remains unaffected |
| CI cache budget exceeded after prune | Warm workflow fails; do not raise the budget automatically |

## Delivery sequence and approval gates

### 1. Land deterministic implementation without external mutation

- implement and test local installer, checker and `dev-go` in `dev-rules`;
- refactor `sub2api` cache action, warm workflow, key contract and prune script;
- expand the warm workflow so it can produce all five families before any required job loses its current writer;
- delete `refresh_daily` / week-epoch inputs; keep `outputs.build_cache_hit` as primary exact-hit only;
- rewrite existing job-level `GOFLAGS` so every Go workflow keeps `-trimpath`;
- add mechanical tests that reject `setup-go cache: true`, large `github.run_id` keys, date/week keys, unbounded writers and missing `-trimpath`;
- do not create a Volume, alter `go env`, write Keychain, delete cache or change GitHub cache settings in this phase.

### 2. Apply the local boundary after explicit approval

- re-resolve the target APFS Container and current FileVault state immediately before execution;
- show the exact Volume, quota, Reserve, encryption and mount actions for approval;
- create and verify `DevCache`;
- migrate Go-native environment values;
- run raw-Go and `dev-go` verification from two independent worktrees;
- preserve the old cache path until the new path passes verification, then request approval before deleting any remaining legacy cache.

### 3. Cut over CI without a cold-cache cliff

今天的 warm 只写 `release` 和 `integration`。unit / lint / preflight / security 的写入者仍是 required job。必须按这个顺序切，禁止先把 required job 改成 restore-only：

1. 先把 warm workflow 扩成五类都写，key 改为内容寻址；旧 required-job writer 可暂时并存；
2. 观察两代成功的 main warm，并且 required CI 能从新 key restore（含 #1873 的 exact-hit 热路径）；
3. 再把 required job 改为 restore-only，只留 warm 作为写入者；
4. inventory legacy cache by exact ID, prefix, ref, size and last access;
5. request approval for that resolved deletion set;
6. delete only the approved legacy Release, benchmark, setup-go, date/week and superseded Go family caches;
7. verify the active working set is at or below `6 GiB`.

Local APFS creation, Keychain mutation, legacy local cache deletion and GitHub cache deletion are separate external-state approval gates. Approval of this architecture does not authorize those apply operations implicitly.

## Acceptance criteria

### Local

- Disk Utility and the checker report `quota = 64 GiB` and no Reserve;
- the Volume is encrypted, automatically available after login and excluded from Spotlight and Time Machine;
- empty capacity is not preallocated; physical usage tracks actual cache contents;
- `go env` resolves build, module and temp cache under the verified Volume;
- `GOFLAGS` contains exactly one effective `-trimpath` while preserving existing flags;
- manifest 记录的真实 Go binary（本机当前为 `/opt/homebrew/bin/go`）直接调用时，与 `dev-go` 解析到相同的 bounded cache paths；
- identical source in two worktrees reuses cache rather than creating path-specific copies;
- filling the Volume cannot increase the legacy `~/Library/Caches/go-build` path;
- a cache-full recovery never deletes shared state while another build is active.

### GitHub Actions

- no required PR, tag or release job uploads a Go build cache;
- only the default-branch warm workflow owns Go cache writes and pruning;
- no persistent large Go key contains run ID, date, branch or checkout path;
- every Go job uses the exact `go.mod` version and effective `-trimpath`，即使 job 自己设置了 `GOFLAGS`；
- `analysis` 的最新代同时包含 lint/security `GOCACHE` 与 `~/.cache/golangci-lint`；没有独立的 `run_id` golangci family；
- restore consumer 在 primary exact-hit 时导出 `build_cache_hit=true`，restore-key 或 miss 时为 `false`；
- the five active families always have a latest generation and at most one previous generation;
- total active managed Go cache is `<= 6 GiB` after every successful warm workflow;
- a full cache miss still produces a correct green build;
- Release tags restore main-owned cache and create no tag-scoped Go cache.

## Non-goals

- asking developers to reduce Agent or worktree concurrency;
- reserving 64 GiB of physical disk space;
- managing pnpm, Docker, browser, IDE or Agent session caches in this change;
- preserving cache contents as durable data or backing them up;
- providing per-worktree cache knobs, user-configurable thresholds or manual cleanup schedules;
- operating a daemon, cache server or GOCACHEPROG;
- guaranteeing successful builds when both the shared APFS Container and the bounded cache Volume lack writable capacity.
