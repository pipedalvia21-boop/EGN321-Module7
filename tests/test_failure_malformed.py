"""Assignment 7.2 verification tests: malformed sensor input must not take the system down."""
import shutil
import sqlite3
from pathlib import Path

import pytest

from monitor import db, health
from monitor.validation import validate
from monitor.web import create_app
from monitor.worker import Worker
from scripts.make_malformed_feed import build as build_malformed
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parent.parent
FAILURE_DB = ROOT / "evidence" / "failure_run" / "db_before_recovery.db"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("raw, status, text", [
    ("ERR", "FAULT", "ERR"), ("23.9C", "FAULT", "23.9C"), ("--", "FAULT", "--"), (True, "FAULT", "True"),
    ("", "MISSING", ""), ("nan", "MISSING", "nan"), (None, "MISSING", None), (" 23.9 ", "OK", " 23.9 "),
])
def test_malformed_values_are_classified_not_crashing(settings, raw, status, text):
    d = validate(raw, settings)
    assert (d.status, d.raw_text) == (status, text)
    assert d.accepted_value is None or status == "OK"
    if status == "FAULT":
        assert "malformed value" in d.reason and repr(str(raw)) in d.reason


def test_database_refuses_text_in_numeric_raw_value(conn):
    with pytest.raises(ValueError, match="raw_text"):
        db.insert_reading(conn, {"dataset": "live", "sample_number": 1, "timestamp": "2026-10-18T10:00:00.000",
                                 "timestamp_source": "received", "sensor_name": "temperature", "unit": "C",
                                 "raw_value": "ERR", "status": "FAULT"})


# ---------------------------------------------------------------- collector on the malformed feed
@pytest.fixture
def malformed(tmp_path, clock):
    s = make_settings(tmp_path, SOURCE_DATASET="practice_malformed", REPLAY_SPEED=60, REPLAY_START_SAMPLE=390)
    db.init_db(s)
    c = db.connect(s.db_path)
    build_malformed(c)
    w = Worker(s, c, clock=clock)
    w.start()
    return s, c, w


def run(w, clock, seconds, step=0.25):
    states = []
    for _ in range(int(seconds / step)):
        clock.advance(step)
        w.step()                                       # before the fix this raised ValueError at sample 400
        if not states or states[-1] != w.health.state:
            states.append(w.health.state)
    return states


def test_collector_survives_the_whole_malformed_block(malformed, clock):
    s, c, w = malformed
    states = run(w, clock, 110)                         # samples 390-499: 60 malformed in the middle
    rows = c.execute("SELECT raw_value, raw_text, accepted_value, status, status_reason, source_ref "
                     "FROM sensor_readings WHERE dataset = 'live' ORDER BY id").fetchall()
    bad = [r for r in rows if r["raw_text"] is not None and r["status"] != "OK"]
    assert len(bad) == 60                               # every malformed reading stored once, with what arrived
    assert all(r["raw_value"] is None and r["accepted_value"] is None for r in bad)
    assert {r["status"] for r in bad} == {"FAULT", "MISSING"}
    refs = [r["source_ref"] for r in rows]
    assert len(refs) == len(set(refs))                  # no duplicates (the failure run stored sample 400 twice)
    assert "DEGRADED" in states and states[-1] == "HEALTHY"
    assert "FAILED" not in states
    assert c.execute("SELECT COUNT(*) FROM events WHERE event IN ('APPLICATION_ERROR', 'READING_ERROR')"
                     ).fetchone()[0] == 0


def test_rejected_reading_log_shows_exact_text(malformed, clock):
    s, c, w = malformed
    run(w, clock, 20)                                   # samples 390-409 ('' is sample 403)
    msgs = [e["message"] for e in db.recent_events(c, 500) if e["event"] == "READING_REJECTED"]
    assert any("raw='ERR'" in m and "malformed value 'ERR'" in m and "practice_malformed#400" in m for m in msgs)
    assert any("raw=''" in m for m in msgs)            # empty line is distinguishable from 'ERR' and from None


def test_unexpected_error_in_one_reading_is_isolated_and_not_redelivered(malformed, clock, monkeypatch):
    s, c, w = malformed
    original = w.process
    calls = []

    def flaky(row, cycle, now):
        calls.append(row.sample)
        if row.sample == 395:
            raise RuntimeError("boom")
        return original(row, cycle, now)
    monkeypatch.setattr(w, "process", flaky)
    run(w, clock, 10)
    assert calls.count(395) == 1                        # skipped, never retried
    ev = [e for e in db.recent_events(c, 100) if e["event"] == "READING_ERROR"]
    assert len(ev) == 1
    m = ev[0]["message"]
    assert "component=collector" in m and "practice_malformed#395" in m and "RuntimeError: boom" in m
    assert w.health.state != "FAILED"


def test_cursor_saved_before_processing(malformed, clock):
    s, c, w = malformed
    run(w, clock, 3)
    saved = db.kv_get_json(c, "replay")["cursor"]
    assert tuple(saved) == w.cursor


# ---------------------------------------------------------------- dashboard
def test_dashboard_reports_crash_loop_clearly(settings):
    now = 1_800_000_000.0
    loop = {"child": "worker", "at": now - 5, "exits": 6, "last_exit": "exit code 1"}
    h = health.dashboard_health(now, settings, {"state": "HEALTHY", "reason": "x", "data_state": "FRESH"},
                                now - 40, crash_loop=loop)
    assert h.state == "FAILED" and "crash-loop guard" in h.reason and "human intervention" in h.reason


def test_dashboard_not_healthy_while_collector_restarting(settings):
    now = 1_800_000_000.0
    ok = {"state": "HEALTHY", "reason": "x", "data_state": "FRESH"}
    exits = {"times": [now - 2], "last_exit": "exit code 1"}
    h = health.dashboard_health(now, settings, ok, now - 3, now - 1, worker_exits=exits)
    assert h.state == "DEGRADED" and "restarting" in h.reason     # was HEALTHY in the failure run
    one_old_exit = {"times": [now - 20], "last_exit": "killed by signal 9"}
    assert health.dashboard_health(now, settings, ok, now - 0.5, now - 0.4,
                                   worker_exits=one_old_exit).state == "HEALTHY"


def test_dashboard_logs_health_it_decides_itself(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    for _ in range(40):
        clock.advance(0.1)
        w.step()
    client = create_app(settings, clock=clock).test_client()
    client.get("/api/status")
    clock.advance(60)                                    # collector silent
    client.get("/api/status")
    msgs = [e["message"] for e in db.recent_events(conn, 20) if e["event"] == "DASHBOARD_HEALTH"]
    assert any("-> FAILED" in m and "not responding" in m for m in msgs)


@pytest.mark.skipif(not FAILURE_DB.exists(), reason="failure-run evidence not present")
def test_old_database_from_failure_run_is_migrated_and_renders(tmp_path, clock):
    """The real database saved during the failure (text 'ERR' in raw_value, no raw_text column)."""
    copy = tmp_path / "monitor.db"
    shutil.copy(FAILURE_DB, copy)
    old_cols = [r[1] for r in sqlite3.connect(copy).execute("PRAGMA table_info(sensor_readings)")]
    assert "raw_text" not in old_cols
    s = make_settings(tmp_path, SOURCE_DATASET="practice_malformed", REPLAY_SPEED=60, REPLAY_START_SAMPLE=370)
    db.init_db(s)
    c = db.connect(copy)
    assert "raw_text" in [r[1] for r in c.execute("PRAGMA table_info(sensor_readings)")]
    assert c.execute("SELECT COUNT(*) FROM sensor_readings WHERE dataset = 'live' "
                     "AND typeof(raw_value) = 'text'").fetchone()[0] == 6
    last = c.execute("SELECT MAX(timestamp) FROM sensor_readings WHERE dataset='live'").fetchone()[0]
    clock.t = db.epoch(last) + 1
    client = create_app(s, clock=clock).test_client()
    assert client.get("/").status_code == 200           # was HTTP 500 for 12 readings after recovery
    assert client.get("/chart/live.svg?minutes=5").status_code == 200   # was HTTP 500 for 5 minutes
    shown = [r["raw_display"] for r in client.get("/api/status").get_json()["recent"]]
    assert "'ERR'" in shown
