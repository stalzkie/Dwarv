raise NotImplementedError(
    "Step 6: the chat loop. On start: run doctor's checks, call models.suite.choose_model, "
    "load it, print the opening explanation (Step 5) and the active sandbox tier. Per turn: "
    "read a message, answer directly or propose a patch via repo/patch.py, verify via "
    "verify/sandbox.py in the disposable worktree when a test command exists, hand failures "
    "to controller/policy.py instead of guessing again blindly. Exposes /status."
)
