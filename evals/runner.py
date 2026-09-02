"""Eval harness: runs the full triage pipeline against each scenario in
evals/scenarios/ and reports Pass@1, reproduction success rate, and timing.

IMPORTANT — what this number actually means:
This harness has two modes, controlled by LLM_PROVIDER:

  * LLM_PROVIDER=ollama|gemini|openai (default when a real key/daemon is set):
    the agent generates its own patch via the LLM and we test THAT patch.
    This is the real, meaningful Pass@1 number.

  * LLM_PROVIDER=mock (or no LLM reachable): the harness falls back to each
    scenario's known-good fixture patch (fix.py) purely to validate that the
    graph/sandbox/eval plumbing works end-to-end. This is a plumbing check,
    NOT a claim about model reasoning quality, and the report says so
    explicitly rather than presenting it as a resolution rate.

Scenario count is intentionally small and hand-written (4 scenarios as of
this writing) -- report it as N, not as a percentage dressed up to look
like a SWE-bench-scale result.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from enigma.config import Settings
from enigma.graph.state import IncidentState
from enigma.graph.workflow import build_workflow
from enigma.llm import get_provider
from enigma.sandbox.local_sandbox import LocalSandbox


def _load_scenario(scenario_dir: Path) -> dict:
    incident = json.loads((scenario_dir / "incident.json").read_text())
    incident["_dir"] = scenario_dir
    return incident


def _make_fixture_patch_fn(scenario_dir: Path, using_mock: bool):
    """Only supply the fixture patch when we're in mock/plumbing-check mode.
    When a real LLM is configured, return None so the graph actually asks
    the LLM to patch the sandboxed workspace file directly.
    """
    if not using_mock:
        return None

    def _get_fixture_patch(state: IncidentState) -> str:
        fix_file = scenario_dir / state["fixture_patch_file"] if isinstance(state, dict) else None
        return ""  # placeholder; real application happens via _apply_fixture below

    return _get_fixture_patch


def run_all_scenarios(scenarios_dir: str, console: Console) -> None:
    settings = Settings.load()
    using_mock = settings.llm_provider == "mock"
    llm = get_provider(settings)
    sandbox = LocalSandbox()

    scenario_dirs = sorted(p for p in Path(scenarios_dir).iterdir() if p.is_dir())
    if not scenario_dirs:
        console.print(f"[red]No scenarios found under {scenarios_dir}[/red]")
        return

    results = []
    for scenario_dir in scenario_dirs:
        result = _run_single_scenario(scenario_dir, llm, sandbox, settings, using_mock)
        results.append(result)

    _print_report(console, results, using_mock, settings)


def _run_single_scenario(scenario_dir: Path, llm, sandbox, settings: Settings, using_mock: bool) -> dict:
    incident = _load_scenario(scenario_dir)
    name = scenario_dir.name
    start = time.time()

    # Work on a throwaway copy of the scenario dir so patch application never
    # mutates the checked-in fixtures.
    work_dir = Path(tempfile.mkdtemp(prefix=f"enigma_eval_{name}_"))
    shutil.copytree(scenario_dir, work_dir, dirs_exist_ok=True)

    fixture_applied = False
    if using_mock:
        # Plumbing-check mode: copy the known-good fix over the buggy file
        # AFTER reproduction (so reproduction still tests the real bug) but
        # BEFORE the test step, simulating "agent produced the correct patch".
        pass  # applied inside a patched fixture-aware workflow run below

    def get_fixture_patch(state):
        nonlocal fixture_applied
        if not using_mock:
            return None
        fix_src = scenario_dir / incident["fixture_patch_file"]
        fix_dst = work_dir / "service.py"
        shutil.copy(fix_src, fix_dst)
        fixture_applied = True
        return f"applied {incident['fixture_patch_file']} -> service.py"

    workflow = build_workflow(
        llm=llm,
        sandbox=sandbox,
        sandbox_timeout=settings.sandbox_timeout_seconds,
        test_command=[sys.executable] + list(incident["test_command"]),
        get_fixture_patch=get_fixture_patch,
    )

    initial_state = IncidentState(raw_log=incident["log"], repo_path=str(work_dir), max_retries=settings.max_retries)
    final_raw = workflow.invoke(initial_state, config={"recursion_limit": 50})
    final_state = IncidentState.model_validate(final_raw)

    elapsed = time.time() - start
    shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "name": name,
        "resolved": final_state.status == "resolved",
        "reproduced": bool(final_state.reproduction and final_state.reproduction.reproduced),
        "retry_count": final_state.retry_count,
        "elapsed_s": round(elapsed, 2),
        "status": final_state.status,
        "step_log": final_state.step_log,
        "last_patch_explanation": final_state.patch_attempts[-1].explanation if final_state.patch_attempts else None,
        "test_stderr": final_state.test_result.stderr if final_state.test_result else None,
    }


def _print_report(console: Console, results: list[dict], using_mock: bool, settings: Settings) -> None:
    table = Table(title="ENIGMA Eval Report")
    table.add_column("Scenario")
    table.add_column("Reproduced")
    table.add_column("Resolved")
    table.add_column("Retries")
    table.add_column("Time (s)")

    for r in results:
        table.add_row(
            r["name"],
            "✓" if r["reproduced"] else "✗",
            "✓" if r["resolved"] else "✗",
            str(r["retry_count"]),
            str(r["elapsed_s"]),
        )
    console.print(table)

    n = len(results)
    resolved = sum(1 for r in results if r["resolved"])
    reproduced = sum(1 for r in results if r["reproduced"])

    console.print(f"\n[bold]Reproduction success rate:[/bold] {reproduced}/{n}")
    console.print(f"[bold]Pass@1 (resolved on first patch cycle... or within retry cap):[/bold] {resolved}/{n}")

    if using_mock:
        console.print(
            "\n[yellow]Note: LLM_PROVIDER=mock — this run validates pipeline plumbing using "
            "known-good fixture patches, not the model's own reasoning. "
            "Run with LLM_PROVIDER=ollama for a real resolution-rate number.[/yellow]"
        )
    else:
        console.print(
            f"\n[dim]Provider: {settings.llm_provider} / {settings.model_name}. "
            f"N={n} hand-written scenarios — not a claim of SWE-bench-scale coverage.[/dim]"
        )

    failed = [r for r in results if not r["resolved"]]
    if failed:
        console.print("\n[bold red]Failure diagnostics:[/bold red]")
        for r in failed:
            console.print(f"\n[bold]{r['name']}[/bold] (status: {r['status']})")
            console.print(f"  Last patch explanation: {r['last_patch_explanation']!r}")
            parse_failures = [s for s in r["step_log"] if "JSON parse failed" in s]
            for pf in parse_failures:
                console.print(f"  [yellow]{pf}[/yellow]")
            if r["test_stderr"]:
                stderr_preview = r["test_stderr"][:400]
                console.print(f"  Test stderr (truncated): {stderr_preview!r}")
