"""Collector end-to-end with a fake clock: alert, stale data, missing data, restart and recovery."""
from monitor import db
from monitor.health import DEGRADED, FAILED, HEALTHY, RECOVERING
from monitor.history_m6 import window_summary
from monitor.worker import Worker


def run(worker, clock, seconds, step=0.1):
    states = []
    for _ in range(int(seconds / step)):
        clock.advance(step)
        worker.step()
        if not states or states[-1] != worker.health.state:
            states.append(worker.health.state)
    return states


def events(conn):
    return [e["event"] for e in reversed(db.recent_events(conn, 500))]


def test_full_cycle_alert_stale_missing_recovered(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    states = run(w, clock, 60)            # samples 250 .. ~400 incl. the 26-min source gap (13 s)
    ev = events(conn)
    assert "ALERT_ACTIVE" in ev
    for name in ("DATA_STALE", "DATA_MISSING", "DATA_RESUMED", "RECOVERED"):
        assert name in ev
    assert ev.index("DATA_STALE") < ev.index("DATA_MISSING") < ev.index("DATA_RESUMED") < ev.index("RECOVERED")
    assert [s for s in states if s in (HEALTHY, DEGRADED, FAILED, RECOVERING)][:5] == \
        [HEALTHY, DEGRADED, FAILED, RECOVERING, HEALTHY]
    # the gap stays a gap: no rows were invented for it
    ts = [db.epoch(r[0]) for r in conn.execute(
        "SELECT timestamp FROM sensor_readings WHERE dataset='live' ORDER BY timestamp")]
    assert max(b - a for a, b in zip(ts, ts[1:])) > 12.5


def test_stale_data_keeps_alert_but_resets_window(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    run(w, clock, 25)                     # alert active, then source gap begins (~sample 310)
    assert w.alert.active
    run(w, clock, 10)                     # inside the 13 s gap
    assert w.alert.active and w.health.state in (DEGRADED, FAILED)


def test_restart_after_kill_recovers_and_does_not_backfill(settings, conn, clock):
    w1 = Worker(settings, conn, clock=clock)
    w1.start()
    run(w1, clock, 5)
    before = conn.execute("SELECT COUNT(*) FROM sensor_readings WHERE dataset='live'").fetchone()[0]
    alert_before = w1.alert.active
    # simulated kill: no stop() call; 10 s pass with nothing running
    clock.advance(10)
    w2 = Worker(settings, conn, clock=clock)
    w2.start()
    assert w2.warmup == RECOVERING
    assert w2.alert.active == alert_before                       # alert state restored, not reset
    w2.step()
    ev = events(conn)
    assert "RECOVERY" in ev and "DATA_LOST" in ev and "REPLAY_RESUMED" in ev
    after = conn.execute("SELECT COUNT(*) FROM sensor_readings WHERE dataset='live'").fetchone()[0]
    assert after - before <= 2                                   # nothing from the downtime was backfilled
    nums = [r[0] for r in conn.execute("SELECT sample_number FROM sensor_readings WHERE dataset='live' ORDER BY id")]
    assert nums == list(range(1, len(nums) + 1))                 # numbering continues, no duplicates
    states = run(w2, clock, 4)
    assert RECOVERING in states and states[-1] == HEALTHY
    assert window_summary(conn, "practice", 24)["sensors"]["temperature"]["received"] == 1420


def test_clean_stop_is_not_reported_as_recovery(settings, conn, clock):
    w1 = Worker(settings, conn, clock=clock)
    w1.start()
    run(w1, clock, 2)
    w1.stop()
    w2 = Worker(settings, conn, clock=clock)
    w2.start()
    assert w2.warmup != RECOVERING
    assert "RECOVERY" not in events(conn)
