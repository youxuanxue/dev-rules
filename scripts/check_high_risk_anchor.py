#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

from preflight_common import (
    changed_paths,
    cli_fail,
    commit_text,
    compile_patterns,
    matches_any,
    parse_ini_sections,
)

DEFAULTS = {
    "high_risk_paths": [
        r"^migrations?/",
        r"^db/migrations?/",
        r"^schema/",
    ],
    "anchor_paths": [
        r"^docs/approved/.*\.md$",
    ],
    "anchor_tokens": [
        r"high[-_ ]risk[-_ ]anchor",
        r"approved[-_ ]anchor",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Require approval anchor for high-risk changes.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/high-risk-anchor.conf",
        help="optional rules file with [high_risk_paths], [anchor_paths], [anchor_tokens]",
    )
    args = parser.parse_args()

    cfg_path = pathlib.Path(args.rules)
    cfg = parse_ini_sections(cfg_path, DEFAULTS)

    if not cfg["high_risk_paths"]:
        if cfg_path.is_file():
            sys.stderr.write("[check_high_risk_anchor] config error: [high_risk_paths] is empty\n")
            return 2
        print("[check_high_risk_anchor] skip: no high-risk path patterns configured")
        return 0

    high_re = compile_patterns(cfg["high_risk_paths"])
    anchor_re = compile_patterns(cfg["anchor_paths"])
    token_re = compile_patterns(cfg["anchor_tokens"], ignore_case=True)

    try:
        paths = changed_paths(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    risky = [p for p in paths if matches_any(p, high_re)]
    if not risky:
        print("[check_high_risk_anchor] no high-risk paths changed")
        return 0

    if any(matches_any(p, anchor_re) for p in paths):
        print("[check_high_risk_anchor] anchor present (docs/approved evidence)")
        return 0

    try:
        text = commit_text(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    if matches_any(text, token_re):
        print("[check_high_risk_anchor] anchor present (commit token)")
        return 0

    return cli_fail(
        "check_high_risk_anchor",
        "high-risk changes require approval anchor (docs/approved/* or commit token like 'high-risk-anchor')",
        *risky,
    )


if __name__ == "__main__":
    sys.exit(main())
