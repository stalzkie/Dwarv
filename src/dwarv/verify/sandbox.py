raise NotImplementedError(
    "Step 4: sandboxed verifier. Three tiers per DWARV_PLAN.md section 2.3 behind one "
    "run(code, tests, limits) -> SandboxResult interface, auto-selected from dwarv.cli._sandbox_tier(): "
    "1) Docker --network none (any OS), 2) resource.setrlimit + unshare -n (Linux/macOS, no Docker), "
    "3) Windows Job Object + timeout, reduced isolation (native Windows, no Docker)."
)
