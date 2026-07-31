from app.main import events_for_tick


def test_exact_multiple_produces_a_constant_count_per_tick():
    # 200 events/s at 50 ticks/s (0.02s) is exactly 4/tick, every tick.
    carry = 0.0
    counts = []
    for _ in range(20):
        n, carry = events_for_tick(200.0, 0.02, carry)
        counts.append(n)
    assert counts == [4] * 20


def test_non_exact_rate_averages_out_over_many_ticks():
    # 190 events/s at 50 ticks/s is 3.8/tick - not an integer, so individual
    # ticks must vary (3 or 4), but the long-run average must still land on
    # the true target rate, not silently drift low (round()/int() truncating
    # every tick independently) or high.
    carry = 0.0
    total = 0
    ticks = 1000
    for _ in range(ticks):
        n, carry = events_for_tick(190.0, 0.02, carry)
        total += n
    achieved_rate = total / (ticks * 0.02)
    assert abs(achieved_rate - 190.0) < 0.1


def test_rate_below_one_tick_per_event_still_averages_correctly():
    # 10 events/s at 50 ticks/s is 0.2/tick - most ticks produce 0 events,
    # a few produce 1, not "at least 1 every tick" (that would 5x the rate).
    carry = 0.0
    total = 0
    ticks = 500
    for _ in range(ticks):
        n, carry = events_for_tick(10.0, 0.02, carry)
        total += n
    achieved_rate = total / (ticks * 0.02)
    assert abs(achieved_rate - 10.0) < 0.5


def test_carry_never_grows_unbounded():
    carry = 0.0
    for _ in range(10_000):
        _n, carry = events_for_tick(37.0, 0.02, carry)
    assert 0.0 <= carry < 1.0
