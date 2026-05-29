"""Circuit-breaker state for the /xj-review fix/CI loop.

`commands/xj-review.md` runs an autonomous review → fix → push → watch-CI
loop. Its stop-the-line safety used to be four hand-counted thresholds the
model had to remember across a long loop (and across context summarization):

  - big-loop rounds            cap 3   (xj-review §115 + §128, merged budget)
  - same script fails in a row cap 2   (xj-review §112)
  - same finding unfixed       cap 3   (xj-review §115)
  - same CI job fails in a row cap 3   (xj-review §128)

Counts and state-derivation are exactly what `dev-rules-convention.mdc §75`
says MUST be scripted: same input → same output, no creativity. This module
owns that bookkeeping; the prompt only calls it and forwards the verdict.

The big-loop budget is a single shared counter on purpose: §128 merges the
fix loop and the CI-failure reflow into one budget so a `fix → CI fail → fix`
chain cannot smuggle in 6 attempts under two separate "3 round" caps.

Per-key counters reset to 0 on a `pass` outcome and increment on `fail`; the
big-loop round only ever increments. A counter reaching its cap yields
`verdict=halt` — the model must stop and surface the reason, not loop on.

It also carries the change's risk level (a prompt judgement passed at `init`)
and exposes a `gate` check the loop calls before pushing: a HIGH-risk change
halts before the outward push so the human approves it — OPC's sole
human-intervention point (global CLAUDE.md §1, product-dev §180). low/normal
push autonomously.

CLI mirrors `python3 -m scripts.twin`: argparse subcommands, `key=value`
stdout lines the model forwards verbatim, `--json` for machine reads.
Exit codes: 0 = verdict=continue, 1 = verdict=halt (stop-the-line),
2 = usage/IO error. `selftest` exits 0 on pass, 1 on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# Lookup table — the thresholds the skill prose used to spell out. Single
# source of truth so a rule change is a one-line edit, not a prose hunt.
ROUND_CAP = 3  # xj-review §115 + §128 (merged fix/CI big-loop budget)
KIND_CAPS = {
    "script": 2,  # xj-review §112: same mechanical gate fails twice in a row
    "finding": 3,  # xj-review §115: same finding survives three fix rounds
    "ci-job": 3,  # xj-review §128: same CI job fails three times in a row
}

# Risk level is a prompt judgement (product-dev.mdc + global CLAUDE.md §1 own
# the "blast radius / rollback cost" call); this module only stores the
# verdict and applies its one deterministic consequence: a HIGH-risk change
# must not be silently committed-and-pushed by the autonomous loop — OPC's
# sole human-intervention point is the high-risk approval gate. `gate` halts
# before push so the human approves the outward action. Unknown/normal/low
# pass through (product-dev §65: when unsure, treat as normal).
RISK_LEVELS = ("low", "normal", "high")
GATED_RISK = "high"

EXIT_CONTINUE = 0
EXIT_HALT = 1
EXIT_ERROR = 2


class LoopStateError(Exception):
    """Usage or IO error worth a nonzero exit and a stderr message."""


def _state_path(key: str, override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    base = Path(os.environ.get("TMPDIR", "/tmp"))
    return base / f"xj-review-loop-{digest}.json"


def _load(path: Path, key: str) -> dict:
    if not path.exists():
        raise LoopStateError(
            f"no loop state at {path} for key={key!r}; run `init --key {key}` first"
        )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopStateError(f"cannot read loop state {path}: {exc}") from exc
    if not isinstance(state, dict) or "round" not in state or "counters" not in state:
        raise LoopStateError(f"corrupt loop state {path}; re-run `init`")
    return state


def _save(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise LoopStateError(f"cannot write loop state {path}: {exc}") from exc


def _emit(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for field in ("key", "verdict", "reason", "risk", "round", "round_cap", "kind", "id", "count", "cap", "state_file"):
        if field in payload and payload[field] is not None:
            print(f"{field}={payload[field]}")


def _verdict_exit(verdict: str) -> int:
    return EXIT_HALT if verdict == "halt" else EXIT_CONTINUE


def cmd_init(args: argparse.Namespace) -> int:
    if args.risk not in RISK_LEVELS:
        raise LoopStateError(f"unknown risk {args.risk!r}; expected one of {list(RISK_LEVELS)}")
    path = _state_path(args.key, args.state_file)
    state = {"key": args.key, "round": 0, "counters": {}, "risk": args.risk}
    _save(path, state)
    _emit(
        {
            "key": args.key,
            "verdict": "continue",
            "reason": f"loop initialized (risk={args.risk})",
            "risk": args.risk,
            "round": 0,
            "round_cap": ROUND_CAP,
            "state_file": str(path),
        },
        as_json=args.json,
    )
    return EXIT_CONTINUE


def cmd_gate(args: argparse.Namespace) -> int:
    """High-risk approval gate, called before an outward push."""
    path = _state_path(args.key, args.state_file)
    state = _load(path, args.key)
    risk = state.get("risk", "normal")
    if risk == GATED_RISK:
        verdict, reason = "halt", (
            "high-risk change: do NOT auto-push. Commit locally is fine, but "
            "present the fix diff and wait for human approval before push "
            "(OPC §1 sole human-gate; product-dev §180 high-risk approval anchor)"
        )
    else:
        verdict, reason = "continue", f"risk={risk}: autonomous push allowed"
    _emit(
        {
            "key": args.key,
            "verdict": verdict,
            "reason": reason,
            "risk": risk,
            "state_file": str(path),
        },
        as_json=args.json,
    )
    return _verdict_exit(verdict)


def cmd_round_start(args: argparse.Namespace) -> int:
    path = _state_path(args.key, args.state_file)
    state = _load(path, args.key)
    state["round"] = int(state["round"]) + 1
    rnd = state["round"]
    if rnd > ROUND_CAP:
        verdict, reason = "halt", (
            f"big-loop round {rnd} exceeds cap {ROUND_CAP} "
            "(xj-review §115/§128): stop-the-line, surface root cause to the user"
        )
    else:
        verdict, reason = "continue", f"round {rnd} of at most {ROUND_CAP}"
    _save(path, state)
    _emit(
        {
            "key": args.key,
            "verdict": verdict,
            "reason": reason,
            "round": rnd,
            "round_cap": ROUND_CAP,
            "state_file": str(path),
        },
        as_json=args.json,
    )
    return _verdict_exit(verdict)


def cmd_record(args: argparse.Namespace) -> int:
    if args.kind not in KIND_CAPS:
        raise LoopStateError(f"unknown kind {args.kind!r}; expected one of {sorted(KIND_CAPS)}")
    path = _state_path(args.key, args.state_file)
    state = _load(path, args.key)
    counters: dict = state["counters"]
    ckey = f"{args.kind}:{args.id}"
    cap = KIND_CAPS[args.kind]
    if args.outcome == "pass":
        counters[ckey] = 0
        verdict, reason = "continue", f"{ckey} passed; counter reset"
        count = 0
    else:
        count = int(counters.get(ckey, 0)) + 1
        counters[ckey] = count
        if count >= cap:
            verdict, reason = "halt", (
                f"{ckey} failed {count} time(s) in a row (cap {cap}): "
                "stop-the-line, surface root cause to the user"
            )
        else:
            verdict, reason = "continue", f"{ckey} failed {count}/{cap}; keep fixing"
    _save(path, state)
    _emit(
        {
            "key": args.key,
            "verdict": verdict,
            "reason": reason,
            "kind": args.kind,
            "id": args.id,
            "count": count,
            "cap": cap,
            "state_file": str(path),
        },
        as_json=args.json,
    )
    return _verdict_exit(verdict)


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Scripted sequence asserting the four caps and the reset semantics."""
    import io
    import contextlib

    failures: list[str] = []

    def run(argv: list[str], expect_exit: int) -> dict:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = main(argv)
        try:
            payload = json.loads(buf.getvalue())
        except json.JSONDecodeError:
            payload = {}
        if code != expect_exit:
            failures.append(f"{' '.join(argv[:2])}: exit {code}, expected {expect_exit}")
        return payload

    with tempfile.TemporaryDirectory() as tmp:
        sf = str(Path(tmp) / "state.json")
        base = ["--state-file", sf, "--key", "selftest", "--json"]

        run(["init", *base], EXIT_CONTINUE)

        # big-loop: 3 rounds continue, 4th halts.
        for _ in range(ROUND_CAP):
            run(["round-start", *base], EXIT_CONTINUE)
        p = run(["round-start", *base], EXIT_HALT)
        if p.get("verdict") != "halt":
            failures.append("round-start: 4th round did not halt")

        # script cap 2: first fail continues, second fail halts.
        run(["init", *base], EXIT_CONTINUE)
        run(["record", *base, "--kind", "script", "--id", "preflight", "--outcome", "fail"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "script", "--id", "preflight", "--outcome", "fail"], EXIT_HALT)

        # a pass between fails resets the counter (no premature halt).
        run(["init", *base], EXIT_CONTINUE)
        run(["record", *base, "--kind", "finding", "--id", "R-001", "--outcome", "fail"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "finding", "--id", "R-001", "--outcome", "fail"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "finding", "--id", "R-001", "--outcome", "pass"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "finding", "--id", "R-001", "--outcome", "fail"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "finding", "--id", "R-001", "--outcome", "fail"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "finding", "--id", "R-001", "--outcome", "fail"], EXIT_HALT)

        # distinct ids keep independent counters.
        run(["init", *base], EXIT_CONTINUE)
        run(["record", *base, "--kind", "ci-job", "--id", "lint", "--outcome", "fail"], EXIT_CONTINUE)
        run(["record", *base, "--kind", "ci-job", "--id", "test", "--outcome", "fail"], EXIT_CONTINUE)

        # high-risk push gate halts; normal/low pass through; default is normal.
        run(["init", *base, "--risk", "high"], EXIT_CONTINUE)
        g = run(["gate", *base], EXIT_HALT)
        if g.get("verdict") != "halt":
            failures.append("gate: high risk did not halt")
        run(["init", *base, "--risk", "normal"], EXIT_CONTINUE)
        run(["gate", *base], EXIT_CONTINUE)
        run(["init", *base], EXIT_CONTINUE)  # default risk = normal
        run(["gate", *base], EXIT_CONTINUE)

        # unknown kind / risk are usage errors.
        run(["record", *base, "--kind", "bogus", "--id", "x", "--outcome", "fail"], EXIT_ERROR)
        run(["init", *base, "--risk", "bogus"], EXIT_ERROR)

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        print("loop_state selftest: FAIL")
        return 1
    print("loop_state selftest: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.review.loop_state")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--key", default="default", help="review session id (e.g. owner/repo#123)")
        p.add_argument("--state-file", default=None, help="override state path (testing)")
        p.add_argument("--json", action="store_true")

    p_init = sub.add_parser("init", help="start/reset a review loop session")
    add_common(p_init)
    # No argparse `choices`: cmd_init validates --risk itself (same reason as
    # --kind below). Default normal = product-dev §65 "when unsure, normal".
    p_init.add_argument("--risk", default="normal", help="risk level: low|normal|high")
    p_init.set_defaults(func=cmd_init)

    p_round = sub.add_parser("round-start", help="enter a fix/CI big-loop round")
    add_common(p_round)
    p_round.set_defaults(func=cmd_round_start)

    p_gate = sub.add_parser("gate", help="high-risk approval gate before an outward push")
    add_common(p_gate)
    p_gate.set_defaults(func=cmd_gate)

    p_record = sub.add_parser("record", help="record a script/finding/ci-job outcome")
    add_common(p_record)
    # No argparse `choices`: cmd_record validates --kind itself so an unknown
    # kind exits 2 (usage error) rather than argparse's exit 2 with a different
    # message — and so selftest can exercise that path.
    p_record.add_argument("--kind", required=True)
    p_record.add_argument("--id", required=True)
    p_record.add_argument("--outcome", required=True, choices=["pass", "fail"])
    p_record.set_defaults(func=cmd_record)

    p_selftest = sub.add_parser("selftest", help="run scripted cap/reset asserts")
    p_selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LoopStateError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
