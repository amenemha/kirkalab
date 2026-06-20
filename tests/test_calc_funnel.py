"""Unit tests for the pure FREE-tier calc funnel logic."""
from app.services.calc.funnel import (
    DAILY_LIMIT,
    INTRO_CALCS,
    FunnelStage,
    evaluate,
)


def test_intro_first_three_are_local_full():
    for total in (0, 1, 2):
        state = evaluate(is_pro=False, total_runs=total, runs_today=total)
        assert state.allowed
        assert state.stage is FunnelStage.LOCAL_FULL
        assert state.calc_index == total + 1
        assert state.intro_left == INTRO_CALCS - total
        assert state.daily_left is None
        assert not state.intro_spent
        assert state.pro_hint


def test_intro_four_and_five_are_blurred():
    for total in (3, 4):
        state = evaluate(is_pro=False, total_runs=total, runs_today=total)
        assert state.allowed
        assert state.stage is FunnelStage.LOCAL_BLURRED
        assert state.calc_index == total + 1
        assert not state.intro_spent


def test_after_intro_is_usdt_only_with_daily_quota():
    # 5 intro runs already done; first post-intro calc of the day.
    state = evaluate(is_pro=False, total_runs=INTRO_CALCS, runs_today=0)
    assert state.allowed
    assert state.stage is FunnelStage.USDT_ONLY
    assert state.intro_spent
    assert state.calc_index == INTRO_CALCS + 1
    assert state.daily_left == DAILY_LIMIT
    assert state.intro_left == 0


def test_daily_quota_decrements_and_last_calc_hint():
    # Two already done today -> this is the 3rd (last) of the day.
    state = evaluate(is_pro=False, total_runs=INTRO_CALCS + 2, runs_today=2)
    assert state.allowed
    assert state.daily_left == 1
    assert "последний" in state.pro_hint


def test_daily_quota_exhausted_blocks():
    state = evaluate(
        is_pro=False, total_runs=INTRO_CALCS + 3, runs_today=DAILY_LIMIT
    )
    assert not state.allowed
    assert state.stage is FunnelStage.USDT_ONLY
    assert state.daily_left == 0
    assert state.pro_hint  # paywall invite present


def test_pro_has_no_funnel_or_limits():
    state = evaluate(is_pro=True, total_runs=1000, runs_today=1000)
    assert state.is_pro
    assert state.allowed
    assert state.stage is FunnelStage.PRO
    assert state.calc_index is None
    assert state.daily_left is None
    assert state.pro_hint is None


def test_state_carries_configured_limits():
    state = evaluate(is_pro=False, total_runs=0, runs_today=0)
    assert state.intro_calcs == INTRO_CALCS
    assert state.daily_limit == DAILY_LIMIT


def test_custom_intro_extends_intro_window():
    # With intro_calcs=2, the 3rd total run is already post-intro.
    state = evaluate(is_pro=False, total_runs=2, runs_today=0, intro_calcs=2)
    assert state.intro_spent
    assert state.stage is FunnelStage.USDT_ONLY
    assert state.intro_calcs == 2
    # And it carries the daily limit through.
    assert state.daily_left == DAILY_LIMIT


def test_custom_daily_limit_governs_quota():
    # intro_calcs=2 spent; daily_limit=5; 4 already done today -> 1 left.
    state = evaluate(
        is_pro=False,
        total_runs=2 + 4,
        runs_today=4,
        intro_calcs=2,
        daily_limit=5,
    )
    assert state.allowed
    assert state.daily_left == 1
    assert state.daily_limit == 5


def test_custom_daily_limit_exhausted_blocks():
    state = evaluate(
        is_pro=False,
        total_runs=2 + 5,
        runs_today=5,
        intro_calcs=2,
        daily_limit=5,
    )
    assert not state.allowed
    assert state.daily_left == 0
