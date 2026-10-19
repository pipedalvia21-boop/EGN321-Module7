"""Stale/missing detection, health states and the replay schedule."""
import pytest

from monitor import health
from monitor.health import DEGRADED, FAILED, HEALTHY, RECOVERING, STARTING, dashboard_health, evaluate
from monitor.replay import ReplaySchedule, load_source, start_index_for

NOW = 1_800_000_000.0


def ev(settings, age, acc_age=None, statuses=("OK",) * 30, **kw):
    return evaluate(NOW, settings, last_row_epoch=None if age is None else NOW - age,
                    last_accepted_epoch=None if age is None else NOW - (age if acc_age is None else acc_age),
                    recent_statuses=list(statuses), started_epoch=NOW - 100, **kw)


def test_fresh_data_is_healthy(settings):
    assert ev(settings, 0.4).state == HEALTHY


def test_stale_boundary(settings):          # interval 0.5 s at 120x -> stale after 1.5 s
    assert settings.stale_after_s == 1.5
    assert ev(settings, 1.5).data_state == health.FRESH
    h = ev(settings, 1.51)
    assert (h.state, h.data_state) == (DEGRADED, health.STALE)


def test_missing_data_boundary_is_failed(settings):    # failed after 15 intervals = 7.5 s
    assert ev(settings, 7.5).state == DEGRADED
    h = ev(settings, 7.51)
    assert (h.state, h.data_state) == (FAILED, health.LOST)
    assert "stopped arriving" in h.reason


def test_no_data_since_start(settings):
    assert evaluate(NOW, settings, last_row_epoch=None, last_accepted_epoch=None, recent_statuses=[],
                    started_epoch=NOW - 2).state == STARTING
    assert evaluate(NOW, settings, last_row_epoch=None, last_accepted_epoch=None, recent_statuses=[],
                    started_epoch=NOW - 60).state == FAILED


def test_rows_arriving_but_none_accepted_is_degraded(settings):
    assert ev(settings, 0.2, acc_age=5.0, statuses=["OUT_OF_RANGE"] * 30).state == DEGRADED


@pytest.mark.parametrize("bad, state", [(5, HEALTHY), (6, DEGRADED)])
def test_rejection_ratio(settings, bad, state):
    assert ev(settings, 0.2, statuses=["OK"] * (30 - bad) + ["OUT_OF_RANGE"] * bad).state == state


def test_warmup_states(settings):
    assert ev(settings, 0.2, warmup=RECOVERING, warm_count=2).state == RECOVERING
    assert ev(settings, 0.2, warmup=STARTING, warm_count=2).state == STARTING


def test_dashboard_reports_failed_when_collector_is_dead(settings):
    ok = {"state": HEALTHY, "reason": "fine", "data_state": "FRESH"}
    assert dashboard_health(NOW, settings, ok, NOW - 1).state == HEALTHY
    dead = dashboard_health(NOW, settings, ok, NOW - 30)
    assert dead.state == FAILED and "not responding" in dead.reason
    assert dashboard_health(NOW, settings, None, None).state == FAILED


# ---------------------------------------------------------------- replay
def test_schedule_keeps_source_gaps(settings, conn):
    rows = load_source(conn, "practice", "temperature")
    i = start_index_for(rows, 310)
    sched = ReplaySchedule(rows, 120, 60, NOW, start_index_for(rows, 250))
    gap = sched.wall_time(0, i + 1) - sched.wall_time(0, i)
    assert rows[i].sample == 310 and rows[i + 1].sample == 336
    assert gap == pytest.approx(26 * 60 / 120)          # 26-minute source gap -> 13 s with no data


def test_downtime_readings_are_skipped_not_backfilled(settings, conn):
    rows = load_source(conn, "practice", "temperature")
    start = start_index_for(rows, 250)
    sched = ReplaySchedule(rows, 120, 60, NOW, start)
    deliver, skipped, cursor = sched.due((0, start), NOW + 10.0, late_tolerance_s=1.0)
    assert skipped == 18 and len(deliver) == 3         # 21 readings due in 0..10 s: 18 older than 1 s, 3 recent
    assert all(NOW + 10.0 - sched.wall_time(0, i) <= 1.0 for _, i, _ in deliver)


def test_replay_loops_after_last_row(settings, conn):
    rows = load_source(conn, "practice", "temperature")
    sched = ReplaySchedule(rows, 120, 60, NOW, len(rows) - 1)
    assert sched.advance(0, len(rows) - 1) == (1, 0)
    assert sched.wall_time(1, 0) - sched.wall_time(0, len(rows) - 1) == pytest.approx(0.5)
