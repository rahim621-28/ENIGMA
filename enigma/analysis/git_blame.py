"""Git blame correlation: attribute a failing line to a commit/author.

Degrades gracefully (returns None, None) when the repo isn't a git repo or
the file isn't tracked -- this is common in the eval scenarios, which are
plain directories, not git repos.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def blame_line(repo_path: str, file_path: str, line: int) -> tuple[str | None, str | None]:
    """Return (commit_sha, author) for the given line, or (None, None) if unavailable."""
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        return None, None

    try:
        rel_path = str(Path(file_path).resolve().relative_to(repo))
    except ValueError:
        rel_path = file_path

    try:
        result = subprocess.run(
            ["git", "blame", "-L", f"{line},{line}", "--porcelain", rel_path],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, None

    if result.returncode != 0:
        return None, None

    commit_sha = None
    author = None
    for out_line in result.stdout.splitlines():
        if commit_sha is None and len(out_line.split()) >= 1 and len(out_line.split()[0]) == 40:
            commit_sha = out_line.split()[0]
        if out_line.startswith("author "):
            author = out_line[len("author "):]
    return commit_sha, author
