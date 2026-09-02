# ENIGMA

I built ENIGMA to see how far I could push an autonomous debugging agent:
give it a crash log, and it locates the failing code, reproduces the bug
in an isolated sandbox, asks an LLM to write a fix, applies and tests
that fix, and writes up a root cause analysis — end to end, no human in
the loop until there's a PR to review.

I built it as a deterministic LangGraph state machine instead of a
free-form ReAct loop on purpose. Free-form agent loops are prone to
looping forever when a test keeps failing; here every retry is bounded
by `max_retries`, with a hard LangGraph `recursion_limit` as a backstop
even if my retry-counting logic ever has a bug.

## Architecture

```
Crash Log ──► Ingest ──► AST + Git Blame ──► Hypothesis ──► Sandbox Reproduce ──► Patch ──► Test ──► RCA Report
                                                                                    ▲          │
                                                                                    └──retry────┘ (capped)
```

- **Orchestration**: LangGraph `StateGraph` with a typed Pydantic v2 state
  schema (`enigma/graph/state.py`) and a conditional edge that only
  routes back to `patch` while `retry_count < max_retries`.
- **Analysis**: `ast.NodeVisitor`-based symbol extraction
  (`enigma/analysis/ast_analyzer.py`) plus `git blame` correlation, so
  the agent can point at the exact function and the commit that
  introduced it.
- **Sandbox**: `LocalSandbox` for fast local iteration (subprocess
  isolation, stripped-secrets env, hard timeout), and `DockerSandbox`
  (`--network none`, memory/CPU/pids caps, non-root, `--cap-drop ALL`)
  for real isolation when running LLM-generated code against something
  I don't fully trust.
- **LLM providers**: Ollama by default (local, offline, no API costs),
  with Gemini and OpenAI as drop-in alternatives, plus a deterministic
  mock provider I use to sanity-check the pipeline wiring without
  burning model calls.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

### Running offline with Ollama (my default setup)

```bash
ollama pull qwen2.5-coder:7b
ollama serve   # separate terminal, if it's not already running as a service

export LLM_PROVIDER=ollama
export MODEL_NAME=qwen2.5-coder:7b
enigma doctor      # confirms Ollama is reachable and the model responds
```

### Healthcheck

```bash
enigma doctor
```

### Triaging a real incident

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

### Eval harness

```bash
enigma eval
```

I run this in two modes. `LLM_PROVIDER=mock` swaps in each scenario's
known-good fix instead of calling a model — good for a quick check that
ingest → AST → sandbox repro → patch → test → RCA are all wired up
correctly. `LLM_PROVIDER=ollama` (or gemini/openai) is the real thing:
the model generates its own patch, and that's what actually gets applied
and tested.

Right now I'm at Pass@1 of 2/4 on qwen2.5-coder:7b, across 4 hand-written
scenarios (`ZeroDivisionError`, `KeyError`, `IndexError`, `TypeError`).
It nails the `IndexError` and `TypeError` cases cleanly. It struggles on
the other two — more on that below. My next step is growing the scenario
set to 15-20+ cases across a wider spread of bug types before I put much
weight on the aggregate number.

## Deployment

`enigma/server/app.py` wraps the same pipeline behind a FastAPI service,
with a small demo page at `/` so it's explorable in a browser.

**Endpoints:**

- `GET /healthz` — liveness check
- `GET /` — demo UI
- `POST /triage` — `{"log": str, "repo_url": Optional[str]}` → runs the
  pipeline, returns the RCA report and step log

Run it locally:

```bash
pip install -e ".[server]"
uvicorn enigma.server.app:app --reload
```

### Deploying to Render

1. Push this repo to GitHub.
2. On [dashboard.render.com](https://dashboard.render.com): New + → Web Service → select the repo.
3. Build command: `pip install -e ".[server]"`
4. Start command: `uvicorn enigma.server.app:app --host 0.0.0.0 --port $PORT`
5. Env vars: `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=<your key>`

Ollama doesn't work on free hosting — no GPU, no persistent model
storage — so the deployed version runs on Gemini or OpenAI instead. If
no key is configured it falls back to a demo mode automatically:
reproduction and AST analysis still run for real, patch generation is
just skipped, and the response says so rather than the request silently
failing.

## Sandbox notes

`LocalSandbox` runs with the same interpreter as the host process
(`sys.executable`) and redacts anything with `KEY`, `TOKEN`, `SECRET`,
`PASSWORD`, or `CREDENTIAL` in the env var name. It's a blast-radius
reducer, not a real security boundary — for that, `DockerSandbox` is the
one that actually matters: `--network none`, memory/CPU/pids caps,
read-only root filesystem, non-root user, all capabilities dropped.
Select it with `SANDBOX_BACKEND=docker`.

## What I'd tackle next

- The repro step re-runs the whole failing module rather than tracing
  the actual call chain from the traceback — fine for single-file
  scenarios, not built out for multi-module import graphs yet.
- Patch generation asks the model to return the full corrected file
  rather than a diff. I went this way after a 7B local model kept
  producing diffs that didn't apply cleanly — full-file replacement
  turned out to be the more reliable trade for single-file fixes, though
  it won't scale to multi-file patches as-is.
- On the `ZeroDivisionError` case specifically, the model sometimes
  claims the file is "already correct" without actually having changed
  it — looks like it's misjudging its own output rather than a parsing
  bug on my end. Worth trying tighter prompting (e.g. explicitly
  requiring the returned content to differ from the input) to see if
  that closes the gap.
- `DockerSandbox` hasn't been exercised by the test suite yet since I
  built this without a Docker daemon on hand — the isolation flags are
  correct, but I'd want to smoke test it for real before relying on it.

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
  server/
    app.py                  # FastAPI service + demo UI
evals/
  runner.py                # eval harness (Pass@1, reproduction rate)
  scenarios/                # 4 hand-written incident scenarios
tests/                      # unit tests for AST analyzer + sandbox
```

## Author

Rahim Khan
