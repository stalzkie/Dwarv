raise NotImplementedError(
    "Step 4: run(cmd, cwd, limits) -> SandboxResult, tagged with which tier ran it. Three "
    "tiers per DWARV_PLAN.md section 2.3, auto-selected from dwarv.cli._sandbox_tier(): "
    "1) Docker --network none (any OS), 2) resource.setrlimit + unshare -n (Linux/macOS, no "
    "Docker), 3) Windows Job Object + timeout, reduced isolation (native Windows, no Docker). "
    "`cwd` must always be a disposable worktree (repo/worktree.py), never the user's real tree."
)
