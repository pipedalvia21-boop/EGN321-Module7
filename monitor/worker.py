"""Collector: replay source -> validation -> SQLite history -> alert logic -> health -> events.

Runs unattended (started by supervisor.py). Each loop it:
  1. delivers the source readings that are due (skips readings that fell due while it was down),
  2. validates and stores every reading (raw value, accepted value, status, reason),
  3. updates the HIGH_TEMP alert from accepted readings only,
  4. checks data freshness and system health, and logs every change,
  5. writes a heartbeat so the dashboard can tell whether the collector is alive.

Stop it cleanly by creating run/stop.flag (the supervisor does this); any other stop is treated
as unexpected, and the next start reports it and enters RECOVERING.
"""
from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import time
from collections import deque

from monitor import db, guard, health
from monitor.alerts import ACTIVATED, CLEARED, RollingMedianAlert
from monitor.config import load_settings
from monitor.logging_setup import configure
from monitor.replay import ReplaySchedule, load_source, start_index_for
from monitor.validation import validate

SOURCE = "collector"


class Worker:
    def __init__(self, settings, conn, clock=time.time, logger=None):
        self.s = settings
        self.conn = conn
        self.clock = clock
        self.log = logger or logging.getLogger("worker")
        self.recent_status = deque(maxlen=settings.bad_window)
        self.last_row_epoch = None
        self.last_accepted_epoch = None
        self.last_accepted_value = None
        self.previous = None                  # (status, raw) of the reading before
        self.warmup = health.STARTING
        self.warm_count = 0
        self.health = None
        self.last_heartbeat = 0.0
        self.readings_processed = 0

    # ------------------------------------------------------------ helpers
    def _ts(self, t=None):
        return db.ts_from_epoch(self.clock() if t is None else t)

    def event(self, level, name, message, t=None):
        db.record_event(self.conn, level, SOURCE, name, message, ts=self._ts(t), logger=self.log)

    # ------------------------------------------------------------ start / stop
    def start(self):
        now = self.clock()
        self.started_epoch = now
        s = self.s
        self.event("INFO", "STARTUP",
                   f"collector starting: source {s.source_dataset}/{s.source_sensor} at {s.replay_speed:g}x, "
                   f"expected interval {s.interval_s:g} s, stale after {s.stale_after_s:g} s, "
                   f"failed after {s.failed_after_s:g} s")

        was_running = db.kv_get(self.conn, "worker_running") == "1"
        last_hb = db.kv_get(self.conn, "worker_heartbeat")
        if was_running:
            down = f"; down about {now - float(last_hb):.0f} s" if last_hb else ""
            self.event("WARNING", "RECOVERY",
                       f"previous collector run ended unexpectedly (last heartbeat "
                       f"{db.ts_from_epoch(float(last_hb)) if last_hb else 'unknown'}{down}); "
                       f"resuming from SQLite state")
            self.warmup = health.RECOVERING
        db.kv_set(self.conn, "worker_running", "1", self._ts(now))
        old_iv = self.conn.execute("SELECT interval_seconds FROM datasets WHERE name = ?", (s.live_dataset,)).fetchone()
        if old_iv and abs(old_iv[0] - s.interval_s) > 1e-9:
            self.event("WARNING", "CONFIG_CHANGE", f"expected interval changed from {old_iv[0]:g} s to {s.interval_s:g} s "
                       "(REPLAY_SPEED changed); older live rows are judged with the new interval")
        db.register_live_dataset(self.conn, s, update=True)

        # restore recent history so freshness and validation continue where they stopped
        for r in db.recent_readings(self.conn, s.live_dataset, s.live_sensor, s.bad_window):
            self.recent_status.append(r["status"])
            self.last_row_epoch = db.epoch(r["timestamp"])
            self.previous = (r["status"], r["raw_value"])
        last_acc = db.last_accepted(self.conn, s.live_dataset, s.live_sensor)
        if last_acc:
            self.last_accepted_epoch = db.epoch(last_acc["timestamp"])
            self.last_accepted_value = last_acc["accepted_value"]

        saved = db.load_alert_state(self.conn, s.alert_name)
        self.alert = RollingMedianAlert(s.alert_name, s.alert_on_c, s.alert_off_c, s.alert_window,
                                        active=bool(saved and saved["active"]), since=saved and saved["since"])
        if saved:
            since = f" since {saved['since']}" if saved["since"] else ""
            self.event("INFO", "ALERT_RESTORED", f"{s.alert_name} state restored from SQLite: "
                       f"{'ACTIVE' if saved['active'] else 'clear'}{since}")

        self._init_schedule(now)

    def _init_schedule(self, now):
        s = self.s
        self.source = load_source(self.conn, s.source_dataset, s.source_sensor)
        config = {"speed": s.replay_speed, "start_sample": s.replay_start_sample, "rows": len(self.source)}
        saved = db.kv_get_json(self.conn, "replay")
        if saved and saved.get("config") == config:
            self.schedule = ReplaySchedule(self.source, s.replay_speed, s.source_interval_s,
                                           saved["anchor_wall"], saved["start_index"])
            self.cursor = tuple(saved["cursor"])
            self.event("INFO", "REPLAY_RESUMED", f"replay resumed at source sample "
                       f"{self.source[self.cursor[1]].sample} (cycle {self.cursor[0]})")
        else:
            start = start_index_for(self.source, s.replay_start_sample)
            self.schedule = ReplaySchedule(self.source, s.replay_speed, s.source_interval_s, now, start)
            self.cursor = (0, start)
            self.event("INFO", "REPLAY_STARTED", f"replay starts at source sample {self.source[start].sample} "
                       f"({self.source[start].source_ts})")
            self._save_cursor()

    def _save_cursor(self):
        db.kv_set(self.conn, "replay", {
            "config": {"speed": self.s.replay_speed, "start_sample": self.s.replay_start_sample,
                       "rows": len(self.source)},
            "anchor_wall": self.schedule.anchor_wall, "start_index": self.schedule.start_index,
            "cursor": list(self.cursor)}, self._ts())

    def stop(self):
        self.event("INFO", "SHUTDOWN", f"collector stopped cleanly after {self.readings_processed} readings")
        db.kv_set(self.conn, "worker_running", "0", self._ts())

    # ------------------------------------------------------------ main step
    def step(self):
        now = self.clock()
        deliver, skipped, cursor = self.schedule.due(self.cursor, now, late_tolerance_s=2 * self.s.interval_s)
        if skipped:
            self.event("WARNING", "DATA_LOST",
                       f"{skipped} readings fell due while the collector was not running; they were not "
                       f"recorded and the gap stays in the history (no backfill)")
        for cycle, index, row in deliver:
            self.process(row, cycle, now)
        if deliver or skipped:
            self.cursor = cursor
            self._save_cursor()
        self.update_health(now)
        if now - self.last_heartbeat >= 1.0:
            db.kv_set(self.conn, "worker_heartbeat", f"{now:.3f}", self._ts(now))
            self.last_heartbeat = now
        return len(deliver)

    def process(self, row, cycle, now):
        s = self.s
        # after a gap, the alert decides only from new contiguous readings
        if self.last_row_epoch is not None and now - self.last_row_epoch > s.stale_after_s:
            self.alert.reset_window()
        d = validate(row.raw_value, s, self.last_accepted_value, self.previous)
        ts = self._ts(now)
        sample = db.next_sample_number(self.conn, s.live_dataset, s.live_sensor)
        db.insert_reading(self.conn, {
            "dataset": s.live_dataset, "sample_number": sample, "timestamp": ts, "timestamp_source": "received",
            "sensor_name": s.live_sensor, "unit": "C", "raw_value": row.raw_value,
            "accepted_value": d.accepted_value, "status": d.status, "source_status": row.source_status,
            "status_reason": d.reason,
            "source_ref": f"{s.source_dataset}#{row.sample} @ {row.source_ts}" + (f" (cycle {cycle})" if cycle else ""),
        })
        self.readings_processed += 1
        self.recent_status.append(d.status)
        self.previous = (d.status, row.raw_value)
        self.last_row_epoch = now
        raw = "none" if row.raw_value is None else f"{row.raw_value:.2f}"
        if d.status == "OK":
            self.log.info("READING sample=%d raw=%s status=OK accepted=%.2f source=%s#%d",
                          sample, raw, d.accepted_value, s.source_dataset, row.sample)
            self.last_accepted_epoch, self.last_accepted_value = now, d.accepted_value
            if self.warmup:
                self.warm_count += 1
            change = self.alert.update(d.accepted_value, ts)
            if change == ACTIVATED:
                self.event("WARNING", ACTIVATED, self.alert.describe(d.accepted_value), now)
            elif change == CLEARED:
                self.event("INFO", CLEARED, self.alert.describe(d.accepted_value), now)
            db.save_alert_state(self.conn, s.alert_name, self.alert.active, self.alert.since, self.alert.median, ts)
        else:
            self.event("WARNING", "READING_REJECTED",
                       f"sample={sample} raw={raw} status={d.status}: {d.reason} (source {s.source_dataset}#{row.sample})",
                       now)

    def update_health(self, now):
        s = self.s
        if self.warmup and self.warm_count >= s.recovery_readings:
            label = self.warmup
            self.warmup, self.warm_count = None, 0
            self.event("INFO", "RECOVERED" if label == health.RECOVERING else "STARTED",
                       f"{s.recovery_readings} fresh accepted readings received: normal operation")
        h = health.evaluate(now, s, last_row_epoch=self.last_row_epoch, last_accepted_epoch=self.last_accepted_epoch,
                            recent_statuses=self.recent_status, warmup=self.warmup, warm_count=self.warm_count,
                            started_epoch=self.started_epoch)
        old = self.health
        if old is None or h.state != old.state or h.data_state != old.data_state:
            self._log_transition(old, h, now)
        if old is None or h != old or now - self.last_heartbeat >= 1.0:
            since = self._ts(now) if old is None or h.state != old.state else self.health_since
            self.health_since = since
            db.kv_set(self.conn, "health", {"state": h.state, "reason": h.reason, "data_state": h.data_state,
                                            "since": since}, self._ts(now))
        self.health = h

    def _log_transition(self, old, new, now):
        last = db.ts_from_epoch(self.last_row_epoch) if self.last_row_epoch else "none"
        if old is not None and new.data_state != old.data_state:
            if new.data_state == health.STALE:
                self.event("WARNING", "DATA_STALE", f"no reading for more than {self.s.stale_after_s:g} s "
                           f"(expected every {self.s.interval_s:g} s); last reading {last}", now)
            elif new.data_state == health.LOST:
                self.event("ERROR", "DATA_MISSING", f"expected data stopped arriving: no reading for more than "
                           f"{self.s.failed_after_s:g} s; last reading {last}", now)
                self.warmup, self.warm_count = health.RECOVERING, 0     # must re-prove itself when data returns
            elif new.data_state == health.FRESH and old.data_state in (health.STALE, health.LOST):
                self.event("INFO", "DATA_RESUMED", "data is arriving again; the missing interval is kept as a gap "
                           "(not filled)", now)
        if old is None or new.state != old.state:
            frm = old.state if old else "(none)"
            self.event(health.LEVEL[new.state], "HEALTH_CHANGE", f"{frm} -> {new.state}: {new.reason}", now)


def main():
    settings = load_settings()
    log = configure("worker", settings)
    db.init_db(settings)
    conn = db.connect(settings.db_path)
    worker = Worker(settings, conn, logger=log)
    stop = {"requested": False}

    def request_stop(*_):
        stop["requested"] = True
    signal.signal(signal.SIGTERM, request_stop)              # systemd / supervisor stop -> clean shutdown
    if os.environ.get("MONITOR_SUPERVISED"):
        signal.signal(signal.SIGINT, signal.SIG_IGN)         # Ctrl+C is handled by the supervisor
    errors = 0
    sup_created = guard.supervisor_create_time()
    last_guard = 0.0
    try:
        worker.start()
        while not settings.stop_flag.exists() and not stop["requested"]:
            if time.time() - last_guard > 2.0:
                last_guard = time.time()
                if not guard.supervisor_alive(created=sup_created):
                    log.error("ORPHANED supervisor is gone; collector exits so the restarted supervisor "
                              "can start a fresh one (no clean-shutdown mark -> next start reports RECOVERY)")
                    sys.exit(3)
            try:
                worker.step()
                errors = 0
            except sqlite3.OperationalError as exc:      # e.g. database briefly locked
                errors += 1
                log.error("APPLICATION_ERROR database error (%d in a row): %s", errors, exc)
                if errors >= 10:
                    raise
                time.sleep(1.0)
            time.sleep(settings.tick_s)
    except Exception:
        log.exception("APPLICATION_ERROR collector crashed; the supervisor will restart it")
        try:
            db.record_event(conn, "ERROR", SOURCE, "APPLICATION_ERROR", "collector crashed (see worker.log)", logger=log)
        finally:
            sys.exit(1)
    worker.stop()


if __name__ == "__main__":
    main()
