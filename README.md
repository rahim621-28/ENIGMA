# ENIGMA

Autonomous incident-triage agent: crash log → AST analysis → sandboxed
reproduction → LLM-synthesized patch → test verification → RCA report.

Built as a deterministic **LangGraph** state machine rather than a
free-form ReAct loop, so it can't hallucinate its way into an infinite
retry loop — every retry is bounded by `max_retries` and a hard LangGraph
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
  adversarial isolation of LLM-generated code.
- **LLM providers**: Ollama (local, offline, default), Gemini, OpenAI, and
  a deterministic `mock` provider used by the eval harness to validate
  pipeline wiring without requiring a live model.

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

The harness has two modes:

- `LLM_PROVIDER=mock` applies each scenario's known-good fixture patch
  instead of asking a model to generate one. It's a wiring check —
  confirms ingest → AST → sandbox repro → patch-apply → test → RCA all
  connect correctly — not a measure of model reasoning.
- `LLM_PROVIDER=ollama` (or `gemini`/`openai`) has the agent generate its
  own patch, which is then applied and tested for real. Currently run
  against 4 hand-written scenarios in `evals/scenarios/`, covering
  `ZeroDivisionError`, `KeyError`, `IndexError`, and `TypeError` patterns.
  Pass@1 on qwen2.5-coder:7b is currently 2/4 — the model correctly fixes
  `IndexError` and `TypeError` cases but struggles with the `KeyError`
  and `ZeroDivisionError` scenarios (see Known limitations below).

Planned next step: expand the scenario set to 15-20+ cases spanning a
wider range of bug classes and difficulty levels.

## Sandbox isolation notes

`LocalSandbox` copies the repo into a temp dir and runs with the same
interpreter as the host process (`sys.executable`), with secret-looking
env vars (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*CREDENTIAL*`)
redacted. This reduces blast radius from buggy generated code; it is not
an adversarial security boundary.

`DockerSandbox` is the real isolation layer: `--network none`, memory/CPU/
pids limits, read-only root filesystem with a tmpfs scratch dir, non-root
user, and all Linux capabilities dropped. Requires Docker; select it with
`SANDBOX_BACKEND=docker`.

## Known limitations

- `_build_repro_script` re-runs the whole failing module rather than
  parsing the traceback's call chain into a minimal repro — works for the
  current flat-script scenarios, not yet built for multi-module import
  graphs.
- The eval scenario set is small (4 hand-written cases) and doesn't claim
  SWE-bench-scale coverage.
- Patch generation asks the model for the full corrected file content
  rather than a unified diff, since a 7B local model produces malformed
  diffs often enough that diff application became its own failure mode.
  This works well for single-file fixes but doesn't extend to multi-file
  patches yet.
- On the `ZeroDivisionError` scenario specifically, qwen2.5-coder:7b
  sometimes reports the file as "already correct" without actually
  changing it, which reads as the model misjudging its own diff rather
  than a parsing or pipeline issue — a good candidate for tighter
  prompting (e.g., explicitly requiring the returned content to differ
  from the input) in a future pass.
- `DockerSandbox` isn't yet exercised by the automated test suite (no
  Docker daemon available in the environment used to build it) — the
  isolation flags are correct Docker semantics but worth a manual smoke
  test before relying on it in production.

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

## AUTHOR

RAHIM KHAN
