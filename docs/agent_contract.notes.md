# dev-rules Agent Contract Notes

- `twin` is the provider-neutral supervisor CLI. Use `twin run <workspace> --supervisor host/codex|host/claude|host/antigravity --json`; Claude `/twin` is a thin adapter over the same action protocol.
- Host actions are self-describing: `supervisor_instruction` and `review_run` carry bounded context, expected output, a state revision, a one-time token, and the exact stdin submit command. Python owns worker execution and artifact mutation.
- A workspace binds lazily to its first host route. Stale revisions, duplicate tokens, wrong action/run/workspace, and route drift fail closed. Existing schema-version-1 workspaces remain readable.
- `plan.yaml.execution` defaults to `claude_headless`. `backend: local_cli` directly invokes the installed `claude`, `codex`, or `gemini` CLI; `backend: cao` uses CAO's external `POST /terminals/run-step` contract. CAO provider profiles remain owned by the CAO installation.
- Local Codex fresh/resume turns enforce `workspace-write` plus `approval_policy=never`; local Gemini turns enforce its OS sandbox plus yolo approvals. A provider timeout terminates the spawned process group before the turn is finalized.
- `plan.yaml.execution.agent` is required only for `backend: cao` and must name a profile returned by `cao profile list`; `developer` is the portable built-in example. CAO permissions are resolved from that profile rather than from Claude-native tool names.
- `TWIN_CAO_BASE_URL` selects the CAO control plane. `CAO_AUTH_LOCAL_TOKEN` is read only for auth-enabled CAO, is never persisted in twin artifacts, requires HTTPS outside loopback, and is never forwarded through HTTP redirects.
- `research.yaml` is optional, read-only provenance. The twin supervisor owns final `goal.yaml` and `plan.yaml` decisions.
- The approved team runtime boundary is `docs/approved/twin-team-runtime-architecture.md`; practical CAO/Codex setup is in `docs/twin-cao-operator-guide.md`.
- The cross-system comparison and operating decisions are summarized in `docs/agent-team-playbook.md`; the approved host-supervisor boundary is in `docs/approved/twin-universal-host-supervisor.md`.
- `global/bin/twin` is distributed to `~/.local/bin/twin` by `sync.sh`. No local server is required for host supervision or `local_cli` workers; CAO remains optional for remote and profile-managed workers.
- `global/CLAUDE.md` and generated project `AGENTS.md` blocks are navigation and policy surfaces. Runtime command and schema inventories are generated here.
