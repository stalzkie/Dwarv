raise NotImplementedError(
    "Step 4: a disposable copy of the working tree for trial edits + test runs -- "
    "`git worktree add` into a temp dir when the repo is git-tracked, otherwise a plain "
    "temp-dir copy. Trial changes never touch the real working tree until verified."
)
