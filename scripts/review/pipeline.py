"""Bounded, STATELESS fan-out for /xj-review (D2 Dynamic Workflow tool).

Sibling of deep-research: parallel multi-dimension review -> adversarial
verify -> dedup. Pure function over (scope, findings) -- holds NO cross-round
state, NEVER touches git push / gh. The stateful loop (rounds, circuit
breaker, high-risk push gate, CI tracking) stays in loop_state.py + the skill
prose (D6). This boundary IS D2: stateless fan-out here, stateful closure there.

Today the xj-review fan-out lives as prose in SKILL.md: the model re-infers
*which dimensions to review*, *how to dedup*, and *when the two adversarial
gates fire* every turn. That is exactly the "mechanizable work written as
prose" that dev-rules-convention.mdc §75 says MUST be scripted (enumerate /
sort / dedupe / parse / state-derive). This module owns that recall; the
prompt keeps only the irreducible judgement (escalate/downgrade/drop, the Jobs
god-view pass).

The split with loop_state.py is deliberate and load-bearing:

  - loop_state.py is STATEFUL: per-session counters, --state-file, the
    high-risk push gate. It remembers across rounds and context summaries.
  - pipeline.py is STATELESS: four pure subcommands, no --state-file, no git,
    no gh, no .reviews/ writes. scope/findings in, findings out.

Recall belongs to the script, judgement belongs to the model:

  dimensions  -- table lookup of the fixed review-dimension enum; risk/mode
                 only PRUNE the set, never invent a dimension. The model no
                 longer recalls "which dimensions" each turn.
  find        -- deterministic pre-processing before the model reviews code:
                 parse the preflight output into severity>=high finding drafts
                 (every FAIL segment surfaces), collect the warn-only points
                 (|| true / except:pass / --no-verify) as a candidate list the
                 model judges, and emit the changed-file x dimension fan-out
                 matrix that bounds each parallel review unit.
  adversarial -- run the two gates' TRIGGERS (direction calibration, self-
  -verify        reference calibration). The script only guarantees recall
                 (the question gets asked, the flag gets set); the verdict --
                 escalate / downgrade-to-question / drop -- stays the model's.
  dedup       -- pure mechanics: normalize-key dedup, severity sort, same-topic
                 merge, R-00x numbering. Output is review.schema.json shaped.

CLI mirrors loop_state.py / `python3 -m scripts.twin`: argparse subcommands,
`key=value` stdout lines the model forwards verbatim, `--json` for machine
reads. No state file. Exit codes: 0 = ok, 2 = usage/IO error. `selftest`
exits 0 on pass, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 2

# ── Lookup table — the prose §3 checklist, now a table (convention §75) ──
# Fixed dimension enum. risk/mode only PRUNE this set; never invent dimensions.
# `always` dims fan out on every review (concise default); conformance is the
# only dimension gated behind full_conformance mode (high-risk reviews).
DIMENSIONS = {
    "code-quality":   {"always": True},
    "security":       {"always": True},
    "test-coverage":  {"always": True},
    "architecture":   {"always": True},   # 分层依赖 / 契约同步 / web-surface
    "input-safety":   {"always": True},
    "design-quality": {"always": True},   # Jobs 五条的可落 finding 投影
    "scope-creep":    {"always": True},   # out-of-scope 顺手问题(§4 必报)
    "conformance":    {"always": False},  # 仅 full_conformance 加
}

REVIEW_MODES = ("concise", "full_conformance")
RISK_LEVELS = ("low", "normal", "high")

# Direction-calibration trigger (§2.1 ①). A suggested_fix that pushes the PR
# *away* from automation / autonomous closure / lower approval friction is the
# kind of finding that can quietly reverse the project's direction. We only
# RECALL it (flag for the model to downgrade to a question); we never decide.
# Signals are deliberately broad — recall over precision — covering the CN
# prose the skill/rules are written in plus the obvious EN equivalents.
_REDUCES_AUTOMATION_SIGNALS = (
    "改回人工",
    "改为人工",
    "回退到人工",
    "人工介入",
    "人工审批",
    "手动",
    "拆掉闭环",
    "拆除闭环",
    "去掉闭环",
    "砍掉闭环",
    "加审批",
    "增加审批",
    "新增审批",
    "审批门",
    "砍 agent",
    "砍掉 agent",
    "限制 agent",
    "减少自动化",
    "降低自动化",
    "关闭自动化",
    "禁用自动化",
    "remove automation",
    "disable automation",
    "less automation",
    "reduce automation",
    "manual approval",
    "manual step",
    "manual review",
    "add approval",
    "extra approval",
    "human in the loop",
    "require human",
)

# Self-reference-calibration trigger (§2.1 ②). prose-rules carriers: editing a
# rule/skill/command file with a "this should be mechanized" finding is the
# convention §75-vs-§77 fork — the model must answer *which step is
# mechanizable and what script carries it*, or downgrade to a question.
_PROSE_RULES_SUFFIXES = (".mdc",)
_PROSE_RULES_NAMES = ("SKILL.md", "AGENTS.md", "CLAUDE.md")


def _prose_rules_path(path: str) -> bool:
    """True when `path` is a prose-rules carrier (*.mdc, commands/*.md, SKILL/
    AGENTS/CLAUDE.md). These are the §75-vs-§77 self-reference surfaces."""
    p = path.strip()
    if not p:
        return False
    name = Path(p).name
    if name in _PROSE_RULES_NAMES:
        return True
    if any(name.endswith(suf) for suf in _PROSE_RULES_SUFFIXES):
        return True
    # commands/*.md — slash-command prose carriers.
    if p.endswith(".md") and ("/commands/" in p or p.startswith("commands/")):
        return True
    return False


# "本可机械化却写成 prose" claim signals — the finding *says* something should
# be a script/table/lookup but is written as prose. Recall only; the model
# decides whether the claim holds (§75 truly mechanizable) or is over-
# mechanization (§77 irreducible judgement).
_MECHANIZABLE_SIGNALS = (
    "机械化",
    "可机械",
    "查表",
    "脚本化",
    "脚本承载",
    "确定性",
    "排序去重",
    "去重",
    "计数",
    "枚举",
    "解析",
    "校验",
    "mechaniz",
    "deterministic",
    "lookup table",
    "should be a script",
    "should be scripted",
    "hard-code",
    "hardcode",
)


class PipelineError(Exception):
    """Usage or IO error worth a nonzero exit and a stderr message."""


# ── helpers ──────────────────────────────────────────────────────────────

def _emit(payload: dict, *, as_json: bool) -> None:
    """Mirror loop_state.py._emit: --json dumps the whole payload; otherwise
    a stable subset of scalar fields is printed as key=value lines the model
    forwards verbatim. List/dict fields (dimensions, findings, matrix …) are
    machine-shaped and only surface under --json."""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for field in ("verb", "scope", "risk", "mode", "dimensions",
                  "fail_count", "warn_count", "file_count", "finding_count"):
        if field in payload and payload[field] is not None:
            val = payload[field]
            if isinstance(val, list):
                val = ",".join(str(x) for x in val)
            print(f"{field}={val}")


def _changed_files(scope: str | None) -> list[str]:
    """`git diff --name-only <scope>` → sorted unique file list. A None/empty
    scope diffs the working tree against HEAD. Recall is the contract: if git
    fails we surface the error rather than silently returning [] (which would
    look like 'no files to review')."""
    cmd = ["git", "diff", "--name-only"]
    if scope:
        cmd.append(scope)
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
    except OSError as exc:  # git not on PATH etc.
        raise PipelineError(f"cannot run {' '.join(cmd)}: {exc}") from exc
    if out.returncode != 0:
        raise PipelineError(
            f"git diff failed for scope={scope!r}: {out.stderr.strip()}"
        )
    files = sorted({line for line in out.stdout.splitlines() if line.strip()})
    return files


def _parse_preflight(path: str) -> dict:
    """Parse a captured preflight/verify-rules log into {fails, warns}.

    Recall contract (SKILL §0.2): every `FAIL:` line becomes a fail segment
    (→ a severity>=high finding draft) and every warn-only marker line becomes
    a candidate the model judges. We match the leading-token shape preflight.sh
    emits (`  FAIL: …`, `  warn: …`) plus the inline warn-only swallow points
    (`|| true`, `except: pass`/`except:pass`, `--no-verify`) so a log that
    embeds those is not missed."""
    p = Path(path).expanduser()
    if not p.exists():
        raise PipelineError(f"no preflight log at {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineError(f"cannot read preflight log {p}: {exc}") from exc

    fails: list[str] = []
    warns: list[str] = []
    fail_re = re.compile(r"^\s*FAIL:\s*(.+?)\s*$")
    warn_re = re.compile(r"^\s*warn:\s*(.+?)\s*$")
    swallow_re = re.compile(r"\|\|\s*true|except\s*:\s*pass|--no-verify")
    for line in text.splitlines():
        m = fail_re.match(line)
        if m:
            fails.append(m.group(1))
            continue
        m = warn_re.match(line)
        if m:
            warns.append(m.group(1))
            continue
        if swallow_re.search(line):
            warns.append(line.strip())
    return {"fails": fails, "warns": warns}


def _finding_from_preflight_fail(segment: str) -> dict:
    """A preflight FAIL is a mechanical, already-proven failure → a
    severity=high finding draft (SKILL §0.2). category=conformance because a
    failed gate is non-conformance to the repo's own preflight contract; the
    schema requires a `reference` for conformance findings, so we point it at
    the preflight gate. human_verdict stays {} (filled at calibration)."""
    return {
        "severity": "high",
        "category": "conformance",
        "automatable": True,
        "file": "scripts/preflight.sh",
        "line": None,
        "description": f"preflight gate failed: {segment}",
        "reference": "scripts/preflight.sh#gate",
        "suggested_fix": None,
        "human_verdict": {},
    }


def _reduces_automation(suggested_fix: str | None) -> bool:
    """Direction-calibration recall: does the proposed fix push toward less
    automation / more approval friction / dismantled closure? Case-insensitive
    substring scan over the broad signal set. Recall only."""
    if not suggested_fix:
        return False
    hay = suggested_fix.lower()
    return any(sig.lower() in hay for sig in _REDUCES_AUTOMATION_SIGNALS)


def _is_prose_rules_target(file: str | None) -> bool:
    return _prose_rules_path(file or "")


def _is_mechanizable_claim(finding: dict) -> bool:
    """Self-reference recall: does this finding *claim* prose should be
    mechanized? Scan description + category over the signal set. The
    scope-creep / over-mechanization verdict (§75 vs §77) stays the model's."""
    text = " ".join(
        str(finding.get(k, "")) for k in ("description", "category", "suggested_fix")
    ).lower()
    return any(sig.lower() in text for sig in _MECHANIZABLE_SIGNALS)


def _normalize(description: str) -> str:
    """Dedup key normalization: lowercase, collapse whitespace, strip trailing
    punctuation so two phrasings of the same finding share a key."""
    s = re.sub(r"\s+", " ", (description or "").strip().lower())
    return s.strip(" .,:;。，；：")


def _load_findings(args: argparse.Namespace) -> list[dict]:
    """Load findings from a file, stdin, or an inline JSON argument."""
    source = "inline JSON"
    try:
        if args.findings_json is not None:
            text = args.findings_json
        elif args.findings_file == "-":
            source = "stdin"
            text = sys.stdin.read()
        else:
            p = Path(args.findings_file).expanduser()
            source = f"findings file {p}"
            if not p.exists():
                raise PipelineError(f"no findings file at {p}")
            text = p.read_text(encoding="utf-8")
        data = json.loads(text)
    except OSError as exc:
        raise PipelineError(f"cannot read {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"invalid JSON from {source}: {exc}") from exc
    if isinstance(data, dict) and "findings" in data:
        data = data["findings"]
    if not isinstance(data, list):
        raise PipelineError(
            f"{source} must contain a JSON array or {{'findings': [...]}}"
        )
    return data


# ── subcommands ──────────────────────────────────────────────────────────

def cmd_dimensions(args: argparse.Namespace) -> int:
    if args.mode not in REVIEW_MODES:
        raise PipelineError(
            f"unknown mode {args.mode!r}; expected one of {list(REVIEW_MODES)}"
        )
    if args.risk not in RISK_LEVELS:
        raise PipelineError(
            f"unknown risk {args.risk!r}; expected one of {list(RISK_LEVELS)}"
        )
    dims = [
        d for d, m in DIMENSIONS.items()
        if m["always"] or args.mode == "full_conformance"
    ]
    _emit(
        {
            "verb": "dimensions",
            "scope": args.scope,
            "risk": args.risk,
            "mode": args.mode,
            "dimensions": dims,
        },
        as_json=args.json,
    )
    return EXIT_OK


def cmd_find(args: argparse.Namespace) -> int:
    # ① preflight 文本 → severity>=high finding 草稿(机械召回,SKILL §0.2)
    pf = _parse_preflight(args.preflight) if args.preflight else {"fails": [], "warns": []}
    fail_findings = [_finding_from_preflight_fail(seg) for seg in pf["fails"]]
    warn_candidates = pf["warns"]  # 候选清单:模型逐项判断是否掩盖真实失败
    # ② diff 文件 × 维度矩阵:标注每个并行扇出单元的边界(模型对每单元做真判断)
    files = _changed_files(args.scope)
    matrix = {f: list(DIMENSIONS) for f in files}
    _emit(
        {
            "verb": "find",
            "scope": args.scope,
            "fail_count": len(fail_findings),
            "warn_count": len(warn_candidates),
            "file_count": len(files),
            "preflight_fail_findings": fail_findings,
            "warn_candidates": warn_candidates,
            "fanout_matrix": matrix,
        },
        as_json=args.json,
    )
    return EXIT_OK


def cmd_adversarial_verify(args: argparse.Namespace) -> int:
    raw = _load_findings(args)
    out = []
    for f in raw:
        flags = []
        # 方向校准触发(机械召回;裁决=模型)
        if _reduces_automation(f.get("suggested_fix", "")):
            flags.append("needs-direction-check")
        # 自指校准触发(机械召回;§75 vs §77 二分=模型)
        if _is_prose_rules_target(f.get("file", "")) and _is_mechanizable_claim(f):
            flags.append("answer-mechanization-or-downgrade")
        out.append({**f, "verify_flags": flags})
    _emit(
        {"verb": "adversarial-verify", "finding_count": len(out), "findings": out},
        as_json=args.json,
    )
    return EXIT_OK


# severity ordering for the dedup sort + merge. Single source of truth so the
# enum stays aligned with review.schema.json's severity enum.
SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def cmd_dedup(args: argparse.Namespace) -> int:
    findings = _load_findings(args)
    # 纯机械:归一化 key 去重 + severity 排序 + 同主题合并(convention §75)
    seen: dict = {}
    merged: list[dict] = []
    for f in findings:
        try:
            key = (f["category"], f["file"], _normalize(f["description"]))
        except (KeyError, TypeError) as exc:
            raise PipelineError(
                f"finding missing required key for dedup (need category/file/"
                f"description): {exc}"
            ) from exc
        sev = f.get("severity")
        if sev not in SEV:
            raise PipelineError(
                f"finding has unknown severity {sev!r}; expected one of {list(SEV)}"
            )
        if key in seen:  # 同主题:取更高 severity(数值更小)
            i = seen[key]
            if SEV[sev] < SEV[merged[i]["severity"]]:
                merged[i]["severity"] = sev
            continue
        seen[key] = len(merged)
        merged.append(dict(f))
    # stable severity sort (critical→low); ties keep first-seen order.
    merged.sort(key=lambda f: SEV[f["severity"]])
    for i, f in enumerate(merged, 1):
        f["id"] = f"R-{i:03d}"
    _emit(
        {"verb": "dedup", "finding_count": len(merged), "findings": merged},
        as_json=args.json,
    )
    return EXIT_OK


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Fixed-input asserts mirroring loop_state.cmd_selftest: dimension pruning
    (concise vs full_conformance), dedup (merge + severity sort + R-00x), and
    both adversarial triggers' recall. Hung off preflight; must run green."""
    import io
    import contextlib
    import tempfile

    failures: list[str] = []

    def run_json(argv: list[str], expect_exit: int, *, stdin: str | None = None) -> dict:
        buf = io.StringIO()
        original_stdin = sys.stdin
        try:
            if stdin is not None:
                sys.stdin = io.StringIO(stdin)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = main(argv)
                except SystemExit as exc:
                    code = int(exc.code)
        finally:
            sys.stdin = original_stdin
        try:
            payload = json.loads(buf.getvalue())
        except json.JSONDecodeError:
            payload = {}
        if code != expect_exit:
            failures.append(f"{' '.join(argv[:1])}: exit {code}, expected {expect_exit}")
        return payload

    # — dimensions: concise prunes conformance; full_conformance keeps it —
    concise = run_json(["dimensions", "--mode", "concise", "--json"], EXIT_OK)
    if "conformance" in concise.get("dimensions", []):
        failures.append("dimensions concise: conformance not pruned")
    if "code-quality" not in concise.get("dimensions", []):
        failures.append("dimensions concise: dropped an always-dimension")
    full = run_json(["dimensions", "--mode", "full_conformance", "--json"], EXIT_OK)
    if "conformance" not in full.get("dimensions", []):
        failures.append("dimensions full_conformance: conformance not added")
    if len(full.get("dimensions", [])) != len(DIMENSIONS):
        failures.append("dimensions full_conformance: not the full enum")
    # unknown mode/risk are usage errors
    run_json(["dimensions", "--mode", "bogus", "--json"], EXIT_ERROR)
    run_json(["dimensions", "--risk", "bogus", "--json"], EXIT_ERROR)

    with tempfile.TemporaryDirectory() as tmp:
        ff = Path(tmp) / "findings.json"

        # — dedup: same-topic merge takes higher severity; sort; R-00x —
        raw = [
            {"severity": "low", "category": "code-quality", "file": "a.py",
             "description": "Unused import os", "human_verdict": {}},
            {"severity": "high", "category": "code-quality", "file": "a.py",
             "description": "unused import   os.", "human_verdict": {}},  # dup → merge to high
            {"severity": "critical", "category": "security", "file": "b.py",
             "description": "SQL injection in query builder", "human_verdict": {}},
            {"severity": "medium", "category": "test-coverage", "file": "c.py",
             "description": "no test for edge case", "human_verdict": {}},
        ]
        ff.write_text(json.dumps(raw), encoding="utf-8")
        dd = run_json(["dedup", "--findings-file", str(ff), "--json"], EXIT_OK)
        fs = dd.get("findings", [])
        if len(fs) != 3:
            failures.append(f"dedup: expected 3 after merge, got {len(fs)}")
        else:
            # merged a.py finding must be high (higher of low/high)
            apy = [f for f in fs if f["file"] == "a.py"]
            if not apy or apy[0]["severity"] != "high":
                failures.append("dedup: same-topic merge did not take higher severity")
            # severity sorted critical→...; first must be the critical security one
            if fs[0]["severity"] != "critical" or fs[0]["category"] != "security":
                failures.append("dedup: not sorted critical-first")
            # R-00x numbering, contiguous from 1
            ids = [f.get("id") for f in fs]
            if ids != [f"R-{i:03d}" for i in range(1, len(fs) + 1)]:
                failures.append(f"dedup: bad R-00x numbering: {ids}")
        # dedup on bad severity is a usage error
        ff.write_text(json.dumps([{"severity": "bogus", "category": "x",
                                    "file": "f", "description": "d"}]), encoding="utf-8")
        run_json(["dedup", "--findings", str(ff), "--json"], EXIT_ERROR)

        # Input contract: inline JSON, stdin, and the compatibility alias all
        # feed the same parser; argparse rejects ambiguous sources.
        inline = run_json(
            ["dedup", "--findings-json", json.dumps(raw), "--json"], EXIT_OK
        )
        if inline.get("finding_count") != 3:
            failures.append("dedup: inline JSON input not loaded")
        piped = run_json(
            ["dedup", "--findings-file", "-", "--json"],
            EXIT_OK,
            stdin=json.dumps(raw),
        )
        if piped.get("finding_count") != 3:
            failures.append("dedup: stdin input not loaded")
        run_json(["dedup", "--findings-json", "not-json", "--json"], EXIT_ERROR)
        run_json(
            ["dedup", "--findings-file", str(ff), "--findings-json", "[]", "--json"],
            EXIT_ERROR,
        )

        # — adversarial-verify: both triggers' recall —
        verify_in = [
            # direction trigger: suggested_fix pushes back to manual approval
            {"severity": "medium", "category": "architecture", "file": "x.py",
             "description": "loop is too autonomous",
             "suggested_fix": "改回人工审批,拆掉闭环", "human_verdict": {}},
            # self-reference trigger: prose-rules carrier + mechanizable claim
            {"severity": "low", "category": "design-quality", "file": "rules/foo.mdc",
             "description": "这段清单本可机械化查表,却写成 prose",
             "suggested_fix": None, "human_verdict": {}},
            # neither trigger fires
            {"severity": "high", "category": "security", "file": "y.py",
             "description": "missing input validation",
             "suggested_fix": "validate the payload", "human_verdict": {}},
        ]
        ff.write_text(json.dumps(verify_in), encoding="utf-8")
        av = run_json(["adversarial-verify", "--findings", str(ff), "--json"], EXIT_OK)
        avf = av.get("findings", [])
        if len(avf) != 3:
            failures.append(f"adversarial-verify: expected 3, got {len(avf)}")
        else:
            if "needs-direction-check" not in avf[0].get("verify_flags", []):
                failures.append("adversarial-verify: direction trigger missed")
            if "answer-mechanization-or-downgrade" not in avf[1].get("verify_flags", []):
                failures.append("adversarial-verify: self-reference trigger missed")
            if avf[2].get("verify_flags"):
                failures.append("adversarial-verify: false-positive flag on clean finding")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        print("pipeline selftest: FAIL")
        return 1
    print("pipeline selftest: PASS")
    return 0


# ── parser / main ────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.review.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true")

    def add_findings_input(p: argparse.ArgumentParser, noun: str) -> None:
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--findings-file",
            "--findings",
            dest="findings_file",
            help=f"{noun} JSON file, or '-' to read stdin (--findings is a compatibility alias)",
        )
        group.add_argument(
            "--findings-json",
            help=f"inline {noun} JSON (array or {{findings:[...]}})",
        )

    p_dim = sub.add_parser("dimensions", help="table-lookup the review dimensions for this risk/mode")
    p_dim.add_argument("--scope", default=None, help="git range under review (informational)")
    # No argparse `choices`: cmd_dimensions validates --risk/--mode itself so an
    # unknown value exits 2 (usage error) with our message and selftest can
    # exercise that path — same convention as loop_state.cmd_record/--kind.
    p_dim.add_argument("--risk", default="normal", help="risk level: low|normal|high")
    p_dim.add_argument("--mode", default="concise", help="review mode: concise|full_conformance")
    add_json(p_dim)
    p_dim.set_defaults(func=cmd_dimensions)

    p_find = sub.add_parser("find", help="deterministic pre-processing: preflight drafts + fan-out matrix")
    p_find.add_argument("--scope", default=None, help="git range (passed to git diff --name-only)")
    p_find.add_argument("--preflight", default=None, help="path to a captured preflight/verify-rules log")
    add_json(p_find)
    p_find.set_defaults(func=cmd_find)

    p_av = sub.add_parser("adversarial-verify", help="run the two gate triggers (recall; verdict stays the model's)")
    add_findings_input(p_av, "raw findings")
    add_json(p_av)
    p_av.set_defaults(func=cmd_adversarial_verify)

    p_dedup = sub.add_parser("dedup", help="normalize-key dedup + severity sort + R-00x numbering")
    add_findings_input(p_dedup, "verified findings")
    add_json(p_dedup)
    p_dedup.set_defaults(func=cmd_dedup)

    p_self = sub.add_parser("selftest", help="run fixed-input pruning/dedup/trigger asserts")
    p_self.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
