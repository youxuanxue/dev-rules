---
approved_by: user-chat-2026-08-25
status: approved
risk_level: high
---

# Remove Twin product ownership from dev-rules

## Decision

Twin becomes an independent product repository:

- local checkout: `/Users/feng/Codes/twin`
- remote: `youxuanxue/twin`
- Git history: new history, without importing commits from `dev-rules` or `agent-skills`

The split follows ownership rather than file location. Everything that exists only to build, run, install, document, test, or distribute Twin belongs to the Twin repository. `dev-rules` keeps development policy and project gates; `agent-skills` keeps only skills whose owners remain there.

This document is the approval anchor for removing Twin product ownership from `dev-rules`. After cutover it remains a migration decision record, while the live Twin architecture and agent contract are owned by the Twin repository.

## Why the existing design is rejected

Twin currently treats `dev-rules` as an implicit application framework:

- the launcher resolves `scripts/twin` through `$DEV_RULES`;
- runtime code resolves personas and schemas from the `dev-rules` checkout;
- the host skill reads a generated contract from `dev-rules`;
- `sync.sh`, repository verification and preflight contain Twin-specific distribution and checks;
- Twin's product design, runtime, policy and host integration have multiple owners across repositories.

This violates the intended minimal Agent OS design. Policy, product runtime and reusable agent capabilities must not share a release unit merely because they were developed together.

## Repository responsibilities

| Owner | Responsibility | Explicit non-responsibility |
| --- | --- | --- |
| `twin` | Supervisor product, domain state machine, CLI, schemas, personas, host skill, installation, tests and runtime adapters | Global development policy and generic coding-agent runtime primitives |
| `dev-rules` | Cross-project development policy, explicit project onboarding and mechanical project gates | Twin runtime, Twin installation, Twin schemas, Twin skill distribution and Twin compatibility |
| DeepSeek Harness | Generic agent execution, sessions, persistence, tools, providers and jobs consumed through an adapter | Twin goals, plans, evidence semantics, review decisions and human gates |
| Pi | Interactive coding-agent and harness design reference | A supported Twin runtime adapter until a concrete need and stable contract exist |
| `agent-skills` | Reusable skills whose lifecycle is independent of Twin | The Twin skill or any Twin-specific asset |

The long-term dependency direction is:

```text
human
  -> host skill
  -> Twin CLI
  -> Twin domain kernel
  -> runtime adapters
  -> DeepSeek Harness

target project policy -> verification commands consumed by Twin
```

Twin has no runtime dependency on `dev-rules`. A target project may declare a `dev-rules` preflight command, but Twin treats it like any other project-declared verification command.

## New repository shape

```text
twin/
├── src/twin/                 # CLI, domain kernel and adapters
├── schemas/                  # public workspace and action contracts
├── personas/                 # supervisor and worker personas
├── skills/twin/              # shared Claude, Codex and Antigravity host skill
├── templates/workspace/      # authoring defaults only
├── tests/                    # domain, adapter, contract and installation tests
├── docs/                     # current product and operator documentation
├── scripts/                  # contract export, setup and repository gates
└── pyproject.toml            # package metadata and `twin` console entry point
```

The repository is self-contained: moving or deleting the `dev-rules` and `agent-skills` checkouts must not break an installed Twin.

## Runtime boundary

### Host skill

`skills/twin` is a thin, explicit host adapter. It translates Claude, Codex or Antigravity invocation into public Twin CLI operations and consumes the live machine contract exposed by Twin. It does not implement another loop, copy schema fields or read repository documentation as a runtime contract.

### Domain kernel

The domain kernel exclusively owns:

- goals and plans;
- deterministic state transitions;
- evidence and run evaluation;
- supervisor review;
- human gates;
- audit events.

It does not import provider, host, project-policy, DeepSeek Harness or Pi concepts.

### Runtime adapters

External capabilities sit behind narrow interfaces such as:

- `WorkerRuntime`;
- `WorkspaceIsolation`;
- `HostSupervisor`;
- `ArtifactStore`.

The physical repository split happens before changing the execution engine. The first implementation preserves current execution behavior behind adapters while removing source-tree coupling. A later, separately verified change replaces worker, session and persistence implementation with DeepSeek Harness. Harness failures are explicit; Twin does not silently fall back to a legacy runtime.

Pi remains a design reference only. No speculative Pi adapter is included.

### Project verification

Twin does not infer a target project's policy stack and does not hard-code `scripts/preflight.sh`. The host-authored plan or target-project contract declares verification commands. Twin executes those commands, records evidence and fails closed when required evidence is missing or a command fails.

Workspace isolation is owned behind Twin's adapter. Twin no longer imports `wtree.py` from a `dev-rules` or skill checkout.

## Installation and live contracts

Twin is installed as a Python package with a console entry point:

```toml
[project.scripts]
twin = "twin.cli:main"
```

Development installation uses an editable tool install from `/Users/feng/Codes/twin`; published installation uses a versioned release from `youxuanxue/twin`. There is no shell launcher that searches for a source checkout.

`twin setup` installs the repository-owned skill into a stable Twin home and creates host links:

```text
~/.twin/skills/twin/
~/.cursor/skills/twin                    -> ~/.twin/skills/twin
~/.claude/skills/twin                    -> ~/.twin/skills/twin
~/.codex/skills/twin                     -> ~/.twin/skills/twin
~/.gemini/antigravity-cli/skills/twin    -> ~/.twin/skills/twin
```

`twin setup --check` detects drift. `twin uninstall` removes only links that still point to the Twin-owned installation. It never replaces or deletes a real user file.

Live code exposes the machine-owned interfaces:

- `twin contract --json`: public commands, hidden action protocol, schema locations and contract version;
- `twin doctor --json`: package, skill-link, runtime and provider diagnostics.

Human-readable integration documentation may be generated, but it is not a runtime dependency or contract owner. `dev-rules sync.sh` does not install, update or verify Twin.

## User command surface

The supported human-facing commands are intentionally small:

```text
twin start "<goal>"
twin run [workspace]
twin status [workspace]
twin respond "<answer>"
twin handoff ...
twin doctor
```

The machine discovery surface is:

```text
twin contract --json
```

`twin start` creates the workspace and returns an `author_plan` action. The current host performs optional research, authors the goal and plan, submits them through the token-bound protocol, and enters the same run loop used for resumption.

The following historical user commands are removed rather than deprecated:

- `scaffold` and `bootstrap`;
- standalone `research` and `plan` entry points;
- `next`, `watch`, `worker-turn` and `review-context`;
- other compatibility commands and source-module entry points.

Token-bound submission commands may remain hidden implementation protocol. They are discoverable through `twin contract --json`, absent from normal help and never constructed from memory by the skill.

## Workspace and artifact model

Twin runtime data lives outside the target repository:

```text
~/.twin/
├── config.toml
├── skills/
├── active-workspaces/
├── locks/
└── workspaces/<workspace-id>/
    ├── meta.json
    ├── goal.yaml
    ├── plan.yaml
    ├── state.json
    ├── events.jsonl
    ├── runs/<run-id>/
    │   ├── request.json
    │   ├── result.json
    │   └── evidence.json
    └── artifacts/
```

The core contracts are limited to goal, plan, state, action and run/evidence semantics.

- Research is an artifact referenced by the plan, not a parallel lifecycle object.
- Human responses are controlled artifacts. Audit events record references, lengths and hashes rather than response bodies.
- Supervisor review is represented by the unified action and evidence contracts rather than an independent schema family.
- `CURRENT.md` is deleted. `twin status` renders human-readable state from live facts.
- `state.json` is the current snapshot; `events.jsonl` is the append-only audit trail.
- State mutations hold a workspace lock and use atomic replacement.
- Actions bind workspace, revision, route and a one-time token.
- Inconsistent state, events or artifacts fail closed.

Twin does not read project-local legacy `.twin` state, old Claude-specific active pointers or old schemas, and supplies no automatic migration command.

## Asset disposition

The new repository receives only current, necessary product assets:

- runtime and domain code currently under `scripts/twin`;
- Twin schemas and personas;
- the Twin CLI launcher behavior, rewritten as the package entry point;
- the Twin workspace template;
- `agent-skills/twin/SKILL.md` and its host metadata;
- current fixtures and contract tests whose assertions remain valid;
- rewritten current product, operator and agent-integration documentation.

The following are deliberately not copied:

- superseded files under `docs/approved/twin-*`;
- historical compatibility branches and fixtures;
- the untracked Liang Wenfeng PDF under `agent-skills/twin`;
- Git history from either source repository;
- `$DEV_RULES`, source-directory or old installation-path assumptions.

During cutover, `dev-rules` deletes Twin runtime, schemas, personas, templates, product documentation, launcher, contract export and all Twin-specific branches in sync, verification and preflight code. `agent-skills` deletes its Twin directory after the new installer owns the active host links. No forwarding wrapper, compatibility switch or duplicated skill remains.

## Delivery sequence

### Establish the independent repository

- Initialize `/Users/feng/Codes/twin` with a new Git history and configure `youxuanxue/twin` as its remote.
- Create the package, domain, adapter, schema, skill, documentation and test boundaries.
- Preserve current execution behavior only where it remains part of the approved public experience.
- Remove source-checkout assumptions before changing the execution engine.

### Cut over ownership

- Install the new package and host skill.
- Remove Twin-owned assets and checks from `dev-rules`.
- Remove the old Twin skill owner from `agent-skills` without touching unrelated tracked or untracked user assets.
- Reject old commands and paths explicitly; do not add migration or fallback behavior.

### Prove independence

Verify the package in an environment with no `dev-rules` or `agent-skills` checkout available. Cover installation, setup drift, contract discovery, diagnosis, goal start, continuation, status, human response, restart recovery, timeout, worker failure, token replay, route mismatch, concurrency lock, verification failure and host handoff.

### Replace the runtime adapter

Only after the independent repository is stable, integrate DeepSeek Harness through the adapter boundary. Twin domain tests remain Harness-independent. Adapter tests use the real Harness contract. Harness unavailability produces a clear diagnosis and failure.

## Acceptance criteria

- Twin installs, runs, resumes and upgrades without a `dev-rules` or `agent-skills` checkout.
- The Twin repository is the sole owner of its CLI, domain code, schemas, personas, skill, setup, tests and live contract.
- `dev-rules` contains no Twin runtime owner, distribution branch, contract export or product-specific gate.
- `agent-skills` contains no Twin skill owner.
- No `$DEV_RULES`, old source-module, old active-pointer or legacy workspace fallback remains.
- Old command and data paths fail clearly instead of being translated.
- Action replay, route drift, concurrent mutation and inconsistent artifacts fail closed.
- Target-project verification is explicit and evidence-backed, without assuming dev-rules.
- A clean-environment end-to-end run reaches a terminal result or a genuine human gate.
- Twin can publish a version without a synchronized `dev-rules` or `agent-skills` change.

## Non-goals

- Importing old Git history.
- Preserving existing workspaces or compatibility commands.
- Moving generic skills such as `git-worktree-submodule` or `xj-review` into Twin.
- Forking or embedding DeepSeek Harness implementation.
- Implementing a Pi runtime adapter.
- Adding Web UI, distributed scheduling, a general Agent platform or speculative provider abstractions.

## Superseded decisions

This approval replaces prior Twin architecture documents in `dev-rules`, including designs that made `dev-rules`, `sync.sh`, `$DEV_RULES`, shared personas, shared `wtree.py`, legacy workspaces or compatibility commands part of the Twin product contract. Those documents are migration inputs only and are deleted during implementation rather than copied to the new repository.
