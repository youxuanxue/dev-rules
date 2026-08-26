# Twin Independent Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/Users/feng/Codes/twin` as a self-contained, installable Twin product that runs without a `dev-rules` or `agent-skills` checkout.

**Architecture:** Package Twin as a Python application with a small public CLI, a provider-neutral domain kernel, injected runtime adapters, and an external `~/.twin` workspace store. Package schemas, personas, templates, and the host skill as installed data; expose their live contract through the CLI rather than source-tree paths.

**Tech Stack:** Python 3.9+, standard library runtime, `setuptools` packaging, `unittest`, JSON Schema documents, YAML-compatible Twin codec, Git CLI, Claude/Codex/Gemini CLI adapters, optional CAO HTTP adapter.

**Spec:** `/Users/feng/Codes/dev-rules/docs/approved/remove-twin-product-ownership.md`

## Global Constraints

- Initialize a new Git history; do not import commits from `dev-rules` or `agent-skills`.
- Create `youxuanxue/twin` as a private repository initially; changing visibility is a separate explicit action.
- Runtime code must not reference `$DEV_RULES`, a `dev-rules` path, an `agent-skills` path, or `python3 -m scripts.twin`.
- Runtime dependencies remain standard-library-only during the physical split.
- Public human commands are `start`, `run`, `status`, `respond`, `handoff`, and `doctor`; `contract --json` is the machine discovery surface.
- Hidden submission commands may exist only as token-bound machine protocol and must not appear in normal help.
- Runtime data lives under `~/.twin`; target repositories contain code changes and isolated worktrees, not Twin state.
- Do not read legacy project-local `.twin`, `~/.claude/twin-*`, old schemas, or old workspace pointers.
- Do not copy superseded Twin design documents, historical compatibility fixtures, or the untracked Liang Wenfeng PDF.
- Keep DeepSeek Harness behind a future adapter task; do not integrate it in this plan.
- Every state mutation is locked, atomic, revision-bound, and recorded in `events.jsonl` without sensitive response bodies.

---

## File Structure

```text
/Users/feng/Codes/twin/
├── .gitignore
├── LICENSE
├── README.md
├── pyproject.toml
├── schemas/
│   ├── twin.action.schema.json
│   ├── twin.goal.schema.json
│   ├── twin.plan.schema.json
│   ├── twin.run-evidence.schema.json
│   └── twin.state.schema.json
├── personas/
│   ├── supervisor.md
│   └── worker.md
├── skills/twin/
│   ├── SKILL.md
│   └── agents/openai.yaml
├── templates/workspace/
│   ├── goal.yaml
│   └── plan.yaml
├── src/twin/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── contract.py
│   ├── doctor.py
│   ├── errors.py
│   ├── paths.py
│   ├── resources.py
│   ├── schema.py
│   ├── setup.py
│   ├── yaml_codec.py
│   ├── domain/
│   │   ├── actions.py
│   │   ├── evidence.py
│   │   ├── plan.py
│   │   ├── service.py
│   │   └── state.py
│   ├── runtime/
│   │   ├── cao.py
│   │   ├── local_cli.py
│   │   ├── process.py
│   │   ├── protocols.py
│   │   └── worktree.py
│   └── storage/
│       ├── atomic.py
│       ├── events.py
│       ├── locks.py
│       └── workspaces.py
├── tests/
│   ├── __init__.py
│   ├── fixtures/fake_provider.py
│   ├── test_cli.py
│   ├── test_contract.py
│   ├── test_domain.py
│   ├── test_resources.py
│   ├── test_runtime.py
│   ├── test_setup.py
│   └── test_storage.py
└── scripts/
    ├── preflight.sh
    └── smoke-clean-home.sh
```

Top-level resource directories are the repository owners. `setuptools` installs them under `share/twin`; `twin.resources.ResourceCatalog` resolves only that installed data root or an explicitly injected test root.

---

### Task 1: Initialize the repository and prove installed-resource ownership

**Files:**
- Create: `/Users/feng/Codes/twin/pyproject.toml`
- Create: `/Users/feng/Codes/twin/.gitignore`
- Create: `/Users/feng/Codes/twin/LICENSE`
- Create: `/Users/feng/Codes/twin/README.md`
- Create: `/Users/feng/Codes/twin/src/twin/__init__.py`
- Create: `/Users/feng/Codes/twin/src/twin/__main__.py`
- Create: `/Users/feng/Codes/twin/src/twin/paths.py`
- Create: `/Users/feng/Codes/twin/src/twin/resources.py`
- Create: `/Users/feng/Codes/twin/tests/test_resources.py`
- Create: `/Users/feng/Codes/twin/tests/__init__.py`
- Create: `/Users/feng/Codes/twin/schemas/twin.goal.schema.json`
- Create: `/Users/feng/Codes/twin/personas/supervisor.md`
- Create: `/Users/feng/Codes/twin/personas/worker.md`
- Create: `/Users/feng/Codes/twin/skills/twin/SKILL.md`
- Create: `/Users/feng/Codes/twin/skills/twin/agents/openai.yaml`
- Create: `/Users/feng/Codes/twin/templates/workspace/goal.yaml`
- Create: `/Users/feng/Codes/twin/templates/workspace/plan.yaml`

**Interfaces:**
- Produces: `TwinPaths.for_home(home: Path) -> TwinPaths`
- Produces: `ResourceCatalog(root: Path | None = None)`
- Produces: `ResourceCatalog.schema(name: str) -> Path`
- Produces: `ResourceCatalog.persona(name: str) -> Path`
- Produces: `ResourceCatalog.skill_dir() -> Path`
- Produces: `ResourceCatalog.template(name: str) -> Path`

- [ ] **Step 1: Verify the target paths and remote are absent**

Run:

```bash
test ! -e /Users/feng/Codes/twin
! gh repo view youxuanxue/twin >/dev/null 2>&1
```

Expected: both commands exit 0. If either target exists, stop and inspect it instead of overwriting it.

- [ ] **Step 2: Initialize the local repository and private GitHub remote**

Run:

```bash
mkdir /Users/feng/Codes/twin
git -C /Users/feng/Codes/twin init -b main
gh repo create youxuanxue/twin --private --source /Users/feng/Codes/twin --remote origin
```

Expected: `git -C /Users/feng/Codes/twin remote get-url origin` prints the `youxuanxue/twin` remote.

- [ ] **Step 3: Write the failing installed-resource test**

```python
# tests/test_resources.py
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from twin.paths import TwinPaths
from twin.resources import ResourceCatalog


class ResourceCatalogTest(TestCase):
    def test_paths_are_home_scoped(self) -> None:
        with TemporaryDirectory() as raw:
            paths = TwinPaths.for_home(Path(raw))
            self.assertEqual(paths.root, Path(raw) / ".twin")
            self.assertEqual(paths.workspaces, paths.root / "workspaces")
            self.assertEqual(paths.active_workspaces, paths.root / "active-workspaces")
            self.assertEqual(paths.locks, paths.root / "locks")

    def test_catalog_rejects_missing_installed_resource(self) -> None:
        with TemporaryDirectory() as raw:
            catalog = ResourceCatalog(Path(raw))
            with self.assertRaisesRegex(FileNotFoundError, "schema resource missing"):
                catalog.schema("goal")
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_resources -v`

Expected: FAIL because `twin.paths` and `twin.resources` do not exist.

- [ ] **Step 5: Add packaging and resource resolution**

Use this package configuration:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "xuejiao-twin"
version = "0.1.0"
description = "Provider-neutral supervisor for evidence-driven agent work"
requires-python = ">=3.9"
dependencies = []

[project.scripts]
twin = "twin.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.data-files]
"share/twin/schemas" = ["schemas/*.json"]
"share/twin/personas" = ["personas/*.md"]
"share/twin/skills/twin" = ["skills/twin/SKILL.md"]
"share/twin/skills/twin/agents" = ["skills/twin/agents/*.yaml"]
"share/twin/templates/workspace" = ["templates/workspace/*.yaml"]
```

Implement the stable path objects:

```python
# src/twin/paths.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TwinPaths:
    root: Path
    workspaces: Path
    active_workspaces: Path
    locks: Path
    installed_skills: Path
    config: Path

    @classmethod
    def for_home(cls, home: Path) -> "TwinPaths":
        root = home.expanduser().resolve() / ".twin"
        return cls(
            root=root,
            workspaces=root / "workspaces",
            active_workspaces=root / "active-workspaces",
            locks=root / "locks",
            installed_skills=root / "skills",
            config=root / "config.toml",
        )
```

Implement `ResourceCatalog` using `sysconfig.get_path("data") / "share" / "twin"` when no root is injected. Every accessor must require a real file or directory and raise `FileNotFoundError` naming the missing installed resource.

- [ ] **Step 6: Add only current resource seeds**

Copy the current supervisor and worker persona content into the new names, replacing every `$DEV_RULES` or source-path statement with installed-resource language. Use `Copyright (c) 2026 youxuanxue` in the standard MIT license. The initial README contains the one-line product purpose, Python requirement, `uv tool install --editable .`, and a statement that the repository is not part of dev-rules.

Seed the workspace templates exactly as valid authoring drafts:

```yaml
# templates/workspace/goal.yaml
schema_version: 1
id: replace-with-goal-id
one_liner: Replace with one verifiable outcome
core_goal: |
  Replace with the user or business result, not implementation steps.
acceptance_criteria: []
non_goals: []
```

```yaml
# templates/workspace/plan.yaml
schema_version: 1
goal_id: replace-with-goal-id
items: []
verification: []
```

Create a minimal valid explicit-only skill resource so the first wheel contains every declared data file:

```markdown
---
name: twin
description: Run the installed Twin supervisor when the user explicitly invokes Twin.
---

# Twin

Run `twin doctor --json` and `twin contract --json`. Follow the returned live contract; do not read or reconstruct a source-tree runtime.
```

The initial `agents/openai.yaml` contains:

```yaml
interface:
  display_name: "Twin"
  short_description: "Run the installed Twin supervisor"
  default_prompt: "Use $twin to supervise this goal."
policy:
  allow_implicit_invocation: false
```

Omit `research.yaml`, `CURRENT.md`, compatibility fields, and provider-specific defaults.

Create the initial goal schema with `$id` under `https://github.com/youxuanxue/twin/` and these required keys:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/youxuanxue/twin/schemas/twin.goal.schema.json",
  "title": "Twin goal",
  "type": "object",
  "required": ["schema_version", "id", "one_liner", "core_goal", "acceptance_criteria", "non_goals"],
  "properties": {
    "schema_version": {"const": 1},
    "id": {"type": "string", "minLength": 1},
    "one_liner": {"type": "string", "minLength": 1},
    "core_goal": {"type": "string", "minLength": 1},
    "acceptance_criteria": {"type": "array"},
    "non_goals": {"type": "array"}
  },
  "additionalProperties": false
}
```

- [ ] **Step 7: Run the resource tests and build an installable wheel**

Run:

```bash
cd /Users/feng/Codes/twin
PYTHONPATH=src python3 -m unittest tests.test_resources -v
python3 -m pip wheel --no-deps --wheel-dir /tmp/twin-wheel .
```

Expected: tests PASS and one `xuejiao_twin-0.1.0-*.whl` is created.

- [ ] **Step 8: Commit the repository foundation**

```bash
git -C /Users/feng/Codes/twin add pyproject.toml .gitignore LICENSE README.md src tests schemas personas skills templates
git -C /Users/feng/Codes/twin commit -m "feat: establish independent Twin package"
```

---

### Task 2: Implement the external workspace store and reduced schemas

**Files:**
- Create: `/Users/feng/Codes/twin/src/twin/errors.py`
- Create: `/Users/feng/Codes/twin/src/twin/yaml_codec.py`
- Create: `/Users/feng/Codes/twin/src/twin/schema.py`
- Create: `/Users/feng/Codes/twin/src/twin/storage/atomic.py`
- Create: `/Users/feng/Codes/twin/src/twin/storage/events.py`
- Create: `/Users/feng/Codes/twin/src/twin/storage/locks.py`
- Create: `/Users/feng/Codes/twin/src/twin/storage/workspaces.py`
- Create: `/Users/feng/Codes/twin/schemas/twin.plan.schema.json`
- Create: `/Users/feng/Codes/twin/schemas/twin.state.schema.json`
- Create: `/Users/feng/Codes/twin/schemas/twin.action.schema.json`
- Create: `/Users/feng/Codes/twin/schemas/twin.run-evidence.schema.json`
- Create: `/Users/feng/Codes/twin/tests/test_storage.py`

**Interfaces:**
- Consumes: `TwinPaths`, `ResourceCatalog`
- Produces: `WorkspaceStore.create(request: str, repo_root: Path, route: str) -> str`
- Produces: `WorkspaceStore.resolve(ref: str | None, project_root: Path) -> Path`
- Produces: `WorkspaceStore.load_state(workspace: Path) -> dict[str, object]`
- Produces: `WorkspaceStore.replace_state(workspace: Path, expected_revision: int, value: dict[str, object]) -> None`
- Produces: `WorkspaceStore.append_event(workspace: Path, event: dict[str, object]) -> None`
- Produces: `WorkspaceStore.write_artifact(workspace: Path, relative: str, body: bytes) -> dict[str, object]`

- [ ] **Step 1: Write failing storage tests**

```python
# tests/test_storage.py
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from twin.paths import TwinPaths
from twin.storage.workspaces import WorkspaceStore


class WorkspaceStoreTest(TestCase):
    def test_create_writes_outside_target_repo(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            store = WorkspaceStore(TwinPaths.for_home(root / "home"))
            workspace_id = store.create("ship feature", repo, "host/codex")
            workspace = store.resolve(workspace_id, repo)
            self.assertTrue(str(workspace).startswith(str(root / "home" / ".twin")))
            self.assertFalse((repo / ".twin").exists())

    def test_revision_mismatch_fails_closed(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            store = WorkspaceStore(TwinPaths.for_home(root / "home"))
            workspace = store.resolve(store.create("ship feature", repo, "host/codex"), repo)
            state = store.load_state(workspace)
            with self.assertRaisesRegex(ValueError, "state revision mismatch"):
                store.replace_state(workspace, 99, state)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_storage -v`

Expected: FAIL because the storage package is absent.

- [ ] **Step 3: Port only the deterministic YAML and schema primitives**

Move the useful parsing and emitting behavior from `dev-rules/scripts/twin/util.py` into `yaml_codec.py`, and the useful JSON Schema subset from `scripts/twin/schema_contract.py` plus `scripts/_schema_lite.py` into `schema.py`. Remove all global roots. Schema lookup must go through `ResourceCatalog.schema()`.

The schema API is:

```python
def validate_document(value: object, schema_name: str, resources: ResourceCatalog) -> list[str]: ...
def load_yaml(path: Path) -> dict[str, object]: ...
def dump_yaml(path: Path, value: dict[str, object]) -> None: ...
```

- [ ] **Step 4: Implement atomic storage, locking, and redacted events**

Use sibling temporary files plus `os.replace` for snapshots. Use `fcntl.flock(..., LOCK_EX | LOCK_NB)` on macOS/Linux and raise `WorkspaceBusyError` rather than waiting indefinitely.

Event records must have this shape:

```python
{
    "schema_version": 1,
    "recorded_at": now_utc(),
    "event": event_name,
    "workspace_id": workspace_id,
    "state_revision": revision,
    "details": redacted_metadata,
}
```

Do not put human answers, host instructions, provider output, tokens, or secrets in `details`.

- [ ] **Step 5: Implement `WorkspaceStore`**

`create()` must generate an ID from UTC time, a slug of the request, and eight random hex characters; write `meta.json`, valid draft `goal.yaml`, valid draft `plan.yaml`, `state.json`, and an initial event. `state.json` starts with:

```python
{
    "schema_version": 1,
    "workspace_id": workspace_id,
    "status": "awaiting_plan",
    "state_revision": 0,
    "supervisor_route": route,
    "pending_action": None,
    "current_run_id": None,
    "current_item_id": None,
    "terminal_summary": None,
}
```

Active workspace pointers are keyed by the SHA-256 hash of the canonical target repository path and contain only the workspace ID. `resolve()` accepts an ID or absolute workspace path under `TwinPaths.workspaces`; reject all paths outside that root.

- [ ] **Step 6: Add the four remaining schemas**

Require these top-level fields:

- plan: `schema_version`, `goal_id`, `items`, `verification`
- state: `schema_version`, `workspace_id`, `status`, `state_revision`, `supervisor_route`, `pending_action`
- action: `contract_version`, `action`, `workspace`, `supervisor_route`, `state_revision`, `action_token`, `context`, `expected_output`, `submit`
- run/evidence: `schema_version`, `run_id`, `item_id`, `request`, `result`, `evidence`, `status`

All schemas use `additionalProperties: false` except bounded metadata maps named explicitly in the schema.

- [ ] **Step 7: Run storage and schema tests**

Run:

```bash
cd /Users/feng/Codes/twin
PYTHONPATH=src python3 -m unittest tests.test_storage tests.test_resources -v
```

Expected: PASS.

- [ ] **Step 8: Commit the workspace data plane**

```bash
git -C /Users/feng/Codes/twin add src/twin/errors.py src/twin/yaml_codec.py src/twin/schema.py src/twin/storage schemas tests/test_storage.py
git -C /Users/feng/Codes/twin commit -m "feat: add external Twin workspace store"
```

---

### Task 3: Implement the domain state machine and token-bound actions

**Files:**
- Create: `/Users/feng/Codes/twin/src/twin/domain/actions.py`
- Create: `/Users/feng/Codes/twin/src/twin/domain/evidence.py`
- Create: `/Users/feng/Codes/twin/src/twin/domain/plan.py`
- Create: `/Users/feng/Codes/twin/src/twin/domain/state.py`
- Create: `/Users/feng/Codes/twin/src/twin/domain/service.py`
- Create: `/Users/feng/Codes/twin/tests/test_domain.py`

**Interfaces:**
- Consumes: `WorkspaceStore`
- Produces: `TwinService.start(goal: str, repo_root: Path, route: str) -> dict[str, object]`
- Produces: `TwinService.run(workspace_ref: str | None, repo_root: Path, route: str) -> dict[str, object]`
- Produces: `TwinService.submit_plan(workspace_ref: str, route: str, revision: int, token: str, payload: dict[str, object]) -> dict[str, object]`
- Produces: `TwinService.submit_instruction(...) -> dict[str, object]`
- Produces: `TwinService.submit_review(...) -> dict[str, object]`
- Produces: `TwinService.respond(workspace_ref: str | None, repo_root: Path, answer: str) -> dict[str, object]`
- Produces: `TwinService.handoff(workspace_ref: str, repo_root: Path, from_route: str, to_route: str) -> dict[str, object]`
- Produces: `TwinService.status(workspace_ref: str | None, repo_root: Path) -> dict[str, object]`

- [ ] **Step 1: Write failing action and replay tests**

```python
# tests/test_domain.py
class TwinServiceTest(TestCase):
    def test_start_returns_author_plan_action(self) -> None:
        action = self.service.start("ship feature", self.repo, "host/codex")
        self.assertEqual(action["action"], "author_plan")
        self.assertEqual(action["state_revision"], 1)
        self.assertIn("submit-plan", action["submit"]["command"])

    def test_action_token_is_single_use(self) -> None:
        action = self.service.start("ship feature", self.repo, "host/codex")
        payload = valid_goal_and_plan()
        self.service.submit_plan(
            action["workspace"], "host/codex", action["state_revision"], action["action_token"], payload
        )
        with self.assertRaisesRegex(ValueError, "stale or consumed action"):
            self.service.submit_plan(
                action["workspace"], "host/codex", action["state_revision"], action["action_token"], payload
            )

    def test_wrong_route_cannot_submit(self) -> None:
        action = self.service.start("ship feature", self.repo, "host/codex")
        with self.assertRaisesRegex(ValueError, "supervisor route mismatch"):
            self.service.submit_plan(
                action["workspace"], "host/claude", action["state_revision"], action["action_token"], valid_goal_and_plan()
            )
```

- [ ] **Step 2: Run domain tests to verify they fail**

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_domain -v`

Expected: FAIL because `TwinService` is absent.

- [ ] **Step 3: Implement action issuance and validation**

`issue_action()` advances the revision once, stores only a SHA-256 token hash in state, and returns the plaintext token once. The stored pending action contains `kind`, `state_revision`, `route`, `token_hash`, and optional `run_id`.

```python
def validate_submission(
    state: dict[str, object], *, kind: str, route: str, revision: int, token: str, run_id: str | None = None
) -> None:
    ...
```

Reject wrong workspace, route, revision, kind, run ID, token, duplicate submission, and terminal workspaces before any mutation.

- [ ] **Step 4: Implement plan validation and evidence completion rules**

Port the useful AC coverage, dependency ordering, plan update, and completion checks from `scripts/twin/plan.py`. Remove provider defaults and legacy fields. A plan item may become complete only when every declared evidence entry has a stored artifact or successful command result. `accepted_done` requires every acceptance criterion to map to accepted evidence.

- [ ] **Step 5: Implement lifecycle transitions**

Use these states only:

```text
awaiting_plan -> ready -> worker_running -> review_required
review_required -> ready | needs_human | accepted_done | failed
needs_human -> ready
```

`start()` creates a workspace and issues `author_plan`. `submit_plan()` validates and stores goal/plan, consumes the token, and moves to `ready`. `run()` returns a self-describing action or delegates deterministic worker execution through the injected runtime. `respond()` stores the answer under `artifacts/human/<hash>.txt`, records only reference/length/hash in the event, and returns to `ready`. `handoff()` is allowed only with no pending action and increments the revision.

- [ ] **Step 6: Test all negative transition cases**

Add tests for terminal mutation, handoff with a pending action, wrong run ID, missing evidence, inconsistent state/event workspace IDs, and a response when the workspace is not at `needs_human`.

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_domain -v`

Expected: PASS with both positive and negative cases.

- [ ] **Step 7: Commit the domain kernel**

```bash
git -C /Users/feng/Codes/twin add src/twin/domain tests/test_domain.py
git -C /Users/feng/Codes/twin commit -m "feat: add token-bound Twin domain kernel"
```

---

### Task 4: Put worker execution and worktree isolation behind adapters

**Files:**
- Create: `/Users/feng/Codes/twin/src/twin/runtime/protocols.py`
- Create: `/Users/feng/Codes/twin/src/twin/runtime/process.py`
- Create: `/Users/feng/Codes/twin/src/twin/runtime/local_cli.py`
- Create: `/Users/feng/Codes/twin/src/twin/runtime/cao.py`
- Create: `/Users/feng/Codes/twin/src/twin/runtime/worktree.py`
- Create: `/Users/feng/Codes/twin/tests/fixtures/fake_provider.py`
- Create: `/Users/feng/Codes/twin/tests/test_runtime.py`
- Modify: `/Users/feng/Codes/twin/src/twin/domain/service.py`

**Interfaces:**
- Produces: `WorkerTurnRequest`
- Produces: `WorkerTurnResult`
- Produces: `WorkerRuntime.run_turn(request: WorkerTurnRequest) -> WorkerTurnResult`
- Produces: `WorkspaceIsolation.prepare(repo_root: Path, workspace_id: str) -> Path`
- Produces: `WorkspaceIsolation.cleanup(repo_root: Path, workspace_id: str) -> bool`
- Consumes: these protocols through constructor injection in `TwinService`

- [ ] **Step 1: Write failing adapter contract tests**

```python
# tests/test_runtime.py
class RuntimeAdapterTest(TestCase):
    def test_worker_request_has_no_dev_rules_environment(self) -> None:
        request = WorkerTurnRequest(
            prompt="do work", cwd=Path("/tmp/repo"), provider="codex", session_id="", timeout_seconds=30
        )
        self.assertNotIn("DEV_RULES", request.environment)

    def test_cleanup_preserves_dirty_worktree(self) -> None:
        worktree = self.isolation.prepare(self.repo, "ws-1")
        (worktree / "unsaved.txt").write_text("keep", encoding="utf-8")
        self.assertFalse(self.isolation.cleanup(self.repo, "ws-1"))
        self.assertTrue(worktree.exists())
```

- [ ] **Step 2: Run adapter tests to verify they fail**

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_runtime -v`

Expected: FAIL because the runtime protocols and adapters are absent.

- [ ] **Step 3: Define narrow runtime data types**

```python
@dataclass(frozen=True)
class WorkerTurnRequest:
    prompt: str
    cwd: Path
    provider: str
    session_id: str
    timeout_seconds: int
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerTurnResult:
    output_text: str
    returncode: int
    session_id: str
    events: tuple[dict[str, object], ...]
    timed_out: bool = False


class WorkerRuntime(Protocol):
    def run_turn(self, request: WorkerTurnRequest) -> WorkerTurnResult: ...


class WorkspaceIsolation(Protocol):
    def prepare(self, repo_root: Path, workspace_id: str) -> Path: ...
    def cleanup(self, repo_root: Path, workspace_id: str) -> bool: ...
```

- [ ] **Step 4: Port provider process behavior without source coupling**

Move the tested process-group timeout, Claude stream parsing, Codex/Gemini JSON parsing, permission modes, and CAO redirect/auth protection from the old runtime into the new adapters. Persona text is provided in the prompt by `ResourceCatalog`; no adapter receives `DEV_RULES` or a persona path.

Support the existing execution values only:

```text
claude_headless
local_cli: claude | codex | gemini
cao: provider + agent
```

Provider absence, unsupported budgets, non-loopback plaintext CAO, redirects, timeouts, and malformed responses must return explicit failure evidence.

- [ ] **Step 5: Implement native product-owned worktree isolation**

Use Git directly; do not locate or invoke `wtree.py`. Derive a sibling path `<repo>-twin-<workspace-id>` and branch `twin/<workspace-id>`. Create from the exact target repository `HEAD`, validate branch/path on reuse, initialize submodules recursively in the isolated checkout, and reject any mismatch. Cleanup runs only after `git status --porcelain=v1 --untracked-files=all --ignore-submodules=none` is empty for the superproject and initialized submodules.

Add integration fixtures for a normal repository, same-basename workspace IDs, a repository with a local submodule, wrong-branch reuse, dirty cleanup, and missing Git.

- [ ] **Step 6: Connect runtime evidence to the domain service**

For each turn, write `runs/<run-id>/request.json`, `result.json`, and `evidence.json` through `WorkspaceStore`. Provider event streams may be stored as artifacts, but `events.jsonl` contains only artifact references and summary metadata. Worker completion always transitions to `review_required`; it never directly produces `accepted_done`.

- [ ] **Step 7: Run runtime and domain tests**

Run:

```bash
cd /Users/feng/Codes/twin
PYTHONPATH=src python3 -m unittest tests.test_runtime tests.test_domain -v
```

Expected: PASS without network access or real provider credentials; `fake_provider.py` supplies deterministic provider output.

- [ ] **Step 8: Commit the adapters**

```bash
git -C /Users/feng/Codes/twin add src/twin/runtime src/twin/domain/service.py tests/test_runtime.py tests/fixtures
git -C /Users/feng/Codes/twin commit -m "feat: isolate Twin worker runtimes"
```

---

### Task 5: Expose the reduced CLI and live machine contract

**Files:**
- Create: `/Users/feng/Codes/twin/src/twin/cli.py`
- Create: `/Users/feng/Codes/twin/src/twin/contract.py`
- Create: `/Users/feng/Codes/twin/src/twin/doctor.py`
- Modify: `/Users/feng/Codes/twin/src/twin/__main__.py`
- Create: `/Users/feng/Codes/twin/tests/test_cli.py`
- Create: `/Users/feng/Codes/twin/tests/test_contract.py`

**Interfaces:**
- Consumes: `TwinService`, `ResourceCatalog`, runtime adapters
- Produces: `build_parser() -> argparse.ArgumentParser`
- Produces: `render_contract(parser: argparse.ArgumentParser, resources: ResourceCatalog) -> dict[str, object]`
- Produces: `doctor_report(paths: TwinPaths, resources: ResourceCatalog) -> dict[str, object]`
- Produces: `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Write failing CLI surface tests**

```python
# tests/test_cli.py
class CliSurfaceTest(TestCase):
    def test_public_help_is_small(self) -> None:
        help_text = parser_help(build_parser())
        for command in ("start", "run", "status", "respond", "handoff", "doctor", "contract"):
            self.assertIn(command, help_text)
        for removed in ("scaffold", "bootstrap", "research", "plan", "next", "watch", "worker-turn", "review-context"):
            self.assertNotIn(removed, help_text)

    def test_contract_includes_hidden_submission_commands(self) -> None:
        contract = render_contract(build_parser(), self.resources)
        self.assertEqual(contract["contract_version"], 1)
        self.assertIn("submit-plan", contract["action_commands"])
        self.assertIn("submit-instruction", contract["action_commands"])
        self.assertIn("submit-review", contract["action_commands"])
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_contract -v`

Expected: FAIL because the CLI and contract exporter are absent.

- [ ] **Step 3: Implement explicit command visibility metadata**

Each parser stores one of `public`, `administrative`, or `action-only`. Normal argparse help includes public and administrative commands only. `contract --json` exports all public and action-only commands with exact argv fields, action output shapes, schema paths, package version, and contract version.

The public host path uses:

```text
twin start "<goal>" --supervisor host/<provider> --json
twin run [workspace] --supervisor host/<provider> --json
twin status [workspace] [--json]
twin respond "<answer>" [--workspace <id>] [--json]
twin handoff <workspace> --from host/<provider> --to host/<provider> --json
twin doctor [--json]
twin contract --json
```

Submission commands read JSON or text from stdin using `--payload-file -`; never interpolate user content into shell command strings.

- [ ] **Step 4: Implement `doctor` without checkout checks**

Report these named checks:

```text
python
package_resources
state_home
cursor_skill
claude_skill
codex_skill
antigravity_skill
git
claude
codex
gemini
cao_configuration
```

`ok` depends only on Python, package resources, writable state home, Git, and installed Twin skill links after setup. Provider checks are capabilities and do not make the base installation unhealthy.

- [ ] **Step 5: Run CLI and contract tests**

Run:

```bash
cd /Users/feng/Codes/twin
PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_contract -v
PYTHONPATH=src python3 -m twin --help
PYTHONPATH=src python3 -m twin contract --json
```

Expected: tests PASS; help omits hidden/removed commands; contract JSON validates against the action schema references it exposes.

- [ ] **Step 6: Commit the product surface**

```bash
git -C /Users/feng/Codes/twin add src/twin/cli.py src/twin/contract.py src/twin/doctor.py src/twin/__main__.py tests/test_cli.py tests/test_contract.py
git -C /Users/feng/Codes/twin commit -m "feat: expose the focused Twin CLI contract"
```

---

### Task 6: Install the Twin-owned skill without adopting foreign entries

**Files:**
- Create: `/Users/feng/Codes/twin/src/twin/setup.py`
- Modify: `/Users/feng/Codes/twin/skills/twin/SKILL.md`
- Modify: `/Users/feng/Codes/twin/skills/twin/agents/openai.yaml`
- Create: `/Users/feng/Codes/twin/tests/test_setup.py`
- Modify: `/Users/feng/Codes/twin/src/twin/cli.py`
- Modify: `/Users/feng/Codes/twin/src/twin/doctor.py`

**Interfaces:**
- Produces: `install_skill(paths: TwinPaths, resources: ResourceCatalog, home: Path) -> list[LinkResult]`
- Produces: `check_skill_links(paths: TwinPaths, home: Path) -> list[LinkResult]`
- Produces: `uninstall_skill(paths: TwinPaths, home: Path) -> list[LinkResult]`

- [ ] **Step 1: Write failing ownership tests**

```python
# tests/test_setup.py
class SetupOwnershipTest(TestCase):
    def test_setup_refuses_foreign_cursor_entry(self) -> None:
        foreign = self.home / ".cursor" / "skills" / "twin"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("user owned", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "refusing to replace user-owned"):
            install_skill(self.paths, self.resources, self.home)

    def test_uninstall_removes_only_twin_owned_links(self) -> None:
        install_skill(self.paths, self.resources, self.home)
        foreign = self.home / ".codex" / "skills" / "other"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.symlink_to(self.home / "other-skill")
        uninstall_skill(self.paths, self.home)
        self.assertTrue(foreign.is_symlink())
        self.assertFalse((self.home / ".codex" / "skills" / "twin").exists())
```

- [ ] **Step 2: Run setup tests to verify they fail**

Run: `cd /Users/feng/Codes/twin && PYTHONPATH=src python3 -m unittest tests.test_setup -v`

Expected: FAIL because `twin.setup` is absent.

- [ ] **Step 3: Implement copy-then-link installation**

Copy the installed skill resource atomically into `~/.twin/skills/twin`, then create these direct links:

```text
~/.cursor/skills/twin
~/.codex/skills/twin
~/.gemini/antigravity-cli/skills/twin
```

Each points directly to `~/.twin/skills/twin`. Require `~/.cursor/skills` to be a real directory; if it is still the legacy whole-directory symlink, fail with the exact instruction to complete the dev-rules additive-registry cutover first. `~/.claude/skills` must remain a symlink to `~/.cursor/skills`; create it only when absent, and refuse a real or foreign target.

Only replace an existing `twin` link when it already points inside `~/.twin`. Treat real files, real directories, and foreign symlinks as ownership conflicts.

- [ ] **Step 4: Rewrite the host skill around the live contract**

The skill must:

- remain explicit-only;
- run `twin doctor --json`, then `twin contract --json`;
- map a new goal to `twin start`, a workspace to `twin run`, `status` to `twin status`, and `respond` to `twin respond`;
- select `host/codex`, `host/claude`, or `host/antigravity` from the current host;
- consume each self-describing action's `context`, `expected_output`, `submit.command`, and `next_command` literally;
- never read a source checkout, construct tokens, edit state, or reproduce schema fields.

- [ ] **Step 5: Run setup and skill-contract tests**

Run:

```bash
cd /Users/feng/Codes/twin
PYTHONPATH=src python3 -m unittest tests.test_setup tests.test_contract -v
```

Expected: PASS, including foreign-entry preservation and legacy-root rejection.

- [ ] **Step 6: Commit setup and skill ownership**

```bash
git -C /Users/feng/Codes/twin add src/twin/setup.py src/twin/cli.py src/twin/doctor.py skills tests/test_setup.py
git -C /Users/feng/Codes/twin commit -m "feat: install the Twin-owned host skill"
```

---

### Task 7: Add clean-environment verification, current docs, and the first push

**Files:**
- Create: `/Users/feng/Codes/twin/scripts/preflight.sh`
- Create: `/Users/feng/Codes/twin/scripts/smoke-clean-home.sh`
- Create: `/Users/feng/Codes/twin/tests/smoke_installed.py`
- Create: `/Users/feng/Codes/twin/docs/architecture.md`
- Create: `/Users/feng/Codes/twin/docs/agent-integration.md` as generated output
- Create: `/Users/feng/Codes/twin/docs/operator-guide.md`
- Modify: `/Users/feng/Codes/twin/README.md`
- Modify: `/Users/feng/Codes/twin/src/twin/contract.py`

**Interfaces:**
- Consumes: every earlier task
- Produces: `scripts/preflight.sh` as the repository gate
- Produces: `scripts/smoke-clean-home.sh` as the filesystem-isolated checkout-independence proof
- Produces: `tests/smoke_installed.py` as the staged installed-wheel lifecycle driver

- [ ] **Step 1: Write the clean-home smoke script before claiming independence**

The script accepts the exact wheel through required `TWIN_WHEEL=/absolute/path/to/xuejiao_twin-*.whl`. It stages only that wheel and `tests/smoke_installed.py` into a temporary input directory, then runs the stage read-only in `python:3.9-slim-bookworm` with `--network none`. The container command creates a temporary HOME, target repository, and `/opt/twin-venv`, installs the staged wheel into that venv, materializes a real `~/.cursor/skills`, and invokes setup, `contract --json`, `doctor --json`, and every lifecycle action only through `/opt/twin-venv/bin/twin`. The smoke driver exercises start, plan submission, run, review submission, human response, status, handoff, restart recovery, token replay rejection, and dirty-worktree preservation.

Support Docker and Podman only. Selection is deterministic: use executable `TWIN_CONTAINER_RUNTIME` when it is exactly `docker` or `podman`, otherwise choose a usable Docker daemon, then a usable Podman service. If neither is usable, exit `77` with `SKIP: no supported container runtime`; `scripts/preflight.sh` may report that skip only for an ordinary local run. CI, release, push, and the plan completion gate set `TWIN_REQUIRE_CONTAINER=1`, which converts exit `77` into failure.

Mount only the temporary staged input directory. Do not mount `/Users/feng/Codes/twin`, `/Users/feng/Codes/dev-rules`, `/Users/feng/Codes/agent-skills`, their parents, or the host HOME. Inside the container, fail if any of those host paths exist. After setup, locate the installed `twin` package and `share/twin` data through the venv Python, and inspect those roots plus `$HOME/.twin/skills/twin`: no file or directory inside them may be a symlink, and no text file may contain `$DEV_RULES`, `/Users/feng/Codes/dev-rules`, `/Users/feng/Codes/agent-skills`, `scripts.twin`, or another source-checkout path. The expected host entry links may point only to the container's `$HOME/.twin/skills/twin`.

- [ ] **Step 2: Run the smoke script and verify the initial failure**

Run:

```bash
cd /Users/feng/Codes/twin
tmp_wheels="$(mktemp -d)"
python3 -m pip wheel --no-deps --wheel-dir "$tmp_wheels" .
TWIN_WHEEL="$(find "$tmp_wheels" -maxdepth 1 -name 'xuejiao_twin-*.whl' -print -quit)" \
  TWIN_REQUIRE_CONTAINER=1 \
  bash scripts/smoke-clean-home.sh
```

Expected before wiring all cases: non-zero with the first missing lifecycle assertion, not a false success.

- [ ] **Step 3: Complete the smoke fixture and repository preflight**

`scripts/preflight.sh` creates one temporary wheel directory and runs, in order:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --wheel-dir "$tmp_wheels" .
wheel="$(find "$tmp_wheels" -maxdepth 1 -name 'xuejiao_twin-*.whl' -print -quit)"
test -n "$wheel"
python3 -m venv "$tmp_venv"
"$tmp_venv/bin/python" -m pip install --no-deps "$wheel"
mkdir -p "$tmp_home/.cursor/skills"
HOME="$tmp_home" "$tmp_venv/bin/twin" setup
HOME="$tmp_home" "$tmp_venv/bin/twin" contract --json
HOME="$tmp_home" "$tmp_venv/bin/twin" doctor --json
TWIN_WHEEL="$wheel" TWIN_REQUIRE_CONTAINER="${TWIN_REQUIRE_CONTAINER:-0}" bash scripts/smoke-clean-home.sh
```

No installed verification command may use ambient `python3 -m twin`; source tests always use `PYTHONPATH=src`, while installation evidence always uses the exact wheel's venv console script. Treat smoke exit `77` as a visible local skip only when `TWIN_REQUIRE_CONTAINER` is not `1` and CI is false; otherwise fail preflight.

Add a supplemental static `rg` gate that fails on `$DEV_RULES`, `/Codes/dev-rules`, `/Codes/agent-skills`, `scripts.twin`, old active-pointer names, `scaffold`, `bootstrap`, or legacy command registration in runtime/skill/docs. Allow the approved migration rationale only in Git history, not current Twin files. This scan does not replace the container filesystem and installed-resource inspection.

- [ ] **Step 4: Generate current documentation from live code**

`docs/architecture.md` explains the four-layer boundary and workspace model. `docs/operator-guide.md` documents installation, setup, diagnosis, providers, recovery, and uninstall. `docs/agent-integration.md` is rendered from `twin contract --json`; it is never read at runtime.

- [ ] **Step 5: Run full verification**

Run:

```bash
cd /Users/feng/Codes/twin
TWIN_REQUIRE_CONTAINER=1 bash scripts/preflight.sh
git diff --check
git status --short
```

Expected: preflight PASS, no whitespace errors, and only intended files modified.

- [ ] **Step 6: Commit and push the independently usable product**

```bash
git -C /Users/feng/Codes/twin add README.md docs scripts src tests schemas personas skills templates pyproject.toml .gitignore LICENSE
git -C /Users/feng/Codes/twin commit -m "feat: deliver standalone Twin supervisor"
git -C /Users/feng/Codes/twin push -u origin main
```

Expected: `origin/main` contains a fresh Twin-only history, and its release unit needs no synchronized dev-rules or agent-skills commit.

---

## Plan Completion Gate

Do not begin the owner cutover plan until all of these are true:

- `/Users/feng/Codes/twin/scripts/preflight.sh` passes freshly.
- A wheel-installed Twin passes `smoke-clean-home.sh` inside the isolated Linux filesystem with no source checkout mounted or present.
- `twin contract --json`, `twin doctor --json`, and the smoke lifecycle are valid only through console scripts from fresh venvs containing the exact built wheel.
- Installed package resources and the installed skill contain no symlink or text reference back to a source checkout.
- The skill setup tests prove foreign entries are preserved.
- The old dev-rules and agent-skills Twin owners are still untouched, providing a clean rollback point before cutover.
