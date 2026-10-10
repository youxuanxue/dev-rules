#!/usr/bin/env bash
#
# install-upstream-merge-notify.sh — install detection-only upstream drift notify
# into a consumer project when it has an `upstream` git remote.
#
# Copies managed payload from templates/upstream-merge-notify/ into:
#   .github/workflows/upstream-merge-notify.yml
#   scripts/lib/upstream-drift.sh
#   scripts/upstream/check-drift.sh
#   scripts/upstream/notify-merge-needed.py
#
# Policy:
#   - no upstream remote → no-op (exit 0)
#   - missing managed file → install
#   - existing file with managed-by marker → refresh
#   - existing file without marker → skip (preserve project-specific forks)
#
# Usage:
#   bash templates/install-upstream-merge-notify.sh [project_root]
#   bash templates/install-upstream-merge-notify.sh --selftest
#
# Called automatically from sync.sh sync_to_project when a project registers.

set -euo pipefail

MANAGED_MARKER="managed-by: dev-rules/templates/upstream-merge-notify"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="${SCRIPT_DIR}/upstream-merge-notify"

normalize_https_github_url() {
  local url="$1"
  case "$url" in
    git@github.com:*)
      local rest="${url#git@github.com:}"
      printf 'https://github.com/%s\n' "$rest"
      ;;
    ssh://git@github.com/*)
      local rest="${url#ssh://git@github.com/}"
      printf 'https://github.com/%s\n' "$rest"
      ;;
    *)
      printf '%s\n' "$url"
      ;;
  esac
}

is_managed_file() {
  local path="$1"
  [ -f "$path" ] && grep -qF "$MANAGED_MARKER" "$path"
}

should_write() {
  local dest="$1"
  if [ ! -e "$dest" ]; then
    return 0
  fi
  if is_managed_file "$dest"; then
    return 0
  fi
  return 1
}

install_into() {
  local project_dir="$1"
  project_dir="$(cd "$project_dir" && pwd)"

  if ! git -C "$project_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "  SKIP upstream-merge-notify: not a git checkout ($project_dir)"
    return 0
  fi

  local upstream_url
  if ! upstream_url="$(git -C "$project_dir" remote get-url upstream 2>/dev/null)"; then
    echo "  SKIP upstream-merge-notify: no upstream remote in $(basename "$project_dir")"
    return 0
  fi
  upstream_url="$(normalize_https_github_url "$upstream_url")"

  if [ ! -d "$PAYLOAD_DIR" ]; then
    echo "  FAIL: payload missing at $PAYLOAD_DIR" >&2
    return 1
  fi

  local rels=(
    ".github/workflows/upstream-merge-notify.yml"
    "scripts/lib/upstream-drift.sh"
    "scripts/lib/upstream-url.conf"
    "scripts/upstream/check-drift.sh"
    "scripts/upstream/notify-merge-needed.py"
  )

  local changed=0 skipped=0
  for rel in "${rels[@]}"; do
    local src="$PAYLOAD_DIR/$rel"
    local dest="$project_dir/$rel"
    if [ ! -f "$src" ]; then
      echo "  FAIL: template file missing: $src" >&2
      return 1
    fi
    if ! should_write "$dest"; then
      echo "  skip (custom): $rel → $(basename "$project_dir")"
      skipped=$((skipped + 1))
      continue
    fi
    local rendered
    rendered="$(mktemp)"
    python3 - "$src" "$rendered" "$upstream_url" <<'PY'
import pathlib, sys
src, dest, url = sys.argv[1], sys.argv[2], sys.argv[3]
text = pathlib.Path(src).read_text(encoding="utf-8").replace("__UPSTREAM_URL__", url)
pathlib.Path(dest).write_text(text, encoding="utf-8")
PY
    if [ -f "$dest" ] && diff -q "$rendered" "$dest" >/dev/null 2>&1; then
      rm -f "$rendered"
      continue
    fi
    mkdir -p "$(dirname "$dest")"
    mv "$rendered" "$dest"
    if [[ "$dest" == *.sh ]] || [[ "$dest" == *.py ]]; then
      chmod +x "$dest" || true
    fi
    echo "  installed: $rel → $(basename "$project_dir")"
    changed=$((changed + 1))
  done

  if [ "$changed" -eq 0 ] && [ "$skipped" -eq 0 ]; then
    echo "  ok: upstream-merge-notify up to date in $(basename "$project_dir")"
  elif [ "$changed" -eq 0 ]; then
    echo "  ok: upstream-merge-notify left custom files untouched in $(basename "$project_dir")"
  fi
  return 0
}

run_selftest() {
  local failures=0
  local tmp tmp2
  tmp="$(mktemp -d)"
  tmp2="$(mktemp -d)"

  # Case 1: no upstream → skip, no files.
  git -C "$tmp" init -q
  git -C "$tmp" remote add origin https://github.com/example/fork.git
  if ! bash "$SCRIPT_DIR/install-upstream-merge-notify.sh" "$tmp" >/tmp/umn-selftest-1.log 2>&1; then
    echo "FAIL install_no_upstream_exit"
    failures=$((failures + 1))
  else
    echo "PASS install_no_upstream_exit"
  fi
  if [ -f "$tmp/.github/workflows/upstream-merge-notify.yml" ]; then
    echo "FAIL install_no_upstream_no_files"
    failures=$((failures + 1))
  else
    echo "PASS install_no_upstream_no_files"
  fi

  # Case 2: with upstream → install managed files with rendered URL.
  git -C "$tmp" remote add upstream git@github.com:example/upstream.git
  if ! bash "$SCRIPT_DIR/install-upstream-merge-notify.sh" "$tmp" >/tmp/umn-selftest-2.log 2>&1; then
    echo "FAIL install_with_upstream_exit"
    failures=$((failures + 1))
  else
    echo "PASS install_with_upstream_exit"
  fi
  if grep -qF "https://github.com/example/upstream.git" "$tmp/.github/workflows/upstream-merge-notify.yml" \
    && grep -qF "$MANAGED_MARKER" "$tmp/.github/workflows/upstream-merge-notify.yml" \
    && grep -qF "https://github.com/example/upstream.git" "$tmp/scripts/lib/upstream-url.conf"; then
    echo "PASS install_renders_https_url"
  else
    echo "FAIL install_renders_https_url"
    failures=$((failures + 1))
  fi

  # Case 3: custom unmanaged file is preserved.
  git -C "$tmp2" init -q
  git -C "$tmp2" remote add origin https://github.com/example/fork.git
  git -C "$tmp2" remote add upstream https://github.com/example/upstream.git
  mkdir -p "$tmp2/.github/workflows"
  printf 'custom workflow\n' > "$tmp2/.github/workflows/upstream-merge-notify.yml"
  bash "$SCRIPT_DIR/install-upstream-merge-notify.sh" "$tmp2" >/tmp/umn-selftest-3.log 2>&1 || true
  if grep -qx "custom workflow" "$tmp2/.github/workflows/upstream-merge-notify.yml"; then
    echo "PASS install_preserves_custom"
  else
    echo "FAIL install_preserves_custom"
    failures=$((failures + 1))
  fi

  # Payload self-test.
  if python3 "$PAYLOAD_DIR/scripts/upstream/notify-merge-needed.py" --selftest >/tmp/umn-selftest-py.log 2>&1; then
    echo "PASS payload_python_selftest"
  else
    echo "FAIL payload_python_selftest"
    cat /tmp/umn-selftest-py.log >&2 || true
    failures=$((failures + 1))
  fi

  rm -rf "$tmp" "$tmp2"

  if [ "$failures" -ne 0 ]; then
    echo "install-upstream-merge-notify self-test FAILED ($failures)"
    return 1
  fi
  echo "install-upstream-merge-notify self-test OK"
  return 0
}

main() {
  if [ "${1:-}" = "--selftest" ]; then
    run_selftest
    return $?
  fi
  local project_dir="${1:-.}"
  install_into "$project_dir"
}

main "$@"
