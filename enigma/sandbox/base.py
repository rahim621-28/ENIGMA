"""Sandbox interface: anything that can run untrusted, LLM-generated code
and report back stdout/stderr/exit code under a hard timeout.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from enigma.graph.state import ReproductionResult, TestRunResult


class BaseSandbox(ABC):
    @abstractmethod
    def run_script(self, repo_path: str, script: str, timeout_seconds: int) -> ReproductionResult:
        """Copy repo_path into an isolated workspace and execute `script` against it."""
        ...

    @abstractmethod
    def run_tests(self, repo_path: str, test_command: list[str], timeout_seconds: int) -> TestRunResult:
        """Run the test suite for a (patched) repo copy and report pass/fail."""
        ...
