<p align="center">
  <img src="docs/assets/logo.svg" alt="Dwarv" width="220" />
</p>

# Dwarv

A local, conversational coding assistant — "a local Claude Code" — with a bundled suite of three Qwen2.5-Coder models (1.5B / 7B / 14B). At the start of each session Dwarv reads your real CPU/RAM/GPU, picks the model that fits, and explains why out loud. When there's something to verify against (your repo's own tests), it checks its work in a disposable copy of your working tree before touching your real files, and retries with the actual failure feedback instead of guessing again blindly. If memory gets tight mid-conversation, it says so and steps down to a smaller model instead of hanging or crashing.

Everything below describes what's actually built and tested, not a roadmap — see [Status](#status) and [Limitations](#limitations) for what isn't.

## Install

```bash
pip install -e ".[dev,gui]"   # or just ".[dev]" to skip the optional transparency panel
```

Needs Python 3.10+. No GPU required — Dwarv runs entirely on CPU by default (see [Resource-awareness](#resource-awareness) for what GPU offload would add).

## Quickstart

```bash
dwarv setup-offline   # one-time: downloads llama-server + the 3 bundled models (~14.8GB)
dwarv                 # starts a chat session in the current directory
```

Inside the session: plain text for a direct answer, or ask for a code change and Dwarv will show you the diff before applying it. `/status` shows the current model, sandbox tier, and last decision; `/help` lists every slash command.

```bash
dwarv doctor         # OS/CPU/RAM/GPU/sandbox-tier detection, no models needed
dwarv check-offline  # proves no non-local network connections happen after setup
```

## What it actually does

- **Hardware-aware model choice, narrated.** Picks the largest of the 3 bundled models that fits your measured-available RAM (with a safety margin), and says the real numbers it used to decide — not a canned message. If a tier's default quantization doesn't fit, `setup-offline` downloads a more compressed variant instead (see [Resource-awareness](#resource-awareness)) and the session narrates that tradeoff too.
- **Sandboxed, verify-before-apply patches.** Proposed changes run in a disposable git worktree first — Docker (`--network none`) when available, falling back to `resource.setrlimit`+`unshare -n` on Linux/macOS or a Windows Job-Object-style watchdog, never your real files. The model is asked for the complete new content of each changed file (not a diff it has to get byte-exact), and the diff you see is computed by Dwarv itself, not parsed out of model output.
- **Resource-aware retry policy.** On a failure, Dwarv retries with the real failure feedback, adjusts sampling, shrinks context, or steps down to a smaller model — all before giving up — and every decision is a real policy function, not a hardcoded message.
- **Live memory-squeeze handling.** If available RAM drops mid-conversation (a real OS event, or `/squeeze <MB>` for a demo), Dwarv checks *before* generating, not just after a failure, narrates the step-down, and keeps the conversation working on the smaller model.
- **Structured output, not regex-parsed prose.** Model responses are grammar-constrained to a JSON schema (`{kind, message, files}`) via `llama-server`'s own `--json-schema` support, so "the model forgot to format its code block" is structurally impossible rather than a failure mode to detect after the fact.
- **Graph-based context, not a whole-repo dump.** A lightweight AST-based code graph is built once per session; each turn queries it for only the functions/classes actually relevant to that question (plus their real callers/callees), instead of stuffing the whole repository into the prompt.
- **Offline after setup.** `dwarv check-offline` loads the small model, generates once, and inspects the server process's own network connections to confirm none left the machine.

## Resource-awareness (the core bet)

Dwarv's premise is that a coding assistant should actively manage its own resource footprint rather than assume unlimited RAM/VRAM. What's real today:

- RAM-aware model tier selection, with the actual decision explained in plain language every time.
- Quantization-aware downloads: `setup-offline` checks real available RAM per tier and downloads a lower-footprint I-quant (from a verified community source — Qwen's own repos publish no I-quants) when the default doesn't fit, rather than silently failing or forcing a smaller model. Live-validated: real measured RSS for one such download (766.5MB) came in under a deliberately conservative estimate (957.1MB), confirming the safety margin holds in practice.
- **GPU offload, when there's a GPU to use.** `setup-offline` detects an NVIDIA GPU (`nvidia-smi`) and additionally downloads a 33MB Vulkan-enabled llama-server build — never replacing the CPU-only one, and never attempted at all on a machine with no detected GPU. Measured before building it, not guessed: on a real RTX 3050, full GPU offload ran **8.4x–9.7x faster** token generation than CPU-only for the small/medium models, and **5.5x** for the large one (`llama-bench`, `-ngl 0` vs `-ngl 99`). Picked Vulkan over CUDA deliberately — CUDA needs ~650MB of extra downloads and is NVIDIA-only; Vulkan is 33MB and works across NVIDIA/AMD/Intel GPUs, though VRAM *detection* is still NVIDIA-only for now (a real, stated gap). Falls back live to CPU-only if the GPU binary ever fails to start.
- Flash-attention and KV-cache quantization remain a documented, researched plan, not yet built. See `DWARV_PLAN.md` section 11 for what's live versus what's scoped.

We also built and honestly evaluated our own activation-aware quantization method from scratch, to see whether it preserves quality better than naive rounding at the same bit-width: it did, measurably, on a 0.5B model — and the improvement did **not** replicate on a 1.5B model in the same test. We reported that negative result rather than hiding it; see `docs/DECISIONS.md` and `scripts/compression/`.

## Internal evaluation

A developer-only harness (`dwarv eval`, never the end-user surface) compares Dwarv's real policy against three baselines — a fixed model, verification-guided retry, and retry-with-escalation-but-no-resource-awareness — on real HumanEval+ tasks, verified through Dwarv's own cross-platform sandbox. Results use paired statistics appropriate for identical-task comparisons (McNemar's test for pass/fail, Wilcoxon signed-rank for continuous metrics), not just independent confidence intervals.

**Honestly, the hypothesis is suggestive, not proven.** A real, hackathon-scoped run (`realcompare1`: 20 tasks, both `static_loose` and the memory-constrained `squeeze_mid`, 4 systems) is in: the comparison that actually isolates resource-awareness — Dwarv vs. a retry-and-escalate baseline that is *not* RAM-aware — shows Dwarv winning every task where the two disagreed under memory pressure (4 wins, 0 losses; pass rate 0.60 vs. 0.40, the widest gap in the run), the right direction with the largest effect size observed. But at n=20 with 1 seed, that's not statistically significant (McNemar p=0.125) — real signal, not yet proof. Dwarv also reliably costs more wall-clock time than a no-policy baseline (p<0.01), the honest price of retrying instead of just failing. See `docs/EXPERIMENT.md` for the full numbers, the frozen protocol, the decision criteria written in advance, and exactly what's been measured versus what hasn't.

## Status

Functional end-to-end through hardware-aware model+quant selection, sandboxed verify-before-apply, the resource-aware policy, structured output, and graph-based context — all live-tested against real models, not just unit-tested against fakes. The optional session-transparency panel (`dwarv gui`, hidden from `--help` by default while the project's focus is resource-awareness rather than UI) is built and tested but not the primary surface. The full statistically-powered internal evaluation has not been run. See `docs/PROGRESS.md` for the step-by-step build log and `docs/DECISIONS.md` for every non-obvious choice and why.

## Limitations

- The resource-awareness hypothesis has real, directionally-supportive evidence but isn't statistically proven at the scale run so far (see [Internal evaluation](#internal-evaluation)).
- The code graph is Python-only (stdlib `ast`), and call-graph edges are matched by name, not type-resolved — two unrelated functions sharing a name are treated as one node. Stated plainly in `repo/graph.py`'s own docstring, not hidden.
- Windows' sandbox tier (no Docker, no `unshare`) is a polled memory watchdog, not a kernel-enforced limit — real and functional, but weaker than the Linux/macOS tiers.
- Our own compression method is a research thread, not a production path — see above. The production path for shrinking a model's footprint is llama.cpp's own mature I-quant family.
- `docs/PRIOR_ART.md`'s broader "related products" survey (distinct from `docs/PRIOR_ART_COMPRESSION.md`, which *is* fully researched) still needs verification against primary sources.

## Layout

See `DWARV_PLAN.md` section 3 for the full repository layout, section 4 for the step-by-step build order, and section 11 for the resource-awareness work built after the core plan shipped.
