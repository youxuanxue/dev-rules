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
    run_git,
)

_WEB_SURFACE = [
    r"^frontend/",
    r"^web/",
    r"^client/",
    r"^ui/",
    r"^dashboard/",
    r"^admin/",
    r"^pages/",
    r"^components/",
    r"^app/(?:routes?|pages|components|.*\.(?:tsx|jsx|vue|svelte)$)",
    r"^src/(?:pages|components|views|routes?|app|web|ui)/",
    r"^apps/[^/]+/(?:web|frontend|client|ui|dashboard|admin)/",
    r"^.*\.(?:tsx|jsx|vue|svelte)$",
]

DEFAULTS = {
    "backend_paths": [
        r"^backend/",
        r"^server/",
        r"^service/",
        r"^services/",
        r"^api/",
        r"^routes?/",
        r"^controllers?/",
        r"^handlers?/",
        r"^domain/",
        r"^core/",
        r"^internal/",
        r"^pkg/",
        r"^cmd/",
        r"^app/",
        r"^apps/[^/]+/(?:api|server|backend|service|services)/",
        r"^src/(?:api|server|backend|service|services|routes?|controllers?|handlers?|domain|core)/",
    ],
    "web_surface_paths": _WEB_SURFACE,
    "alignment_paths": [
        *_WEB_SURFACE,
        r"^docs/(?:agent_integration\.md|openapi(?:/.*|\.ya?ml|\.json)?|spec-delta-|approved/)",
        r"^openapi(?:/.*|\.ya?ml|\.json)?$",
        r"^api/(?:openapi|contract)(?:/.*|\.ya?ml|\.json)?$",
        r"^schemas?/",
        r"^scripts/export_agent_contract\.py$",
        r"^\.testing/user-stories/",
    ],
    "web_roots": [
        r"^frontend$",
        r"^web$",
        r"^client$",
        r"^ui$",
        r"^dashboard$",
        r"^admin$",
        r"^apps/[^/]+/(?:web|frontend|client|ui|dashboard|admin)$",
        r"^zw-brain-web$",
        r"^zw-brain-dashboard$",
    ],
    "justification_tokens": [
        r"no[-_ ]web[-_ ]impact",
        r"web[-_ ]impact:\s*none",
        r"ui[-_ ]impact:\s*none",
        r"frontend[-_ ]impact:\s*none",
    ],
}


def repo_paths() -> list[str]:
    out = run_git(["ls-files"])
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    roots = set()
    for p in paths:
        parts = p.split("/")
        for idx in range(1, min(len(parts), 4) + 1):
            roots.add("/".join(parts[:idx]))
    return sorted(set(paths) | roots)


def staged_paths() -> list[str]:
    out = run_git(["diff", "--cached", "--name-only"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require Web/config/contract alignment evidence for backend or business-logic changes."
    )
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/web-surface-alignment.conf",
        help="optional rules file with [backend_paths], [web_surface_paths], [alignment_paths], [web_roots], [justification_tokens]",
    )
    args = parser.parse_args()

    cfg_path = pathlib.Path(args.rules)
    cfg = parse_ini_sections(cfg_path, DEFAULTS)

    if cfg_path.is_file():
        if not cfg["backend_paths"]:
            sys.stderr.write("[check_web_surface_alignment] config error: [backend_paths] is empty\n")
            return 2
        if not cfg["web_roots"]:
            sys.stderr.write("[check_web_surface_alignment] config error: [web_roots] is empty\n")
            return 2

    backend_re = compile_patterns(cfg["backend_paths"])

    try:
        paths = changed_paths(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    backend_changed = [p for p in paths if matches_any(p, backend_re)]
    if not backend_changed:
        print("[check_web_surface_alignment] no backend/business-logic paths changed")
        return 0

    try:
        all_paths = repo_paths()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    web_surface_re = compile_patterns(cfg["web_surface_paths"])
    web_root_re = compile_patterns(cfg["web_roots"])
    if not any(matches_any(p, web_root_re) or matches_any(p, web_surface_re) for p in all_paths):
        print("[check_web_surface_alignment] skip: no Web surface detected")
        return 0

    alignment_re = compile_patterns(cfg["alignment_paths"])
    if any(matches_any(p, alignment_re) for p in paths):
        print("[check_web_surface_alignment] Web/config/contract alignment evidence present")
        return 0

    try:
        staged_alignment_paths = [p for p in staged_paths() if matches_any(p, alignment_re)]
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2
    if staged_alignment_paths:
        print("[check_web_surface_alignment] staged Web/config/contract alignment evidence present")
        return 0

    try:
        text = commit_text(args.base, fallback_head=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    token_re = compile_patterns(cfg["justification_tokens"], ignore_case=True)
    if any(rx.search(text) for rx in token_re):
        print("[check_web_surface_alignment] explicit no-web-impact justification present")
        return 0

    return cli_fail(
        "check_web_surface_alignment",
        "backend/business-logic changes require Web/config/contract alignment evidence "
        "(or commit token like 'no-web-impact')",
        *backend_changed,
    )


if __name__ == "__main__":
    sys.exit(main())
