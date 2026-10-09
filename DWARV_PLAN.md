# DWARV: Build Instructions for Claude Code

> Hand this whole file to Claude Code as the project brief. Work through it in order. Do not skip the acceptance checks. Do not fabricate benchmark results, repo features, or citations.

---

## 0. How to work on this project

- You are building a hackathon prototype called **Dwarv**: a resource-aware, verification-guided local AI controller for offline coding tasks.
- Be pragmatic. A reliable end-to-end demo beats speculative features.
- After finishing each step, run its **Acceptance check**, commit with a clear message, and append a short entry to `docs/PROGRESS.md` (what was done, what was measured, what is next).
- If a step is blocked for more than ~30 minutes, use the listed **Fallback**, note it in `docs/PROGRESS.md`, and move on.
- Before using any llama.cpp flag or HTTP endpoint, **verify it against the installed build** (`llama-server --help`, and by calling the endpoint). Flags and defaults in llama.cpp change often. Do not trust the examples in this file blindly.
- Never invent measurements. Any number in a report must come from a file under `results/`.
- Ask the user only when a decision is truly theirs (hardware target, hackathon deadline, which models they have downloaded). Otherwise pick the default stated here and record the choice in `docs/DECISIONS.md`.

---

## 1. Project summary

### 1.1 One-sentence pitch
Given a coding task, the device's **current** resources, and a hard time and RAM budget, Dwarv picks the local execution strategy most likely to produce a **test-verified** solution, and re-decides after each failed verification using both the test feedback and live resource measurements.

### 1.2 Core hypothesis (a hypothesis, not an established contribution)
> A fully local developer agent can achieve a higher verified task-completion rate under a hard, dynamically changing RAM budget by choosing its next action using both test feedback and live resource measurements, compared with (A) a fixed config, (C) verification-guided retries with no resource awareness, and (C+) retries plus model escalation with no resource awareness.

### 1.3 Honest novelty position
Do not claim algorithmic novelty. Frame Dwarv as:
1. An open-source harness for **RAM-budgeted, test-guided local code repair**, and
2. An **empirical study** of when adaptive control helps or does not help.

Related work found during research (verify before citing; read primary sources):
- **LMForge** (https://github.com/phoenixtb/lmforge): hardware-aware daemon; engine selection, VRAM admission, LRU eviction, telemetry, model switch API. Its docs describe no test-driven verification/retry loop (this is "not documented", not a confirmed limitation).
- **TinyForge** (https://github.com/ranausmanai/tinyforge): MLX/Apple Silicon; test-failure-driven evolutionary search and repair-pair LoRA training; results on small HumanEval slices.
- **llama.cpp** (https://github.com/ggml-org/llama.cpp): the inference engine we reuse. Router mode, `--fit`, KV-cache types.
- **CodeRescue** (arXiv 2607.19338): budget-calibrated recovery routing for coding agents using execution feedback; budget is cost, not live RAM. Closest research.
- **Resample or Reroute** (arXiv 2607.08665): resample vs reroute as competing uses of one per-query budget.
- **MemSpec** (arXiv 2608.10362): memory-aware runtime for adaptive draft scheduling on edge devices (speculative decoding, not task correctness).
- **EvalPlus** (https://github.com/evalplus/evalplus): HumanEval+ / MBPP+ benchmark with extended tests.

### 1.4 Key design insight
A **static** RAM budget will not show an advantage, because a well-chosen fixed configuration wins. Dwarv only has a chance to win when the **budget changes mid-run**. Therefore the project MUST include a deterministic "memory squeeze" injector (Step 8). Without it, there is no meaningful experiment.

---

## 2. Scope

### 2.1 In scope (MVP)
- Python CLI, **cross-platform**: Windows (native), macOS, and Linux — a single `pip install dwarv` (or `pipx install dwarv`) must work on all three without WSL2 or a container being mandatory. See 2.3 for how sandboxing degrades per platform.
- Inference through `llama-server` (llama.cpp) as a subprocess, accessed over local HTTP. Use the project's native prebuilt binary for the host OS/arch (llama.cpp ships Windows/macOS/Linux releases); do not require building from source.
- 2 to 3 GGUF coding models from one family, small to mid size.
- Self-contained bug-fix / code-generation tasks with deterministic tests (EvalPlus subset).
- Resource monitor (RSS + system available memory), budget manager, sandboxed verifier.
- Deterministic rule-based controller with fully logged decisions.
- Baselines A, C, C+ and Dwarv; optional Baseline B (LMForge).
- Memory squeeze injector, benchmark harness, analysis script, terminal demo.
- Simple read-only local web dashboard: benchmark results view + live/replay view of the controller's processing flow (Step 10A).
- Offline-after-setup proof.

### 2.2 Non-goals (do NOT build)
- Training or fine-tuning any model.
- A general-purpose autonomous agent.
- A new inference engine or quantization format.
- Retrieval / source-code indexing, resumable task state, power measurement.
- Any feature that requires a remote API at runtime.
- A polished or editable GUI. The MVP includes only a **simple, read-only, local dashboard** (Step 10A). No accounts, no settings pages, no write actions.

### 2.3 Sandboxing strategy per platform

The only piece of this project with a real OS dependency is the untrusted-code verifier (Step 4): Linux's `resource.setrlimit` (address space / CPU time) and `unshare -n` (no network) have no Windows equivalent, and `resource` does not exist on Windows at all. Everything else (the CLI, `llama-server`, the dashboard) is genuinely cross-platform. Resolve this with a tiered sandbox, chosen automatically at runtime and recorded in the run's metadata:

1. **Docker available** (any OS, including Windows via Docker Desktop): run the verifier in a container with `--network none`, a memory limit, and a CPU/time limit. This is the **primary, recommended** mode and gives the strongest, most uniform guarantee across platforms.
2. **No Docker, Linux/WSL2/macOS**: fall back to `resource.setrlimit` + `unshare -n` (Linux) or `resource.setrlimit` alone (macOS, which lacks `unshare`; document the reduced network guarantee and monitor connections instead).
3. **No Docker, native Windows**: fall back to a subprocess with a wall-clock timeout and a Windows Job Object (via `pywin32` or `subprocess` + `CREATE_NEW_PROCESS_GROUP` and a job-object memory cap) for the process-kill and memory-cap guarantee; there is no rlimit-equivalent address-space cap and no `unshare`, so **document this tier as reduced isolation** rather than pretending it matches tier 1/2.

`dwarv doctor` must detect and report which tier is active (Docker present? OS?) so every run's logs and the dashboard's Setup tab show which sandbox guarantee was in force — never silently run under a weaker guarantee than the user believes they have.

---

## 3. Repository layout

Create exactly this structure (add files only when needed):

```
dwarv/
├── README.md
├── pyproject.toml
├── docs/
│   ├── DECISIONS.md          # every non-obvious choice + reason
│   ├── PROGRESS.md           # running log per step
│   ├── EXPERIMENT.md         # protocol, frozen before running
│   └── PRIOR_ART.md          # verified notes + links (primary sources only)
├── configs/
│   ├── models.yaml           # model registry (paths, sizes, measured RSS)
│   ├── budgets.yaml          # budget profiles + squeeze schedules
│   └── systems.yaml          # baseline/system definitions
├── src/dwarv/
│   ├── __init__.py
│   ├── cli.py                # entry point: doctor, setup-offline, check-offline, run, bench, demo, profile, gui
│   ├── types.py              # dataclasses (Task, Attempt, Decision, State, Result)
│   ├── runtime/
│   │   ├── base.py           # RuntimeAdapter protocol
│   │   └── llamacpp.py       # llama-server subprocess adapter
│   ├── resources/
│   │   ├── monitor.py        # psutil sampler (thread)
│   │   ├── budget.py         # BudgetManager, enforcement
│   │   └── squeeze.py        # memory squeeze injector
│   ├── verify/
│   │   ├── sandbox.py        # subprocess sandbox runner
│   │   ├── failure.py        # failure classification + feedback formatting
│   │   └── evalplus_adapter.py
│   ├── tasks/
│   │   └── loader.py         # frozen task subset loader
│   ├── prompts/
│   │   └── templates.py      # initial + repair prompt builders
│   ├── systems/
│   │   ├── base.py           # System interface: solve(task, budget) -> Result
│   │   ├── fixed.py          # Baseline A
│   │   ├── retry.py          # Baseline C
│   │   ├── retry_escalate.py # Baseline C+
│   │   ├── lmforge.py        # Baseline B (optional)
│   │   └── dwarv.py         # Dwarv controller
│   ├── controller/
│   │   ├── policy.py         # rule-based policy
│   │   └── actions.py        # Action enum + executors
│   ├── telemetry/
│   │   └── logger.py         # JSONL event logging
│   ├── bench/
│   │   ├── harness.py        # runs systems x tasks x seeds x budgets
│   │   └── analyze.py        # tables + plots from results/ (GUI reuses these functions)
│   └── gui/                  # simple read-only local dashboard (Step 10A)
│       ├── server.py         # FastAPI app, binds 127.0.0.1 only
│       ├── data.py           # reads results/ JSONL; builds summaries and traces
│       ├── live.py           # tails the active run's JSONL -> SSE
│       └── static/
│           ├── index.html    # single page, no build step
│           ├── app.js        # vanilla JS
│           ├── style.css
│           ├── flow.json     # controller flow graph definition (nodes, edges)
│           └── vendor/       # vendored chart lib (NO CDN; must work offline)
├── tasks/
│   └── subset_v1.json        # FROZEN task IDs + seed used to pick them
├── results/                  # raw JSONL per run (committed)
├── scripts/                   # thin POSIX convenience wrappers; the real, cross-platform
│   ├── setup_offline.sh       # entry points are the `dwarv setup-offline` / `dwarv check-offline`
│   ├── check_offline.sh       # CLI subcommands, which also work unwrapped on Windows
│   └── run_demo.sh
└── tests/
    ├── test_sandbox.py
    ├── test_policy.py
    ├── test_budget.py
    └── test_failure.py
```

---

## 4. Step-by-step build

Estimated hours are for one focused developer. Steps 0 to 7 + 9 are the **24h MVP**. Everything is the **48h version**.

---

### Step 0: Decisions and project skeleton (about 1h)

**Tasks**
1. Create the repo layout above, `pyproject.toml` (Python 3.10+), and a `dwarv` console script. Target a plain `pip install dwarv` / `pipx install dwarv` working unmodified on Windows, macOS, and Linux.
2. Dependencies: `psutil`, `pyyaml`, `httpx` (or `requests`), `rich`, `typer` (or `argparse`), `pytest`, `evalplus`, `pandas`, `matplotlib`, `fastapi`, `uvicorn` (the last two only for the dashboard in Step 10A; keep them in an optional extra `dwarv[gui]` so the core stays light). Pin **minimum compatible version ranges** (e.g. `numpy>=1.26`), not exact pins — exact old pins can lack prebuilt wheels for the installer's current Python, which forces a from-source build and a compiler that most end users (especially on Windows) do not have.
3. Write `docs/DECISIONS.md` with these initial decisions (edit if the user overrides):
   - OS target: cross-platform (Windows, macOS, Linux) — see §2.3 for the per-platform sandbox tiering; WSL2 is supported but never required.
   - Runtime: `llama-server` from llama.cpp, HTTP, one model resident at a time in MVP. Use the official prebuilt binary release for the host OS/arch; only build from source as a fallback.
   - Language: Python.
   - Benchmark: EvalPlus (HumanEval+ / MBPP+) subset, 40 to 60 tasks, frozen.
   - Models: one family, 2 to 3 sizes (see Step 1).
4. Run `dwarv doctor` stub that prints OS, CPU count, total/available RAM, GPU presence (`nvidia-smi` if available), Python version, llama-server version, Docker presence, and the resulting **sandbox tier** (1/2/3 per §2.3).

**Acceptance check**
- `pip install -e .` works on the developer's current OS with no compiler required; `dwarv doctor` prints hardware info and the active sandbox tier without errors.
- `pytest` runs (even with zero real tests).

**Fallback**: if a dependency fails to install, drop it from the MVP (e.g., `rich`, `typer`) and use stdlib.

---

### Step 1: Offline-ready environment (about 2h)

**Tasks**
1. Download the official prebuilt `llama-server` release for the host OS/arch (Windows/macOS/Linux all have releases); only build from source if no prebuilt release fits. Record the version/commit in `docs/DECISIONS.md`. Run `llama-server --help` and save the output to `docs/llama_server_help.txt` so later steps can reference real flags.
2. Choose models (default suggestion, adjust to what the user's machine can hold):
   - `small`: ~1.5B coder, Q4_K_M
   - `medium`: ~7B coder, Q4_K_M
   - optional `large`: ~14B coder, Q4_K_M (only if the machine has enough RAM)
   Use one family (e.g., Qwen2.5-Coder GGUF) so only size varies. Verify actual file sizes and licenses on Hugging Face; do not rely on blog figures. Record them in `configs/models.yaml`.
3. Implement `dwarv setup-offline` (a CLI subcommand, so it runs the same way on every OS): downloads models and the EvalPlus datasets into `./cache/`, sets `LLAMA_CACHE` and `HF_HOME` inside the project, installs pip deps into a local wheelhouse if possible. Ship `scripts/setup_offline.sh` as a thin POSIX convenience wrapper around it for Linux/macOS/WSL2 users who prefer a script; Windows users just run the CLI command directly.
4. Implement `dwarv check-offline` (CLI subcommand): disables network for the test using the sandbox tier detected by `dwarv doctor` (Docker `--network none` on tier 1; `unshare -n` on tier 2/Linux; on tier 3/native Windows without Docker, document how to disable the adapter or use airplane mode, since there is no per-process network kill), starts llama-server with the small model, sends one prompt, runs one EvalPlus task verification, exits 0 on success. `scripts/check_offline.sh` wraps it the same way as above.

**Acceptance check**
- With the network disabled, `dwarv check-offline` (or `scripts/check_offline.sh` on POSIX) passes.
- `configs/models.yaml` lists each model with path, file size, quant, and context sizes tested.

**Fallback**: if the network cannot be disabled in the environment, assert that no outbound connections are made by monitoring with `ss`/`lsof` during the run, and document the limitation.

---

### Step 2: Runtime adapter (about 2h)

**Interface** (`runtime/base.py`)

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass
class GenParams:
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 1024
    seed: int | None = None
    stop: list[str] | None = None


@dataclass
class GenResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    wall_s: float
    finish_reason: str


class RuntimeAdapter(Protocol):
    def load(self, model_id: str, ctx_size: int) -> float: ...  # returns load seconds
    def unload(self) -> None: ...
    def generate(self, prompt: str, params: GenParams) -> GenResult: ...
    def pid(self) -> int | None: ...  # server pid for RSS reads
    def current(self) -> tuple[str | None, int | None]: ...  # (model_id, ctx_size)
```

**Tasks**
1. Implement `LlamaCppRuntime`:
   - Launch `llama-server` as a subprocess with `-m <gguf> -c <ctx> --port <free port> --host 127.0.0.1`, plus flags you have verified from `--help` (e.g., GPU layer setting, flash attention, KV cache type). Keep flags in config, not hard-coded.
   - Wait for readiness by polling the health endpoint with timeout.
   - Use the OpenAI-compatible chat completions endpoint (verify path) for generation. Pass sampling params and seed.
   - `load()` for a different model or ctx = stop server and relaunch (simple and robust). Time it and return the seconds.
   - `unload()` terminates the process cleanly, then kills if needed.
2. Make sure the server binds to `127.0.0.1` only.
3. Handle crashes: if the process dies (e.g., OOM), raise `RuntimeCrashed` with the exit code and last log lines.
4. Log load time per model/ctx combination into `results/model_load_times.jsonl`.

**Acceptance check**
- A script generates a completion with each configured model.
- Switching small to medium to small works 3 times with no orphan processes (`pgrep llama-server` empty after unload).
- Load times are recorded.

**Fallback**: if subprocess management is unreliable, run `llama-server` manually outside Python and have the adapter only talk HTTP (document this).

**Optional later optimization**: llama.cpp router mode (`--models-max`, on-demand load, unload endpoint). Only attempt after the MVP works; verify availability in the installed build first.

---

### Step 3: Resource monitor and budget manager (about 2h)

**Tasks**
1. `resources/monitor.py`: background thread sampling every 0.5s (configurable):
   - `server_rss_mb`: RSS of the llama-server process (include children).
   - `sys_available_mb`, `sys_total_mb`, `swap_used_mb` via `psutil`.
   - Optional GPU memory via `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits` if present. Skip silently if unavailable.
   - Keep a ring buffer; expose `latest()`, `peak_rss_mb()`, and `mean_overhead_ms()` (the monitor's own cost).
2. `resources/budget.py`: `BudgetManager` with:
   - `ram_limit_mb` (can change at runtime via `set_ram_limit(new_limit, reason)`), `time_limit_s`, `max_attempts`.
   - `headroom_mb()` = `ram_limit_mb - server_rss_mb` (and a separate safety check against `sys_available_mb`).
   - `remaining_time_s()`, `attempts_left()`.
   - `check()` returns `OK | WARN | VIOLATION`. `WARN` when headroom is under a configurable fraction (default 10%). `VIOLATION` when RSS exceeds the limit.
   - **Enforcement**: on VIOLATION, record the event, kill the server, and surface `BudgetViolation`. Violations count in metrics.
3. Separate concepts clearly in code and logs: process RSS, system available memory, model file size, KV-cache estimate. Never conflate them.

**Acceptance check**
- Unit tests in `tests/test_budget.py` with a fake monitor cover WARN/VIOLATION/limit-change behavior.
- Loading the medium model shows a plausible RSS in the monitor log; record RSS per (model, ctx) into `configs/models.yaml` (these measured values drive the controller later).

**Fallback**: if RSS of children is hard to get, measure the main PID and document it.

---

### Step 4: Tasks and sandboxed verifier (about 3h)

**Tasks**
1. `tasks/loader.py`: load EvalPlus HumanEval+ and MBPP+ problems. Build `tasks/subset_v1.json`:
   - Choose 40 to 60 task IDs with a **seeded random sample** (record the seed in the file). Never hand-pick tasks. If results later look bad, do NOT swap tasks; instead record a new `subset_v2.json` and report both.
   - Use the EvalPlus tests for the final verdict. Use a lighter public test subset (base tests) for the **feedback** shown to the model, to avoid leaking hidden-test details. Document exactly which tests feed back versus which grade.
2. `verify/sandbox.py`: implement the three tiers from §2.3 behind one interface (`run(code, tests, limits) -> SandboxResult`), auto-selecting the tier `dwarv doctor` detected, with every result record carrying which tier ran it:
   - **Tier 1 (Docker, any OS)**: container with `--network none`, a memory limit, and a CPU/wall-clock timeout.
   - **Tier 2 (no Docker, Linux/macOS/WSL2)**: subprocess with a wall-clock timeout (default 10s per task run), `resource.setrlimit` for address space and CPU time, `unshare -n` on Linux (macOS has no `unshare`; monitor connections instead and document the gap).
   - **Tier 3 (no Docker, native Windows)**: subprocess with a wall-clock timeout and a Windows Job Object memory/process cap; no address-space rlimit and no per-process network kill exist on Windows, so disable host networking for the whole check (§Step 1) rather than per-process, and mark every result from this tier as reduced-isolation.
   - All tiers: a fresh temp directory as cwd (cleaned afterward), stdout/stderr capture size-capped.
   Treat all generated code as untrusted. Never `exec` it in the Dwarv process.
3. `verify/failure.py`: classify each result into one of:
   `PASS`, `SYNTAX_ERROR`, `IMPORT_ERROR`, `RUNTIME_ERROR`, `WRONG_OUTPUT`, `TIMEOUT`, `EMPTY_OR_NO_CODE`.
   Produce **specific feedback** (e.g., `input=[15] expected='FizzBuzz' got='Fizz'`, or the exception type + line). Truncate to a configured token budget.
4. `prompts/templates.py`: two builders:
   - `initial_prompt(task)`: task description + required signature, ask for a single fenced code block.
   - `repair_prompt(task, previous_code, feedback)`: previous attempt + failure feedback, ask for a corrected full function.
   Implement `extract_code(text)` that robustly pulls the first fenced Python block.

**Acceptance check**
- `tests/test_sandbox.py`: infinite loop is killed by timeout; memory bomb is stopped; network access fails; file writes stay in the temp dir.
- `tests/test_failure.py`: each failure class is reproduced and classified correctly.
- A "reference solution" smoke test: EvalPlus canonical solutions pass the verifier for a sample of tasks (this validates the verifier itself).

**Fallback**: if EvalPlus integration is slow, start with the HumanEval+ base dataset only and add MBPP+ later.

---

### Step 5: Baseline A, fixed local config (about 1.5h)

**Interface** (`systems/base.py`)

```python
@dataclass
class Result:
    system: str
    task_id: str
    passed: bool  # final hidden-test verdict
    attempts: int
    wall_s: float
    peak_rss_mb: float
    budget_violation: bool
    final_code: str | None
    decisions: list[dict]  # empty for baselines unless they make choices
    stop_reason: str  # PASS | BUDGET_TIME | BUDGET_ATTEMPTS | BUDGET_RAM | CRASH | GAVE_UP


class System(Protocol):
    name: str

    def solve(
        self,
        task,
        budget: "BudgetManager",
        runtime: "RuntimeAdapter",
        monitor: "ResourceMonitor",
        logger: "EventLogger",
    ) -> Result: ...
```

**Tasks**
1. `systems/fixed.py`: one model, one ctx size (from `systems.yaml`), single generation, verify, done. No retries.
2. `bench/harness.py` skeleton: iterate tasks, create a fresh budget per task, call `system.solve`, write one JSON line per result to `results/<run_id>/<system>.jsonl`.
3. `dwarv run --system fixed --task <id>` CLI for single-task debugging.

**Acceptance check**
- Run Baseline A on the full frozen subset under a static budget. Produce a first pass-rate number. Save raw JSONL. This is your first end-to-end result and safety net.

---

### Step 6: Baseline C, verification-guided retries (about 2h)

**Tasks**
1. `systems/retry.py`: same fixed model/ctx as Baseline A. On failure, build a repair prompt with structured feedback and retry, up to `max_attempts` and the time budget.
2. Identical caps across systems: same `max_attempts`, same `time_limit_s`, same sampling params and seed policy. Put these in `configs/budgets.yaml`.
3. Log each attempt as an event (`attempt_started`, `generation_done`, `verified`).

**Acceptance check**
- Baseline C pass-rate is greater than or equal to Baseline A on the subset (if lower, investigate the repair prompt before moving on; do not tune against hidden tests).

---

### Step 7: Dwarv controller v1 (about 4h)

Use a **deterministic rule-based policy**. A learned policy is not justified for the MVP.

**State** (`types.py`)

```python
@dataclass
class State:
    task_id: str
    attempt_idx: int
    attempts_left: int
    time_left_s: float
    ram_limit_mb: float
    server_rss_mb: float
    headroom_mb: float
    sys_available_mb: float
    model_id: str
    ctx_size: int
    last_failure: str | None  # failure class from verify/failure.py
    failure_history: list[str]
    repeated_same_failure: int
    model_load_cost_s: dict[str, float]  # measured, per model
    est_rss_mb: dict[tuple[str, int], float]  # measured table from configs/models.yaml
```

**Actions** (`controller/actions.py`)

```python
class Action(Enum):
    RETRY_WITH_FEEDBACK = 1  # same model, same ctx
    RETRY_LOWER_TEMP = 2
    RETRY_HIGHER_TEMP = 3  # resample for diversity
    SHRINK_CONTEXT = 4  # reload same model with smaller ctx (needs reload)
    SWITCH_SMALLER_MODEL = 5  # needs reload
    SWITCH_LARGER_MODEL = 6  # needs reload, only if predicted RSS fits headroom
    STOP_SAFELY = 7
```

**Policy v1** (`controller/policy.py`). Implement as an ordered list of rules; the first matching rule fires. Every decision returns `Decision(action, reason, inputs_snapshot)` and is logged.

1. **Hard stop**: no attempts left, or `time_left_s` less than the estimated cost of the cheapest next action, then `STOP_SAFELY`.
2. **Budget shrank / violation risk**: if `server_rss_mb > ram_limit_mb`, or `headroom_mb` below `warn_fraction * ram_limit_mb`:
   - if a smaller (model, ctx) combination with measured RSS under the new limit exists, choose `SHRINK_CONTEXT` first if ctx > min_ctx, else `SWITCH_SMALLER_MODEL`;
   - if none fits, `STOP_SAFELY`.
3. **Syntax/format failures** (`SYNTAX_ERROR`, `EMPTY_OR_NO_CODE`, `IMPORT_ERROR`): `RETRY_WITH_FEEDBACK` (cheap, same model). If it repeats twice, `RETRY_LOWER_TEMP`.
4. **Logic failures** (`WRONG_OUTPUT`, `RUNTIME_ERROR`):
   - first occurrence: `RETRY_WITH_FEEDBACK`;
   - same failure repeated 2 times and a larger model's measured RSS fits `ram_limit_mb - safety_margin` and its load cost fits `time_left_s`: `SWITCH_LARGER_MODEL`;
   - otherwise `RETRY_HIGHER_TEMP`.
5. **TIMEOUT** (the code is too slow/looping): `RETRY_WITH_FEEDBACK` with an explicit "avoid infinite loops / improve efficiency" note.
6. **Default**: `RETRY_WITH_FEEDBACK`.

**Initial choice (before any attempt)**: pick the **largest** (model, ctx) whose measured RSS fits `ram_limit_mb - safety_margin` and whose load cost fits the time budget; otherwise the smallest model. Make `safety_margin` configurable (start at 10% of the limit).

**Rules of the road**
- All thresholds live in `configs/systems.yaml`. No magic numbers in code.
- The policy is a pure function `decide(state) -> Decision`, so it is trivially unit-testable.
- Log the monitor's overhead and every model reload (cost counts against the time budget).
- Cap total reloads per task (default 2) to prevent thrashing.

**Acceptance check**
- `tests/test_policy.py`: table-driven tests for each rule, including: low headroom triggers a downgrade; larger model chosen only when RSS fits; repeated logic failure escalates; no attempts left stops; reload cap enforced.
- `dwarv run --system dwarv --task <id>` prints a readable decision trace.

---

### Step 8: Memory squeeze injector and ablations (about 2h)

**Tasks**
1. `resources/squeeze.py`: a deterministic scheduler that changes the budget during a run. Implement two modes:
   - **Budget-cut mode**: at time `t` into the task (or after attempt `k`), call `budget.set_ram_limit(new_limit)`. Deterministic and reproducible; this is the primary experiment mode.
   - **Competing-process mode** (optional, demo only): a background process allocates and holds N MB, reducing `sys_available_mb`. Less reproducible, so do not use it for the primary results.
2. Define profiles in `configs/budgets.yaml`, for example:
   - `static_tight`: constant limit sized so only the small and medium models fit.
   - `static_loose`: constant limit where the medium model fits comfortably.
   - `squeeze_mid`: starts loose, drops to tight after attempt 1 or at a fixed time.
   Derive the actual MB values from **your measured RSS table**, not guesses.
3. Implement the ablation systems:
   - `systems/retry_escalate.py` (**Baseline C+**): verification-guided retries plus model escalation on repeated failure, **no** RAM awareness (it ignores headroom and may pick a model that violates the new limit; that is what we are testing).
   - A Dwarv variant with **feedback signals disabled** (uses resource state only; failure type ignored). Define it in `systems.yaml` via a flag.
   - A Dwarv variant with **resource signals disabled** (equivalent to C+ with Dwarv's feedback rules), if time allows.
4. Make sure all systems receive identical squeeze events at identical points.

**Acceptance check**
- Running the same task under `squeeze_mid` with Baseline C+ shows budget-violation events when it escalates after the cut; Dwarv avoids them or degrades gracefully. If this does not happen, the squeeze values are wrong; recalibrate from measured RSS.

---

### Step 9: Benchmark harness and analysis (about 3h)

**Experiment protocol** (write this into `docs/EXPERIMENT.md` and **freeze it before the final run**)

- Same machine, same background load, same llama.cpp build, same models/quants, same task subset, same budget profiles, same sampling parameters.
- Systems: A (fixed), C (retry), C+ (retry+escalate), Dwarv. Optional B (LMForge-hosted fixed model) only if it installs cleanly in under one hour; otherwise record "not run" with the reason.
- Equalize **total resources**: same `max_attempts`, same `time_limit_s`, same models available to C+ and Dwarv. Any difference must be an explicit, reported variable.
- Budget profiles: `static_tight`, `static_loose`, `squeeze_mid`.
- Seeds: at least 3 per (system, profile, task). Report mean and spread.
- Warm-up: one discarded run per model to warm disk cache. Record whether the page cache was cold or warm.
- Primary metric: **verified pass rate** under the hidden EvalPlus tests, at equal budget.
- Secondary metrics: peak RSS, budget violations, wall-clock latency, attempts used, failed-retry rate, model-switch overhead, offline completion.
- Noise sources to record: other processes, thermal throttling, page cache, swap use, sampling randomness.
- Decision criteria (write in advance): the hypothesis is **supported** only if Dwarv beats C+ on verified pass rate under `squeeze_mid` with a margin larger than seed-to-seed spread, **and** has fewer budget violations. If Dwarv only beats A or C but not C+, report that the gain comes from escalation, not resource awareness.

**Tasks**
1. `bench/harness.py`: nested loop over systems, profiles, tasks, seeds; resume support (skip completed runs by key); crash-safe JSONL writes; one fresh runtime/monitor/budget per run; disk-space and process-leak checks between runs.
2. `bench/analyze.py`: read `results/`, produce:
   - a summary table (CSV + Markdown) per system x profile with pass rate, mean peak RSS, violations, mean latency;
   - bar chart of pass rate with error bars;
   - scatter of peak RSS vs pass;
   - a per-task difference table (which tasks Dwarv solved that C+ did not, and vice versa);
   - bootstrap confidence intervals for pass-rate differences.
3. Never overwrite raw results. Each run gets a `run_id` directory with a copy of the config used and the git commit hash.

**Acceptance check**
- A small dry run (5 tasks, 1 seed) completes end-to-end and produces all tables/plots.
- Re-running with the same `run_id` resumes without duplicating results.

**If the hypothesis is not supported**: report that plainly. Useful conclusions still available: when adaptive control does not help, what failure modes dominated, what overheads (reload cost, monitoring) cost, and a reusable harness.

---

### Step 10: Demo and CLI (about 3h)

**Tasks**
1. `dwarv demo --task <id>` shows a live terminal view (use `rich.live`):
   - current model + ctx, RSS vs limit (bar), headroom, remaining time and attempts,
   - latest decision with its reason,
   - test results of the last attempt,
   - an event log tail.
2. `scripts/run_demo.sh` runs the **required demo sequence**:
   1. Run a real task with the fixed config (Baseline A or C).
   2. Run the same task with Dwarv.
   3. Trigger the memory squeeze mid-run and show the different behavior (C+ violates / fails; Dwarv adapts).
   4. Show verification results for each.
   5. Turn the network off (or run inside a no-network container) and rerun one task to show offline operation.
   6. Print the benchmark summary table from `results/` (real numbers only).
   7. Open the dashboard (`dwarv gui`) to show the Results tab and the controller Flow tab for the squeeze run (label any replayed run as "replay").
3. Choose demo tasks **before** running the demo, and also show at least one task where Dwarv does not help (honesty strengthens credibility).

**Acceptance check**
- The demo runs end-to-end from a clean shell in under ~10 minutes and never crashes the machine.

---

### Step 10A: Simple local dashboard, results + controller flow (about 4 to 5h)

**Purpose**: a small web UI that (1) shows benchmark results and (2) visualizes the AI controller's processing flow, either live during a run or as a replay of a finished run. It is a **viewer**, not a control panel.

**Hard constraints**
- **Read-only.** It only reads `results/` JSONL and the active run's event stream. It must never start models, run code, or modify files. No write endpoints.
- **Local and offline.** Bind to `127.0.0.1` only. No CDN, no web fonts, no external requests. Vendor any chart library into `gui/static/vendor/`. No npm or build step; plain HTML + vanilla JS + CSS (or a vendored lib such as uPlot / Chart.js).
- **Untrusted content.** Model output, generated code, task text, and feedback strings are untrusted. Render them with `textContent` (never `innerHTML`), and escape in any template.
- **No fake data.** If `results/` is empty, show "No results yet". Never show placeholder or sample numbers.
- Keep it simple. One page, tabs, no state beyond the URL query string.

**Tasks**

1. **Backend** (`gui/server.py`, FastAPI). Reuse aggregation functions from `bench/analyze.py` rather than duplicating logic. Validate every path/query parameter against a whitelist of existing run ids (prevent path traversal). Endpoints:
   - `GET /api/health`
   - `GET /api/runs`: list run ids with metadata (timestamp, git commit, task-subset hash, profiles, systems).
   - `GET /api/runs/{run_id}/summary`: per system x profile: pass rate with bootstrap CI, mean and peak RSS, budget violations, mean latency, attempts used, failed-retry rate, model-switch overhead.
   - `GET /api/runs/{run_id}/tasks`: per-task, per-system outcomes (for the diff table).
   - `GET /api/runs/{run_id}/trace?system=&profile=&task=&seed=`: ordered events for one run (sorted by `seq`).
   - `GET /api/live/stream`: Server-Sent Events that tail the active run's JSONL file (resume from the last `seq`; heartbeat every 15s). Return an empty stream if nothing is running.
   - `GET /api/setup`: contents of `doctor` output, measured RSS table from `configs/models.yaml`, and the last offline-check result.
2. **Frontend** (`gui/static/`). Four tabs:

   **Tab 1: Results**
   - Header banner: run id, git commit, task subset hash, hardware summary, and a clear label of what is measured versus planned.
   - Summary table (sortable): system, profile, verified pass rate (with CI), peak RSS, violations, mean latency.
   - Bar chart of pass rate per system, grouped by profile, with error bars.
   - Scatter: peak RSS vs pass rate per run.
   - Per-task diff table: tasks Dwarv solved that Baseline C+ did not, and vice versa, with links that open the Flow tab at that run.
   - "Hypothesis check" panel: evaluates the **pre-registered criteria** from `docs/EXPERIMENT.md` against the data (Dwarv vs C+ under `squeeze_mid`, margin vs seed spread, violations) and shows Supported / Not supported / Inconclusive with the numbers behind it. Compute it from data only.

   **Tab 2: Controller flow (live or replay)**
   - **Flow diagram** (inline SVG built from `flow.json`): nodes for `Task` → `Read resources + budget` → `Choose initial config` → `Load model` → `Generate` → `Verify` → (`Pass` → `Done`) or (`Fail` → `Policy decides` → action nodes: `Retry with feedback`, `Lower/Higher temp`, `Shrink context`, `Smaller model`, `Larger model`, `Stop safely`) → back to `Generate` / `Load model`. Highlight the node of the currently replayed event, animate the edge just taken, and keep a count badge on each node for how often it was visited. Clicking a node filters the event list to that node.
   - **Attempt timeline**: one card per attempt showing model, ctx, temperature, generation time, failure class, a short size-capped feedback snippet, and the decision that followed (rule id, action, reason).
   - **Live resource chart**: server RSS vs RAM limit over time, with vertical markers for budget changes (squeeze), model loads, decisions, and violations. Headroom shown as a bar.
   - **Budget gauges**: time left, attempts left, reloads used.
   - **Decision inspector**: selecting a decision shows the full `inputs_snapshot` the policy saw and the rule that fired. This is the key "why did it do that" view.
   - **Replay controls**: play/pause, step forward/back, speed (1x/2x/5x), scrub bar. Replay is built from the same event list as live mode.

   **Tab 3: Compare**
   - Pick one task + profile + seed and two systems (default: Baseline C+ vs Dwarv). Show two aligned timelines and RSS charts side by side so the divergence at the squeeze point is visible (for example, C+ escalating into a violation while Dwarv steps down).

   **Tab 4: Setup / offline**
   - Hardware summary, model table with measured RSS and load times, offline-check status (pass/fail + timestamp), and the list of loaded config files.
3. **Flow definition**: create `gui/static/flow.json` with node ids, labels, positions, and edges. **Node ids must equal the `flow_node` values emitted in the event logs** (Step 5.3), and action nodes must map 1:1 to the `Action` enum, so highlighting is a simple lookup with no special-casing. Add a unit test that every `flow_node` emitted by the code exists in `flow.json` and vice versa.
4. **CLI**:
   - `dwarv gui --results results/ --port 8765` starts the server and prints `http://127.0.0.1:8765`.
   - `dwarv gui --replay <run_id>` opens directly on the Flow tab.
   - `dwarv demo` may start the GUI alongside the terminal view; the terminal view must still work without the GUI.
5. **Build order (to protect the schedule)**: (a) Results tab from finished JSONL; (b) Flow tab in **replay** mode from a finished trace; (c) Compare tab; (d) live SSE mode; (e) Setup tab. Stop after (b) if time is short.
6. **Static fallback**: add `dwarv report --run <run_id> --out report.html` that writes a single self-contained HTML file (data embedded as JSON, charts as inline SVG, no external requests). Use this if the server approach causes trouble before the demo.

**Acceptance check**
- With the network disabled, the dashboard loads fully; the browser dev tools show **zero** external requests.
- Rendered from a dry-run (5 tasks, 1 seed): the Results tab shows correct tables and charts that match `analyze.py` output exactly.
- For a Dwarv run with a squeeze, replay highlights the same node sequence as the `decision`/`flow_node` events in the JSONL, and the decision inspector shows the correct `rule_id` and `inputs_snapshot`.
- XSS check: a task whose model output contains `<script>alert(1)</script>` and `<img onerror=...>` renders as plain text and executes nothing.
- No write endpoints exist (a `POST/PUT/DELETE` returns 405).
- Empty `results/` shows "No results yet" without errors.
- Layout is usable at 1366x768.
- Live mode: start a benchmark run in one terminal; the Flow tab updates within about 2 seconds of each event.

**Fallback**: if live mode (SSE) is flaky, ship replay-only plus the static `report.html`. For the demo, replay a pre-recorded run (clearly labeled "replay") alongside one genuinely live run.

---

### Step 11: Write-up and cleanup (about 2h)

**Tasks**
1. `README.md`: what Dwarv is, install, offline setup, quickstart, how to reproduce the benchmark, limitations.
2. `docs/PRIOR_ART.md`: for each related project record: name, URL, date or latest verifiable activity, purpose, approach, hardware/offline assumptions, overlap, "not documented" features (clearly separated from verified limitations), and implications. Read primary sources; do not copy from this file blindly.
3. `docs/EXPERIMENT.md`: final protocol + results + honest interpretation.
4. A **claims table**: each claim in the README or pitch maps to a file in `results/` or a primary source link. Remove any claim that has no backing.
5. Pitch outline (5 slides worth of bullet points): problem, approach, experiment, results (real), limits.

---

## 5. Cross-cutting requirements

### 5.1 Privacy and offline behavior
- Core workflow makes **no** network calls after setup. Add a startup assertion/log line stating whether any network access occurred.
- No telemetry leaves the device. All logs are local JSONL.
- Model downloads happen only in `dwarv setup-offline` (or its `scripts/setup_offline.sh` wrapper), never implicitly at runtime.
- `llama-server` binds to localhost only.

### 5.2 Security and sandboxing
- Generated code is untrusted. Always run it through the sandbox (Step 4).
- Task files and repositories may contain hostile content. Never execute scripts from a task directory outside the sandbox.
- Do not store secrets in logs; truncate captured outputs.

### 5.3 Observability
Log every event as one JSON object per line with at least: `ts`, `run_id`, `system`, `task_id`, `seed`, `event`, plus event-specific fields. Required event types:
`run_started`, `model_loaded`, `monitor_sample` (downsampled), `attempt_started`, `generation_done`, `verified`, `decision`, `budget_change`, `budget_warn`, `budget_violation`, `model_unloaded`, `run_finished`.

**The dashboard (Step 10A) is driven entirely by these logs**, so every event must also carry:
- `seq`: monotonically increasing integer per run (lets the GUI order and tail events safely).
- `rel_t_s`: seconds since `run_started` (for timeline and chart alignment).
- `flow_node`: the id of the controller flow node the event belongs to (ids defined in `gui/static/flow.json`; see Step 10A).
- `attempt_idx`: attempt number where relevant.
- For `decision` events: `rule_id` (which policy rule fired), `action`, `reason`, and the `inputs_snapshot` the policy saw (RSS, limit, headroom, time and attempts left, last failure class, model, ctx).
- For `monitor_sample`: `server_rss_mb`, `ram_limit_mb`, `headroom_mb`, `sys_available_mb`. Downsample to at most 2 samples per second in the log.
- For `verified`: `failure_class`, a size-capped `feedback` string, and `passed_public` (feedback tests) kept separate from the final hidden verdict.

### 5.4 Reliability
- Every external call has a timeout.
- No orphaned `llama-server` processes after any exit path (use `atexit` + signal handlers + process groups).
- The harness can be killed and resumed.

### 5.5 Code quality
- Type hints and docstrings on public functions.
- `ruff` or `flake8` clean; `pytest` passes before each commit.
- Small commits, one per step or sub-step.

---

## 6. Risk register (keep updated in `docs/PROGRESS.md`)

| Risk | Impact | Mitigation |
|---|---|---|
| Existing tools already cover most features | Weak novelty | Frame as an empirical harness/study; cite closest work honestly |
| Adaptive decisions make results worse | Negative result | Pre-register decision criteria; report honestly; keep C+ ablation |
| Gains come from "more compute" not adaptivity | Misleading claim | Equal caps across systems; C+ baseline; report attempts and time |
| Model reload cost dominates | Controller looks bad | Measure load times; include them in the time budget; cap reloads |
| RSS measurement is noisy or misleading | Wrong budget decisions | Use measured RSS per (model, ctx); document mmap and page cache effects; add safety margin |
| Small test subset gives high variance | Unreliable conclusions | 40 to 60 tasks, 3+ seeds, bootstrap CIs; no hand-picking |
| Sandbox escape or hostile generated code | Security | Docker (`--network none`) as the primary tier on any OS; rlimits + `unshare -n` on Linux/macOS without Docker; Windows Job Objects on native Windows without Docker, explicitly logged as reduced isolation |
| llama.cpp flags change | Build breaks | Verify against `--help`; keep flags in config; pin the build |
| Time overrun | No demo | Follow the 24h cut; use listed fallbacks |
| GUI becomes a time sink | Core experiment unfinished | Build GUI only after Step 9 produces results; replay-first; static `report.html` fallback |
| GUI renders untrusted model output unsafely | XSS in demo machine | `textContent` only, localhost bind, read-only API, XSS test in acceptance |
| GUI shows data that does not match the logs | Misleading demo | All views derived from `results/` via shared `analyze.py` functions; no placeholder data |

---

## 7. Fallback plans

- **Model switching flaky**: restart `llama-server` per switch and count the cost in the time budget.
- **GPU profiling hard**: CPU RAM only; mention GPU as future work.
- **Controller too ambitious**: ship only the rule "when headroom is low, shrink context or step down a model; otherwise retry with feedback".
- **LMForge baseline won't install fast**: skip Baseline B and say so in the report.
- **EvalPlus integration slow**: begin with a handful of self-written tasks with deterministic tests, then port to EvalPlus.
- **No way to disable network per-process (native Windows, no Docker)**: disable the host's network adapter / airplane mode for the whole `check-offline` run instead of per-process isolation, and monitor connections; document the limitation.

---

## 8. 24-hour vs 48-hour cut

**24h (priority order)**
1. Steps 0 to 5 (environment, runtime, monitor, verifier, Baseline A)
2. Step 6 (Baseline C)
3. Step 7 (Dwarv policy v1)
4. Step 8 (budget-cut squeeze + C+)
5. Step 9 on a **smaller** subset (about 20 tasks, 1 to 2 seeds)
6. Step 10 (basic terminal demo) and a short README
7. Step 10A, **minimal**: Results tab + Flow tab in replay mode only (or the static `report.html` fallback)

**48h additions**
- Full subset, 3+ seeds, bootstrap CIs
- Dwarv ablations (feedback-off, resource-off)
- Optional Baseline B (LMForge)
- Competing-process squeeze for the demo
- Router-mode model switching optimization
- Full dashboard: live SSE mode, Compare tab, Setup tab, hypothesis-check panel
- Polished docs and claims table

---

## 9. Final deliverables checklist

- [ ] Working `dwarv` CLI, cross-platform (`doctor`, `setup-offline`, `check-offline`, `run`, `bench`, `demo`, `gui`, `report`)
- [ ] Local dashboard (results + controller flow replay) working offline, read-only, XSS-safe
- [ ] Flow graph (`flow.json`) consistent with emitted `flow_node` values (tested)
- [ ] Sandboxed verifier with tests
- [ ] Baselines A, C, C+ and Dwarv policy
- [ ] Memory squeeze injector and budget profiles
- [ ] Frozen task subset and frozen experiment protocol
- [ ] Raw results in `results/` and analysis outputs
- [ ] Offline proof (`dwarv check-offline`) passing
- [ ] `README.md`, `docs/PRIOR_ART.md`, `docs/EXPERIMENT.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`
- [ ] Claims table linking every claim to evidence
- [ ] Final go/no-go note in `docs/PROGRESS.md`: does the data support the hypothesis, partially, or not at all?

---

## 10. First message to the user when you start

Before coding, reply with:
1. The machine you detected (`dwarv doctor` output once Step 0 is done).
2. The models you plan to use and their verified file sizes.
3. Any decision from this file you want the user to override.
Then proceed with Step 0 without waiting, unless a decision is blocking.
