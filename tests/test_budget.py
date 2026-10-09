from dwarv.resources.budget import BudgetManager, BudgetStatus


def test_warn_below_headroom_fraction():
    budget = BudgetManager(
        ram_limit_mb=1000.0,
        time_limit_s=60.0,
        max_attempts=3,
        warn_fraction=0.10,
        rss_fn=lambda: 950.0,  # headroom = 50, warn threshold = 100
    )
    assert budget.check() == BudgetStatus.WARN


def test_violation_when_rss_exceeds_limit():
    budget = BudgetManager(
        ram_limit_mb=1000.0,
        time_limit_s=60.0,
        max_attempts=3,
        rss_fn=lambda: 1200.0,
    )
    assert budget.check() == BudgetStatus.VIOLATION
    assert len(budget.violations) == 1
    assert budget.violations[0]["rss_mb"] == 1200.0


def test_limit_change_at_runtime():
    budget = BudgetManager(
        ram_limit_mb=1000.0,
        time_limit_s=60.0,
        max_attempts=3,
        rss_fn=lambda: 200.0,
    )
    assert budget.check() == BudgetStatus.OK

    budget.set_ram_limit(500.0, reason="memory squeeze")

    assert budget.ram_limit_mb == 500.0
    assert len(budget.changes) == 1
    assert budget.changes[0].old_limit_mb == 1000.0
    assert budget.changes[0].new_limit_mb == 500.0
    assert budget.changes[0].reason == "memory squeeze"
