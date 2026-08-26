# Twin Owner Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch all host integrations to the installed Twin product, remove Twin ownership from `dev-rules` and `agent-skills`, and prove no legacy dependency or compatibility path remains.

**Architecture:** First convert the global Cursor skill root into a real additive registry with per-owner links. Transfer the existing Twin entries through one verified one-time handoff that normal reconcilers never perform. Then merge dev-rules product/navigation removal, update the agent-skills dev-rules submodule and generated AGENTS navigation, remove the old skill owner, and resume normal reconciliation only after both canonical checkouts contain the merged removals.

**Tech Stack:** Bash, Python 3.9+, Git, existing dev-rules verification scripts, Twin `setup`/`doctor`/`contract`, GitHub CLI.

**Spec:** `/Users/feng/Codes/dev-rules/docs/approved/remove-twin-product-ownership.md`

## Global Constraints

- Execute this plan only after the independent-repository plan completion gate passes.
- Use `git-worktree-submodule` for isolated worktrees in `dev-rules` and `agent-skills`; never edit their dirty main checkouts.
- `~/.cursor/skills` becomes a real additive registry; `~/.claude/skills` remains a symlink to it.
- dev-rules owns only links whose source is its configured `agent-skills` checkout.
- Twin owns only links whose source is `~/.twin/skills/twin`.
- Neither reconciler may replace or delete foreign symlinks, real files, or real directories.
- The one-time Twin handoff verifies and removes only links that exactly target the configured old `agent-skills/twin`, then immediately invokes `twin setup`; it is not part of either normal reconciler.
- After the handoff, do not run dev-rules sync until `agent-skills/twin` has been removed and both canonical checkouts have been updated. If sync runs early, it must fail on the foreign Twin collision without mutating it.
- Remove old Twin behavior rather than forwarding, deprecating, migrating, or silently reading it.
- Preserve the untracked Liang Wenfeng PDF by moving it outside all code repositories before removing the old directory.
- Keep `/Users/feng/Codes/dev-rules/docs/approved/remove-twin-product-ownership.md` as the migration decision record.
- Do not integrate DeepSeek Harness in this plan.

---

## File Structure Changes

`dev-rules` deletes:

```text
scripts/twin/
schemas/twin.*.schema.json
personas/supervisor-persona.md
personas/worker-persona.md
templates/twin-workspace/
global/bin/twin
docs/twin-*.md
docs/approved/twin-*.md
.testing/user-stories/stories/US-088-twin-universal-runtime.md
.testing/user-stories/verify_quality.py
```

`dev-rules` modifies the generic skill registry, constitution, README, contract generator, AGENTS generator, preflight, verification, user-story index, hooks, and any remaining Twin references.

`agent-skills` deletes tracked `twin/SKILL.md` and `twin/agents/openai.yaml`. The PDF moves to:

```text
/Users/feng/Documents/Twin Research/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf
```

---

### Task 1: Convert dev-rules home skills into an owner-safe additive registry

**Files:**
- Modify: `/Users/feng/Codes/dev-rules/sync.sh`
- Modify: `/Users/feng/Codes/dev-rules/verify-rules.sh`
- Modify: `/Users/feng/Codes/dev-rules/rules/dev-rules-convention.mdc`
- Modify: `/Users/feng/Codes/dev-rules/README.md`

**Interfaces:**
- Produces: `ensure_additive_skill_root <path>` shell function
- Produces: `reconcile_owned_skill_links <source> <destination> <label> [reserved]` shell function
- Preserves: `link_skills_dir` for project-local `.claude/skills -> ../.cursor/skills` only

- [ ] **Step 1: Create an isolated dev-rules worktree after the design branch is merged**

Run from `/Users/feng/Codes/dev-rules` using the installed helper:

```bash
python3 /Users/feng/.codex/skills/git-worktree-submodule/scripts/wtree.py create \
  --repo /Users/feng/Codes/dev-rules --no-open-workspace --json twin-owner-cutover
```

Bind the returned `session_switch.workdir`, then run its exact `session-check` command before reading or editing relative paths.

- [ ] **Step 2: Add phased failing additive-registry integration tests to `verify-rules.sh`**

Phase A materializes a pure legacy source. Create only an agent-owned `demo/SKILL.md`, then create the legacy chain:

```bash
ln -s "$test_agent_skills" "$test_canonical/.cursor/skills"
ln -s "$test_canonical/.cursor/skills" "$test_home/.cursor/skills"
ln -s "$test_home/.cursor/skills" "$test_home/.claude/skills"
```

Run `sync.sh` before creating any foreign or user-owned child. Assert:

```bash
test -d "$test_home/.cursor/skills"
test ! -L "$test_home/.cursor/skills"
test "$(readlink "$test_home/.cursor/skills/demo")" = "$test_agent_skills/demo"
test "$(readlink "$test_home/.claude/skills")" = "$test_home/.cursor/skills"
```

Phase B starts from that now-real registry. Create a fake foreign Twin target under `$test_home/.twin/skills/twin`, link `~/.cursor/skills/twin` to it, create the real directory `~/.cursor/skills/local-user`, record the Twin link target, and rerun reconciliation. Assert:

```bash
test -d "$test_home/.cursor/skills"
test ! -L "$test_home/.cursor/skills"
test "$(readlink "$test_home/.cursor/skills/demo")" = "$test_agent_skills/demo"
test "$(readlink "$test_home/.cursor/skills/twin")" = "$test_home/.twin/skills/twin"
test -d "$test_home/.cursor/skills/local-user"
test ! -L "$test_home/.cursor/skills/local-user"
test "$(readlink "$test_home/.claude/skills")" = "$test_home/.cursor/skills"
```

Use a separate temporary HOME for a same-name collision test. Put `collision/SKILL.md` in the configured source and a `~/.cursor/skills/collision` symlink to a foreign target. Reconciliation must exit non-zero, name the ownership conflict, and leave the foreign target byte-for-byte unchanged. This fixture does not simulate the real Twin transfer; the installed-Twin handoff test belongs to Task 2.

- [ ] **Step 3: Run the focused verifier and observe failure**

Run: `./verify-rules.sh`

Expected: Phase A FAIL because current sync requires `~/.cursor/skills` to remain a whole-directory symlink. The collision case must also fail closed rather than replacing the foreign entry.

- [ ] **Step 4: Implement safe legacy-root materialization**

`ensure_additive_skill_root()` follows these rules:

```text
missing path                         -> mkdir
real directory                      -> preserve
symlink to configured skill source  -> unlink symlink, mkdir
any other symlink                   -> fail without mutation
real file                           -> fail without mutation
```

Use `unlink` only after comparing the exact legacy target. Never recursively delete a skill root.

- [ ] **Step 5: Replace global whole-root linking with owned per-skill reconciliation**

`reconcile_owned_skill_links()` must create/update only desired links from the passed source. It may remove a stale destination link only when its resolved or textual target is inside that same source directory. A collision with a foreign link or real path fails and names the owner conflict.

Use the `agent-skills` source directly for all dev-rules-owned consumers:

```bash
reconcile_owned_skill_links "$HOME_CURSOR_SKILLS_SRC" "$CURSOR_SKILLS" "cursor-skills"
reconcile_owned_skill_links "$HOME_CURSOR_SKILLS_SRC" "$CODEX_SKILLS" "codex-skills" "$CODEX_SKILL_RESERVED"
reconcile_owned_skill_links "$HOME_CURSOR_SKILLS_SRC" "$ANTIGRAVITY_SKILLS" "antigravity-skills" "$ANTIGRAVITY_SKILL_RESERVED"
```

Do not scan the mixed `~/.cursor/skills` registry when reconciling Codex or Antigravity; otherwise dev-rules would adopt Twin-owned entries.

- [ ] **Step 6: Update drift checks and conventions**

Replace `check_home_cursor_skills_drift()` with a check that requires a real directory and verifies only the links derived from `HOME_CURSOR_SKILLS_SRC`. Keep the Claude whole-directory link check. Update prose so `.cursor/skills` is the authoring source inside projects, while the home path is an additive consumer registry rather than a global source owner.

- [ ] **Step 7: Run verification and preflight**

Run:

```bash
./verify-rules.sh
./scripts/preflight.sh
```

Expected: PASS for Phase A materialization and Phase B preservation; the separate same-name source/foreign collision exits non-zero and preserves the foreign target.

- [ ] **Step 8: Commit the additive registry**

```bash
git add sync.sh verify-rules.sh rules/dev-rules-convention.mdc README.md
git commit -m "refactor(sync): make global skill registry additive"
```

- [ ] **Step 9: Push, open the additive-registry PR, and wait for explicit merge authorization**

Run the fresh preflight again immediately before push. Open a Chinese PR containing `摘要`, `风险`, `验证`, and `提交`. Stop until the user authorizes merge and the PR is merged; the live cutover must not depend on an unmerged worktree script.

---

### Task 2: Cut the live machine over to the Twin-owned installation

**Files:**
- Runtime state only: `/Users/feng/.cursor/skills`, `/Users/feng/.claude/skills`, `/Users/feng/.codex/skills/twin`, `/Users/feng/.gemini/antigravity-cli/skills/twin`, `/Users/feng/.twin`

**Interfaces:**
- Consumes: the new dev-rules additive registry
- Consumes: `twin setup`, `twin setup --check`, `twin doctor --json`, `twin contract --json`

- [ ] **Step 1: Capture current link targets without mutating them**

Run:

```bash
for path in \
  /Users/feng/.cursor/skills \
  /Users/feng/.claude/skills \
  /Users/feng/.codex/skills/twin \
  /Users/feng/.gemini/antigravity-cli/skills/twin; do
  if [ -L "$path" ]; then printf '%s -> %s\n' "$path" "$(readlink "$path")"; else printf '%s : non-symlink or missing\n' "$path"; fi
done
```

Save the output in the implementation log, not in memory or repository state.

- [ ] **Step 2: Run the new dev-rules sync to materialize the additive root**

Run from the updated canonical checkout after the additive-registry PR merges:

```bash
cd /Users/feng/Codes/dev-rules
git pull --ff-only origin main
./sync.sh
./sync.sh --check
```

Expected: `~/.cursor/skills` is a real directory containing per-skill links, and `~/.claude/skills` points to that directory.

- [ ] **Step 3: Install the Twin console without asking it to adopt the old links**

Run:

```bash
uv tool install --editable /Users/feng/Codes/twin --force
command -v twin
twin --help
```

Expected: the console comes from the uv tool installation. Do not run `twin setup` directly yet: the verified handoff transaction owns the only permitted removal of the old links.

- [ ] **Step 4: Create and test the one-time ownership-handoff transaction**

Create `/tmp/twin-skill-owner-handoff.sh` as an execution artifact, not a repository file. It accepts `<old-twin-skill> <home> <twin-bin>` and implements this exact transaction:

```text
1. Require <old-twin-skill> to be the configured old skill directory.
2. Require <home>/.cursor/skills to be a real directory and <home>/.claude/skills to be a symlink whose exact target is <home>/.cursor/skills.
3. Preflight all three direct entries before mutating anything:
   <home>/.cursor/skills/twin
   <home>/.codex/skills/twin
   <home>/.gemini/antigravity-cli/skills/twin
   Each must be a symlink and `readlink` must equal <old-twin-skill> exactly.
4. Also require `readlink <home>/.claude/skills/twin` to equal <old-twin-skill>; this is the Cursor entry viewed through the shared Claude root and is not unlinked separately.
5. Install an ERR/INT/TERM trap. Unlink only the three preflighted direct entries, then immediately run `HOME=<home> <twin-bin> setup` with no intervening dev-rules command.
6. Run `HOME=<home> <twin-bin> setup --check`, `doctor --json`, and `contract --json`. Verify all three direct entries and the Claude view now resolve to <home>/.twin/skills/twin.
7. On failure after unlinking, run `HOME=<home> <twin-bin> uninstall` to remove only any partially created Twin-owned links, then recreate an old link only where the path is absent. Never overwrite a path created by another owner during rollback. Exit non-zero and report every unrestored collision.
8. Remove the trap only after every new-owner check passes.
```

Exercise the exact script first with a temporary HOME, a fake configured `agent-skills/twin` directory, the full old three-link topology, the real installed `twin` console, and `~/.claude/skills -> ~/.cursor/skills`. Assert that a wrong target aborts before any unlink, then rebuild the fixture and assert the valid transaction replaces every old link with the Twin-owned target.

- [ ] **Step 5: Execute the live ownership handoff and prove normal reconcilers cannot adopt across the boundary**

Run:

```bash
/tmp/twin-skill-owner-handoff.sh \
  /Users/feng/Codes/agent-skills/twin \
  /Users/feng \
  "$(command -v twin)"

if cd /Users/feng/Codes/dev-rules && ./sync.sh --check; then
  echo "expected dev-rules check to reject the still-declared foreign twin entry" >&2
  exit 1
fi

twin setup --check
twin doctor --json
twin contract --json
twin --help
```

Expected: every active Twin host entry points to `/Users/feng/.twin/skills/twin`; dev-rules check mode reports an owner collision while its configured `agent-skills` source still contains `twin`, leaves all Twin-owned targets unchanged, and does not adopt them. From this point until Task 4 merges, do not run mutating dev-rules sync or its scheduled fan-out. Twin's reconciler continues to mutate only links under `~/.twin` ownership.

- [ ] **Step 6: Run one disposable target-repository smoke cycle**

Use the fake provider or a zero-cost deterministic provider fixture to run `start`, submit the returned plan action, `run`, `status`, and a human-gate `respond`. Do not use a production repository or paid provider for this cutover proof.

Expected: all runtime artifacts are under `~/.twin/workspaces`; the disposable target repository has no project-local `.twin` state.

---

### Task 3: Remove Twin product ownership from dev-rules

**Files:**
- Delete: `/Users/feng/Codes/dev-rules/scripts/twin/`
- Delete: `/Users/feng/Codes/dev-rules/schemas/twin.*.schema.json`
- Delete: `/Users/feng/Codes/dev-rules/personas/supervisor-persona.md`
- Delete: `/Users/feng/Codes/dev-rules/personas/worker-persona.md`
- Delete: `/Users/feng/Codes/dev-rules/templates/twin-workspace/`
- Delete: `/Users/feng/Codes/dev-rules/global/bin/twin`
- Delete: `/Users/feng/Codes/dev-rules/docs/twin-cao-operator-guide.md`
- Delete: `/Users/feng/Codes/dev-rules/docs/twin-design.md`
- Delete: `/Users/feng/Codes/dev-rules/docs/twin-supervisor-runbook.md`
- Delete: `/Users/feng/Codes/dev-rules/docs/twin-universal-command.md`
- Delete: `/Users/feng/Codes/dev-rules/docs/approved/twin-runtime-reentry-watchdog.md`
- Delete: `/Users/feng/Codes/dev-rules/docs/approved/twin-team-runtime-architecture.md`
- Delete: `/Users/feng/Codes/dev-rules/docs/approved/twin-universal-host-supervisor.md`
- Delete: `/Users/feng/Codes/dev-rules/.testing/user-stories/stories/US-088-twin-universal-runtime.md`
- Delete: `/Users/feng/Codes/dev-rules/.testing/user-stories/verify_quality.py`
- Delete if Twin-only: `/Users/feng/Codes/dev-rules/.github/workflows/agent-contract.yml`
- Modify or delete: `/Users/feng/Codes/dev-rules/scripts/export_agent_contract.py`
- Modify or delete: `/Users/feng/Codes/dev-rules/docs/agent_contract.notes.md`
- Modify or delete: `/Users/feng/Codes/dev-rules/docs/agent_integration.md`
- Modify: `/Users/feng/Codes/dev-rules/scripts/gen_codex_agents.py`
- Modify: `/Users/feng/Codes/dev-rules/scripts/preflight.sh`
- Modify: `/Users/feng/Codes/dev-rules/verify-rules.sh`
- Modify: `/Users/feng/Codes/dev-rules/global/CLAUDE.md`
- Modify: `/Users/feng/Codes/dev-rules/README.md`
- Modify: `/Users/feng/Codes/dev-rules/rules/dev-rules-convention.mdc`
- Modify: `/Users/feng/Codes/dev-rules/.testing/user-stories/index.md`
- Modify: `/Users/feng/Codes/dev-rules/global/hooks/skill-reflect.sh`
- Inspect and modify only if matched: remaining files returned by the Twin residue scan

**Interfaces:**
- Preserves: generic project policy, generic skill distribution, `xj-review`, project preflight, and this approved migration record
- Removes: every Twin runtime, product, schema, contract, story, and compatibility owner

- [ ] **Step 1: Create a fresh dev-rules removal worktree from the merged additive-registry main**

Run:

```bash
python3 /Users/feng/.codex/skills/git-worktree-submodule/scripts/wtree.py create \
  --repo /Users/feng/Codes/dev-rules --no-open-workspace --json remove-twin-product-owner
```

Bind the returned workdir and run its exact session check before edits.

- [ ] **Step 2: Add a failing Twin ownership residue gate**

Add a `verify-rules.sh` section that scans tracked files while excluding only:

```text
docs/approved/remove-twin-product-ownership.md
docs/superpowers/plans/2026-08-26-twin-independent-repository.md
docs/superpowers/plans/2026-08-26-twin-owner-cutover.md
```

Fail on product-owner patterns such as `scripts/twin`, `schemas/twin.`, `global/bin/twin`, `agent-skills/twin`, `twin doctor`, `twin run`, `supervisor-persona`, `worker-persona`, or generated Twin contract headings.

- [ ] **Step 3: Run the residue gate and observe failure**

Run: `./verify-rules.sh`

Expected: FAIL and enumerate the current Twin-owned files.

- [ ] **Step 4: Remove tracked Twin assets explicitly**

Use `git rm` with the exact paths listed in this task. Do not use broad filesystem deletion, globs rooted above the repository, or cleanup commands that can touch untracked user files.

- [ ] **Step 5: Remove Twin-specific generator and gate branches**

Delete `_twin_cli_rows()`, Twin schema enumeration, Twin sections in generated Agent integration, Twin-specific AGENTS navigation, fixture/story checks, persona checks, worktree selftests, launcher documentation, and special hook comments/branches. If `scripts/export_agent_contract.py` and its two docs have no non-Twin responsibility after removal, delete all three rather than inventing a new purpose.

Keep the generic additive skill reconciler from Task 1. Do not add a special exclusion named `twin`; ownership isolation must come from source-root reconciliation, not product-name conditionals.

- [ ] **Step 6: Update the global constitution and conventions**

Remove Twin from the dev-rules command/skill table and any statement that dev-rules distributes or locates Twin. If navigation is useful, keep at most one product-neutral sentence: independently installed tools own their own skills and contracts. Do not advertise Twin as a dev-rules component.

- [ ] **Step 7: Regenerate or remove derived artifacts**

Run the remaining generators required by the repository. If Agent integration was Twin-only, remove its workflow entry and generated files. Regenerate project AGENTS fixtures so the managed block contains no Twin-specific navigation.

- [ ] **Step 8: Run the residue scan and full preflight**

Run:

```bash
./verify-rules.sh
./scripts/preflight.sh
git diff --check
for legacy in \
  scripts/twin \
  schemas/twin.goal.schema.json \
  schemas/twin.human_response.schema.json \
  schemas/twin.plan.schema.json \
  schemas/twin.research.schema.json \
  schemas/twin.run.schema.json \
  schemas/twin.supervisor_review.schema.json \
  schemas/twin.supervisor_state.schema.json \
  personas/supervisor-persona.md \
  personas/worker-persona.md \
  templates/twin-workspace \
  global/bin/twin \
  docs/twin-cao-operator-guide.md \
  docs/twin-design.md \
  docs/twin-supervisor-runbook.md \
  docs/twin-universal-command.md \
  docs/approved/twin-runtime-reentry-watchdog.md \
  docs/approved/twin-team-runtime-architecture.md \
  docs/approved/twin-universal-host-supervisor.md \
  .testing/user-stories/stories/US-088-twin-universal-runtime.md \
  .testing/user-stories/verify_quality.py; do
  test ! -e "$legacy"
done
```

Expected: PASS in the dev-rules removal worktree. These are pre-merge worktree checks only; do not infer anything yet about `/Users/feng/Codes/dev-rules` canonical main.

- [ ] **Step 9: Commit the dev-rules owner removal**

```bash
git add -u
git add sync.sh verify-rules.sh scripts/preflight.sh scripts/gen_codex_agents.py global/CLAUDE.md README.md rules/dev-rules-convention.mdc .testing/user-stories/index.md docs/approved/remove-twin-product-ownership.md docs/superpowers/plans
git commit -m "refactor: remove Twin product ownership"
```

- [ ] **Step 10: Merge dev-rules removal before generating the agent-skills change**

Run the fresh preflight immediately before push, push the dev-rules removal branch, and open its Chinese PR with `摘要`, `风险`, `验证`, and `提交`. Stop for explicit merge authorization. After it merges, update only the canonical dev-rules checkout:

```bash
git -C /Users/feng/Codes/dev-rules pull --ff-only origin main
git -C /Users/feng/Codes/dev-rules rev-parse HEAD | tee /tmp/twin-dev-rules-removal.sha
```

Require `/tmp/twin-dev-rules-removal.sha` to contain exactly one 40-character lowercase Git object ID and keep it with the execution log for Task 4. Do not run `sync.sh` yet: canonical `agent-skills` still declares the old Twin skill, so reconciliation must remain paused until its removal merges.

---

### Task 4: Remove the old agent-skills owner while preserving the PDF

**Files:**
- Delete: `/Users/feng/Codes/agent-skills/twin/SKILL.md`
- Delete: `/Users/feng/Codes/agent-skills/twin/agents/openai.yaml`
- Move outside Git repositories: `/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf`
- Modify gitlink: `/Users/feng/Codes/agent-skills/dev-rules`
- Regenerate: `/Users/feng/Codes/agent-skills/AGENTS.md`
- Modify if present: `/Users/feng/Codes/agent-skills/README.md`

**Interfaces:**
- Preserves: all unrelated tracked changes and untracked skills in the dirty main checkout
- Removes: the old Twin skill owner
- Consumes: the exact merged dev-rules removal SHA from Task 3
- Produces: `agent-skills/dev-rules` and the generated `agent-skills/AGENTS.md` from the same merged dev-rules contract

- [ ] **Step 1: Record the PDF checksum and classify the exact destination before moving**

Run:

```bash
source_pdf="/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
archive_dir="/Users/feng/Documents/Twin Research"
archive_pdf="$archive_dir/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
source_sha="$(shasum -a 256 "$source_pdf" | awk '{print $1}')"
mkdir -p "/Users/feng/Documents/Twin Research"
printf 'source %s\n' "$source_sha"

if [ -e "$archive_pdf" ] || [ -L "$archive_pdf" ]; then
  test -f "$archive_pdf" && test ! -L "$archive_pdf"
  archive_sha="$(shasum -a 256 "$archive_pdf" | awk '{print $1}')"
  printf 'archive %s\n' "$archive_sha"
fi
```

Keep the checksum and selected branch in the execution report, not in a repository file. A symlink or non-regular archive destination is an ownership conflict and stops the task.

- [ ] **Step 2: Execute exactly one non-overwriting archive branch**

If the destination is absent, move and verify:

```bash
source_pdf="/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
archive_pdf="/Users/feng/Documents/Twin Research/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
source_sha="$(shasum -a 256 "$source_pdf" | awk '{print $1}')"
test ! -e "$archive_pdf" && test ! -L "$archive_pdf"
mv "$source_pdf" "$archive_pdf"
test "$(shasum -a 256 "$archive_pdf" | awk '{print $1}')" = "$source_sha"
```

If the destination exists with the same checksum, preserve it and quarantine the verified duplicate source recoverably:

```bash
source_pdf="/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
archive_pdf="/Users/feng/Documents/Twin Research/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
source_sha="$(shasum -a 256 "$source_pdf" | awk '{print $1}')"
archive_sha="$(shasum -a 256 "$archive_pdf" | awk '{print $1}')"
test "$archive_sha" = "$source_sha"
quarantine_dir="$(mktemp -d "/Users/feng/.Trash/twin-pdf-duplicate.XXXXXX")"
mv "$source_pdf" "$quarantine_dir/"
test "$(shasum -a 256 "$quarantine_dir/$(basename "$source_pdf")" | awk '{print $1}')" = "$source_sha"
printf 'quarantine %s\n' "$quarantine_dir"
```

Keep that quarantine path intact through this task and record it in the execution report; do not unlink or overwrite either copy. If the destination checksum differs, stop for human decision before any move. Never call `mv` with an existing destination of unknown or different content.

- [ ] **Step 3: Create an isolated agent-skills worktree**

Run:

```bash
python3 /Users/feng/.codex/skills/git-worktree-submodule/scripts/wtree.py create \
  --repo /Users/feng/Codes/agent-skills --no-open-workspace --json remove-twin-owner
```

Bind the returned workdir and run its exact session check. Confirm the dirty main checkout's unrelated changes remain untouched.

- [ ] **Step 4: Update the dev-rules submodule and regenerate AGENTS from the merged owner removal**

Run in the isolated agent-skills worktree, using the exact merged SHA recorded in Task 3:

```bash
dev_rules_removal_sha="$(tr -d '\n' < /tmp/twin-dev-rules-removal.sha)"
printf '%s\n' "$dev_rules_removal_sha" | grep -Eq '^[0-9a-f]{40}$'
git -C dev-rules fetch origin main
git -C dev-rules cat-file -e "$dev_rules_removal_sha^{commit}"
git -C dev-rules checkout --detach "$dev_rules_removal_sha"
test "$(git -C dev-rules rev-parse HEAD)" = "$dev_rules_removal_sha"
python3 dev-rules/scripts/gen_codex_agents.py --project "$PWD"
python3 dev-rules/scripts/gen_codex_agents.py --project "$PWD" --check
! rg -n 'twin.*dev-rules/docs/agent_integration|dev-rules.*twin|shared twin' AGENTS.md
```

Expected: the gitlink points to the merged dev-rules removal commit and the generated managed block no longer advertises Twin through dev-rules. Do not hand-edit the managed block.

- [ ] **Step 5: Delete only the tracked Twin skill files**

Run in the isolated worktree:

```bash
git rm twin/SKILL.md twin/agents/openai.yaml
rg -n 'agent-skills/twin|\btwin\b' README.md . --glob '!twin/**' --glob '!*.pdf'
```

Remove only stale Twin index entries found by the scan. Do not stage unrelated files.

- [ ] **Step 6: Verify the installed Twin and pre-merge removal worktree**

Run:

```bash
twin setup --check
twin doctor --json
twin contract --json
python3 dev-rules/scripts/gen_codex_agents.py --project "$PWD" --check
git diff --check
test ! -e twin/SKILL.md
test ! -e twin/agents/openai.yaml
```

Expected: PASS because active host links point to `~/.twin`, not the agent-skills worktree. The absence checks apply to this removal worktree only; canonical absence is checked after merge in Task 5.

- [ ] **Step 7: Commit the old skill removal, submodule pointer, and generated navigation together**

```bash
git add -u twin/SKILL.md twin/agents/openai.yaml
git add dev-rules AGENTS.md
git add README.md
git commit -m "refactor: remove the old Twin skill owner"
```

If README did not change, omit it from `git add`. Before committing, require `git diff --cached --name-only` to contain `dev-rules`, `AGENTS.md`, and the two deleted Twin paths, plus README only when intentionally changed.

- [ ] **Step 8: Push and merge the agent-skills removal only after dev-rules removal is merged**

Run the repository-specific verification documented by agent-skills, push the branch, and open its Chinese PR with `摘要`, `风险`, `验证`, and `提交`. Stop for explicit merge authorization. The PR must show the dev-rules gitlink and generated `AGENTS.md` in the same commit as the old Twin owner removal.

---

### Task 5: Update canonical checkouts, resume reconciliation, and prove the cutover

**Files:**
- No new product files
- Canonical `dev-rules` and `agent-skills` checkouts

**Interfaces:**
- Consumes: installed Twin, cleaned dev-rules, cleaned agent-skills
- Produces: post-merge evidence that each repository can release independently

- [ ] **Step 1: Fast-forward both canonical checkouts after both removal PRs merge**

Run only after Task 3's dev-rules PR and Task 4's agent-skills PR are merged:

```bash
git -C /Users/feng/Codes/dev-rules pull --ff-only origin main
git -C /Users/feng/Codes/agent-skills pull --ff-only origin main
git -C /Users/feng/Codes/agent-skills submodule update --init dev-rules
expected_dev_rules_sha="$(git -C /Users/feng/Codes/agent-skills ls-tree HEAD dev-rules | awk '{print $3}')"
test "$(git -C /Users/feng/Codes/agent-skills/dev-rules rev-parse HEAD)" = "$expected_dev_rules_sha"
git -C /Users/feng/Codes/dev-rules merge-base --is-ancestor \
  "$expected_dev_rules_sha" "$(git -C /Users/feng/Codes/dev-rules rev-parse HEAD)"
```

Expected: both canonical main checkouts contain their merged removal commits, and the agent-skills submodule is materialized at its committed dev-rules gitlink, which is the merged removal SHA used to generate `AGENTS.md` (or an ancestor of a subsequently advanced canonical dev-rules main).

- [ ] **Step 2: Repeat canonical legacy-absence checks after merge**

Run against canonical paths, not the old removal worktrees:

```bash
for legacy in \
  scripts/twin \
  schemas/twin.goal.schema.json \
  schemas/twin.human_response.schema.json \
  schemas/twin.plan.schema.json \
  schemas/twin.research.schema.json \
  schemas/twin.run.schema.json \
  schemas/twin.supervisor_review.schema.json \
  schemas/twin.supervisor_state.schema.json \
  personas/supervisor-persona.md \
  personas/worker-persona.md \
  templates/twin-workspace \
  global/bin/twin \
  docs/twin-cao-operator-guide.md \
  docs/twin-design.md \
  docs/twin-supervisor-runbook.md \
  docs/twin-universal-command.md \
  docs/approved/twin-runtime-reentry-watchdog.md \
  docs/approved/twin-team-runtime-architecture.md \
  docs/approved/twin-universal-host-supervisor.md \
  .testing/user-stories/stories/US-088-twin-universal-runtime.md \
  .testing/user-stories/verify_quality.py; do
  test ! -e "/Users/feng/Codes/dev-rules/$legacy"
done
test ! -e /Users/feng/Codes/agent-skills/twin/SKILL.md
test ! -e /Users/feng/Codes/agent-skills/twin/agents/openai.yaml
python3 /Users/feng/Codes/agent-skills/dev-rules/scripts/gen_codex_agents.py \
  --project /Users/feng/Codes/agent-skills --check
```

Expected: the same legacy paths proven absent in the pre-merge worktrees are now absent from canonical main, and canonical `agent-skills/AGENTS.md` matches the merged generator.

- [ ] **Step 3: Run cross-repository ownership scans**

Run:

```bash
rg -n --hidden '\$DEV_RULES|scripts\.twin|global/bin/twin|schemas/twin\.|agent-skills/twin|\.claude/twin-active-workspaces' \
  /Users/feng/Codes/twin \
  /Users/feng/Codes/dev-rules \
  /Users/feng/Codes/agent-skills
```

Expected: no live implementation or compatibility matches. Approved decision/plan prose may match and must be reviewed manually rather than deleted.

- [ ] **Step 4: Re-run all repository gates freshly**

Run:

```bash
cd /Users/feng/Codes/twin && bash scripts/preflight.sh
cd /Users/feng/Codes/dev-rules && ./scripts/preflight.sh
cd /Users/feng/Codes/agent-skills && git diff --check
```

Also run any agent-skills repository-specific verification command documented in its root instructions.

- [ ] **Step 5: Run the filesystem-isolated Twin smoke after old owners are absent from the execution path**

Run: `cd /Users/feng/Codes/twin && TWIN_REQUIRE_CONTAINER=1 bash scripts/preflight.sh`

Expected: PASS in the minimal Linux container with no dev-rules, agent-skills, or Twin source checkout mounted; the exact wheel's venv console script drives contract, doctor, and lifecycle smoke.

- [ ] **Step 6: Verify legacy surfaces fail clearly**

Run:

```bash
! twin scaffold "legacy"
! twin bootstrap --help
! python3 -m scripts.twin --help
test ! -e /Users/feng/Codes/dev-rules/global/bin/twin
test ! -e /Users/feng/Codes/agent-skills/twin/SKILL.md
```

Expected: every legacy path fails or is absent in canonical main; none forwards to the new product.

- [ ] **Step 7: Resume normal reconciliation and verify owner isolation**

Run:

```bash
cd /Users/feng/Codes/dev-rules && ./sync.sh
twin setup
cd /Users/feng/Codes/dev-rules && ./sync.sh --check
twin setup --check
twin doctor --json
```

Expected: dev-rules no longer desires a `twin` entry because canonical agent-skills no longer contains that source; it reports only its own skill links healthy. Twin reports only its own links healthy. Neither reconciler adopts or rewrites the other's entries.

---

## Cutover Completion Gate

The physical split is complete only when:

- Twin's clean-home smoke and repository preflight pass.
- dev-rules preflight passes with no Twin runtime, schema, product docs, skill distribution, contract export, or special gate.
- agent-skills contains no tracked Twin skill and its unrelated dirty/untracked content is intact.
- The PDF exists under `/Users/feng/Documents/Twin Research/` with the original checksum.
- All active Twin host links point to `~/.twin/skills/twin`.
- Legacy CLI and workspace paths fail clearly.
- The Twin, dev-rules, and agent-skills repositories can each change and publish without synchronized commits.
