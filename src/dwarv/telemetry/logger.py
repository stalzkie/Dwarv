raise NotImplementedError(
    "Section 5.3: JSONL event logger. One object per line: ts, session_id, event, plus "
    "event-specific fields (run_started, model_loaded, monitor_sample, turn_started, "
    "patch_proposed, verified, decision, budget_change, budget_warn, budget_violation, "
    "model_unloaded, run_finished), each carrying seq/rel_t_s/flow_node. This log is the "
    "single source of truth behind both /status narration and the internal eval harness."
)
