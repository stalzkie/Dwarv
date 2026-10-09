# Decisions

Every non-obvious choice and the reason behind it. Edit entries if the user overrides them; do not delete history, append a new entry noting the change instead.

## Initial decisions (Step 0)

- **OS target**: cross-platform (Windows, macOS, Linux) via a plain `pip install dwarv`. WSL2 is supported but never required. See §2.3 of `DWARV_PLAN.md` for the tiered sandbox this implies.
- **Runtime**: `llama-server` from llama.cpp, over HTTP, one model resident at a time in the MVP. Use the official prebuilt binary release for the host OS/arch; build from source only as a fallback.
- **Language**: Python (3.10+).
- **Internal eval benchmark**: EvalPlus (HumanEval+ / MBPP+) subset, 40 to 60 tasks, frozen (`eval_tasks/subset_v1.json`). Used only to validate the policy; never user-facing — see the 2026-10-09 product-pivot entry below.
- **Models**: Qwen2.5-Coder family, exactly three bundled sizes (see Step 1 of `DWARV_PLAN.md`), never an arbitrary user-supplied model.
- **Package layout**: `src/dwarv`, console script `dwarv`, GUI deps kept in the optional `dwarv[gui]` extra so the core install stays light.
- **Dependency pinning**: minimum-version ranges (`>=`) in `pyproject.toml`, not exact pins, so installs resolve to a wheel that exists for the user's current Python/OS instead of forcing a from-source build. Residual risk: a transitive dependency (e.g. `evalplus`'s own `numpy` bound) can still force a source build on a brand-new Python release; this is upstream and outside `dwarv`'s control — if hit, document it in `docs/PROGRESS.md` rather than fighting it with aggressive overrides.

## 2026-10-09: cross-platform sandboxing, replacing a hard WSL2 requirement

The original plan required Linux/WSL2 because the sandboxed verifier (Step 4) leaned on two Linux-only primitives: `resource.setrlimit` and `unshare -n`. That's real, but it's the *only* OS-specific part of the whole project — `llama-server`, the CLI, and the dashboard are not OS-specific. Decision: keep Linux/macOS's rlimit+`unshare` path, add Docker (`--network none`) as the primary, cross-platform tier, and add a Windows Job Object fallback (reduced isolation, explicitly logged) for native Windows with no Docker. `dwarv doctor` detects and reports which tier is active. Full detail in `DWARV_PLAN.md` §2.3 and Step 4.

## 2026-10-09: product pivot — conversational "local Claude Code," not a benchmark harness

Original framing was a benchmark/research harness first (`dwarv run --task <evalplus-id>`, `dwarv bench`), with the resource-aware controller as the thing being measured. Reframed as a product: `dwarv` with no arguments opens a chat session in the user's own repo, backed by a **fixed, bundled suite of three Qwen2.5-Coder sizes** (never an arbitrary scan of whatever models the user already has — that would break the clean "same family, different size" comparison the policy depends on). At session start, and at every model switch, Dwarv states which model it picked and *why*, using real measured RAM/RSS numbers — this narration is the headline feature, not an internal log line.

Consequences for scope:
- `Task` generalizes from "a frozen EvalPlus problem" to "whatever the user is asking about their own repo." Verification happens by running the repo's own test command (when one is discoverable) in a disposable worktree/temp copy — real files are never touched until a trial change passes there.
- The EvalPlus-based benchmark (baselines A/C/C+, seeds, bootstrap CIs) survives as an **internal eval harness** (`eval/`, `eval_tasks/`, `eval_results/`) that validates the policy during development; it is not a command an end user is expected to run.
- The read-only dashboard (formerly "the GUI deliverable") is now an optional, secondary stretch goal — a `/status` command inside the chat covers the same transparency goal without a server.

Full detail in the rewritten `DWARV_PLAN.md` (all sections). The `src/dwarv` scaffold on disk (from the prior benchmark-first framing: `systems/`, `bench/`, `tasks/`, `results/`, `run`/`bench` CLI subcommands) has **not** been restructured to match yet — that is a separate, explicit follow-up task, not implied by this plan rewrite.

**Update (same day)**: the restructure is done (new `agent/`, `models/`, `repo/` packages; `bench/`→`eval/`; old `systems/` and `src/dwarv/tasks/` removed; `cli.py` now defaults to `chat`). Committed and pushed.

## 2026-10-09: Step 1 real values — llama.cpp build, bundled models, and a disk-space override

Verified against real sources rather than guessed, per the plan's "do not fabricate repo features" rule:

- **llama.cpp**: pinned to numbered release `b11516` (not "latest" — that tag only carries a `nightly-tag.txt`, not binaries; GitHub Actions' `latest` alias doesn't point at a build with real assets for this project). Windows CPU asset is `llama-b11516-bin-win-cpu-x64.zip` (~18MB); Linux/macOS asset names for the same tag are recorded in `configs/models.yaml`'s `llama_cpp.assets` map but not yet downloaded/tested on those OSes. GPU acceleration (CUDA/ROCm/Vulkan builds also exist for this tag) is deliberately skipped for the MVP — matches the plan's risk register ("GPU profiling hard: CPU RAM only; mention GPU as future work").
- **Bundled models**: the official `Qwen/Qwen2.5-Coder-{1.5B,7B,14B}-Instruct-GGUF` repos on Hugging Face, Q4_K_M quant, apache-2.0 licensed. Verified file listing + sizes directly from each repo's file tree (not blog posts): 1.5B = 1,117,320,768 bytes; 7B and 14B sizes recorded in `configs/models.yaml` once their downloads finish (started this session).
- **Disk space override**: this dev machine only had 22GB free on `C:` (91% full) against a ~14.8GB total model download — downloading there would have pushed it to ~98% full. Added `DWARV_CACHE_DIR` as an environment-variable override for the cache location (default stays `./cache` inside the project per the plan); for this machine, set to `D:\dwarv-cache` (295GB free). This is a per-machine override, not a new default — `dwarv setup-offline`/`check-offline`/`doctor` all read it via `_cache_dir()` in `cli.py`.
- **Runtime protocol evolution**: `RuntimeAdapter.generate()` changed from a single `prompt: str` to `messages: list[dict[str, str]]` (OpenAI chat-style) — the original protocol code in the plan predates Step 6's explicit multi-turn requirement; using the single-message form under the hood would have silently dropped conversation history once Step 6 is built. Verified live end-to-end against the real small model: load (~2s), a correct chat completion, clean unload, and a second reload with no orphaned process.
- **`check-offline`**: implemented as an automatable proxy for "no network after setup" — loads the small model, generates once, and asserts the server process opened zero non-loopback connections (via `psutil.Process.net_connections()`), rather than requiring the user to physically disconnect networking. This matches the plan's own documented Tier 3 (native Windows, no Docker) fallback: no per-process network kill exists there, so connection-monitoring is the honest substitute. Verified live: passes.
