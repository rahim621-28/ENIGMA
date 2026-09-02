"""Typed state schema passed between LangGraph nodes.

Keeping this strictly typed (rather than a raw dict) is what lets the graph
fail fast on malformed intermediate output instead of silently propagating
garbage between steps.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Symbol(BaseModel):
    name: str
    kind: str  # "function" | "class" | "method"
    file_path: str
    line_start: int
    line_end: int
    source: str


class Hypothesis(BaseModel):
    suspect_symbol: str
    file_path: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


class ReproductionResult(BaseModel):
    reproduced: bool
    stdout: str
    stderr: str
    exit_code: int


class PatchAttempt(BaseModel):
    patched_content: str  # full corrected content of the target file
    explanation: str


class TestRunResult(BaseModel):
    passed: bool
    stdout: str
    stderr: str
    exit_code: int


class IncidentState(BaseModel):
    # inputs
    raw_log: str
    repo_path: str

    # ingest
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    failing_file: Optional[str] = None
    failing_line: Optional[int] = None

    # analysis
    symbols: list[Symbol] = Field(default_factory=list)
    git_blame_author: Optional[str] = None
    git_blame_commit: Optional[str] = None

    # hypothesis + repro
    hypothesis: Optional[Hypothesis] = None
    reproduction: Optional[ReproductionResult] = None

    # patch loop
    patch_attempts: list[PatchAttempt] = Field(default_factory=list)
    test_result: Optional[TestRunResult] = None
    retry_count: int = 0
    max_retries: int = 3

    # output
    status: str = "pending"  # pending | resolved | failed | max_retries_exceeded
    rca_report: Optional[str] = None

    # observability
    step_log: list[str] = Field(default_factory=list)

    def log_step(self, message: str) -> None:
        self.step_log.append(message)
