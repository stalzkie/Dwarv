# Decisions

Every non-obvious choice and the reason behind it. Edit entries if the user overrides them; do not delete history, append a new entry noting the change instead.

## Initial decisions (Step 0)

- **OS target**: cross-platform (Windows, macOS, Linux) via a plain `pip install dwarv`. WSL2 is supported but never required. See §2.3 of `DWARV_PLAN.md` for the tiered sandbox this implies.
- **Runtime**: `llama-server` from llama.cpp, over HTTP, one model resident at a time in the MVP. Use the official prebuilt binary release for the host OS/arch; build from source only as a fallback.
- **Language**: Python (3.10+).
- **Benchmark**: EvalPlus (HumanEval+ / MBPP+) subset, 40 to 60 tasks, frozen (`tasks/subset_v1.json`).
- **Models**: one family, 2 to 3 sizes (see Step 1 of `DWARV_PLAN.md`).
- **Package layout**: `src/dwarv`, console script `dwarv`, GUI deps kept in the optional `dwarv[gui]` extra so the core install stays light.
- **Dependency pinning**: minimum-version ranges (`>=`) in `pyproject.toml`, not exact pins, so installs resolve to a wheel that exists for the user's current Python/OS instead of forcing a from-source build. Residual risk: a transitive dependency (e.g. `evalplus`'s own `numpy` bound) can still force a source build on a brand-new Python release; this is upstream and outside `dwarv`'s control — if hit, document it in `docs/PROGRESS.md` rather than fighting it with aggressive overrides.

## 2026-10-09: cross-platform sandboxing, replacing a hard WSL2 requirement

The original plan required Linux/WSL2 because the sandboxed verifier (Step 4) leaned on two Linux-only primitives: `resource.setrlimit` and `unshare -n`. That's real, but it's the *only* OS-specific part of the whole project — `llama-server`, the CLI, and the dashboard are not OS-specific. Decision: keep Linux/macOS's rlimit+`unshare` path, add Docker (`--network none`) as the primary, cross-platform tier, and add a Windows Job Object fallback (reduced isolation, explicitly logged) for native Windows with no Docker. `dwarv doctor` detects and reports which tier is active. Full detail in `DWARV_PLAN.md` §2.3 and Step 4.
