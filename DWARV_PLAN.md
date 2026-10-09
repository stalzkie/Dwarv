# DWARV: Build Instructions for Claude Code

> Hand this whole file to Claude Code as the project brief. Work through it in order. Do not skip the acceptance checks. Do not fabricate benchmark results, repo features, or citations.

---

## 0. How to work on this project

- You are building a hackathon product called **Dwarv**: a local, conversational coding assistant — "a local Claude Code" — that ships with a small bundled suite of local models, picks one for the session based on the user's actual hardware, explains that choice out loud, and adapts (retries, downgrades, upgrades) as it works and as resources change.
- Be pragmatic. A reliable end-to-end conversation beats speculative features.
- After finishing each step, run its **Acceptance check**, commit with a clear message, and append a short entry to `docs/PROGRESS.md` (what was done, what was measured, what is next).
- If a step is blocked for more than ~30 minutes, use the listed **Fallback**, note it in `docs/PROGRESS.md`, and move on.
- Before using any llama.cpp flag or HTTP endpoint, **verify it against the installed build** (`llama-server --help`, and by calling the endpoint). Flags and defaults in llama.cpp change often. Do not trust the examples in this file blindly.
- Never invent measurements. Any number in a report (and anything Dwarv says about its own hardware reasoning) must come from a real reading, not a guess.
- Ask the user only when a decision is truly theirs (hardware target, hackathon deadline, which models they have downloaded). Otherwise pick the default stated here and record the choice in `docs/DECISIONS.md`.

---

## 1. Project summary

### 1.1 One-sentence pitch

Dwarv is a local, offline, conversational coding assistant that ships with three bundled Qwen2.5-Coder models, picks the one that fits the user's **current** hardware for this session (and says why), and — whenever there's something to verify against (a failing test, a repo's own test suite) — retries using both test feedback and live resource measurements, stepping a model up or down mid-conversation if memory pressure changes.

### 1.2 Core hypothesis (a hypothesis, not an established contribution)

> A fully local coding assistant can hold a higher verified task-completion rate under a hard, dynamically changing RAM budget by choosing its next action using both test feedback and live resource measurements, compared with (A) a fixed model/config for the whole session, (C) verification-guided retries with no resource awareness, and (C+) retries plus model escalation with no resource awareness.

This hypothesis is validated internally (Section 9's eval harness) — it is **not** something the end user configures or sees. The user just experiences a tool that keeps working under memory pressure instead of hanging, crashing, or silently getting worse.

### 1.3 Honest novelty position

Do not claim algorithmic novelty. Frame Dwarv as:
1. A genuinely useful, fully local coding assistant with a **small, curated, transparent model suite** instead of one fixed model, and
2. An **empirical study** (internal, Section 9) of when resource-aware, test-guided control helps or does not help.

Related work found during research (verify before citing; read primary sources):
- **LMForge** (https://github.com/phoenixtb/lmforge): hardware-aware daemon; engine selection, VRAM admission, LRU eviction, telemetry, model switch API. Its docs describe no test-driven verification/retry loop (this is "not documented", not a confirmed limitation).
- **TinyForge** (https://github.com/ranausmanai/tinyforge): MLX/Apple Silicon; test-failure-driven evolutionary search and repair-pair LoRA training; results on small HumanEval slices.
- **llama.cpp** (https://github.com/ggml-org/llama.cpp): the inference engine we reuse. Router mode, `--fit`, KV-cache types.
- **CodeRescue** (arXiv 2607.19338): budget-calibrated recovery routing for coding agents using execution feedback; budget is cost, not live RAM. Closest research.
- **Resample or Reroute** (arXiv 2607.08665): resample vs reroute as competing uses of one per-query budget.
- **MemSpec** (arXiv 2608.10362): memory-aware runtime for adaptive draft scheduling on edge devices (speculative decoding, not task correctness).
- **EvalPlus** (https://github.com/evalplus/evalplus): HumanEval+ / MBPP+ benchmark with extended tests. Used only by the internal eval harness (Section 9), never shown to the end user.

### 1.4 Key design insight

A **static** model choice will not show an advantage, because a well-chosen fixed model wins when resources never change. Dwarv only has a chance to show its value when the **budget changes mid-session** (another app opens, a big test run spikes memory, etc.) or when a single model genuinely can't solve something another size can. The internal eval harness (Step 9) MUST include a deterministic "memory squeeze" injector for exactly this reason — but this is now a validation tool, not the product.

---

## 2. Scope

### 2.1 In scope (MVP)

- A **conversational CLI**, cross-platform (Windows, macOS, Linux), that feels like talking to a local coding assistant — not a benchmark tool. `dwarv` with no arguments drops into a chat session in the current directory (the user's repo).
- A **bundled model suite**: exactly three GGUF models from the Qwen2.5-Coder family (small/medium/large — see Step 1), downloaded once during setup, never swapped for an arbitrary model the user happens to have installed elsewhere (that keeps the family fixed, which both the policy and the internal eval depend on).
- **Hardware-based model selection with a spoken reason**: at the start of each session, Dwarv reads real CPU/RAM/GPU numbers, picks the largest bundled model that fits with a safety margin, and tells the user why in one or two sentences (Step 5).
- **Natural-language coding conversation** in the user's own repo: answer questions, explain code, propose and apply changes (Step 6).
- **Verification when there's something to verify against**: if the user's repo has a test suite (or the user points at a specific failing test), Dwarv runs it before and after a change, in an isolated copy of the working tree, and retries with the actual failure feedback instead of guessing again blindly (Step 4, Step 7).
- **Mid-session resource adaptation**: if headroom drops, Dwarv says so and steps down to a smaller bundled model (or back up when room reappears) instead of hanging, OOM-crashing, or silently getting worse (Step 7, Step 8).
- **Offline-after-setup proof**: once the three models are downloaded, Dwarv needs no network to chat, edit code, or verify.
- An **internal eval harness** (EvalPlus-based baselines A/C/C+ vs. Dwarv's policy, memory-squeeze ablations) that validates the hypothesis — run by the developer, never exposed as a user-facing command's primary purpose (Step 9).
- An **optional, read-only "what is it doing right now" panel** (Step 10A) — a stretch goal, not the MVP's core deliverable.

### 2.2 Non-goals (do NOT build)

- Training or fine-tuning any model.
- A general-purpose autonomous agent that plans and executes multi-file projects unsupervised. Dwarv proposes changes and shows diffs; the user stays in the loop.
- Letting Dwarv silently overwrite files with unverified changes. Every applied edit must be visible (a diff) and, where a test suite exists, verified before being called "done."
- A new inference engine or quantization format.
- Arbitrary local-model discovery (scanning Ollama/LM Studio/etc. for whatever the user happens to have). The bundled three-model suite is fixed on purpose — see 1.4.
- Any feature that requires a remote API at runtime.
- A polished, always-on GUI. The optional panel in Step 10A is read-only and secondary to the CLI.

### 2.3 Sandboxing strategy per platform

The verifier (Step 4) now does something riskier than before: it runs **the user's own repo and test suite**, not just self-contained snippets. Two separate safety properties matter and must not be conflated:

1. **Isolation of the process running tests** (CPU/time/network limits on the code being executed) — this is the OS-specific part, and the only one with a real cross-platform gap: Linux's `resource.setrlimit` and `unshare -n` have no Windows equivalent, and `resource` does not exist on Windows at all.
2. **Protection of the user's real working tree** — a generated patch and the test run it triggers must never be applied to the user's actual files until verification passes. Dwarv always generates and verifies against a **disposable copy of the working tree** (a temp clone, or a `git worktree` when the repo is a git repo) and only writes to the real files after the user sees the diff and verification succeeds (or explicitly asks to apply anyway).

Resolve (1) with a tiered sandbox, chosen automatically at runtime and recorded in the session's log:

1. **Docker available** (any OS, including Windows via Docker Desktop): run tests in a container with `--network none`, a memory limit, and a CPU/time limit. This is the **primary, recommended** mode and gives the strongest, most uniform guarantee across platforms.
2. **No Docker, Linux/WSL2/macOS**: fall back to `resource.setrlimit` + `unshare -n` (Linux) or `resource.setrlimit` alone (macOS, which lacks `unshare`; document the reduced network guarantee and monitor connections instead).
3. **No Docker, native Windows**: fall back to a subprocess with a wall-clock timeout and a Windows Job Object (via `pywin32` or `subprocess` + `CREATE_NEW_PROCESS_GROUP` and a job-object memory cap); there is no rlimit-equivalent address-space cap and no `unshare`, so **document this tier as reduced isolation** rather than pretending it matches tier 1/2.

`dwarv doctor` must detect and report which tier is active (Docker present? OS?), and the chat session must mention it once at startup alongside the model choice — never silently run under a weaker guarantee than the user believes they have.

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
│   ├── EXPERIMENT.md         # internal eval protocol, frozen before running
│   └── PRIOR_ART.md          # verified notes + links (primary sources only)
├── configs/
│   ├── models.yaml           # the 3 bundled Qwen2.5-Coder models (paths, sizes, measured RSS)
│   ├── budgets.yaml          # safety margins, warn fractions, squeeze schedules (internal eval only)
│   └── systems.yaml          # policy thresholds + internal eval baseline definitions
├── src/dwarv/
│   ├── __init__.py
│   ├── cli.py                 # entry point: default -> chat; doctor, setup-offline, check-offline, eval, gui
│   ├── types.py                # dataclasses (Session, Turn, Decision, State, EvalResult)
│   ├── runtime/
│   │   ├── base.py             # RuntimeAdapter protocol
│   │   └── llamacpp.py         # llama-server subprocess adapter
│   ├── resources/
│   │   ├── monitor.py          # psutil sampler (thread)
│   │   ├── budget.py           # BudgetManager, enforcement
│   │   └── squeeze.py          # deterministic memory-squeeze injector (internal eval only)
│   ├── models/
│   │   └── suite.py            # the fixed 3-model registry + hardware-based selection + explanation text
│   ├── repo/
│   │   ├── context.py          # detect repo root, VCS, existing test command
│   │   ├── worktree.py         # disposable copy / git worktree for verify-before-apply
│   │   └── patch.py            # propose/show/apply diffs against the real working tree
│   ├── verify/
│   │   ├── sandbox.py          # tiered sandboxed runner (Section 2.3, tier 1/2/3)
│   │   ├── failure.py          # failure classification + feedback formatting
│   │   └── evalplus_adapter.py # EvalPlus loader, internal eval harness only
│   ├── agent/
│   │   ├── session.py          # the chat loop: startup hardware check -> model pick+explain -> turns
│   │   └── prompts.py          # system prompt, repair-prompt builder, extract_code/extract_patch
│   ├── controller/
│   │   ├── policy.py           # rule-based policy (retry / escalate / de-escalate / stop)
│   │   └── actions.py          # Action enum + executors
│   ├── telemetry/
│   │   └── logger.py           # JSONL event logging (drives both narration and the internal eval)
│   ├── eval/                    # internal only -- never user-facing
│   │   ├── baselines.py         # Baseline A (fixed), C (retry), C+ (retry+escalate, no RAM awareness)
│   │   ├── harness.py           # runs baselines x Dwarv x tasks x seeds x squeeze profiles
│   │   └── analyze.py           # tables + plots from eval_results/
│   └── gui/                     # OPTIONAL, read-only session-transparency panel (Step 10A, stretch)
│       ├── server.py            # FastAPI app, binds 127.0.0.1 only
│       ├── data.py              # reads the live session's event log
│       ├── live.py               # tails the active session's JSONL -> SSE
│       └── static/
│           ├── index.html
│           ├── app.js
│           ├── style.css
│           ├── flow.json         # controller flow graph definition (nodes, edges)
│           └── vendor/            # vendored chart lib (NO CDN; must work offline)
├── eval_tasks/
│   └── subset_v1.json         # FROZEN EvalPlus task IDs + seed, internal eval only
├── eval_results/               # raw JSONL per internal eval run (committed)
├── benchmarks/                  # model hardware-profiling runs (Step 3), NOT the eval harness above
│   ├── README.md                 # layout, CSV columns, how to reproduce a run
│   ├── latest/                    # copy of the most recent run
│   └── history/<run_id>/          # every run ever taken, kept forever -- CSV + PNG charts + hardware.json
├── scripts/                     # thin POSIX convenience wrappers; the real, cross-platform
│   ├── setup_offline.sh         # entry points are the `dwarv setup-offline` / `dwarv check-offline`
│   ├── check_offline.sh         # CLI subcommands, which also work unwrapped on Windows
│   ├── run_demo.sh
│   └── benchmark_models.py       # produces benchmarks/ -- not a CLI subcommand, a maintainer tool
└── tests/
    ├── test_sandbox.py
    ├── test_policy.py
    ├── test_budget.py
    ├── test_failure.py
    └── test_model_selection.py
```

---

## 4. Step-by-step build

Estimated hours are for one focused developer. Steps 0 to 7 + 10 are the **24h MVP**: a real conversation, a real hardware-based model choice with a real explanation, and at least one real verified fix. Everything else is the **48h version**.

---

### Step 0: Decisions and project skeleton (about 1h)

**Tasks**
1. Create the repo layout above, `pyproject.toml` (Python 3.10+), and a `dwarv` console script. Target a plain `pip install dwarv` / `pipx install dwarv` working unmodified on Windows, macOS, and Linux.
2. Dependencies: `psutil`, `pyyaml`, `httpx` (or `requests`), `rich`, `typer` (or `argparse`), `pytest`, `evalplus`, `pandas`, `matplotlib`, `fastapi`, `uvicorn` (the last two only for the optional Step 10A panel; keep them in an optional extra `dwarv[gui]` so the core stays light). Pin **minimum compatible version ranges** (e.g. `numpy>=1.26`), not exact pins — exact old pins can lack prebuilt wheels for the installer's current Python, which forces a from-source build most users can't do.
3. Write `docs/DECISIONS.md` with these initial decisions (edit if the user overrides):
   - Product shape: a conversational CLI with a bundled, fixed 3-model suite, not a one-off task runner.
   - OS target: cross-platform (Windows, macOS, Linux) — see §2.3. WSL2 is supported but never required.
   - Runtime: `llama-server` from llama.cpp, HTTP, one model resident at a time. Use the official prebuilt binary for the host OS/arch; build from source only as a fallback.
   - Language: Python.
   - Models: Qwen2.5-Coder, three sizes (see Step 1).
   - Internal eval benchmark: EvalPlus (HumanEval+ / MBPP+) subset, 40 to 60 tasks, frozen — used only to validate the policy, never shown to the end user.
4. Implement `dwarv doctor`: prints OS, CPU count, total/available RAM, GPU presence (`nvidia-smi` if available), Python version, llama-server version, Docker presence, and the resulting **sandbox tier** (§2.3).

**Acceptance check**
- `pip install -e .` works on the developer's current OS with no compiler required; `dwarv doctor` prints hardware info and the active sandbox tier without errors.
- `pytest` runs (even with zero real tests).

**Fallback**: if a dependency fails to install, drop it from the MVP (e.g., `rich`, `typer`) and use stdlib.

---

### Step 1: Offline-ready environment and the bundled model suite (about 2h)

**Tasks**
1. Download the official prebuilt `llama-server` release for the host OS/arch; only build from source if no prebuilt release fits. Record the version/commit in `docs/DECISIONS.md`. Run `llama-server --help` and save the output to `docs/llama_server_help.txt`.
2. Pick the three bundled Qwen2.5-Coder sizes (adjust to what's actually downloadable and what the dev machine can profile):
   - `small`: ~1.5B, Q4_K_M
   - `medium`: ~7B, Q4_K_M
   - `large`: ~14B, Q4_K_M
   Verify actual file sizes and licenses on Hugging Face; do not rely on blog figures. Record them in `configs/models.yaml`. These three and only these three are what Dwarv ever runs — no user-supplied model path in the MVP.
3. Implement `dwarv setup-offline` (a CLI subcommand, cross-platform): downloads the three models into `./cache/`, sets `LLAMA_CACHE` and `HF_HOME` inside the project. `scripts/setup_offline.sh` is a thin POSIX wrapper around it.
4. Implement `dwarv check-offline`: disables network using the sandbox tier detected by `dwarv doctor`, starts `llama-server` with the small model, sends one prompt, exits 0 on success. `scripts/check_offline.sh` wraps it.

**Acceptance check**
- With the network disabled, `dwarv check-offline` passes.
- `configs/models.yaml` lists each of the three models with path, file size, quant, and context sizes tested.

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
   - Launch `llama-server` as a subprocess with `-m <gguf> -c <ctx> --port <free port> --host 127.0.0.1`, plus flags verified from `--help`. Keep flags in config, not hard-coded.
   - Wait for readiness by polling the health endpoint with timeout.
   - Use the OpenAI-compatible chat completions endpoint for generation, including multi-turn chat history (needed for Step 6's conversation, not just one-shot completion).
   - `load()` for a different model = stop server and relaunch. Time it and return the seconds.
   - `unload()` terminates the process cleanly, then kills if needed.
2. Make sure the server binds to `127.0.0.1` only.
3. Handle crashes: if the process dies (e.g., OOM), raise `RuntimeCrashed` with the exit code and last log lines, and let the agent loop (Step 6) narrate this to the user rather than crash the CLI.
4. Log load time per model/ctx combination into `eval_results/model_load_times.jsonl`.

**Acceptance check**
- A script generates a multi-turn chat completion with each of the three configured models.
- Switching small to medium to small works 3 times with no orphan processes (`pgrep llama-server` empty after unload).

**Fallback**: if subprocess management is unreliable, run `llama-server` manually outside Python and have the adapter only talk HTTP (document this).

---

### Step 3: Resource monitor and budget manager (about 2h)

**Tasks**
1. `resources/monitor.py`: background thread sampling every 0.5s (configurable): `server_rss_mb` (llama-server + children), `sys_available_mb`, `sys_total_mb`, `swap_used_mb` via `psutil`. Optional GPU memory via `nvidia-smi` if present, skip silently otherwise. Keep a ring buffer; expose `latest()`, `peak_rss_mb()`.
2. `resources/budget.py`: `BudgetManager` with `ram_limit_mb` (changeable at runtime via `set_ram_limit(new_limit, reason)`), `headroom_mb()`, `check()` returning `OK | WARN | VIOLATION`.
3. Keep process RSS, system available memory, model file size, and KV-cache estimate as separate, clearly-labeled concepts everywhere in code and logs.

**Acceptance check**
- Unit tests in `tests/test_budget.py` cover WARN/VIOLATION/limit-change behavior with a fake monitor.
- Loading each of the three models shows a plausible RSS in the monitor log; record RSS per (model, ctx) into `configs/models.yaml` — these measured values are what Step 5's model choice and its explanation are built on.
- Use `scripts/benchmark_models.py` to produce this measurement as a real, reproducible run (CSV + charts) rather than a one-off manual note — see `benchmarks/README.md`. Every run is kept under `benchmarks/history/`, never overwritten; `benchmarks/latest/` always mirrors the most recent one. Re-run it whenever the model suite, quant, or ctx size changes, and keep `configs/models.yaml` pointed at whichever run is current.

---

### Step 4: Repo context and the sandboxed verifier (about 4h)

This step generalizes "verification" from "a frozen EvalPlus problem" to "whatever the user's own repo can check," while keeping an EvalPlus path alive for the internal eval harness (Step 9).

**Tasks**
1. `repo/context.py`: given the current working directory, detect the repo root (VCS or not), an existing test command if one is discoverable (e.g. a `pytest`/`npm test`/`pyproject.toml` test config), and expose it to the agent loop. If nothing is discoverable, Dwarv can still chat and propose changes — it just can't auto-verify them, and must say so plainly rather than claim an unearned "done."
2. `repo/worktree.py`: before any patch is applied for real, create a disposable copy of the working tree (a `git worktree add` into a temp dir when the repo is git-tracked, otherwise a plain temp-dir copy) and do the trial edit + test run there.
3. `repo/patch.py`: given model output, extract a patch/diff, show it to the user, and apply it to the real working tree only after verification passes (or the user explicitly overrides).
4. `verify/sandbox.py`: implement the three tiers from §2.3 behind one `run(cmd, cwd, limits) -> SandboxResult` interface, auto-selecting the tier `dwarv doctor` detected, every result tagged with which tier ran it. All tiers: a fresh disposable worktree as cwd, stdout/stderr capture size-capped, wall-clock timeout.
5. `verify/failure.py`: classify each result into `PASS`, `SYNTAX_ERROR`, `IMPORT_ERROR`, `RUNTIME_ERROR`, `WRONG_OUTPUT`, `TIMEOUT`, `EMPTY_OR_NO_CODE`. Produce specific feedback (exception type + line, or `input=[15] expected='FizzBuzz' got='Fizz'` for a known test), truncated to a configured token budget.
6. `verify/evalplus_adapter.py`: load EvalPlus HumanEval+ / MBPP+ problems and wrap them behind the **same** `run()`/failure-classification interface, purely for the internal eval harness (Step 9). Build `eval_tasks/subset_v1.json` with a seeded random sample of 40-60 task IDs (never hand-picked; if results look bad later, write `subset_v2.json` instead of editing this one).
7. `agent/prompts.py`: `system_prompt()` for the coding-assistant persona, `repair_prompt(previous_patch, feedback)` for a retry, and `extract_patch(text)` that robustly pulls a diff/code block from model output.

**Acceptance check**
- `tests/test_sandbox.py`: infinite loop is killed by timeout; memory bomb is stopped; network access fails; a trial edit in the disposable worktree never touches the real working tree until verification passes.
- `tests/test_failure.py`: each failure class is reproduced and classified correctly.
- Manual check: in a throwaway git repo with one failing test, ask Dwarv (even with a stub model) to run the flow and confirm the real repo file is untouched until the trial in the worktree passes.

**Fallback**: if EvalPlus integration is slow, build the internal eval path later; the repo-verification path (1-5) is the one the MVP actually needs.

---

### Step 5: Hardware-based model selection, out loud (about 2h)

This is the first-class, user-facing behavior the hackathon pitch is built on — promote it out of internal logging into something the user actually hears.

**Tasks**
1. `models/suite.py`: `choose_model(hardware, models=[small, medium, large]) -> (ModelId, Explanation)`.
   - Pick the **largest** bundled model whose measured RSS (from `configs/models.yaml`, Step 3) fits `sys_available_mb - safety_margin`, where `safety_margin` is configurable (default 10% of available RAM).
   - Build a human-readable `Explanation` from the real numbers used: which model, how much RAM is free, what the margin was, and what the next size up would have needed. Never say anything the numbers don't support — if GPU memory mattered, say so; if it came down to "nothing else fit," say that too.
2. Call this once at the start of every `dwarv` chat session (Step 6) and print the explanation as the session's opening line, before the user's first message — e.g. *"Using Qwen2.5-Coder-7B this session — you have 9.2GB free, which fits 7B with headroom to spare; 14B would need more than that margin allows."*
3. Re-run the same selection function whenever the controller (Step 7) decides to step a model up or down, and narrate that decision the same way, not just log it.

**Acceptance check**
- `tests/test_model_selection.py`: table-driven — low RAM picks `small`, ample RAM picks `large`, borderline cases respect the safety margin, and the explanation text always matches the numbers that were actually used to decide.
- Manual check: run `dwarv doctor` and `dwarv` back to back on the dev machine; confirm the printed explanation's numbers match `doctor`'s.

---

### Step 6: The conversational agent loop (about 4h)

**Tasks**
1. `agent/session.py`: `dwarv` with no subcommand (or `dwarv chat`) starts a session in the current directory:
   - Run `dwarv doctor`'s checks silently, call `models/suite.choose_model`, load that model, print the opening explanation (Step 5) and the active sandbox tier (§2.3).
   - Loop: read a natural-language message from the user, build the prompt (conversation history + `repo/context.py` info when relevant), call the runtime, and either (a) answer directly for a question/explanation, or (b) if the message implies a code change, propose a patch via `repo/patch.py` and show the diff.
   - If a test command exists for the affected area, run it before and after (via `verify/sandbox.py`) in the disposable worktree, and only report success once it actually passes there; otherwise say plainly that the change is unverified.
   - On failure, hand the feedback to the controller (Step 7) instead of silently giving up or looping forever.
2. Keep conversation history bounded to the active model's context size; summarize or truncate older turns rather than crashing or silently dropping the system prompt.
3. Make every "what just happened and why" fact (model in use, sandbox tier, last decision and its reason) available via a `/status` in-chat command, so the transparency doesn't depend on the optional GUI (Step 10A).

**Acceptance check**
- A manual session: ask Dwarv a non-code question (answers directly), ask it to fix a real failing test in a scratch repo (proposes a patch, verifies in the worktree, applies, reports pass), and `/status` reflects the true current model/tier/last decision throughout.
- The session never silently writes to the real working tree without having shown the diff first.

---

### Step 7: Controller policy v1 (about 4h)

Use a **deterministic rule-based policy** — a learned policy is not justified for the MVP. This step is mostly mechanics reused from the original harness-era design; what's new is that it now fires mid-conversation and its decisions are narrated (Step 6), not just logged.

**State** (`types.py`)

```python
@dataclass
class State:
    attempt_idx: int
    attempts_left: int
    time_left_s: float
    ram_limit_mb: float
    server_rss_mb: float
    headroom_mb: float
    sys_available_mb: float
    model_id: str
    ctx_size: int
    last_failure: str | None
    failure_history: list[str]
    repeated_same_failure: int
    model_load_cost_s: dict[str, float]
    est_rss_mb: dict[tuple[str, int], float]
```

**Actions** (`controller/actions.py`)

```python
class Action(Enum):
    RETRY_WITH_FEEDBACK = 1
    RETRY_LOWER_TEMP = 2
    RETRY_HIGHER_TEMP = 3
    SHRINK_CONTEXT = 4
    SWITCH_SMALLER_MODEL = 5
    SWITCH_LARGER_MODEL = 6
    STOP_SAFELY = 7
```

**Policy v1** (`controller/policy.py`), an ordered list of rules; the first match fires, every decision is a logged + narratable `Decision(action, reason, inputs_snapshot)`:

1. **Hard stop**: no attempts left, or `time_left_s` less than the cheapest next action's estimated cost -> `STOP_SAFELY` (and say so plainly — "I've tried N times, stopping here" beats a silent hang).
2. **Budget shrank / violation risk**: `server_rss_mb > ram_limit_mb`, or `headroom_mb` below `warn_fraction * ram_limit_mb` -> step down via `SHRINK_CONTEXT` then `SWITCH_SMALLER_MODEL` if a smaller option fits; `STOP_SAFELY` if none does.
3. **Syntax/format failures** (`SYNTAX_ERROR`, `EMPTY_OR_NO_CODE`, `IMPORT_ERROR`): `RETRY_WITH_FEEDBACK`; if it repeats twice, `RETRY_LOWER_TEMP`.
4. **Logic failures** (`WRONG_OUTPUT`, `RUNTIME_ERROR`): first occurrence `RETRY_WITH_FEEDBACK`; repeated twice and a larger bundled model's measured RSS fits `ram_limit_mb - safety_margin` with load cost fitting `time_left_s`: `SWITCH_LARGER_MODEL`; otherwise `RETRY_HIGHER_TEMP`.
5. **TIMEOUT**: `RETRY_WITH_FEEDBACK` with an explicit "avoid infinite loops / improve efficiency" note.
6. **Default**: `RETRY_WITH_FEEDBACK`.

**Rules of the road**
- All thresholds live in `configs/systems.yaml`. No magic numbers in code.
- The policy is a pure function `decide(state) -> Decision`, trivially unit-testable.
- Cap total reloads per session-turn (default 2) to prevent thrashing — and if the cap is hit, say so rather than quietly stop retrying.

**Acceptance check**
- `tests/test_policy.py`: table-driven tests for each rule (low headroom triggers downgrade; larger model chosen only when RSS fits; repeated logic failure escalates; no attempts left stops; reload cap enforced).
- Manual check: trigger the memory-squeeze injector (Step 8) mid-conversation and confirm the user actually sees a narrated step-down, not just a log line.

---

### Step 8: Memory squeeze demo (about 2h)

**Tasks**
1. `resources/squeeze.py`: a deterministic scheduler that changes `ram_limit_mb` during a session (at a fixed time, or after N turns) — used both for the live demo and for the internal eval's ablations (Step 9). Implement the budget-cut mode as primary; a competing-process mode (a background process holding N MB) is optional and demo-only, not used for eval numbers.
2. Derive the actual MB thresholds from the **measured** RSS table in `configs/models.yaml`, not guesses.
3. Wire it into the live chat session so the demo can say, truthfully: "watch what happens when I squeeze available RAM mid-conversation."

**Acceptance check**
- Triggering a squeeze mid-session visibly and correctly causes a narrated step-down (Step 5/7), with no budget violation, and the conversation keeps working afterward instead of crashing.

---

### Step 9: Internal eval harness (about 3h, 48h version)

This validates the hypothesis (Section 1.2). It is a developer-facing tool (`dwarv eval` or a separate script), never the product's user-facing surface.

**Protocol** (write into `docs/EXPERIMENT.md`, freeze before the final run)
- Same machine, same background load, same llama.cpp build, same three models, same frozen EvalPlus subset, same budget profiles, same sampling parameters.
- Compare: Baseline A (fixed model for the whole run), Baseline C (verification-guided retry, no resource awareness), Baseline C+ (retry + escalation, no resource awareness), and Dwarv's actual policy.
- Equalize total resources across all four: same `max_attempts`, same `time_limit_s`, same three models available to C+ and Dwarv.
- Budget profiles: `static_tight`, `static_loose`, `squeeze_mid`. Seeds: at least 3 per (system, profile, task).
- Primary metric: verified pass rate under the hidden EvalPlus tests, at equal budget. Secondary: peak RSS, budget violations, latency, attempts used, model-switch overhead.
- **Decision criteria, written in advance**: supported only if Dwarv beats C+ on verified pass rate under `squeeze_mid` by a margin larger than seed-to-seed spread, **and** has fewer budget violations. If Dwarv only beats A or C but not C+, report that the gain is from escalation, not resource awareness.

**Tasks**
1. `eval/baselines.py`, `eval/harness.py`: nested loop over systems x profiles x tasks x seeds, crash-safe JSONL to `eval_results/`, resumable by key.
2. `eval/analyze.py`: summary table (CSV + Markdown), pass-rate bar chart with error bars, peak-RSS-vs-pass scatter, per-task diff table, bootstrap CIs.
3. Never overwrite raw results; each run gets a `run_id` directory with the config used and the git commit hash.

**Acceptance check**
- A small dry run (5 tasks, 1 seed) completes end-to-end and produces all tables/plots.

**If the hypothesis is not supported**: report that plainly in `docs/EXPERIMENT.md`. A useful conclusion either way: when resource-aware control does or doesn't help, and why.

---

### Step 10: Demo and CLI polish (about 3h)

**Tasks**
1. `scripts/run_demo.sh` / the live walkthrough:
   1. Start `dwarv` in a scratch repo with network on; show the opening hardware-based model choice and its explanation.
   2. Ask it to fix a real failing test; show the diff, the sandboxed verification run, and the pass.
   3. Trigger the memory squeeze (Step 8) mid-conversation; show the narrated step-down and that the conversation keeps working.
   4. Turn the network off (or run inside a no-network container) and continue the same conversation, to show offline operation.
   5. (48h) Print the internal eval's summary table from `eval_results/` as evidence behind the hypothesis, clearly labeled as the internal validation, not something the end user sees in normal use.
2. Choose the demo repo/bug **before** the demo, and also show one case where Dwarv doesn't help (honesty strengthens credibility) if time allows.

**Acceptance check**
- The demo runs end-to-end from a clean shell in under ~10 minutes and never crashes the machine.

---

### Step 10A: Optional session-transparency panel (about 4h, stretch goal)

**Purpose**: a small, read-only local web view of the live session's state — current model, why, sandbox tier, recent decisions, resource chart — for people who want to watch rather than read terminal output. This is a nice-to-have, not the deliverable; stop here if time is short.

**Hard constraints**
- Read-only, localhost-only (`127.0.0.1`), no CDN/web fonts/external requests, vendor any chart lib. Model output and repo content are untrusted — render with `textContent`, never `innerHTML`.
- No fake data: if there's no active session, show "No active session."

**Tasks**
1. `gui/server.py` (FastAPI): `GET /api/session` (current model, tier, decisions), `GET /api/live/stream` (SSE tailing the active session's JSONL).
2. `gui/static/`: one page — current model + explanation, a live resource chart (RSS vs. limit, with squeeze/decision markers), and a decision list with the `inputs_snapshot` behind each one.
3. `gui/static/flow.json`: node ids matching the `flow_node` values emitted in the event log; add a unit test that every emitted `flow_node` exists in `flow.json` and vice versa.
4. `dwarv gui` starts the server and prints the URL; `dwarv` (the main chat command) can optionally open it alongside the terminal session.

**Acceptance check**
- With network disabled, the panel loads fully with zero external requests; XSS check (model output containing `<script>`) renders as plain text.

**Fallback**: skip entirely; `/status` in the chat (Step 6) already covers the transparency goal without a server.

---

### Step 11: Write-up and cleanup (about 2h)

**Tasks**
1. `README.md`: what Dwarv is (a local coding assistant with a bundled, hardware-aware model suite), install, offline setup, quickstart (`dwarv setup-offline` then `dwarv`), limitations.
2. `docs/PRIOR_ART.md`: for each related project, record name, URL, date/latest activity, purpose, approach, overlap, "not documented" features (kept separate from verified limitations). Read primary sources; do not copy summaries blindly.
3. `docs/EXPERIMENT.md`: final internal-eval protocol + results + honest interpretation.
4. A **claims table**: each claim in the README or pitch maps to a file in `eval_results/` or a primary source link. Remove any claim with no backing.
5. Pitch outline (5 slides): problem, the "feels like Claude Code but local and hardware-aware" demo, the internal evidence for why resource-awareness matters, limits.

---

## 5. Cross-cutting requirements

### 5.1 Privacy and offline behavior
- The chat/edit/verify workflow makes **no** network calls after `dwarv setup-offline`. Add a startup assertion/log line stating whether any network access occurred.
- No telemetry leaves the device. All logs are local JSONL.
- Model downloads happen only in `dwarv setup-offline` (or its `scripts/setup_offline.sh` wrapper), never implicitly at runtime.
- `llama-server` binds to localhost only.

### 5.2 Security and sandboxing
- Generated code and patches are untrusted. Always verify in a disposable worktree (Step 4) before touching the user's real files, and always show the diff.
- Repos may contain hostile content (e.g. a malicious test file). Never execute anything from a repo outside the sandbox tiers (§2.3).
- Do not store secrets in logs; truncate captured outputs.

### 5.3 Observability
Log every event as one JSON object per line with at least: `ts`, `session_id`, `event`, plus event-specific fields: `run_started`, `model_loaded`, `monitor_sample` (downsampled), `turn_started`, `patch_proposed`, `verified`, `decision`, `budget_change`, `budget_warn`, `budget_violation`, `model_unloaded`, `run_finished`. Every event also carries `seq` (monotonic per session), `rel_t_s`, and `flow_node` (matching `gui/static/flow.json` if Step 10A is built). This log is what both `/status` and the internal eval are built on — there is no second source of truth.

### 5.4 Reliability
- Every external call has a timeout.
- No orphaned `llama-server` processes after any exit path (`atexit` + signal handlers + process groups).
- A crashed/killed session never leaves the user's real working tree mid-edit — uncommitted trial changes live only in the disposable worktree until verified.

### 5.5 Code quality
- Type hints and docstrings on public functions.
- `ruff` clean; `pytest` passes before each commit.
- Small commits, one per step or sub-step.

---

## 6. Risk register (keep updated in `docs/PROGRESS.md`)

| Risk | Impact | Mitigation |
|---|---|---|
| A generated patch corrupts the user's real repo | Trust-destroying | Always verify in a disposable worktree first; never write to real files until verified or explicitly overridden; always show the diff |
| No test suite exists for the user's change | Can't verify, risk of false "done" | If nothing's discoverable, say so plainly instead of claiming success |
| Existing tools already cover most features | Weak novelty | Frame as "bundled, hardware-aware, transparent model suite" + the internal empirical study, not algorithmic novelty |
| Adaptive decisions make results worse | Negative internal-eval result | Pre-register decision criteria; report honestly; keep the C+ ablation |
| Gains come from "more compute" not adaptivity | Misleading claim | Equal caps across baselines in Step 9; report attempts and time |
| Model reload cost dominates | Policy looks bad | Measure load times; include them in the time budget; cap reloads |
| RSS measurement is noisy or misleading | Wrong model choice or explanation | Use measured RSS per (model, ctx); document mmap/page-cache effects; add safety margin |
| Sandbox escape or hostile generated code / repo content | Security | Docker (`--network none`) as the primary tier on any OS; rlimits + `unshare -n` on Linux/macOS without Docker; Windows Job Objects on native Windows without Docker, explicitly logged as reduced isolation |
| llama.cpp flags change | Build breaks | Verify against `--help`; keep flags in config; pin the build |
| Time overrun | No demo | Follow the 24h cut; use listed fallbacks |
| Optional GUI becomes a time sink | Core conversation/verification unfinished | Build it only after Step 7 works; `/status` in-chat already covers transparency; stop after Step 10 if time is short |
| GUI (if built) renders untrusted model output unsafely | XSS in demo machine | `textContent` only, localhost bind, read-only API |

---

## 7. Fallback plans

- **Model switching flaky**: restart `llama-server` per switch and count the cost in the time budget.
- **GPU profiling hard**: CPU RAM only; mention GPU as future work.
- **Controller too ambitious**: ship only the rule "when headroom is low, step down a model; otherwise retry with feedback."
- **No discoverable test command in the demo repo**: pick a demo repo with `pytest` in advance; don't rely on live discovery working perfectly for the first demo.
- **EvalPlus integration slow**: the live product path (Step 4, items 1-5) doesn't need it; build the internal eval (Step 9) later or skip it for a 24h cut.
- **No way to disable network per-process (native Windows, no Docker)**: disable the host's network adapter / airplane mode for the whole `check-offline` run instead of per-process isolation; document the limitation.

---

## 8. 24-hour vs 48-hour cut

**24h (priority order)**
1. Steps 0-3 (skeleton, offline models, runtime, resource monitor)
2. Step 4 items 1-5 (repo context, worktree, patch, sandboxed verify) — EvalPlus adapter (item 6) can wait
3. Step 5 (hardware-based model choice + explanation) — this is the headline feature, do not cut it
4. Step 6 (the chat loop itself)
5. Step 7 (policy v1, at least the headroom-downgrade rule)
6. Step 10 demo items 1-4 (model choice, a real verified fix, a squeeze, offline continuation) and a short README

**48h additions**
- Step 8 (polished squeeze demo), Step 9 (internal eval harness + baselines + decision criteria), full EvalPlus adapter
- Step 10A (optional transparency panel)
- Policy ablations (feedback-off, resource-off) for the internal eval
- Polished docs and claims table

---

## 9. Final deliverables checklist

- [ ] Working `dwarv` CLI, cross-platform (default chat session, `doctor`, `setup-offline`, `check-offline`, optional `eval`, optional `gui`)
- [ ] Bundled 3-model Qwen2.5-Coder suite with measured RSS and hardware-based selection
- [ ] A real, spoken explanation of model choice at session start and at every step-up/step-down
- [ ] Repo-aware, sandboxed, verify-before-apply patch flow that never corrupts the real working tree
- [ ] Resource-aware policy (retry / escalate / de-escalate / stop) demonstrated live under a memory squeeze
- [ ] Offline proof (`dwarv check-offline`) passing, and a live demo continuing offline mid-conversation
- [ ] (48h) Internal eval harness with frozen protocol, baselines A/C/C+, and an honest supported/not-supported verdict
- [ ] `README.md`, `docs/PRIOR_ART.md`, `docs/EXPERIMENT.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`
- [ ] Claims table linking every claim to evidence
- [ ] Final go/no-go note in `docs/PROGRESS.md`: does the internal eval data support the hypothesis, partially, or not at all?

---

## 10. First message to the user when you start

Before coding, reply with:
1. The machine you detected (`dwarv doctor` output once Step 0 is done).
2. The three models you plan to bundle and their verified file sizes.
3. Any decision from this file you want the user to override.
Then proceed with Step 0 without waiting, unless a decision is blocking.

---

## 11. Future work: deeper resource-awareness (post-Step 11)

Steps 0-11 above are complete and shipped (see `docs/PROGRESS.md`). This
section is the next phase: making Dwarv's existing hardware-aware model
selection genuinely adaptive, rather than a fixed menu of 3 models at one
quant level each. Written up per-session as the work is scoped; treat each
numbered item below as its own future step, same discipline as Section 4
(real measurements, live-tested, no fabricated numbers).

### 11.1 Local re-quantization (research thread, not the production path -- see 11.4)

**The core idea.** Today each bundled model is downloaded at exactly one
fixed quant (Q4_K_M). If that doesn't fit a given machine, the only lever
is switching to a *smaller model* (Step 7's `SWITCH_SMALLER_MODEL`). The
missing lever: shrink the *same* model further, locally, using the
`llama-quantize` tool that already ships inside the llama.cpp release
archive Dwarv already downloads (verified: `llama-b11516-bin-win-cpu-x64.zip`
contains `llama-quantize.exe` + `llama-quantize-impl.dll` alongside
`llama-server.exe` -- zero new download needed, it is already on disk after
every `setup-offline` run today, just never invoked).

This is deliberately **not** "download a different pre-made quant file
from Hugging Face" -- that only offers whatever discrete levels a publisher
happened to upload, and costs fresh bandwidth per level. Local
re-quantization is a real on-device *process*: it runs offline (consistent
with section 5.1's no-network-after-setup-offline rule), works from
whatever is already cached, and can target any level llama-quantize
supports, not just the ones some third party chose to publish.

**Mechanism** (`llama-quantize --allow-requantize <in.gguf> <out.gguf>
<LEVEL>`, confirmed real CLI per the tool's own README): takes an
already-quantized GGUF as input instead of requiring the original F16/F32
checkpoint. Documented tradeoff, to be stated honestly wherever this result
is surfaced (narration text, `docs/DECISIONS.md`): requantizing from an
already-quantized source is lossier than quantizing fresh from full
precision -- real quality cost, not just a disk-size change.

**Build it as:**
- `models/requantize.py`: `llama_quantize_path(cache_dir)` (locates the
  binary next to `llama-server`), `requantize(input_path, output_path,
  target_level, timeout_s)` -- a real subprocess wrapper, not a sandboxed
  one (this runs Dwarv's own trusted tool against Dwarv's own cached file,
  not untrusted repo/model content), returning real measured wall time and
  input/output file sizes.
- `models/suite.py`: when the hardware-based selector finds that a tier's
  cached baseline doesn't fit, but a derived lower-quant version of that
  *same* cached file would, it requantizes once and caches the result
  (`<filename>.<level>.gguf`) rather than requiring a fresh download or
  silently falling back to a smaller model.
- Validate cheaply first: prove the pipeline end-to-end on the smallest
  real tier (0.5B, ~469MB download) before spending bandwidth/time proving
  it on anything larger.

### 11.2 Worked example: getting the 32B tier onto a tight machine

Real numbers, not estimates (`huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct-GGUF`,
checked via the HF API's `blobs=true` listing):

| Quant | Size | Fits 8GB VRAM + 10.8GB RAM (~18.8GB combined)? |
|---|---|---|
| Q8_0 | 32.4 GB | No |
| Q6_K | 25.0 GB | No |
| Q5_K_M | 21.7 GB | No |
| Q4_K_M (today's single fixed level) | 18.5 GB | No safe headroom |
| Q4_0 | 17.4 GB | Barely, no safe headroom |
| **Q3_K_M** | **14.8 GB** | **Yes -- ~4GB left for KV cache/OS** |
| Q2_K | 11.5 GB | Yes, comfortably -- more quality loss |

Q3_K_M is the real target for this machine specifically: the highest
quality level that leaves genuine headroom once split across GPU VRAM and
system RAM via `-ngl`. Section 11.1's local requantization is how Dwarv
gets there generally (works for any tier, any machine) rather than special-
casing 32B.

### 11.3 Supporting levers (lower priority than 11.1, same phase)

- **Opportunistic GPU offload** (`-ngl`): detect a discrete GPU with real
  VRAM (NVIDIA via `nvidia-smi` first -- already partially wired for
  `dwarv doctor`'s display-only `_gpu_summary()`; AMD/Intel detection is a
  real, documented gap, not faked). No GPU found -> behaves exactly as
  today, pure CPU. Effective capacity becomes `sys_available_mb +
  vram_available_mb`, with `-ngl` computed from how many layers fit in
  VRAM specifically. Confirmed real CUDA/Vulkan/ROCm/SYCL builds exist for
  the pinned release tag (`b11516`), not just the CPU build Dwarv
  downloads today.
- **`setup-offline` downloads the matching llama.cpp build** (CUDA/Vulkan
  vs CPU) based on what hardware detection finds, so a GPU-less machine
  never wastes bandwidth on a build it can't use.
- **Free wins regardless of GPU**: `--flash-attn` and `--cache-type-k/v`
  (KV-cache quantization) -- real llama.cpp flags, lower RAM at any tier,
  no accuracy cost for flash-attention and a small, well-understood one for
  KV-cache quantization.
- **Structured pruning** (genuinely smaller model via removing whole
  layers/heads, not zeroing individual weights): kept in the plan, lower
  priority than the above. Verified why *unstructured* pruning
  (SparseGPT/Wanda-style) is **not** worth building: GGML has no sparse
  tensor support, so a zeroed weight costs exactly as much compute/memory
  as a nonzero one (confirmed directly by llama.cpp's maintainer and an
  independent contributor's empirical test, `ggml-org/llama.cpp` discussion
  #521) -- only *structured* pruning (fewer actual parameters) produces a
  real, measurable benefit under llama.cpp's existing dense kernels.

### 11.4 Our own compression method: real results, and why we pivoted

Built a from-scratch activation-aware quantization method (not an imported
library) to test whether it preserves quality better than naive
round-to-nearest (RTN) at the same bit-width: calibrate per-input-channel
activation magnitude on real code samples, scale weight channels before
quantizing so rounding error falls more on channels the model barely uses,
search a small alpha grid per layer (always including alpha=0, i.e. "fall
back to plain RTN," so the method can never do worse than the baseline for
a given layer) using calibration-only data. Measured with real perplexity
on held-out code (`scripts/compression/`), not a proxy claimed without
evidence.

**Qwen2.5-Coder-0.5B-Instruct, 3-bit, `down_proj` layers:**
| | Perplexity | vs fp32 |
|---|---|---|
| Baseline (fp32) | 1.6074 | -- |
| Naive RTN | 2.1932 | +36.4% |
| Ours (activation-aware) | 2.0569 | +28.0% |

A real, validated win at this scale -- 16 of 24 layers chose nonzero
alpha (scaling helped); the other 8 safely fell back to alpha=0.

**Qwen2.5-Coder-1.5B-Instruct, same method, same bit-width:**
| | Perplexity | vs fp32 |
|---|---|---|
| Baseline (fp32) | 1.6785 | -- |
| Naive RTN | 2.1036 | +25.3% |
| Ours (activation-aware) | 2.2731 | +35.4% |

**Did not hold.** Naive RTN degraded *less* at 1.5B than at 0.5B, while
ours degraded *more* -- the opposite of the smaller-scale result. We did
not re-tune the method to force a favorable number at this scale -- that
would be p-hacking the result, not proving it. Honest root-cause
hypothesis: the alpha-selection criterion (weighted mean-squared
weight-reconstruction error, using activation magnitude as a static
per-channel weight) is a *proxy* for what actually matters -- real
downstream output error -- and it's an imperfect one, apparently more so
at this scale. A more faithful version would measure real per-layer output
reconstruction error against actual calibration inputs rather than a
static weighted-MSE approximation; not yet built.

**Decision**: do not make this method the production compression path.
llama.cpp's own I-quant family (`IQ2_XS`, `IQ1_S`, etc.) already does
importance-matrix-guided low-bit quantization, is community-validated
across many models and scales, and ships in the same release archive we
already download -- strictly lower-risk than shipping an unproven,
scale-sensitive method we built ourselves. Section 11.1's local
requantization work stays as an open research thread (the "fix the proxy
metric, re-test across scales" path above is the real next step if
revisited), not something the shipped product depends on.

### 11.5 The pivot: three independent, stackable levers instead of forcing weight compression

Reframing the goal from "compress the weights harder" to "make the model
do less work, however it's sized" -- three real, separately-buildable
levers, not one combined technique:

1. **I-quants for weight footprint** (11.6) -- the actual production
   answer to "fit a bigger model in less RAM." Mature, pre-built,
   zero new engineering risk.
2. **Graph-based context precision** (11.7) -- shrinks what goes *into*
   the model each turn, inspired by Graphify's code-knowledge-graph
   approach (precise, cited retrieval instead of embedding-based fuzzy
   search). Directly fixes an already-documented gap: `snapshot_repo_files()`
   today just stuffs whatever fits a character budget into context (flagged
   in `docs/DECISIONS.md` as an MVP-scale simplification that "won't scale
   to a large real codebase").
3. **Grammar-constrained structured output** (11.8) -- shrinks what the
   model has to *generate*. Not an integration of TypeSafe's Jev model
   (that's a separately-trained, non-autoregressive architecture --
   verified it is not something we can layer onto Qwen2.5-Coder without
   either using their hosted model directly, unconfirmed whether that's
   even locally-runnable, or training our own from scratch, which is a
   research project of its own). What *does* carry over from that idea:
   forcing structured output via constrained decoding instead of free-form
   generation-then-parsing -- confirmed real and already in our pinned
   llama.cpp build (`--grammar`/`--grammar-file`/`--json-schema`), no new
   download.

Honest scope note: (2) and (3) reduce tokens processed/generated per turn
-- real speed and KV-cache savings, but KV cache is a few hundred MB to
low-GB at normal context sizes versus 9-18GB of model weights at the
large/xl tiers. These levers are genuinely additive with (1), not a
substitute for it -- "the model does less work" does not by itself shrink
what the weights themselves cost to hold in memory.

### 11.6 I-quants as the production weight-compression path

Switch the plan's answer to "how do we shrink a tier's footprint" from
11.1's local requantization to llama.cpp's own I-quant types
(`IQ1_S`/`IQ1_M`/`IQ2_XXS`/`IQ2_XS`/`IQ2_S`/`IQ3_XXS`/etc.), the same way
11.2's Q3_K_M recommendation already worked -- offer the quant level that
actually fits a given machine's hardware, sourced from what Qwen/community
quantizers have already published (or produced via `llama-quantize
--imatrix` locally from a cached baseline using a real code calibration
set, same `llama-quantize`/`llama-imatrix` binaries already confirmed
present in the bundled archive -- the difference from 11.1 is using
llama.cpp's own mature I-quant schemes rather than a hand-rolled method).
Not yet built: extending `configs/models.yaml` and `models/suite.py`'s
selection logic to this quant family; verifying which I-quant levels are
actually published for each bundled tier.

### 11.7 Graph-based context precision

Build a lightweight code knowledge graph (symbols, references, call/import
relationships -- function/class definitions and their real file:line
locations, not embeddings) that `agent/session.py` queries for only the
specific context a given turn needs, replacing `snapshot_repo_files()`'s
whole-repo character-budget dump. Not yet built: graph construction
(likely via Python's `ast` module for a first pass, no new heavy
dependency), the query interface the agent loop calls per turn, and
honest benchmarking of real token-count reduction and verified-fix-rate
versus the current snapshot approach on a real multi-file repo (not just
the Step 10 demo's two-file toy case).

### 11.8 Grammar-constrained structured patch generation

Replace free-form generation + `extract_patch()`'s regex-based fence
parsing with a GBNF grammar (or `--json-schema`) passed to `llama-server`
that constrains the model's output to the exact patch structure Dwarv
expects. Confirmed real and already available (`llama-server --help`
lists `--grammar`/`--grammar-file`/`-j, --json-schema`/`-jf,
--json-schema-file` in the pinned build). Expected real benefits: fewer
generated tokens (no prose preamble/postamble around the code -- direct
speed win, token count drives both time and compute), and "model forgot
to fence the code" becomes structurally impossible rather than a failure
mode `extract_patch()` has to detect after the fact. Not yet built: the
actual grammar/schema definition for Dwarv's patch format, wiring it
through `runtime/llamacpp.py`'s generate() call, and a real before/after
comparison (token count, wall time, parse-failure rate) on live model
output -- this is the next concrete implementation step.
