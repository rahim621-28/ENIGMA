"""ENIGMA web service.

Wraps the same LangGraph pipeline used by the CLI behind an HTTP API, plus
a minimal single-page demo UI so the service is explorable from a browser
without needing curl.

Deployment notes (see README for the full walkthrough):
  - Ollama isn't viable on typical free-tier hosting (no persistent local
    model weights, no GPU), so LLM_PROVIDER should be set to `gemini` or
    `openai` in production, with the corresponding API key configured.
  - If no key is configured, the service transparently falls back to the
    mock provider and labels responses accordingly, rather than 500'ing --
    keeps the demo usable for browsing the UI/architecture even without
    live credentials.
  - SANDBOX_BACKEND stays "local" (subprocess-based) in this deployment;
    Docker isn't available on most free hosting tiers.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from enigma.config import Settings
from enigma.graph.state import IncidentState
from enigma.graph.workflow import build_workflow
from enigma.llm import get_provider
from enigma.sandbox.local_sandbox import LocalSandbox

app = FastAPI(title="ENIGMA", description="Autonomous incident triage agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_CLONE_TIMEOUT_SECONDS = 30
_RECURSION_LIMIT = 50


class TriageRequest(BaseModel):
    log: str
    repo_url: Optional[str] = None  # public https git URL; cloned shallow
    test_command: list[str] = ["python3", "-m", "pytest", "-q"]


class TriageResponse(BaseModel):
    status: str
    rca_report: Optional[str]
    step_log: list[str]
    demo_mode: bool
    demo_mode_reason: Optional[str] = None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    return _DEMO_HTML


@app.post("/triage", response_model=TriageResponse)
def triage(req: TriageRequest):
    settings = Settings.load()
    demo_mode = False
    demo_mode_reason = None

    try:
        llm = get_provider(settings)
    except RuntimeError as e:
        # No API key configured for the selected cloud provider -- fall
        # back to mock rather than failing the request outright, so the
        # demo stays usable (reproduction/AST analysis still run for real).
        from enigma.llm.mock_provider import MockProvider

        llm = MockProvider()
        demo_mode = True
        demo_mode_reason = str(e)

    work_dir = _prepare_workspace(req.repo_url)
    try:
        sandbox = LocalSandbox()
        workflow = build_workflow(
            llm=llm,
            sandbox=sandbox,
            sandbox_timeout=settings.sandbox_timeout_seconds,
            test_command=req.test_command,
            get_fixture_patch=None,
        )
        initial_state = IncidentState(raw_log=req.log, repo_path=str(work_dir), max_retries=settings.max_retries)
        result = workflow.invoke(initial_state, config={"recursion_limit": _RECURSION_LIMIT})
        final_state = IncidentState.model_validate(result)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return TriageResponse(
        status=final_state.status,
        rca_report=final_state.rca_report,
        step_log=final_state.step_log,
        demo_mode=demo_mode,
        demo_mode_reason=demo_mode_reason,
    )


def _prepare_workspace(repo_url: Optional[str]) -> Path:
    work_dir = Path(tempfile.mkdtemp(prefix="enigma_web_"))
    if not repo_url:
        # No repo provided: fall back to the bundled zero_div demo scenario
        # so the endpoint is testable with nothing but a log string.
        bundled = Path(__file__).resolve().parents[2] / "evals" / "scenarios" / "scenario_zero_div"
        if bundled.exists():
            shutil.copytree(bundled, work_dir, dirs_exist_ok=True)
        return work_dir

    if not repo_url.startswith("https://"):
        raise HTTPException(400, "repo_url must be a public https git URL")

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(work_dir)],
            capture_output=True,
            timeout=_CLONE_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, f"Could not clone repo_url: {e.stderr.decode(errors='replace')[:300]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Cloning repo_url exceeded the timeout")

    return work_dir


_DEMO_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ENIGMA - Incident Triage Demo</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }
  h1 { font-size: 1.4rem; }
  textarea { width: 100%; height: 140px; font-family: monospace; font-size: 0.85rem; box-sizing: border-box; }
  input { width: 100%; box-sizing: border-box; padding: 6px; margin-top: 4px; }
  button { margin-top: 12px; padding: 8px 16px; cursor: pointer; }
  pre { background: #f4f4f4; padding: 12px; overflow-x: auto; white-space: pre-wrap; }
  .note { color: #666; font-size: 0.85rem; }
</style>
</head>
<body>
<h1>ENIGMA - autonomous incident triage</h1>
<p class="note">Paste a Python traceback below. Leave the repo URL blank to try it against a bundled demo bug.</p>
<label>Crash log / traceback</label>
<textarea id="log">Traceback (most recent call last):
  File "service.py", line 6, in &lt;module&gt;
    print(calculate_metrics(100, 0))
  File "service.py", line 2, in calculate_metrics
    return total / count
ZeroDivisionError: division by zero</textarea>
<label>Repo URL (optional, public https git URL)</label>
<input id="repo_url" placeholder="https://github.com/you/your-repo">
<br>
<button onclick="runTriage()">Run triage</button>
<div id="result"></div>

<script>
async function runTriage() {
  const resultEl = document.getElementById('result');
  resultEl.innerHTML = '<p>Running...</p>';
  const log = document.getElementById('log').value;
  const repo_url = document.getElementById('repo_url').value || null;
  try {
    const res = await fetch('/triage', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({log, repo_url})
    });
    const data = await res.json();
    let html = '';
    if (data.demo_mode) {
      html += '<p class="note">Running in demo mode (no LLM API key configured) -- reproduction and analysis ran for real, patch generation was skipped.</p>';
    }
    html += '<h3>Status: ' + data.status + '</h3>';
    html += '<pre>' + (data.rca_report || '(no report)') + '</pre>';
    html += '<details><summary>Step log</summary><pre>' + data.step_log.join('\\n') + '</pre></details>';
    resultEl.innerHTML = html;
  } catch (e) {
    resultEl.innerHTML = '<p style="color:red">Request failed: ' + e + '</p>';
  }
}
</script>
</body>
</html>
"""
