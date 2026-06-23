#!/usr/bin/env python3
"""Claude Code hook: enforce rules around `gh pr` operations.

PreToolUse (`pre-merge` subcommand):
  - Block `gh pr merge` invocations. Merge requires explicit user authorization
    in conversation, not auto-firing after PR-create or as a follow-up tool call.
  - Block PR creates / body edits whose body is not Chinese or is stale versus
    the current branch commits.
  - Block `git push` for an existing PR when the PR body has not been rewritten
    for the current branch HEAD.
  exit 2 + stderr blocks the tool call.

PostToolUse (`post-pr-create` subcommand):
  - After `gh pr create` runs, inject a `decision: block` reason that pushes
    the assistant into the /xj-review loop with strict merge-ready criteria.
  - After `git commit` on an open PR branch, inject a `decision: block` reason
    if the PR body has not been rewritten for the new HEAD.

Key correctness property: we MUST distinguish a real command-leading
`gh pr merge` from an incidental literal substring (`echo "gh pr merge"`,
`grep "gh pr merge"`, etc). We do this by:

  1. Splitting the command on shell separators: && || ; |
  2. Stripping leading env-var prefixes (NAME=value NAME2=value2 ...)
  3. Checking the remaining head of each segment starts with the target.

This catches:
  - `gh pr merge 48 --auto`
  - `cd /path && gh pr merge`
  - `ENV=x gh pr merge`

And ignores:
  - `echo "gh pr merge"`
  - `grep 'gh pr merge' README.md`
  - `git log | grep merge`
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


_QUOTED_SINGLE = re.compile(r"'[^']*'")
_QUOTED_DOUBLE = re.compile(r'"[^"]*"')
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_ENV_ASSIGN_RE = re.compile(r"^[A-Z_][A-Z0-9_]*=")
_REQUIRED_BODY_SECTIONS = ("摘要", "风险", "验证", "提交")
_GIT_CONTEXT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


def _split_segments(cmd: str) -> list[str]:
    """Split shell command on common separators outside simple quotes."""
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if escaped:
            current.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            i += 1
            continue
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            i += 2
            continue
        if ch in (";", "|"):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _segment_tokens(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return []
    while tokens and _ENV_ASSIGN_RE.match(tokens[0]):
        tokens.pop(0)
    return tokens


def _is_gh_pr(tokens: list[str], subcommand: str) -> bool:
    if not tokens or tokens[0] != "gh":
        return False
    for i, token in enumerate(tokens[1:], start=1):
        if token == "pr":
            return i + 1 < len(tokens) and tokens[i + 1] == subcommand
    return False


def _git_subcommand_cwd(tokens: list[str], cwd: str | None, subcommand: str) -> str | None:
    if not tokens or tokens[0] != "git":
        return None
    effective_cwd = cwd
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "-C" and i + 1 < len(tokens):
            effective_cwd = _resolve_path(tokens[i + 1], effective_cwd)
            i += 2
            continue
        if token == "--":
            i += 1
            continue
        if token == subcommand:
            return effective_cwd
        if token.startswith("-"):
            i += 1
            continue
        return None
    return None


def _git_push_cwd(tokens: list[str], cwd: str | None) -> str | None:
    return _git_subcommand_cwd(tokens, cwd, "push")


def _git_commit_cwd(tokens: list[str], cwd: str | None) -> str | None:
    return _git_subcommand_cwd(tokens, cwd, "commit")


def _resolve_path(path: str, cwd: str | None) -> str:
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(cwd or os.getcwd(), expanded))


def _cd_cwd(tokens: list[str], cwd: str | None) -> str | None:
    if not tokens or tokens[0] != "cd":
        return None
    if len(tokens) == 1:
        return os.path.expanduser("~")
    target = tokens[1]
    if target == "-":
        return cwd
    return _resolve_path(target, cwd)


def _strip_quoted(cmd: str) -> str:
    """Replace quoted regions with empty placeholders so shell-separator splits
    don't fire inside literal strings (e.g. `echo 'gh pr merge'`).

    Note: does not handle nested or escaped quotes. Threat model is accidental
    auto-merge by the assistant, not adversarial shell crafting.
    """
    cmd = _QUOTED_SINGLE.sub("''", cmd)
    cmd = _QUOTED_DOUBLE.sub('""', cmd)
    return cmd


def command_leading_matches(cmd: str, head_pattern: str) -> bool:
    """True if any shell sub-segment starts with the given regex (after env-var prefix)."""
    if not cmd:
        return False
    cmd = _strip_quoted(cmd)
    head_re = re.compile(head_pattern)
    env_prefix_re = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+")
    for s in _split_segments(cmd):
        s = s.strip()
        s = env_prefix_re.sub("", s)
        if head_re.match(s):
            return True
    return False


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in _GIT_CONTEXT_ENV:
        env.pop(key, None)
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _git(args: list[str], cwd: str | None = None) -> str:
    result = _run(["git", *args], cwd=cwd)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _option_value(tokens: list[str], option: str | tuple[str, ...]) -> str | None:
    options = (option,) if isinstance(option, str) else option
    prefixes = tuple(f"{item}=" for item in options)
    for i, token in enumerate(tokens):
        if token in options:
            if i + 1 < len(tokens):
                return tokens[i + 1]
            return ""
        for prefix in prefixes:
            if token.startswith(prefix):
                return token[len(prefix):]
    return None


def _current_branch(cwd: str | None) -> str:
    return _git(["branch", "--show-current"], cwd)


def _repo_root(cwd: str | None) -> str:
    return _git(["rev-parse", "--show-toplevel"], cwd)


def _body_from_tokens(tokens: list[str], cwd: str | None) -> tuple[str | None, str | None]:
    body = _option_value(tokens, ("--body", "-b"))
    body_file = _option_value(tokens, ("--body-file", "-F"))
    if body is not None:
        if "$(" in body or "`" in body:
            return None, "PR body 使用了命令替换，hook 无法验证；请改用 --body-file 指向已生成的中文 body 文件。"
        return body, None
    if body_file is None:
        return None, None
    if body_file in ("", "-"):
        return None, "PR body 文件不可验证；请改用 --body-file <path>。"
    path = Path(body_file)
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, f"无法读取 PR body 文件 {path}: {exc}"


def _looks_chinese(text: str) -> bool:
    cjk = len(_CJK_RE.findall(text))
    # Technical identifiers, commands and commit subjects may remain English.
    # The body still needs a meaningful amount of Chinese prose.
    return cjk >= 20


def _missing_sections(text: str) -> list[str]:
    return [section for section in _REQUIRED_BODY_SECTIONS if section not in text]


def _default_base_ref(cwd: str | None, base_name: str | None = None) -> str:
    candidates: list[str] = []
    if base_name:
        candidates.extend([f"origin/{base_name}", base_name])
    candidates.extend(["origin/main", "origin/master", "main", "master"])
    for candidate in candidates:
        if _git(["rev-parse", "--verify", "--quiet", candidate], cwd):
            return candidate
    return ""


def _commit_entries_for_body(cwd: str | None, base_name: str | None = None) -> list[tuple[str, str]]:
    if not _repo_root(cwd):
        return []
    head = _git(["rev-parse", "--short", "HEAD"], cwd)
    if not head:
        return []
    base_ref = _default_base_ref(cwd, base_name)
    if not base_ref:
        subject = _git(["log", "-1", "--format=%s", "HEAD"], cwd)
        return [(head, subject)]
    merge_base = _git(["merge-base", "HEAD", base_ref], cwd)
    if not merge_base:
        subject = _git(["log", "-1", "--format=%s", "HEAD"], cwd)
        return [(head, subject)]
    raw_entries = _git(["log", "--format=%h%x00%s", f"{merge_base}..HEAD"], cwd).splitlines()
    entries: list[tuple[str, str]] = []
    for raw in raw_entries:
        if "\x00" in raw:
            sha, subject = raw.split("\x00", 1)
        else:
            parts = raw.split(" ", 1)
            sha = parts[0]
            subject = parts[1] if len(parts) > 1 else ""
        if sha:
            entries.append((sha, subject))
    if entries:
        return entries
    subject = _git(["log", "-1", "--format=%s", "HEAD"], cwd)
    return [(head, subject)]


def validate_body_text(
    body: str,
    *,
    cwd: str | None = None,
    base_name: str | None = None,
    require_commit_refs: bool = True,
) -> list[str]:
    errors: list[str] = []
    if not body.strip():
        errors.append("PR body 不能为空。")
    elif not _looks_chinese(body):
        errors.append("PR body 必须用中文撰写；技术名词、命令和 commit subject 可保留英文。")
    missing_sections = _missing_sections(body)
    if missing_sections:
        errors.append("PR body 必须包含中文小节：" + "、".join(missing_sections) + "。")
    if require_commit_refs:
        entries = _commit_entries_for_body(cwd, base_name)
        missing = [sha for sha, _subject in entries if sha not in body]
        if missing:
            preview = ", ".join(missing[:8])
            more = "" if len(missing) <= 8 else f" 等 {len(missing)} 个"
            errors.append(
                "PR body 必须按当前 commits 重写，并在「提交」小节包含所有 commit short SHA；"
                f"缺少 {preview}{more}。"
            )
        missing_subjects = [
            f"{sha} {subject}"
            for sha, subject in entries
            if subject and subject not in body
        ]
        if missing_subjects:
            preview = "；".join(missing_subjects[:5])
            more = "" if len(missing_subjects) <= 5 else f" 等 {len(missing_subjects)} 个"
            errors.append(
                "PR body 的「提交」小节必须包含所有 commit subject；"
                f"缺少 {preview}{more}。"
            )
        head = _git(["rev-parse", "--short", "HEAD"], cwd)
        if head and not re.search(rf"最新提交[：:]\s*{re.escape(head)}\b", body):
            errors.append(f"PR body 必须包含 freshness anchor：`最新提交：{head}`。")
    return errors


def _body_guard_message(errors: list[str]) -> str:
    return (
        "PR body 不符合 dev-rules：\n"
        + "".join(f"- {error}\n" for error in errors)
        + "请先用中文重写完整 PR body，保留「摘要 / 风险 / 验证 / 提交」小节，"
        + "并用 `git log --oneline <base>..HEAD` 生成提交清单后再执行 gh 命令。\n"
    )


def _gh_pr_view_current(cwd: str | None) -> dict | None:
    result = _run(["gh", "pr", "view", "--json", "body,baseRefName,url"], cwd=cwd)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_input() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def _handle_pr_body_writes(cmd: str, cwd: str | None) -> int:
    effective_cwd = cwd
    for segment in _split_segments(cmd):
        tokens = _segment_tokens(segment)
        new_cwd = _cd_cwd(tokens, effective_cwd)
        if new_cwd is not None:
            effective_cwd = new_cwd
            continue
        if _is_gh_pr(tokens, "create"):
            body, body_error = _body_from_tokens(tokens, effective_cwd)
            if body_error:
                sys.stderr.write(_body_guard_message([body_error]))
                return 2
            if body is None:
                sys.stderr.write(
                    _body_guard_message(
                        [
                            "创建 PR 必须显式提供中文 body；禁止依赖 --fill、模板编辑器或空 body。"
                        ]
                    )
                )
                return 2
            base_name = _option_value(tokens, ("--base", "-B"))
            errors = validate_body_text(body, cwd=effective_cwd, base_name=base_name)
            if errors:
                sys.stderr.write(_body_guard_message(errors))
                return 2
        elif _is_gh_pr(tokens, "edit"):
            body, body_error = _body_from_tokens(tokens, effective_cwd)
            if body_error:
                sys.stderr.write(_body_guard_message([body_error]))
                return 2
            if body is None:
                continue
            pr = _gh_pr_view_current(effective_cwd)
            base_name = (pr or {}).get("baseRefName")
            errors = validate_body_text(body, cwd=effective_cwd, base_name=base_name)
            if errors:
                sys.stderr.write(_body_guard_message(errors))
                return 2
    return 0


def _handle_push_with_open_pr(cmd: str, cwd: str | None) -> int:
    effective_cwd = cwd
    for segment in _split_segments(cmd):
        tokens = _segment_tokens(segment)
        new_cwd = _cd_cwd(tokens, effective_cwd)
        if new_cwd is not None:
            effective_cwd = new_cwd
            continue
        push_cwd = _git_push_cwd(tokens, effective_cwd)
        if not push_cwd:
            continue
        if not _current_branch(push_cwd):
            continue
        pr = _gh_pr_view_current(push_cwd)
        if not pr:
            continue
        body = str(pr.get("body") or "")
        base_name = str(pr.get("baseRefName") or "")
        errors = validate_body_text(body, cwd=push_cwd, base_name=base_name)
        if errors:
            url = pr.get("url") or "当前 PR"
            sys.stderr.write(
                f"{url} 的 PR body 尚未按当前 commits 重写，禁止先 push 新 commit。\n"
                + _body_guard_message(errors)
            )
            return 2
    return 0


def _commit_cwds(cmd: str, cwd: str | None) -> list[str]:
    result: list[str] = []
    effective_cwd = cwd
    for segment in _split_segments(cmd):
        tokens = _segment_tokens(segment)
        new_cwd = _cd_cwd(tokens, effective_cwd)
        if new_cwd is not None:
            effective_cwd = new_cwd
            continue
        commit_cwd = _git_commit_cwd(tokens, effective_cwd)
        if commit_cwd:
            result.append(commit_cwd)
    return result


def _stale_pr_body_errors(cwd: str | None) -> tuple[dict | None, list[str]]:
    if not _current_branch(cwd):
        return None, []
    pr = _gh_pr_view_current(cwd)
    if not pr:
        return None, []
    body = str(pr.get("body") or "")
    base_name = str(pr.get("baseRefName") or "")
    return pr, validate_body_text(body, cwd=cwd, base_name=base_name)


def _contains_gh_pr_merge(cmd: str) -> bool:
    for segment in _split_segments(cmd):
        tokens = _segment_tokens(segment)
        if _is_gh_pr(tokens, "merge"):
            return True
    return False


def handle_pre_merge(cmd: str, cwd: str | None = None) -> int:
    if _contains_gh_pr_merge(cmd):
        sys.stderr.write(
            "禁止无授权 merge：本次会话尚未收到明确合并指令(如 \"合并 #XX\")。"
            "请先与用户确认；merge 不在 /xj-review 自动范围内。\n"
        )
        return 2
    body_status = _handle_pr_body_writes(cmd, cwd)
    if body_status:
        return body_status
    push_status = _handle_push_with_open_pr(cmd, cwd)
    if push_status:
        return push_status
    return 0


def handle_post_pr_create(cmd: str) -> bool:
    if any(_is_gh_pr(_segment_tokens(segment), "create") for segment in _split_segments(cmd)):
        payload = {
            "decision": "block",
            "reason": (
                "PR 已创建。下一步必做：调用 /xj-review 对本 PR 审查，按严格 "
                "merge-ready 准则（零 medium+ finding，包含 out-of-scope 顺手问题 "
                "与 Jobs / 确定性自动化运营和运维原则违背）循环 fix → re-review。达到 merge-ready "
                "后，**停下并在对话中等待用户的明确合并指令**；禁止直接调用 "
                "gh pr merge。PR body 必须保持中文；后续每新增 commit，先按当前 "
                "`git log --oneline <base>..HEAD` 重写 PR body，确保「提交」小节包含所有 "
                "commit short SHA，再 push 或继续 review。"
            ),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return True
    return False


def handle_post_pr_activity(cmd: str, cwd: str | None = None) -> bool:
    for commit_cwd in _commit_cwds(cmd, cwd):
        pr, errors = _stale_pr_body_errors(commit_cwd)
        if pr and errors:
            url = pr.get("url") or "当前 PR"
            payload = {
                "decision": "block",
                "reason": (
                    f"{url} 已有新 commit，但 PR body 尚未按当前 commits 重写。"
                    "下一步必须先用中文重写完整 PR body，保留「摘要 / 风险 / 验证 / 提交」小节，"
                    "在「提交」小节包含 `git log --oneline <base>..HEAD` 的所有 commit short SHA + subject，"
                    "并包含 `最新提交：<HEAD short SHA>`，然后执行 `gh pr edit --body-file <file>`。"
                    "当前缺口："
                    + "；".join(errors)
                ),
            }
            print(json.dumps(payload, ensure_ascii=False))
            return True
    return False


def _self_test() -> int:
    failures: list[str] = []
    if command_leading_matches("echo 'gh pr merge 1'", r"^gh\s+pr\s+merge\b"):
        failures.append("quoted gh pr merge should not match")
    if not command_leading_matches("cd repo && gh pr merge 1", r"^gh\s+pr\s+merge\b"):
        failures.append("command-leading gh pr merge should match after separator")
    if _contains_gh_pr_merge("echo 'gh pr merge 1'"):
        failures.append("token merge detector should ignore quoted echo")
    if not _contains_gh_pr_merge("gh -R owner/repo pr merge 1"):
        failures.append("gh -R owner/repo pr merge should be detected")
    tokens = _segment_tokens("FOO=bar gh pr create --body '摘要：中文说明足够长。风险：低。验证：已跑。提交：abc1234'")
    if not _is_gh_pr(tokens, "create"):
        failures.append("env-prefixed gh pr create tokenization failed")
    repo_tokens = _segment_tokens("gh -R owner/repo pr edit --body-file pr.md")
    if not _is_gh_pr(repo_tokens, "edit"):
        failures.append("gh -R owner/repo pr edit should be detected")
    if _git_push_cwd(_segment_tokens("git -C repo push origin HEAD"), "/tmp") != "/tmp/repo":
        failures.append("git -C repo push cwd detection failed")
    if _git_commit_cwd(_segment_tokens("git -C repo commit -m msg"), "/tmp") != "/tmp/repo":
        failures.append("git -C repo commit cwd detection failed")
    if _cd_cwd(_segment_tokens("cd repo"), "/tmp") != "/tmp/repo":
        failures.append("cd repo cwd tracking failed")
    body, error = _body_from_tokens(tokens, None)
    if error or not body or "中文说明" not in body:
        failures.append("--body extraction failed")
    short_body, short_error = _body_from_tokens(
        _segment_tokens("gh pr create -b '摘要：中文说明足够长。风险：低。验证：已跑。提交：abc1234'"),
        None,
    )
    if short_error or not short_body or "中文说明" not in short_body:
        failures.append("-b body extraction failed")
    if not any(_is_gh_pr(_segment_tokens(segment), "create") for segment in _split_segments("gh -R owner/repo pr create -F body.md")):
        failures.append("post create detector should handle gh -R owner/repo pr create")
    if _commit_cwds("cd repo && git commit -m msg", "/tmp") != ["/tmp/repo"]:
        failures.append("post commit cwd tracking failed")
    if not _looks_chinese("摘要：这里是中文说明，风险较低，验证已经完成，提交包含 abc1234。"):
        failures.append("Chinese body heuristic false negative")
    if _looks_chinese("Summary: update hook. Risk: low. Validation: tests."):
        failures.append("Chinese body heuristic false positive")
    if _missing_sections("## 摘要\n中文\n## 风险\n低\n## 验证\n已跑\n## 提交\nabc"):
        failures.append("required Chinese section detection false negative")
    if "提交" not in _missing_sections("## 摘要\n中文\n## 风险\n低\n## 验证\n已跑\n"):
        failures.append("required Chinese section detection false positive")
    errors = validate_body_text(
        "摘要：这里是中文说明。风险：风险较低。验证：已经完成自测。提交：包含 abc1234。",
        require_commit_refs=False,
    )
    if errors:
        failures.append(f"valid Chinese body rejected: {errors}")
    errors = validate_body_text("Summary only", require_commit_refs=False)
    if not errors:
        failures.append("English body should be rejected")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _run(["git", "init", "-q"], cwd=str(repo))
        _run(["git", "config", "user.email", "test@example.com"], cwd=str(repo))
        _run(["git", "config", "user.name", "Test User"], cwd=str(repo))
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        _run(["git", "add", "a.txt"], cwd=str(repo))
        first = _run(["git", "commit", "-q", "-m", "init baseline"], cwd=str(repo))
        if first.returncode != 0:
            failures.append(f"temp git first commit failed: {first.stderr.strip()}")
        _run(["git", "branch", "-M", "main"], cwd=str(repo))
        _run(["git", "checkout", "-q", "-b", "feature/pr-body"], cwd=str(repo))
        (repo / "a.txt").write_text("a\nb\n", encoding="utf-8")
        _run(["git", "add", "a.txt"], cwd=str(repo))
        second = _run(["git", "commit", "-q", "-m", "fix hook subject"], cwd=str(repo))
        if second.returncode != 0:
            failures.append(f"temp git second commit failed: {second.stderr.strip()}")
        sha = _git(["rev-parse", "--short", "HEAD"], str(repo))
        valid_body = (
            "## 摘要\n这里是中文说明，确保正文使用中文描述。\n\n"
            "## 风险\n风险较低。\n\n"
            "## 验证\n已经完成自测。\n\n"
            f"## 提交\n{sha} fix hook subject\n\n最新提交：{sha}\n"
        )
        git_errors = validate_body_text(valid_body, cwd=str(repo), base_name="main")
        if git_errors:
            failures.append(f"git-backed valid body rejected: {git_errors}")
        stale_errors = validate_body_text(valid_body.replace("fix hook subject", "wrong subject"), cwd=str(repo), base_name="main")
        if not any("commit subject" in error for error in stale_errors):
            failures.append("stale commit subject should be rejected")
    if failures:
        for failure in failures:
            print(f"[gh-pr-guard] FAIL: {failure}", file=sys.stderr)
        return 1
    print("[gh-pr-guard] self-test passed")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        return _self_test()
    if len(sys.argv) >= 2 and sys.argv[1] == "validate-body-file":
        if len(sys.argv) < 3:
            sys.stderr.write("usage: gh-pr-guard.py validate-body-file <path> [cwd] [base]\n")
            return 2
        path = Path(sys.argv[2])
        cwd = sys.argv[3] if len(sys.argv) >= 4 else None
        base_name = sys.argv[4] if len(sys.argv) >= 5 else None
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"无法读取 PR body 文件 {path}: {exc}\n")
            return 2
        errors = validate_body_text(body, cwd=cwd, base_name=base_name)
        if errors:
            sys.stderr.write(_body_guard_message(errors))
            return 2
        return 0
    if len(sys.argv) < 2:
        sys.stderr.write("usage: gh-pr-guard.py <pre-merge|post-pr-create|validate-body-file|--self-test>\n")
        return 2
    mode = sys.argv[1]
    data = load_input()
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or (data.get("tool_input") or {}).get("cwd") or os.getcwd()
    if mode == "pre-merge":
        return handle_pre_merge(cmd, cwd)
    if mode == "post-pr-create":
        if handle_post_pr_create(cmd):
            return 0
        handle_post_pr_activity(cmd, cwd)
        return 0
    sys.stderr.write(f"unknown hook mode: {mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
