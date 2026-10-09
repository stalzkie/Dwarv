# Progress log

One entry per step (or sub-step): what was done, what was measured, what is next. Append; do not rewrite history.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Existing tools already cover most features | Weak novelty | Frame as an empirical harness/study; cite closest work honestly |
| Adaptive decisions make results worse | Negative result | Pre-register decision criteria; report honestly; keep C+ ablation |
| Gains come from "more compute" not adaptivity | Misleading claim | Equal caps across systems; C+ baseline; report attempts and time |
| Model reload cost dominates | Controller looks bad | Measure load times; include them in the time budget; cap reloads |
| RSS measurement is noisy or misleading | Wrong budget decisions | Use measured RSS per (model, ctx); document mmap/page-cache effects; add safety margin |
| Small test subset gives high variance | Unreliable conclusions | 40 to 60 tasks, 3+ seeds, bootstrap CIs; no hand-picking |
| Sandbox escape or hostile generated code | Security | Subprocess + rlimits + no network; Docker if available |
| llama.cpp flags change | Build breaks | Verify against `--help`; keep flags in config; pin the build |
| Time overrun | No demo | Follow the 24h cut; use listed fallbacks |
| GUI becomes a time sink | Core experiment unfinished | Build GUI only after Step 9 produces results; replay-first; static `report.html` fallback |
| GUI renders untrusted model output unsafely | XSS in demo machine | `textContent` only, localhost bind, read-only API, XSS test in acceptance |
| GUI shows data that does not match the logs | Misleading demo | All views derived from `results/` via shared `analyze.py` functions; no placeholder data |

## Log

### Scaffolding (2026-10-09)
- Created repo layout per `DWARV_PLAN.md` section 3: `src/dwarv` packages, `configs/`, `docs/`, `tasks/`, `results/`, `scripts/`, `tests/`.
- Added `pyproject.toml` with the `dwarv` console script and the pinned dependency set (GUI deps in the optional `dwarv[gui]` extra).
- Implemented the `dwarv doctor` stub (OS/CPU/RAM/GPU/Python/llama-server detection) to satisfy the Step 0 acceptance check.
- Added the interfaces given explicitly in the plan as real code: `RuntimeAdapter`/`GenParams`/`GenResult` (`runtime/base.py`), `Result`/`System` (`systems/base.py`), `State` (`types.py`), `Action` (`controller/actions.py`), and the flow graph skeleton (`gui/static/flow.json`).
- Everything else (Steps 1-11 business logic) is a `NotImplementedError` stub matching the file layout, to be filled in step by step.
- Next: Step 0 acceptance check (`pip install -e .`, `dwarv doctor`, `pytest`), then Step 1 (offline-ready environment).

### Cross-platform sandboxing + initial commit/CI (2026-10-09)
- Added the tiered sandbox design (Docker / rlimit+unshare / Windows Job Objects) to the plan and scaffold; `dwarv doctor` now detects and reports Docker presence and the active sandbox tier.
- Loosened `pyproject.toml` pins to `>=` ranges; set explicit `ruff` lint rules (resolved defaults were pulling in the full rule catalog).
- Pushed the initial commit to `github.com/stalzkie/Dwarv` (branch `main`); added `.github/workflows/ci.yml` (lint, test matrix across ubuntu/windows/macos x Python 3.10-3.12, build) — first CI run passed in full.
- Set local (repo-only, not global) git `user.name`/`user.email` from the authenticated `gh` account, since none was configured.

### Product pivot: conversational assistant, not a benchmark harness (2026-10-09)
- Rewrote `DWARV_PLAN.md` end to end: the product is now a chat-first CLI (`dwarv` with no args) backed by a fixed, bundled 3-model Qwen2.5-Coder suite, with hardware-based model selection narrated to the user (not just logged), repo-aware sandboxed verification (disposable worktree, verify-before-apply), and the old EvalPlus benchmark demoted to an internal eval harness. See `docs/DECISIONS.md` for the full rationale.
- **Not yet done**: the `src/dwarv` code scaffold still reflects the old benchmark-first layout (`systems/`, `bench/`, `tasks/`, `results/`, `run`/`bench` CLI subcommands). Restructuring the code to match the new plan (`agent/`, `models/`, `repo/`, `eval/`, chat-first `cli.py`) is a separate follow-up, not yet started.
- **Update**: the restructure above is done (new packages created, `systems/`/old `tasks/` removed, `bench/`→`eval/`, top-level `tasks/`→`eval_tasks/`, `results/`→`eval_results/`, `cli.py` chat-first). Committed and pushed; CI green.

### Step 3 (resource monitor + budget manager) and Step 7 (policy v1) implemented (2026-10-09)
- `resources/budget.py`: real `BudgetManager` (`set_ram_limit`, `headroom_mb`, `remaining_time_s`, `attempts_left`, `check() -> OK|WARN|VIOLATION`, recorded `violations`/`changes`). `tests/test_budget.py` has 3 real passing tests (was skip stubs).
- `resources/monitor.py`: real `ResourceMonitor` (background thread, `pid_fn` injection so it survives model reloads, ring buffer, `latest()`/`peak_rss_mb()`). Not yet unit-tested directly (budget tests use fake `rss_fn`/`sys_available_fn` injection instead, per the plan's "fake monitor" acceptance check) — needs a real `llama-server` process (Step 2) to exercise end-to-end.
- `controller/policy.py`: real `decide(state) -> Decision` implementing all 6 ordered rules (hard stop, budget-shrink step-down, syntax retry/lower-temp, logic retry/escalate, timeout, default), plus the reload cap. Fixed `MODEL_ORDER = [small, medium, large]` matching the bundled suite. `tests/test_policy.py` has 5 real passing tests (was skip stubs).
- `verify/failure.py`: real `classify()` covering all 7 failure classes + feedback truncation. `tests/test_failure.py` rewritten from one parametrized stub into 8 explicit passing tests (one per class + truncation).
- Validated in a fresh venv: 16 new tests pass, `ruff check`/`format --check` clean, no regressions to the 8 still-skipped tests (model selection, sandbox — both still need Step 1/2/4 pieces that aren't built yet).
- Next: Step 2 (runtime adapter — needs an actual `llama-server` binary) or Step 4's sandbox tiers / repo context (needs Docker/worktree work), or Step 1's research into real Qwen2.5-Coder GGUF filenames/sizes on Hugging Face.

### Step 1 (real models + binary) and Step 2 (runtime adapter) implemented and live-verified (2026-10-09)
- Researched and verified real values (see `docs/DECISIONS.md`): llama.cpp build `b11516`, the official `Qwen/Qwen2.5-Coder-{1.5B,7B,14B}-Instruct-GGUF` repos (Q4_K_M, apache-2.0). Recorded in `configs/models.yaml` with `llama_cpp.assets` keyed by `(platform.system(), platform.machine())`.
- `runtime/llamacpp.py`: real `LlamaCppRuntime` (subprocess launch, health-check polling, clean `unload()`, reload). `runtime/base.py`'s `generate()` evolved from `prompt: str` to `messages: list[dict]` to actually support Step 6's multi-turn chat (documented in `docs/DECISIONS.md`).
- `models/suite.py`: added `load_models_config()`/`resolve_model_paths()` (real YAML I/O); `choose_model()` itself stays a Step 5 stub.
- `cli.py`: real `setup-offline` (downloads+extracts the llama-server archive and the 3 models, idempotent by file existence/size, configurable cache dir via `DWARV_CACHE_DIR`) and real `check-offline` (loads the small model, generates once, asserts zero non-loopback connections via `psutil`).
- **Live-verified on this machine** (not just unit tests): downloaded the real llama-server Windows CPU binary and all 3 real GGUF models to `D:\dwarv-cache` (disk-space override, see decisions doc); ran `dwarv doctor` (detects the real binary + cache dir), `dwarv check-offline` (passed: generated text, zero non-local connections), and a manual load→generate→unload→reload cycle against the real 1.5B model (load 1.89-2.19s, correct code response, no orphaned process afterward, confirmed via `Get-Process`). Measured real RSS for the small model at ctx 4096: ~1727MB peak.
- Added `tests/test_setup_offline.py` (3 passing tests, fully mocked — no real network/downloads in CI) covering: downloads when missing, skips when already present with matching size, errors cleanly on an unconfigured platform.
- Validated in a fresh venv: 19 tests pass (3 new), `ruff check`/`format --check` clean.
- Linux/macOS llama-server assets are recorded in `configs/models.yaml` but unverified (no binary downloaded/tested on those OSes yet).

### Medium/large models downloaded and measured (2026-10-09, same session)
- Both finished downloading and are byte-verified: 7B = 4,683,073,536 bytes; 14B = 8,988,110,272 bytes (both match the Hugging Face file listing exactly).
- Live-measured RSS/load time at ctx 4096 for all three, now in `configs/models.yaml`:
  - small: load 1.89s, peak RSS 1727.5MB
  - medium: load 34.14s, peak RSS 6275.9MB
  - large: load 68.25s, peak RSS 10427.7MB
- The 14B measurement ran with only ~6.45GB RAM free against a ~9GB model (confirmed with the user first, given the real risk of disk thrashing) — it worked without crashing (llama.cpp mmaps the GGUF, so pages are file-backed and evictable rather than needing pagefile swap), available RAM dropped to ~296MB at peak, and fully recovered to 10.4GB after a clean `unload()` with no orphaned process. This is itself a real demonstration of the exact resource-pressure scenario Dwarv's policy (Step 7) exists to manage.
- Step 1 is now fully live-verified end to end for all three bundled models on this machine. Next: Step 5 (`models/suite.choose_model`) can now be built against real `configs/models.yaml` data instead of placeholders.
