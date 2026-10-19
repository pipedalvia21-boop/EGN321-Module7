"""SQLite integration: Module 6 history preserved, live readings logged with timestamp/raw/accepted/status."""
import sqlite3

import pytest

from monitor import db
from monitor.history_m6 import window_summary
from monitor.worker import Worker


def test_module6_history_imported_unchanged(settings, conn):
    src = sqlite3.connect(settings.module6_db)
    cols = ", ".join(db.M6_COLUMNS)
    original = src.execute(f"SELECT {cols} FROM sensor_readings ORDER BY id").fetchall()
    copied = [tuple(r) for r in conn.execute(
        f"SELECT {cols} FROM sensor_readings WHERE dataset != 'live' ORDER BY id")]
    assert len(original) == 8900
    assert copied == original


def test_init_is_idempotent(settings, conn):
    db.init_db(settings)
    db.init_db(settings)
    assert conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0] == 8900


def test_module6_windows_still_work(conn):
    day = window_summary(conn, "practice", 24)["sensors"]["temperature"]
    assert (day["expected"], day["received"], day["missing"]) == (1440, 1420, 20)


def test_live_reading_logged_with_all_fields(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    w.step()                                     # the first source row is due at start
    row = dict(conn.execute("SELECT * FROM sensor_readings WHERE dataset = 'live'").fetchone())
    assert row["timestamp"] == db.ts_from_epoch(clock())
    assert row["timestamp_source"] == "received"
    assert row["raw_value"] == row["accepted_value"] and row["status"] == "OK"
    assert row["source_ref"].startswith("practice#250 @ 2026-10-01T12:09:00")
    assert row["unit"] == "C"


def test_rejected_reading_keeps_raw_value_but_no_accepted_value(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_reading(conn, {"dataset": "live", "sample_number": 1, "timestamp": "2026-10-18T10:00:00.000",
                                 "timestamp_source": "received", "sensor_name": "temperature", "unit": "C",
                                 "raw_value": 58.8, "accepted_value": 58.8, "status": "OUT_OF_RANGE"})


def test_missing_reading_stored_as_missing_not_filled(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    from monitor.replay import SourceRow
    w.process(SourceRow(999, "2026-10-01T00:00:00.000", None, "OK", 0.0), 0, clock())
    row = dict(conn.execute("SELECT * FROM sensor_readings WHERE dataset = 'live'").fetchone())
    assert (row["raw_value"], row["accepted_value"], row["status"]) == (None, None, "MISSING")


def test_events_are_stored(settings, conn, clock):
    w = Worker(settings, conn, clock=clock)
    w.start()
    names = [e["event"] for e in db.recent_events(conn, 10)]
    assert "STARTUP" in names and "REPLAY_STARTED" in names
