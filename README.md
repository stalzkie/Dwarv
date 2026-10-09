# Dwarv

A resource-aware, verification-guided local AI controller for offline coding tasks.

Given a coding task, the device's **current** resources, and a hard time and RAM budget, Dwarv picks the local execution strategy most likely to produce a **test-verified** solution, and re-decides after each failed verification using both the test feedback and live resource measurements.

Full build plan: [`DWARV_PLAN.md`](./DWARV_PLAN.md). That file is the project brief — work through it in order, step by step; this README stays a short pointer and gets filled in properly at Step 11.

## Status

Scaffolding only. See `docs/PROGRESS.md` for the running log and `docs/DECISIONS.md` for recorded decisions.

## Install (dev)

```bash
pip install -e ".[dev,gui]"
dwarv doctor
pytest
```

## Layout

See `DWARV_PLAN.md` section 3 for the full repository layout and section 4 for the step-by-step build order.
