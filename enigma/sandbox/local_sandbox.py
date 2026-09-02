"""Local sandbox: isolates execution via a stripped-down temp copy of the repo
plus subprocess-level guards. This is NOT a security boundary against a
truly malicious actor -- it is a blast-radius reducer against buggy
LLM-generated code (infinite loops, accidental file writes, etc).

For real adversarial isolation, use DockerSandbox (sandbox/docker_sandbox.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from enigma.graph.state import ReproductionResult, TestRunResult
from enigma.sandbox.base import BaseSandbox

_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", ".mypy_cache"}

# Redact by name rather than replacing the whole environment: a fully
# stripped env breaks legitimate things (the project's own venv, PYTHONPATH,
# an installed pytest) without meaningfully improving isolation against a
# subprocess that can't reach the network anyway. What actually matters is
# keeping credentials out of the sandboxed process.
_SECRET_ENV_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _sandboxed_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not any(s in k.upper() for s in _SECRET_ENV_SUBSTRINGS)
    }


class LocalSandbox(BaseSandbox):
    def _copy_repo(self, repo_path: str) -> Path:
        tmp_dir = Path(tempfile.mkdtemp(prefix="enigma_sandbox_"))
        shutil.copytree(
            repo_path,
            tmp_dir / "workspace",
            ignore=shutil.ignore_patterns(*_SKIP_DIRS),
            dirs_exist_ok=True,
        )
        return tmp_dir / "workspace"

    def run_script(self, repo_path: str, script: str, timeout_seconds: int) -> ReproductionResult:
        workspace = self._copy_repo(repo_path)
        script_path = workspace / "_enigma_repro.py"
        script_path.write_text(script)

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=_sandboxed_env(),
            )
            return ReproductionResult(
                reproduced=proc.returncode != 0,
                stdout=proc.stdout[-4000:],
                stderr=proc.stderr[-4000:],
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return ReproductionResult(
                reproduced=False,
                stdout="",
                stderr=f"Execution exceeded timeout of {timeout_seconds}s",
                exit_code=-1,
            )
        finally:
            shutil.rmtree(workspace.parent, ignore_errors=True)

    def run_tests(self, repo_path: str, test_command: list[str], timeout_seconds: int) -> TestRunResult:
        workspace = self._copy_repo(repo_path)
        try:
            proc = subprocess.run(
                test_command,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=_sandboxed_env(),
            )
            return TestRunResult(
                passed=proc.returncode == 0,
                stdout=proc.stdout[-4000:],
                stderr=proc.stderr[-4000:],
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return TestRunResult(
                passed=False,
                stdout="",
                stderr=f"Test run exceeded timeout of {timeout_seconds}s",
                exit_code=-1,
            )
        finally:
            shutil.rmtree(workspace.parent, ignore_errors=True)
