#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

from preflight_common import changed_paths, commit_text, compile_patterns, matches_any, run_git

DEFAULT_BACKEND_PATTERNS = [
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
]

DEFAULT_WEB_SURFACE_PATTERNS = [
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

DEFAULT_ALIGNMENT_PATTERNS = [
    *DEFAULT_WEB_SURFACE_PATTERNS,
    r"^docs/(?:agent_integration\.md|openapi(?:/.*|\.ya?ml|\.json)?|spec-delta-|approved/)",
    r"^openapi(?:/.*|\.ya?ml|\.json)?$",
    r"^api/(?:openapi|contract)(?:/.*|\.ya?ml|\.json)?$",
    r"^schemas?/",
    r"^scripts/export_agent_contract\.py$",
    r"^\.testing/user-stories/",
]

DEFAULT_WEB_ROOT_MARKERS = [
    r"^frontend$",
    r"^web$",
    r"^client$",
    r"^ui$",
    r"^dashboard$",
    r"^admin$",
    r"^apps/[^/]+/(?:web|frontend|client|ui|dashboard|admin)$",
    r"^zw-brain-web$",
    r"^zw-brain-dashboard$",
]

DEFAULT_JUSTIFICATION_TOKENS = [
    r"no[-_ ]web[-_ ]impact",
    r"web[-_ ]impact:\s*none",
    r"ui[-_ ]impact:\s*none",
    r"frontend[-_ ]impact:\s*none",
]


def parse_config(path: pathlib.Path | None) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    backend = list(DEFAULT_BACKEND_PATTERNS)
    web_surface = list(DEFAULT_WEB_SURFACE_PATTERNS)
    alignment = list(DEFAULT_ALIGNMENT_PATTERNS)
    web_roots = list(DEFAULT_WEB_ROOT_MARKERS)
    tokens = list(DEFAULT_JUSTIFICATION_TOKENS)

    if path and path.is_file():
        text = path.read_text(encoding="utf-8")
        mode: str | None = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s == "[backend_paths]":
                mode = "backend"
                backend = []
                continue
            if s == "[web_surface_paths]":
                mode = "web_surface"
                web_surface = []
                continue
            if s == "[alignment_paths]":
                mode = "alignment"
                alignment = []
                continue
            if s == "[web_roots]":
                mode = "web_roots"
                web_roots = []
                continue
            if s == "[justification_tokens]":
                mode = "tokens"
                tokens = []
                continue
            if mode == "backend":
                backend.append(s)
            elif mode == "web_surface":
                web_surface.append(s)
            elif mode == "alignment":
                alignment.append(s)
            elif mode == "web_roots":
                web_roots.append(s)
            elif mode == "tokens":
                tokens.append(s)

    return backend, web_surface, alignment, web_roots, tokens


def repo_paths() -> list[str]:
    out = run_git(["ls-files"])
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    roots = set()
    for p in paths:
        parts = p.split("/")
        for idx in range(1, min(len(parts), 4) + 1):
            roots.add("/".join(parts[:idx]))
    return sorted(set(paths) | roots)


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

    cfg = pathlib.Path(args.rules)
    backend_raw, web_surface_raw, alignment_raw, web_roots_raw, tokens_raw = parse_config(cfg if cfg.exists() else None)

    if cfg.exists() and not backend_raw:
        sys.stderr.write("[check_web_surface_alignment] config error: [backend_paths] is empty\n")
        return 2
    if cfg.exists() and not web_roots_raw:
        sys.stderr.write("[check_web_surface_alignment] config error: [web_roots] is empty\n")
        return 2

    backend_re = compile_patterns(backend_raw)

    try:
        paths = changed_paths(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    backend_changed = [p for p in paths if matches_any(p, backend_re)]
    if not backend_changed:
        print("[check_web_surface_alignment] no backend/business-logic paths changed")
        return 0

    web_surface_re = compile_patterns(web_surface_raw)
    web_root_re = compile_patterns(web_roots_raw)
    try:
        all_paths = repo_paths()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    has_web_surface = any(matches_any(p, web_root_re) or matches_any(p, web_surface_re) for p in all_paths)
    if not has_web_surface:
        print("[check_web_surface_alignment] skip: no Web surface detected")
        return 0

    alignment_re = compile_patterns(alignment_raw)
    alignment_changed = [p for p in paths if matches_any(p, alignment_re)]
    if alignment_changed:
        print("[check_web_surface_alignment] Web/config/contract alignment evidence present")
        return 0

    try:
        text = commit_text(args.base, fallback_head=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    token_re = compile_patterns(tokens_raw, ignore_case=True)
    if any(rx.search(text) for rx in token_re):
        print("[check_web_surface_alignment] explicit no-web-impact justification present")
        return 0

    sys.stderr.write("[check_web_surface_alignment] backend/business-logic changes require Web surface alignment evidence\n")
    sys.stderr.write("Backend/business-logic changed paths:\n")
    for p in backend_changed:
        sys.stderr.write(f"  - {p}\n")
    sys.stderr.write(
        "No Web/config/contract/story evidence found. Update the relevant Web surface/config/contract in the same PR, "
        "or add an explicit justification token such as 'no-web-impact' / 'Web impact: none'.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
