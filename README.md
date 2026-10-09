# Dwarv

A local, conversational coding assistant — "a local Claude Code" — with a bundled suite of three Qwen2.5-Coder models. At the start of each session Dwarv reads your real CPU/RAM/GPU, picks the model that fits, and says why. When there's something to verify against (your repo's own tests), it checks its work in a disposable copy of your working tree before touching your real files, and retries with the actual failure feedback instead of guessing again blindly. If memory gets tight mid-conversation, it says so and steps down instead of hanging or crashing.

Full build plan: [`DWARV_PLAN.md`](./DWARV_PLAN.md). That file is the project brief — work through it in order, step by step; this README stays a short pointer and gets filled in properly at Step 11.

## Status

Scaffolding only. See `docs/PROGRESS.md` for the running log and `docs/DECISIONS.md` for recorded decisions.

## Install (dev)

```bash
pip install -e ".[dev,gui]"
dwarv doctor
pytest
```

## Quickstart (once Steps 1-6 are implemented)

```bash
dwarv setup-offline   # one-time: downloads the 3 bundled models into ./cache/
dwarv                 # starts a chat session in the current directory
```

## Layout

See `DWARV_PLAN.md` section 3 for the full repository layout and section 4 for the step-by-step build order.
