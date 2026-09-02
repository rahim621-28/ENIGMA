"""Docker-backed sandbox: actual adversarial isolation for running
LLM-generated code. Requires the Docker CLI/daemon on the host.

The isolation flags below are the ones you should be ready to explain line
by line in an interview -- each one closes a specific escape:

  --rm                 no leftover containers accumulating state
  --network none        no outbound network access (no exfil, no pulling more code)
  --memory 256m          hard cap; OOM-killed instead of exhausting the host
  --cpus 1               no CPU starvation of the host
  --pids-limit 128       fork-bomb guard
  --read-only            container filesystem is immutable except tmpfs mounts below
  --tmpfs /tmp           scratch space that vanishes on exit
  --user 1000:1000       non-root inside the container, even if the base image defaults to root
  --cap-drop ALL         strip all Linux capabilities (no raw sockets, no ptrace, etc.)
  --security-opt no-new-privileges  blocks setuid escalation
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from enigma.graph.state import ReproductionResult, TestRunResult
from enigma.sandbox.base import BaseSandbox

_ISOLATION_FLAGS = [
    "--rm",
    "--network", "none",
    "--memory", "256m",
    "--cpus", "1",
    "--pids-limit", "128",
    "--read-only",
    "--tmpfs", "/tmp",
    "--user", "1000:1000",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
]

_IMAGE = "python:3.11-slim"


class DockerSandbox(BaseSandbox):
    def _check_docker_available(self) -> None:
        try:
            subprocess.run(["docker", "version"], capture_output=True, timeout=5, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(
                "Docker is not available on this host. Fall back to SANDBOX_BACKEND=local "
                "or install/start Docker."
            ) from e

    def run_script(self, repo_path: str, script: str, timeout_seconds: int) -> ReproductionResult:
        self._check_docker_available()
        with tempfile.TemporaryDirectory(prefix="enigma_docker_") as tmp:
            script_path = Path(tmp) / "_enigma_repro.py"
            script_path.write_text(script)

            cmd = [
                "docker", "run",
                *_ISOLATION_FLAGS,
                "-v", f"{repo_path}:/workspace/repo:ro",
                "-v", f"{tmp}:/workspace/script:ro",
                "-w", "/workspace",
                _IMAGE,
                "timeout", str(timeout_seconds),
                "python3", "/workspace/script/_enigma_repro.py",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 10)
                return ReproductionResult(
                    reproduced=proc.returncode != 0,
                    stdout=proc.stdout[-4000:],
                    stderr=proc.stderr[-4000:],
                    exit_code=proc.returncode,
                )
            except subprocess.TimeoutExpired:
                return ReproductionResult(
                    reproduced=False, stdout="", stderr="Docker execution timed out", exit_code=-1
                )

    def run_tests(self, repo_path: str, test_command: list[str], timeout_seconds: int) -> TestRunResult:
        self._check_docker_available()
        cmd = [
            "docker", "run",
            *_ISOLATION_FLAGS,
            "-v", f"{repo_path}:/workspace/repo:rw",
            "-w", "/workspace/repo",
            _IMAGE,
            "timeout", str(timeout_seconds),
            *test_command,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 10)
            return TestRunResult(
                passed=proc.returncode == 0,
                stdout=proc.stdout[-4000:],
                stderr=proc.stderr[-4000:],
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return TestRunResult(passed=False, stdout="", stderr="Docker test run timed out", exit_code=-1)
