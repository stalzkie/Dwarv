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
