import time

from dwarv.resources.squeeze import SqueezeScheduler


def test_fires_after_n_turns():
    scheduler = SqueezeScheduler()
    scheduler.schedule(new_ram_limit_mb=500.0, reason="turn cut", after_turns=2)

    assert scheduler.maybe_fire(turn_index=0) is None
    assert scheduler.maybe_fire(turn_index=1) is None
    event = scheduler.maybe_fire(turn_index=2)

    assert event is not None
    assert event.new_ram_limit_mb == 500.0
    assert event.reason == "turn cut"


def test_fires_after_elapsed_time():
    scheduler = SqueezeScheduler()
    scheduler.schedule(new_ram_limit_mb=500.0, reason="time cut", after_s=0.05)

    assert scheduler.maybe_fire(turn_index=0) is None
    time.sleep(0.1)
    event = scheduler.maybe_fire(turn_index=0)

    assert event is not None
    assert event.reason == "time cut"


def test_never_fires_twice():
    scheduler = SqueezeScheduler()
    scheduler.schedule(new_ram_limit_mb=500.0, after_turns=0)

    first = scheduler.maybe_fire(turn_index=0)
    second = scheduler.maybe_fire(turn_index=5)

    assert first is not None
    assert second is None


def test_fires_first_due_event_only_per_call():
    scheduler = SqueezeScheduler()
    scheduler.schedule(new_ram_limit_mb=500.0, reason="first", after_turns=0)
    scheduler.schedule(new_ram_limit_mb=200.0, reason="second", after_turns=0)

    first_call = scheduler.maybe_fire(turn_index=0)
    second_call = scheduler.maybe_fire(turn_index=0)

    assert first_call.reason == "first"
    assert second_call.reason == "second"


def test_no_events_scheduled_never_fires():
    scheduler = SqueezeScheduler()
    assert scheduler.maybe_fire(turn_index=100) is None
