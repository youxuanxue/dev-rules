# Twin Owner Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch all host integrations to the installed Twin product, remove Twin ownership from `dev-rules` and `agent-skills`, and prove no legacy dependency or compatibility path remains.

**Architecture:** First convert the global Cursor skill root into a real additive registry with per-owner links. Then install Twin's own links, verify the new path, and delete old runtime, skill, contract, documentation, and gate ownership in separate repository commits.

**Tech Stack:** Bash, Python 3.9+, Git, existing dev-rules verification scripts, Twin `setup`/`doctor`/`contract`, GitHub CLI.

**Spec:** `/Users/feng/Codes/dev-rules/docs/approved/remove-twin-product-ownership.md`

## Global Constraints

- Execute this plan only after the independent-repository plan completion gate passes.
- Use `git-worktree-submodule` for isolated worktrees in `dev-rules` and `agent-skills`; never edit their dirty main checkouts.
- `~/.cursor/skills` becomes a real additive registry; `~/.claude/skills` remains a symlink to it.
- dev-rules owns only links whose source is its configured `agent-skills` checkout.
- Twin owns only links whose source is `~/.twin/skills/twin`.
- Neither reconciler may replace or delete foreign symlinks, real files, or real directories.
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

- [ ] **Step 2: Add a failing additive-registry integration test to `verify-rules.sh`**

The temporary-HOME fixture must start with the legacy chain:

```bash
ln -s "$test_agent_skills" "$test_canonical/.cursor/skills"
ln -s "$test_canonical/.cursor/skills" "$test_home/.cursor/skills"
ln -s "$test_home/.cursor/skills" "$test_home/.claude/skills"
```

Create an agent-owned `demo/SKILL.md`, a foreign `~/.cursor/skills/twin` link target under `~/.twin`, and a user-owned real directory `~/.cursor/skills/local-user`. After `sync.sh`, assert:

```bash
test -d "$test_home/.cursor/skills"
test ! -L "$test_home/.cursor/skills"
test "$(readlink "$test_home/.cursor/skills/demo")" = "$test_agent_skills/demo"
test "$(readlink "$test_home/.cursor/skills/twin")" = "$test_home/.twin/skills/twin"
test -d "$test_home/.cursor/skills/local-user"
test "$(readlink "$test_home/.claude/skills")" = "$test_home/.cursor/skills"
```

- [ ] **Step 3: Run the focused verifier and observe failure**

Run: `./verify-rules.sh`

Expected: FAIL because current sync requires `~/.cursor/skills` to remain a whole-directory symlink.

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

Expected: PASS, including preservation of Twin-owned and user-owned entries.

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

- [ ] **Step 3: Install and verify the Twin-owned links**

Run:

```bash
uv tool install --editable /Users/feng/Codes/twin --force
twin setup
twin setup --check
twin doctor --json
twin contract --json
```

Expected: every `twin` host link points directly to `/Users/feng/.twin/skills/twin`; doctor is healthy and contains no dev-rules check.

- [ ] **Step 4: Prove the installed path, not the old launcher, is executing**

Run:

```bash
command -v twin
python3 -c 'import shutil; print(shutil.which("twin"))'
twin --help
```

Expected: the executable belongs to the uv tool installation and help contains no `scaffold`, `bootstrap`, `next`, or `watch`.

- [ ] **Step 5: Run one disposable target-repository smoke cycle**

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
```

Expected: PASS. The only tracked Twin references are the approved decision record and these implementation plans.

- [ ] **Step 9: Commit the dev-rules owner removal**

```bash
git add -u
git add sync.sh verify-rules.sh scripts/preflight.sh scripts/gen_codex_agents.py global/CLAUDE.md README.md rules/dev-rules-convention.mdc .testing/user-stories/index.md docs/approved/remove-twin-product-ownership.md docs/superpowers/plans
git commit -m "refactor: remove Twin product ownership"
```

---

### Task 4: Remove the old agent-skills owner while preserving the PDF

**Files:**
- Delete: `/Users/feng/Codes/agent-skills/twin/SKILL.md`
- Delete: `/Users/feng/Codes/agent-skills/twin/agents/openai.yaml`
- Move outside Git repositories: `/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf`
- Modify if present: `/Users/feng/Codes/agent-skills/README.md`

**Interfaces:**
- Preserves: all unrelated tracked changes and untracked skills in the dirty main checkout
- Removes: the old Twin skill owner

- [ ] **Step 1: Record and verify the PDF checksum**

Run:

```bash
shasum -a 256 "/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
mkdir -p "/Users/feng/Documents/Twin Research"
```

Keep the checksum in the execution report, not in a repository file.

- [ ] **Step 2: Move the PDF to the non-code research archive**

Run:

```bash
mv "/Users/feng/Codes/agent-skills/twin/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf" \
  "/Users/feng/Documents/Twin Research/"
shasum -a 256 "/Users/feng/Documents/Twin Research/梁文锋看到的大模型下一个瓶颈，这个团队在 Agent 系统里也摸到了_删除最后两页.pdf"
```

Expected: the before/after hashes match. This move is recoverable and does not put the PDF into Twin history.

- [ ] **Step 3: Create an isolated agent-skills worktree**

Run:

```bash
python3 /Users/feng/.codex/skills/git-worktree-submodule/scripts/wtree.py create \
  --repo /Users/feng/Codes/agent-skills --no-open-workspace --json remove-twin-owner
```

Bind the returned workdir and run its exact session check. Confirm the dirty main checkout's unrelated changes remain untouched.

- [ ] **Step 4: Delete only the tracked Twin skill files**

Run in the isolated worktree:

```bash
git rm twin/SKILL.md twin/agents/openai.yaml
rg -n 'agent-skills/twin|\btwin\b' README.md . --glob '!twin/**' --glob '!*.pdf'
```

Remove only stale Twin index entries found by the scan. Do not stage unrelated files.

- [ ] **Step 5: Verify the installed Twin still works before committing**

Run:

```bash
twin setup --check
twin doctor --json
twin contract --json
```

Expected: PASS because active host links point to `~/.twin`, not the agent-skills worktree.

- [ ] **Step 6: Commit the old skill removal**

```bash
git add twin/SKILL.md twin/agents/openai.yaml README.md
git commit -m "refactor: remove the old Twin skill owner"
```

If README did not change, omit it from `git add`.

---

### Task 5: Prove the cutover and publish the coordinated repository changes

**Files:**
- No new product files
- PR descriptions for `dev-rules` and `agent-skills`

**Interfaces:**
- Consumes: installed Twin, cleaned dev-rules, cleaned agent-skills
- Produces: evidence that each repository can release independently

- [ ] **Step 1: Run cross-repository ownership scans**

Run:

```bash
rg -n --hidden '\$DEV_RULES|scripts\.twin|global/bin/twin|schemas/twin\.|agent-skills/twin|\.claude/twin-active-workspaces' \
  /Users/feng/Codes/twin \
  /Users/feng/Codes/dev-rules-wt-remove-twin-product-owner \
  /Users/feng/Codes/agent-skills-wt-remove-twin-owner
```

Expected: no live implementation or compatibility matches. Approved decision/plan prose may match and must be reviewed manually rather than deleted.

- [ ] **Step 2: Re-run all repository gates freshly**

Run:

```bash
cd /Users/feng/Codes/twin && bash scripts/preflight.sh
cd /Users/feng/Codes/dev-rules-wt-remove-twin-product-owner && ./scripts/preflight.sh
cd /Users/feng/Codes/agent-skills-wt-remove-twin-owner && git diff --check
```

Also run any agent-skills repository-specific verification command documented in its root instructions.

- [ ] **Step 3: Run the clean-home Twin smoke after old owners are absent from the execution path**

Run: `cd /Users/feng/Codes/twin && bash scripts/smoke-clean-home.sh`

Expected: PASS with `PYTHONPATH` empty and no source-checkout lookup.

- [ ] **Step 4: Verify legacy surfaces fail clearly**

Run:

```bash
! twin scaffold "legacy"
! twin bootstrap --help
! python3 -m scripts.twin --help
test ! -e /Users/feng/Codes/dev-rules/global/bin/twin
test ! -e /Users/feng/Codes/agent-skills/twin/SKILL.md
```

Expected: every legacy path fails or is absent; none forwards to the new product.

- [ ] **Step 5: Push branches and open separate Chinese PRs**

Push the dev-rules and agent-skills branches. Each PR body contains `摘要`, `风险`, `验证`, and `提交`, generated from every commit on that branch. Do not merge either PR without explicit user authorization.

- [ ] **Step 6: After both PRs merge, synchronize and verify live links once more**

Run:

```bash
cd /Users/feng/Codes/dev-rules && ./sync.sh --pull
twin setup
cd /Users/feng/Codes/dev-rules && ./sync.sh --check
twin setup --check
twin doctor --json
```

Expected: dev-rules reports only its own skill links healthy; Twin reports only its own links healthy; neither reports ownership of the other's entries.

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
