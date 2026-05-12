"""Generate GitHub release notes from commits since the previous tag."""
from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_REPO_URL = "https://github.com/myuan19/voiceInput"


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    author: str = ""


@dataclass(frozen=True)
class ParsedSubject:
    kind: str
    scope: str
    text: str
    breaking: bool = False


_CONVENTIONAL_RE = re.compile(
    r"^(?P<kind>[a-zA-Z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<text>.+)$"
)

_SECTIONS = [
    ("feat", "新增"),
    ("fix", "修复"),
    ("perf", "性能优化"),
    ("refactor", "重构"),
]

_SKIP_SUBJECTS = (
    "chore: release",
    "chore(release):",
)


def _run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def _detect_repo_url() -> str:
    try:
        url = _run_git(["config", "--get", "remote.origin.url"])
    except subprocess.CalledProcessError:
        return DEFAULT_REPO_URL
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url.removeprefix("git@github.com:")
    if url.endswith(".git"):
        url = url[:-4]
    return url or DEFAULT_REPO_URL


def _previous_tag(current_tag: str) -> str:
    rev = f"{current_tag}^" if current_tag else "HEAD^"
    try:
        return _run_git(["describe", "--tags", "--abbrev=0", rev])
    except subprocess.CalledProcessError:
        return ""


def _collect_commits(previous_tag: str, current_tag: str) -> list[Commit]:
    rev_range = f"{previous_tag}..{current_tag}" if previous_tag else current_tag
    raw = _run_git([
        "log",
        "--no-merges",
        "--pretty=format:%H%x1f%s%x1f%an%x1e",
        rev_range,
    ])
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) >= 3:
            commits.append(Commit(parts[0], parts[1], parts[2]))
    return commits


def _parse_subject(subject: str) -> ParsedSubject:
    match = _CONVENTIONAL_RE.match(subject.strip())
    if not match:
        return ParsedSubject("other", "", subject.strip())
    kind = match.group("kind").lower()
    scope = match.group("scope") or ""
    text = match.group("text").strip()
    breaking = bool(match.group("breaking"))
    return ParsedSubject(kind, scope, text, breaking)


def _should_skip(commit: Commit) -> bool:
    subject = commit.subject.strip().lower()
    return any(subject.startswith(prefix) for prefix in _SKIP_SUBJECTS)


def _commit_line(commit: Commit, repo_url: str) -> str:
    parsed = _parse_subject(commit.subject)
    scope = f"**{parsed.scope}:** " if parsed.scope else ""
    sha = commit.sha[:7]
    suffix = f" ([`{sha}`]({repo_url}/commit/{commit.sha}))" if commit.sha else ""
    return f"- {scope}{parsed.text}{suffix}"


def _author_handle(author: str) -> str:
    author = author.strip()
    if not author:
        return ""
    if author.startswith("@"):
        return author
    if " " in author:
        return author
    return f"@{author}"


def render_release_body(
    *,
    version: str,
    previous_tag: str,
    current_tag: str,
    commits: list[Commit],
    repo_url: str = DEFAULT_REPO_URL,
) -> str:
    grouped: dict[str, list[Commit]] = {key: [] for key, _ in _SECTIONS}
    authors: list[str] = []
    seen_authors: set[str] = set()

    for commit in commits:
        if _should_skip(commit):
            continue
        parsed = _parse_subject(commit.subject)
        key = parsed.kind
        if key not in grouped:
            continue
        grouped[key].append(commit)
        author = _author_handle(commit.author)
        if author and author not in seen_authors:
            seen_authors.add(author)
            authors.append(author)

    lines = [f"## VoiceInput v{version}", ""]

    if previous_tag:
        lines.extend([
            f"**完整变更**：[{previous_tag}...{current_tag}]({repo_url}/compare/{previous_tag}...{current_tag})",
            "",
        ])

    emitted = False
    for key, title in _SECTIONS:
        items = grouped[key]
        if not items:
            continue
        emitted = True
        lines.append(f"### {title}")
        lines.extend(_commit_line(commit, repo_url) for commit in items)
        lines.append("")

    if authors:
        lines.append("### 贡献者")
        lines.append(", ".join(authors))
        lines.append("")

    lines.extend([
        "### 下载",
        "",
        "| 文件 | 说明 |",
        "|------|------|",
        f"| `VoiceInput-{version}-setup.exe` | 安装包（推荐） |",
        f"| `VoiceInput-{version}-portable.zip` | 便携版（解压即用） |",
        f"| `VoiceInput-{version}-portable.exe` | 单文件版（体积较大，启动较慢） |",
        "",
        "---",
        "**系统要求**：Windows 10/11 64-bit",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate release notes from git history.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--current-tag", required=True)
    parser.add_argument("--output", default="release_body.md")
    parser.add_argument("--repo-url", default="")
    args = parser.parse_args()

    repo_url = args.repo_url or _detect_repo_url()
    previous_tag = _previous_tag(args.current_tag)
    commits = _collect_commits(previous_tag, args.current_tag)
    body = render_release_body(
        version=args.version,
        previous_tag=previous_tag,
        current_tag=args.current_tag,
        commits=commits,
        repo_url=repo_url,
    )
    Path(args.output).write_text(body, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
