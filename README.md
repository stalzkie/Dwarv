<p align="center">
  <img src="docs/assets/logo.svg" alt="Dwarv" width="220" />
</p>

# Dwarv

A local, conversational coding assistant — "a local Claude Code" — with a bundled suite of three Qwen2.5-Coder models (1.5B / 7B / 14B). At the start of each session Dwarv reads your real CPU, RAM, and GPU, picks the model that fits, and explains why out loud. It verifies its own proposed changes against your repo's tests in a disposable copy of your working tree before touching your real files, and retries with the actual failure feedback instead of guessing blindly. If memory gets tight mid-conversation, it says so and steps down to a smaller model instead of hanging or crashing. When a GPU is available, it uses it — offload is detected and applied automatically, with no configuration needed.

Every claim below is backed by a real, reproducible measurement — see [Results](#results) for the numbers and raw data, and [Reproducing the results](#reproducing-the-results) for the exact commands.

## Table of contents

- [For judges: setup and demo](#for-judges-setup-and-demo)
- [What it does](#what-it-does)
- [How the resource-awareness works](#how-the-resource-awareness-works)
- [Results](#results)
- [Reproducing the results](#reproducing-the-results)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)

## For judges: setup and demo

The short version: **download this repo once, install it once, and `dwarv` then works like any other CLI tool — run from inside whatever project you actually want help with, of any language.** You never touch this repo again after step 2 unless you're updating Dwarv itself.

### 1. Get the code

```bash
git clone https://github.com/stalzkie/Dwarv.git
cd Dwarv
```

(Or unzip it, if you received this as a zip rather than a git remote — either way, step 2 below needs to run from the folder this produces, the one containing this README and `pyproject.toml`.)

### 2. Install Dwarv itself

**Run this from inside the folder from step 1** — not from inside the project you want Dwarv to help with. `pip install -e .` installs *Dwarv*; it needs to find Dwarv's own `pyproject.toml` in your current directory, and fails with `does not appear to be a Python project: neither 'setup.py' nor 'pyproject.toml' found` if run anywhere else. This is a one-time install and has nothing to do with what kind of project Dwarv can later be pointed at — see step 4.

```bash
pip install -e ".[dev]"              # add ",gui" too if you also want the optional transparency panel
```

Requires Python 3.10+. Works with or without a GPU — a GPU (NVIDIA, via Vulkan) is detected and used automatically when present; everything runs on CPU otherwise, no configuration required either way.

### 3. One-time download

```bash
dwarv setup-offline
```

Downloads `llama-server` and the 3 bundled models (~15GB total) plus a small (33MB) GPU-offload binary if it detects an NVIDIA GPU. One-time only, and stored in a stable per-user location independent of which directory you happen to run commands from — `~/.cache/dwarv` on Linux/macOS, `%LOCALAPPDATA%\dwarv` on Windows — so running `dwarv setup-offline` once and then `dwarv` later from a completely different project directory finds the same download, no extra configuration needed.

**Setting up a second device?** Copy that cache folder itself (about 11GB) from a machine where `setup-offline` already ran — over a USB drive, say — into the same stable location on the new device, rather than re-downloading at a venue. Or, if you'd rather keep it somewhere else entirely (a different drive, a shared network location), `DWARV_CACHE_DIR` always overrides the default:
```bash
export DWARV_CACHE_DIR=/path/to/copied/cache     # PowerShell: $env:DWARV_CACHE_DIR = "..."
```

### 4. Run it — on any project, anywhere

Once installed (step 2) and downloaded (step 3), `dwarv` is a regular command on your PATH, same as `git` or `node`. You're done with *this* repository — `cd` into any other project and run it there:

```bash
cd ~/some-other-project-in-any-language    # the repo you actually want help with
dwarv                                       # starts a chat session there
```

Ask a question for a direct answer, or ask for a code change and Dwarv shows you the diff and verification result before anything is applied. `/status` shows the current model, sandbox tier, and last decision; `/help` lists every slash command. Test-command auto-detection currently recognizes Python (`pytest`) and JS/npm (`npm test`) projects; other languages still get code-fix attempts and diffs, just without an automated test run to verify against.

```bash
dwarv doctor           # real hardware detection: CPU/RAM/GPU/sandbox tier, no models needed
dwarv check-offline    # proves no non-local network connections happen after setup
```

### 5. Run the full scripted demo

```bash
scripts/run_demo.sh
```

(Git Bash on Windows, or any bash shell on Linux/macOS.) Builds a fresh temporary repo with one real bug, has Dwarv fix it live with full verification, triggers a memory squeeze mid-conversation to show the narrated step-down, proves offline operation, and prints the real evaluation numbers from [Results](#results) below. Runs in well under 2 minutes, end to end, against live models — not a recording.

For a shorter live demo (under a minute), start `dwarv chat` in a small repo with an obvious bug *before* you begin presenting so the model is already loaded, then just ask it to fix the bug live — that single moment (a real diff, applied only after real verification) is the core thing the scripted demo above also shows, without the model-load time eating into your clock.

## What it does

- **Hardware-aware model choice, narrated out loud.** Picks the largest of the 3 bundled models that fits your measured-available RAM (with a safety margin), and states the real numbers it used to decide. If a tier's default quantization doesn't fit, `setup-offline` downloads a smaller, more compressed variant instead, and the session narrates that tradeoff too.
- **GPU offload, automatic.** `setup-offline` detects an NVIDIA GPU and downloads a Vulkan-enabled `llama-server` build; `dwarv chat` uses it whenever the selected model fits in VRAM, falling back live to CPU if it ever fails to start. See [Results](#results) for the measured speedup.
- **Sandboxed, verify-before-apply patches.** Proposed changes run in a disposable git worktree first — Docker (`--network none`) when available, falling back to OS-level isolation on Linux/macOS/Windows — never your real files. The model returns the complete new content of each changed file, and the diff you see is computed by Dwarv itself.
- **Resource-aware retry policy.** On a failure, Dwarv retries with the real failure feedback, adjusts sampling, shrinks context, or steps down to a smaller model — every decision is a real policy function, not a hardcoded message.
- **Live memory-squeeze handling.** If available RAM drops mid-conversation (a real OS event, or `/squeeze <MB>` for a demo), Dwarv checks *before* generating, narrates the step-down, and keeps the conversation working on the smaller model.
- **Structured output, not regex-parsed prose.** Model responses are grammar-constrained to a JSON schema via `llama-server`'s own `--json-schema` support, so a malformed response is structurally close to impossible rather than a failure mode to detect after the fact.
- **Graph-based context, not a whole-repo dump.** A lightweight AST-based code graph is built once per session; each turn queries it for only the functions, classes, and files actually relevant to that question, instead of stuffing the whole repository into the prompt.
- **Offline after setup.** `dwarv check-offline` loads the small model, generates once, and inspects the server process's own network connections to confirm none left the machine.

## How the resource-awareness works

Dwarv actively manages its own resource footprint rather than assuming unlimited RAM or VRAM:

- **RAM-aware model tier selection**, with the actual decision explained in plain language every time.
- **Quantization-aware downloads**: `setup-offline` checks real available RAM per tier and downloads a lower-footprint quantization from a verified community source when the default doesn't fit, rather than silently failing or forcing a smaller model.
- **GPU offload when there's a GPU to use**: detected via `nvidia-smi`, applied via a Vulkan-enabled build chosen specifically because it needs no separate runtime and works across GPU vendors (unlike CUDA, which was evaluated and not used — see `docs/DECISIONS.md`). Falls back live to CPU-only if the GPU binary ever fails to start.
- **A from-scratch activation-aware quantization method**, built and evaluated honestly: it improved quality over naive rounding on a 0.5B model, and that improvement did not replicate on a 1.5B model in the same test. Both results are reported, including the one that didn't hold up — see `docs/DECISIONS.md` and `scripts/compression/`.

## Results

Full writeup with every number, methodology notes, and raw data: **[`results/RESULTS.md`](results/RESULTS.md)**. Headlines:

**GPU offload**, measured on an RTX 3050 before building the feature (CPU vs. full Vulkan offload):

| model | CPU | GPU | speedup |
|---|---|---|---|
| small (1.5B) | 19.5 tok/s | 163.5 tok/s | **8.4x** |
| medium (7B) | 4.6 tok/s | 44.3 tok/s | **9.7x** |
| large (14B) | 3.5 tok/s | 19.2 tok/s | **5.5x** |

**Internal comparison evaluation** (`dwarv eval`, real HumanEval+ tasks, verified through Dwarv's own cross-platform sandbox): Dwarv's resource-aware policy compared against three baselines, using paired statistics (McNemar's exact test, Wilcoxon signed-rank) appropriate for identical-task comparisons. Under simulated memory pressure, Dwarv won every task where it and a non-resource-aware retry-and-escalate baseline disagreed (4 wins, 0 losses out of 20 tasks; pass rate 0.60 vs. 0.40, the widest gap in the run) — the predicted direction with the largest effect size observed, though not statistically significant at this sample size (p=0.125). Dwarv also measurably costs more wall-clock time than a no-policy baseline (p<0.01 both tested profiles) — the real, honest price of retrying instead of failing fast. Full numbers, every comparison, and the frozen protocol: `docs/EXPERIMENT.md` and `results/RESULTS.md`.

## Reproducing the results

```bash
# CPU model benchmarks (load time, RSS, generation speed)
python scripts/benchmark_models.py

# GPU offload benchmark (needs a Vulkan-enabled llama-server/llama-bench build)
llama-bench -m <model.gguf> -ngl 0,99 -p 128 -n 128

# Internal comparison evaluation
dwarv eval --run-id <your-run-id> \
  --systems fixed --systems retry --systems retry_escalate --systems dwarv \
  --profiles static_loose --profiles squeeze_mid --n-tasks 20 --seeds 1
```

Each produces real output under `benchmarks/` or `eval_results/<run-id>/` (summary tables, significance reports, charts) — the same pipeline that produced everything in `results/RESULTS.md`.

## Project layout

```
src/dwarv/
  agent/       chat session loop, prompts, structured-response parsing, terminal rendering
  cli.py       dwarv command-line entry point (chat, doctor, setup-offline, check-offline, eval)
  controller/  the resource-aware retry/escalate/step-down policy
  eval/        internal comparison-evaluation harness (developer tool, not the end-user surface)
  models/      model suite config, hardware-aware selection, quant/GPU-layer sizing
  repo/        repo detection, the AST-based code graph, patch application, disposable worktrees
  resources/   RAM budget tracking, live resource monitoring, memory-squeeze scheduling
  runtime/     the llama-server process wrapper (load/generate/unload)
  telemetry/   event-sourced session logging
  verify/      cross-platform sandboxed execution, EvalPlus task adapter
  gui/         optional, read-only session-transparency panel (hidden from --help by default)

configs/        model suite, resource budgets, baseline-system definitions
docs/           build log, every non-obvious decision and why, the evaluation protocol
benchmarks/     CPU/GPU benchmark data and charts (also consolidated in results/)
eval_results/   raw evaluation run output (also consolidated in results/)
results/        a single place to find the headline benchmark and evaluation numbers together
scripts/        setup/benchmark/demo tooling
tests/          the test suite
```

## Known limitations

- The dwarv-vs-retry_escalate comparison in [Results](#results) is directionally consistent with the resource-awareness premise but does not reach statistical significance at the sample size run (n=20, 1 seed).
- The precise, AST-based code graph (stdlib `ast`) is Python-only, and its call-graph edges are matched by name, not type-resolved — two unrelated functions sharing a name are treated as one node. A repo with no Python files falls back to a simpler whole-file context dump instead of the precise graph, so other languages still get real code context, just without the graph's call-site precision.
- GPU *detection* is NVIDIA-only (`nvidia-smi`); the Vulkan offload binary itself supports AMD and Intel GPUs, but this project has no way to detect their VRAM, so offload is only ever attempted on a machine where an NVIDIA GPU was found.
- Windows' sandbox tier (no Docker, no `unshare`) is a polled memory watchdog, not a kernel-enforced limit — functional, but weaker than the Linux/macOS tiers.
- The from-scratch quantization method in `scripts/compression/` is a research artifact, not the production path — the production path for shrinking a model's footprint is llama.cpp's own quantization family, used throughout the rest of this project.

## License

MIT — see `LICENSE`.
