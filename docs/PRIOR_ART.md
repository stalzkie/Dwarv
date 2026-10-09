# Prior art

Verified notes on related work, primary sources only. Read each source before citing; do not copy the summaries below blindly — they are starting pointers from the project brief, not verified writeups.

For each project, record: name, URL, date or latest verifiable activity, purpose, approach, hardware/offline assumptions, overlap with Dwarv, "not documented" features (kept separate from verified limitations), and implications for Dwarv's novelty framing.

## Candidates to verify

- **LMForge** — https://github.com/phoenixtb/lmforge — hardware-aware daemon; engine selection, VRAM admission, LRU eviction, telemetry, model switch API. Docs describe no test-driven verification/retry loop ("not documented", not a confirmed limitation — verify before citing).
- **TinyForge** — https://github.com/ranausmanai/tinyforge — MLX/Apple Silicon; test-failure-driven evolutionary search and repair-pair LoRA training; results on small HumanEval slices.
- **llama.cpp** — https://github.com/ggml-org/llama.cpp — the inference engine Dwarv reuses. Router mode, `--fit`, KV-cache types.
- **CodeRescue** (arXiv 2607.19338) — budget-calibrated recovery routing for coding agents using execution feedback; budget is cost, not live RAM. Closest research to Dwarv's hypothesis.
- **Resample or Reroute** (arXiv 2607.08665) — resample vs. reroute as competing uses of one per-query budget.
- **MemSpec** (arXiv 2608.10362) — memory-aware runtime for adaptive draft scheduling on edge devices (speculative decoding, not task correctness).
- **EvalPlus** — https://github.com/evalplus/evalplus — HumanEval+ / MBPP+ benchmark with extended tests.

## Honest novelty position

Do not claim algorithmic novelty. Frame Dwarv as:
1. An open-source harness for RAM-budgeted, test-guided local code repair, and
2. An empirical study of when adaptive control helps or does not help.
