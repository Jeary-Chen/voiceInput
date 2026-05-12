"""Prepare manual release notes before pushing a release tag."""
from __future__ import annotations

import argparse
from pathlib import Path

from generate_release_body import (
    Commit,
    DEFAULT_REPO_URL,
    MANUAL_NOTES_DIR,
    _collect_commits,
    _detect_repo_url,
    _previous_tag,
    render_release_body,
)


def _normalize_tag(tag: str) -> str:
    tag = (tag or "").strip().rsplit("/", 1)[-1]
    if not tag:
        raise ValueError("tag is required")
    return tag if tag.lower().startswith("v") else f"v{tag}"


def _version_from_tag(tag: str) -> str:
    return _normalize_tag(tag).lstrip("vV")


def release_notes_path(tag: str, *, root: Path = Path(".")) -> Path:
    return root / MANUAL_NOTES_DIR / f"{_normalize_tag(tag)}.md"


def write_release_notes(
    tag: str,
    *,
    root: Path = Path("."),
    repo_url: str = DEFAULT_REPO_URL,
    force: bool = False,
    previous_tag: str | None = None,
    commits: list[Commit] | None = None,
) -> tuple[Path, bool]:
    current_tag = _normalize_tag(tag)
    path = release_notes_path(current_tag, root=root)
    if path.exists() and not force:
        return path, False

    previous = _previous_tag(current_tag) if previous_tag is None else previous_tag
    release_commits = _collect_commits(previous, current_tag) if commits is None else commits
    body = render_release_body(
        version=_version_from_tag(current_tag),
        previous_tag=previous,
        current_tag=current_tag,
        commits=release_commits,
        repo_url=repo_url,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path, True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate .github/release-notes/<tag>.md before pushing a release tag."
    )
    parser.add_argument("tag", help="Release tag, for example v1.2.6")
    parser.add_argument("--repo-url", default="", help="GitHub repository URL")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing manual notes file")
    args = parser.parse_args()

    repo_url = args.repo_url or _detect_repo_url()
    path, written = write_release_notes(args.tag, repo_url=repo_url, force=args.force)
    if written:
        print(f"Wrote {path}")
        print("Review and edit this file before committing and pushing the release tag.")
    else:
        print(f"Kept existing {path}")
        print("Use --force only if you intentionally want to regenerate it.")


if __name__ == "__main__":
    main()
