# ENIGMA

Autonomous incident-triage agent: crash log → AST analysis → sandboxed
reproduction → LLM-synthesized patch → test verification → RCA report.

Built as a deterministic **LangGraph** state machine (not a free-form
ReAct loop), so it doesn't hallucinate its way into an infinite retry
loop — every retry is bounded by `max_retries` and a hard LangGraph
`recursion_limit` backstop.

## Architecture

```
Crash Log ──► Ingest ──► AST + Git Blame ──► Hypothesis ──► Sandbox Reproduce ──► Patch ──► Test ──► RCA Report
                                                                                    ▲          │
                                                                                    └──retry────┘ (capped)
```

- **Orchestration**: LangGraph `StateGraph` with a typed Pydantic v2 state
  schema (`enigma/graph/state.py`) and a conditional edge that routes
  back to `patch` only while `retry_count < max_retries`.
- **Analysis**: `ast.NodeVisitor`-based symbol extraction
  (`enigma/analysis/ast_analyzer.py`) plus `git blame` correlation.
- **Sandbox**: `LocalSandbox` (subprocess isolation, stripped-secrets env,
  hard timeout) for fast local dev, and `DockerSandbox`
  (`--network none`, memory/CPU/pids caps, non-root, `--cap-drop ALL`) for
  actual adversarial isolation of LLM-generated code.
- **LLM providers**: Ollama (local, offline, default), Gemini, OpenAI, and
  a deterministic `mock` provider used by the eval harness to validate
  pipeline plumbing without requiring a live model.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

### Run 100% offline with Ollama (recommended)

```bash
ollama pull qwen2.5-coder:7b
ollama serve   # in a separate terminal, if not already running

export LLM_PROVIDER=ollama
export MODEL_NAME=qwen2.5-coder:7b
enigma doctor      # confirms Ollama is reachable and the model responds
```

### Healthcheck

```bash
enigma doctor
```

### Triage a real incident

```bash
enigma triage \
  --log 'Traceback (most recent call last):
  File "service.py", line 6, in <module>
    print(calculate_metrics(100, 0))
  File "service.py", line 2, in calculate_metrics
    return total / count
ZeroDivisionError: division by zero' \
  --repo evals/scenarios/scenario_zero_div
```

### Run the eval harness

```bash
enigma eval
```

**What the eval number means — read this before putting it on a resume:**

- With `LLM_PROVIDER=mock` (no key/daemon needed), the harness applies each
  scenario's known-good fixture patch instead of asking a model to
  synthesize one. This validates that ingest → AST → sandbox repro →
  patch-apply → test → RCA all wire together correctly. It is a **pipeline
  plumbing check**, not a claim about model reasoning quality. The CLI
  prints this caveat directly in the report — don't strip it out when you
  screenshot the table.
- With `LLM_PROVIDER=ollama` (or `gemini`/`openai`), the agent generates
  its own patch and that patch is what gets tested. That number is real,
  but it's currently measured against **4 small, hand-written scenarios**
  (`evals/scenarios/`), not SWE-bench itself. Report it as "4/4 on our
  hand-written regression scenarios," not as a bare percentage — a bare
  "100%" figure with no N attached is the single most common way this kind
  of project loses credibility in an interview.

To get a defensible number for a resume, the honest next step is adding
more scenarios (10-20+) covering a range of bug classes and difficulty, and
reporting Pass@1 against that larger, still-disclosed N.

## Sandbox isolation notes

`LocalSandbox` copies the repo into a temp dir and runs with the **same
interpreter** as the host process (`sys.executable`), with secret-looking
env vars (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*CREDENTIAL*`)
redacted. This is a blast-radius reducer against buggy generated code, not
an adversarial security boundary.

`DockerSandbox` is the actual isolation layer: `--network none`, memory/CPU/
pids limits, read-only root filesystem with a tmpfs scratch dir, non-root
user, and all Linux capabilities dropped. Requires Docker; select it with
`SANDBOX_BACKEND=docker`.

## Known limitations (be ready to name these in an interview)

- `_build_repro_script` re-runs the whole failing module rather than
  parsing the traceback's call chain into a minimal repro — fine for the
  current flat-script scenarios, not yet built for multi-module import
  graphs.
- The eval scenario set is small and hand-written, by design disclosed as
  such rather than dressed up as SWE-bench-scale coverage.
- `DockerSandbox` isn't exercised by the automated test suite in this repo
  (no Docker daemon in the CI/sandbox environment it was built in) — the
  isolation flags are correct Docker semantics but worth a manual smoke
  test on a machine with Docker before relying on it.

## Repo layout

```
enigma/
  cli.py                 # enigma doctor / triage / eval
  config.py               # env-var driven settings
  graph/
    state.py              # Pydantic v2 IncidentState schema
    nodes.py               # ingest, analysis, hypothesis, reproduce, patch, test, finalize
    workflow.py             # LangGraph StateGraph wiring + retry routing
  analysis/
    ast_analyzer.py        # ast.NodeVisitor symbol extraction
    git_blame.py             # git blame correlation
  sandbox/
    base.py / local_sandbox.py / docker_sandbox.py
  llm/
    ollama_provider.py, cloud_providers.py, mock_provider.py
evals/
  runner.py                # eval harness (Pass@1, reproduction rate)
  scenarios/                # 4 hand-written incident scenarios
tests/                      # unit tests for AST analyzer + sandbox
```
