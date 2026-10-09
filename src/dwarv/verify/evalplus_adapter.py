raise NotImplementedError(
    "Step 4, internal eval only: load EvalPlus HumanEval+ / MBPP+ problems and wrap them "
    "behind the same run()/failure-classification interface as the live repo-verification "
    "path. Build eval_tasks/subset_v1.json with a seeded random sample of 40-60 task IDs "
    "(never hand-picked). Used only by the Step 9 internal eval harness, never the live chat."
)
