# dev-rules Agent Contract Notes

- `/twin` is a Claude-Code-only supervisor command. The Python CLI is its deterministic runtime substrate, not a second user workflow.
- `plan.yaml.execution` defaults to `claude_headless`. `backend: cao` uses CAO's external `POST /terminals/run-step` contract; CAO provider profiles remain owned by the CAO installation.
- `plan.yaml.execution.agent` must name a profile returned by `cao profile list`; `developer` is the portable built-in example. CAO permissions are resolved from that profile rather than from Claude-native tool names.
- `TWIN_CAO_BASE_URL` selects the CAO control plane. `CAO_AUTH_LOCAL_TOKEN` is read only for auth-enabled CAO and is never persisted in twin artifacts.
- `research.yaml` is optional, read-only provenance. The twin supervisor owns final `goal.yaml` and `plan.yaml` decisions.
- The approved team runtime boundary is `docs/approved/twin-team-runtime-architecture.md`; practical CAO/Codex setup is in `docs/twin-cao-operator-guide.md`.
- The cross-system comparison and operating decisions are summarized in `docs/agent-team-playbook.md`; the provider-neutral twin roadmap is in `docs/twin-universal-command.md`.
- A provider-neutral `twin` CLI and shared supervisor skill are proposed in `docs/twin-universal-command.md`; they are not part of the current runtime contract yet.
- `global/CLAUDE.md` and generated project `AGENTS.md` blocks are navigation and policy surfaces. Runtime command and schema inventories are generated here.
