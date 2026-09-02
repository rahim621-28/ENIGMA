from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.panel import Panel

from enigma.config import Settings
from enigma.graph.state import IncidentState
from enigma.graph.workflow import build_workflow
from enigma.llm import get_provider
from enigma.sandbox.local_sandbox import LocalSandbox

app = typer.Typer(help="ENIGMA: autonomous incident triage agent.")
console = Console()

# LangGraph recursion_limit is counted in super-steps (node executions), not
# retries -- with 7 nodes and up to max_retries patch/test cycles, give
# ourselves headroom rather than tying it 1:1 to max_retries.
_RECURSION_LIMIT = 50


@app.command()
def doctor():
    """Check that the local environment is configured correctly."""
    settings = Settings.load()
    console.print(Panel.fit(f"[bold]LLM provider:[/bold] {settings.llm_provider}\n"
                             f"[bold]Model:[/bold] {settings.model_name}\n"
                             f"[bold]Sandbox backend:[/bold] {settings.sandbox_backend}",
                             title="ENIGMA doctor"))

    ok = True
    try:
        provider = get_provider(settings)
        console.print(f"[green]✓[/green] Provider '{settings.llm_provider}' initialized.")
        if settings.llm_provider == "ollama":
            try:
                provider.complete("You are a test.", "Reply with the single word: OK")
                console.print("[green]✓[/green] Ollama daemon reachable and model responded.")
            except RuntimeError as e:
                ok = False
                console.print(f"[red]✗[/red] {e}")
    except Exception as e:  # noqa: BLE001
        ok = False
        console.print(f"[red]✗[/red] Could not initialize provider: {e}")

    if settings.sandbox_backend == "docker":
        import subprocess
        try:
            subprocess.run(["docker", "version"], capture_output=True, timeout=5, check=True)
            console.print("[green]✓[/green] Docker is reachable.")
        except Exception:  # noqa: BLE001
            ok = False
            console.print("[red]✗[/red] SANDBOX_BACKEND=docker but Docker is not reachable.")

    sys.exit(0 if ok else 1)


@app.command()
def triage(
    log: str = typer.Option(..., "--log", help="Raw crash log / traceback text."),
    repo: str = typer.Option(..., "--repo", help="Path to the repository to analyze."),
    test_command: str = typer.Option(
        f"{sys.executable} -m pytest",
        "--test-command",
        help="Test command to run after patching. Defaults to the current interpreter's pytest.",
    ),
):
    """Run the full triage pipeline against a real incident log + repo."""
    settings = Settings.load()
    llm = get_provider(settings)
    sandbox = LocalSandbox()

    workflow = build_workflow(
        llm=llm,
        sandbox=sandbox,
        sandbox_timeout=settings.sandbox_timeout_seconds,
        test_command=test_command.split(),
    )

    initial_state = IncidentState(raw_log=log, repo_path=repo, max_retries=settings.max_retries)
    console.print("[bold cyan]Running triage pipeline...[/bold cyan]")

    result = workflow.invoke(initial_state, config={"recursion_limit": _RECURSION_LIMIT})
    final_state = IncidentState.model_validate(result)

    console.print(Panel(final_state.rca_report or "(no report generated)", title="RCA Report"))
    for step in final_state.step_log:
        console.print(f"  [dim]· {step}[/dim]")


@app.command(name="eval")
def run_eval(
    scenarios_dir: str = typer.Option("evals/scenarios", "--scenarios", help="Directory of eval scenarios."),
):
    """Run the SWE-bench-style eval harness against local scenarios."""
    # `evals/` lives at the repo root, not inside the installed package, so
    # make sure the current working directory is importable. This assumes
    # `enigma eval` is run from the repo root (documented in README).
    if sys.path[0:1] != [""] and "" not in sys.path:
        sys.path.insert(0, "")

    try:
        from evals.runner import run_all_scenarios
    except ModuleNotFoundError as e:
        console.print(
            "[red]Could not import evals/runner.py — run `enigma eval` from the "
            "repository root.[/red]"
        )
        raise typer.Exit(1) from e

    run_all_scenarios(scenarios_dir, console)


if __name__ == "__main__":
    app()
