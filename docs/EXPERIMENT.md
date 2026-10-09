# Experiment protocol

Freeze this file (Step 9) before the final run. Until then it is a draft.

## Protocol

- Same machine, same background load, same llama.cpp build, same models/quants, same task subset, same budget profiles, same sampling parameters.
- Systems: A (fixed), C (retry), C+ (retry+escalate), Dwarv. Optional B (LMForge-hosted fixed model) only if it installs cleanly in under one hour; otherwise record "not run" with the reason.
- Equalize total resources: same `max_attempts`, same `time_limit_s`, same models available to C+ and Dwarv. Any difference must be an explicit, reported variable.
- Budget profiles: `static_tight`, `static_loose`, `squeeze_mid`.
- Seeds: at least 3 per (system, profile, task). Report mean and spread.
- Warm-up: one discarded run per model to warm disk cache. Record whether the page cache was cold or warm.
- Primary metric: verified pass rate under the hidden EvalPlus tests, at equal budget.
- Secondary metrics: peak RSS, budget violations, wall-clock latency, attempts used, failed-retry rate, model-switch overhead, offline completion.
- Noise sources to record: other processes, thermal throttling, page cache, swap use, sampling randomness.

## Decision criteria (write in advance, do not amend after seeing results)

The hypothesis is **supported** only if Dwarv beats C+ on verified pass rate under `squeeze_mid` with a margin larger than seed-to-seed spread, **and** has fewer budget violations. If Dwarv only beats A or C but not C+, report that the gain comes from escalation, not resource awareness.

## Results

(Filled in after Step 9 runs. Every number here must come from a file under `results/`.)

## Interpretation

(Filled in after Step 9 runs. State plainly whether the hypothesis was supported, partially supported, or not supported.)
