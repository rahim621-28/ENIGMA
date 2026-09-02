"""Node functions for the LangGraph state machine.

Each node takes the IncidentState, mutates/returns a partial update, and
logs a step for observability. Kept as plain functions (not classes) so
they're easy to unit test in isolation.
"""
from __future__ import annotations

import json
import re

from enigma.analysis.ast_analyzer import extract_symbols_from_repo, find_symbol_at_line
from enigma.analysis.git_blame import blame_line
from enigma.graph.state import Hypothesis, IncidentState, PatchAttempt
from enigma.llm.base import BaseLLMProvider
from enigma.sandbox.base import BaseSandbox

_TRACEBACK_LOCATION_RE = re.compile(r'File "([^"]+)", line (\d+)')
_ERROR_TYPE_RE = re.compile(r"(\w+(?:Error|Exception)):\s*(.*)")
_MARKDOWN_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.MULTILINE)


def _parse_json_response(raw: str) -> tuple[dict, str | None]:
    """Best-effort JSON extraction from an LLM response.

    Local coder models very commonly wrap JSON in markdown fences
    (```json ... ```) even when told to return raw JSON, or add a short
    preamble sentence before the JSON block. This strips fences and falls
    back to locating the first balanced {...} block via regex before
    giving up. Returns (parsed_dict, raw_text_if_parse_failed).
    """
    cleaned = _MARKDOWN_FENCE_RE.sub("", raw.strip()).strip()
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass

    # Fallback: find the first { ... } block (handles a leading sentence
    # like "Here's the fix:" before the JSON).
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0)), None
        except json.JSONDecodeError:
            pass

    return {}, raw


def ingest_node(state: IncidentState) -> IncidentState:
    """Parse the raw crash log into structured fields (file, line, error type).

    A traceback lists frames outermost-first, so the LAST "File ..., line N"
    match is the innermost frame -- i.e. where the error actually occurred,
    not just where it was called from.
    """
    loc_matches = list(_TRACEBACK_LOCATION_RE.finditer(state.raw_log))
    err_match = _ERROR_TYPE_RE.search(state.raw_log)

    if loc_matches:
        last = loc_matches[-1]
        state.failing_file = last.group(1)
        state.failing_line = int(last.group(2))
    if err_match:
        state.error_type = err_match.group(1)
        state.error_message = err_match.group(2).strip()

    state.log_step(
        f"ingest: parsed error_type={state.error_type} "
        f"file={state.failing_file} line={state.failing_line}"
    )
    return state


def analysis_node(state: IncidentState) -> IncidentState:
    """Run AST extraction over the repo and correlate the failing line to a symbol + git blame."""
    state.symbols = extract_symbols_from_repo(state.repo_path)

    if state.failing_file and state.failing_line:
        commit, author = blame_line(state.repo_path, state.failing_file, state.failing_line)
        state.git_blame_author = author
        state.git_blame_commit = commit

    state.log_step(f"analysis: extracted {len(state.symbols)} symbols from repo")
    return state


def hypothesis_node(state: IncidentState, llm: BaseLLMProvider) -> IncidentState:
    """Ask the LLM (or fall back to pure AST lookup) which symbol is the likely culprit."""
    suspect = None
    if state.failing_file and state.failing_line:
        suspect = find_symbol_at_line(state.symbols, state.failing_file, state.failing_line)

    if suspect is None:
        state.hypothesis = Hypothesis(
            suspect_symbol="unknown",
            file_path=state.failing_file or "unknown",
            reasoning="Could not locate an enclosing symbol via AST; falling back to file-level patch.",
            confidence=0.1,
        )
        state.log_step("hypothesis: no symbol found at failing line")
        return state

    system_prompt = (
        "You are a senior SRE diagnosing a production incident. "
        "Return ONLY JSON with keys: suspect_symbol, reasoning, confidence (0-1)."
    )
    user_prompt = (
        f"Error: {state.error_type}: {state.error_message}\n"
        f"Symbol: {suspect.name}\n"
        f"Source:\n{suspect.source}\n"
    )
    raw = llm.complete(system_prompt, user_prompt)
    parsed, unparsed_raw = _parse_json_response(raw)
    if unparsed_raw is None and parsed:
        state.hypothesis = Hypothesis(
            suspect_symbol=parsed.get("suspect_symbol", suspect.name),
            file_path=suspect.file_path,
            reasoning=parsed.get("reasoning", ""),
            confidence=float(parsed.get("confidence", 0.5)),
        )
    else:
        state.hypothesis = Hypothesis(
            suspect_symbol=suspect.name,
            file_path=suspect.file_path,
            reasoning="LLM response was not valid JSON; used AST-located symbol directly.",
            confidence=0.4,
        )
        state.log_step(f"hypothesis: JSON parse failed, raw response: {(unparsed_raw or '')[:300]!r}")

    state.log_step(f"hypothesis: suspect={state.hypothesis.suspect_symbol}")
    return state


def reproduce_node(state: IncidentState, sandbox: BaseSandbox, timeout: int) -> IncidentState:
    """Attempt to reproduce the crash in an isolated sandbox by re-running the failing entrypoint."""
    if not state.failing_file:
        state.log_step("reproduce: no failing_file, skipping")
        return state

    repro_script = _build_repro_script(state)
    state.reproduction = sandbox.run_script(state.repo_path, repro_script, timeout)
    state.log_step(f"reproduce: reproduced={state.reproduction.reproduced}")
    return state


def _build_repro_script(state: IncidentState) -> str:
    """Best-effort: just re-execute the failing module as a script.

    This works for the simple scenario shapes in evals/scenarios; a
    production version would parse the traceback's call chain to build a
    more targeted repro instead of re-running the whole module.
    """
    module_path = state.failing_file
    return (
        "import runpy, sys\n"
        f"sys.argv = ['{module_path}']\n"
        f"runpy.run_path({module_path!r}, run_name='__main__')\n"
    )


def patch_node(state: IncidentState, llm: BaseLLMProvider, get_fixture_patch) -> IncidentState:
    """Synthesize a patch and WRITE IT to the working copy on disk.

    Design choice: we ask the LLM for the full corrected file content rather
    than a unified diff. A 7B local model produces malformed diffs (wrong
    line numbers, missing context lines) often enough that diff application
    would fail silently or crash more than the actual bug-fixing reasoning
    does. Full-file replacement is more tokens but far more reliable to
    apply -- and for the single-file scenarios this pipeline targets, that
    trade-off is the right one. A production version handling multi-file
    patches would need real diff application (e.g. `patch`/`git apply`).
    """
    fixture = get_fixture_patch(state) if get_fixture_patch else None
    if fixture is not None:
        state.patch_attempts.append(PatchAttempt(patched_content=fixture, explanation="fixture patch (eval mode)"))
        state.log_step("patch: applied fixture patch")
        return state

    target_file = state.hypothesis.file_path if state.hypothesis else state.failing_file
    if not target_file:
        state.patch_attempts.append(
            PatchAttempt(patched_content="", explanation="No target file identified; cannot patch.")
        )
        state.log_step("patch: no target file, skipping")
        return state

    current_content = _read_target_file(state.repo_path, target_file)

    system_prompt = (
        "You are fixing a bug in a Python file. Return ONLY JSON with keys: "
        "patched_content (the ENTIRE corrected file content, not a diff or snippet), "
        "explanation (one sentence on what you changed and why)."
    )
    user_prompt = (
        f"Error: {state.error_type}: {state.error_message}\n"
        f"Suspect symbol: {state.hypothesis.suspect_symbol if state.hypothesis else 'unknown'}\n"
        f"Reasoning so far: {state.hypothesis.reasoning if state.hypothesis else ''}\n\n"
        f"Current full content of {target_file}:\n{current_content}\n"
    )
    raw = llm.complete(system_prompt, user_prompt)
    parsed, unparsed_raw = _parse_json_response(raw)
    if unparsed_raw is None and parsed:
        patched_content = parsed.get("patched_content", "")
        explanation = parsed.get("explanation", "")
    else:
        patched_content = ""
        explanation = "LLM returned invalid/unparseable JSON"
        state.log_step(f"patch: JSON parse failed, raw response: {(unparsed_raw or '')[:300]!r}")

    state.patch_attempts.append(PatchAttempt(patched_content=patched_content, explanation=explanation))

    if patched_content.strip():
        _write_target_file(state.repo_path, target_file, patched_content)
        state.log_step(f"patch: attempt #{len(state.patch_attempts)} written to {target_file}")
    else:
        state.log_step(f"patch: attempt #{len(state.patch_attempts)} produced no usable content")

    return state


def _read_target_file(repo_path: str, target_file: str) -> str:
    from pathlib import Path

    candidate = Path(repo_path) / Path(target_file).name
    if candidate.exists():
        return candidate.read_text()
    return ""


def _write_target_file(repo_path: str, target_file: str, content: str) -> None:
    from pathlib import Path

    candidate = Path(repo_path) / Path(target_file).name
    candidate.write_text(content)


def test_node(state: IncidentState, sandbox: BaseSandbox, timeout: int, test_command: list[str]) -> IncidentState:
    """Run the repo's test suite against the current (patched) working copy."""
    state.test_result = sandbox.run_tests(state.repo_path, test_command, timeout)
    state.retry_count += 1
    state.log_step(f"test: passed={state.test_result.passed} retry_count={state.retry_count}")
    return state


def finalize_node(state: IncidentState) -> IncidentState:
    """Decide final status and write the RCA report."""
    if state.test_result and state.test_result.passed:
        state.status = "resolved"
    elif state.retry_count >= state.max_retries:
        state.status = "max_retries_exceeded"
    else:
        state.status = "failed"

    state.rca_report = _render_rca(state)
    state.log_step(f"finalize: status={state.status}")
    return state


def _render_rca(state: IncidentState) -> str:
    lines = [
        "# Root Cause Analysis",
        "",
        f"**Error**: {state.error_type}: {state.error_message}",
        f"**Location**: {state.failing_file}:{state.failing_line}",
    ]
    if state.git_blame_commit:
        lines.append(f"**Introduced by**: {state.git_blame_commit[:8]} ({state.git_blame_author})")
    if state.hypothesis:
        lines += [
            "",
            f"**Suspect symbol**: {state.hypothesis.suspect_symbol}",
            f"**Reasoning**: {state.hypothesis.reasoning}",
            f"**Confidence**: {state.hypothesis.confidence:.2f}",
        ]
    if state.reproduction:
        lines.append(f"**Reproduced in sandbox**: {state.reproduction.reproduced}")
    lines.append(f"**Patch attempts**: {len(state.patch_attempts)}")
    lines.append(f"**Final status**: {state.status}")
    return "\n".join(lines)
