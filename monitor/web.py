"""Flask dashboard (served by waitress, a production WSGI server that also runs on Windows).

Pages
  /                 live dashboard: current value, latest timestamp, data status, alert, health,
                    last-5-minute chart with gaps preserved, recent readings, events
  /history          Module 6 history and live history: last hour / last day / full history
  /logs             tail of the log files + events table
JSON / images
  /api/status       everything the dashboard shows
  /healthz          liveness check used by the recovery test
  /chart/<dataset>.svg
"""
from __future__ import annotations

import logging
import time
from collections import Counter, deque
from datetime import datetime, timedelta

from flask import Flask, Response, abort, g, jsonify, render_template, request

from monitor import db, health
from monitor.charts import timeseries_svg
from monitor.config import load_settings
from monitor.history_m6 import completeness, parse_ts, query_window, window_summary
from monitor.logging_setup import configure

WINDOWS = {"hour": ("Last hour", 1), "day": ("Last day", 24), "full": ("Full history", None)}
LOG_FILES = ("supervisor", "worker", "web")


def create_app(settings=None, clock=time.time):
    settings = settings or load_settings()
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    log = logging.getLogger("web")

    def conn():
        if "conn" not in g:
            g.conn = db.connect(settings.db_path)
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    @app.errorhandler(Exception)
    def on_error(exc):
        code = getattr(exc, "code", 500)
        if code and code < 500:
            return exc
        log.exception("APPLICATION_ERROR request %s failed", request.path)
        try:
            db.record_event(conn(), "ERROR", "dashboard", "APPLICATION_ERROR",
                            f"{request.path}: {type(exc).__name__}: {exc}", logger=log)
        except Exception:      # noqa: BLE001 - the log line above is what matters
            pass
        return Response("Internal error - see logs/web.log", status=500)

    # ------------------------------------------------------------ status
    def thresholds():
        return [(settings.alert_on_c, f"alert on: median ≥ {settings.alert_on_c:g} °C", "on"),
                (settings.alert_off_c, f"clears: median ≤ {settings.alert_off_c:g} °C", "off")]

    def build_status():
        now = clock()
        now_dt = datetime.fromtimestamp(now)
        c = conn()
        hb = db.kv_get(c, "worker_heartbeat")
        hb = float(hb) if hb else None
        ds, sensor = settings.live_dataset, settings.live_sensor
        recent = db.recent_readings(c, ds, sensor, 30)
        latest = recent[-1] if recent else None
        worker_health = db.kv_get_json(c, "health")
        h = health.dashboard_health(now, settings, worker_health, hb,
                                    db.epoch(latest["timestamp"]) if latest else None)
        since = worker_health.get("since") if worker_health and h.state == worker_health.get("state") else None
        acc = db.last_accepted(c, ds, sensor)
        latest_age = now - db.epoch(latest["timestamp"]) if latest else None
        acc_age = now - db.epoch(acc["timestamp"]) if acc else None
        fresh = latest_age is not None and latest_age <= settings.stale_after_s and h.state != health.FAILED

        alert_row = db.load_alert_state(c, settings.alert_name)
        five = (now_dt - timedelta(minutes=5))
        win_rows = db.readings_since(c, ds, sensor, db.fmt_ts(five))
        first = c.execute("SELECT MIN(timestamp) FROM sensor_readings WHERE dataset = ?", (ds,)).fetchone()[0]
        comp = None
        if first:
            # the window ends now if data is overdue (the open gap counts as missing); otherwise at the
            # latest reading, so a reading that is not due yet is not counted as missing
            overdue = latest and latest_age > 1.5 * settings.interval_s
            end_dt = now_dt if overdue or not latest else parse_ts(latest["timestamp"])
            comp = completeness(win_rows, settings.interval_s, five, end_dt, parse_ts(first))
            largest = comp["largest_gap_seconds"]
            ongoing = None
            if latest and latest_age > 1.5 * settings.interval_s:
                ongoing = latest_age
                largest = max(largest, latest_age)
            comp = {"expected": comp["expected"], "received": comp["received"], "missing": comp["missing"],
                    "accepted": comp["accepted"],
                    "completeness_pct": round(comp["completeness_pct"], 1) if comp["expected"] else None,
                    "gap_count": comp["gap_count"] + (1 if ongoing else 0),
                    "largest_gap_s": round(largest, 1), "ongoing_gap_s": round(ongoing, 1) if ongoing else None,
                    "status_counts": comp["status_counts"]}
        return {
            "server_time": db.fmt_ts(now_dt),
            "settings": {"interval_s": settings.interval_s, "stale_after_s": settings.stale_after_s,
                         "failed_after_s": settings.failed_after_s, "replay_speed": settings.replay_speed,
                         "alert_on_c": settings.alert_on_c, "alert_off_c": settings.alert_off_c,
                         "alert_window": settings.alert_window, "heartbeat_timeout_s": settings.heartbeat_timeout_s},
            "health": {"state": h.state, "reason": h.reason, "data_state": h.data_state, "since": since},
            "heartbeat_age_s": None if hb is None else round(now - hb, 1),
            "latest_row": None if not latest else {
                "timestamp": latest["timestamp"], "raw_value": latest["raw_value"], "status": latest["status"],
                "status_reason": latest["status_reason"], "age_s": round(latest_age, 1),
                "source_ref": latest["source_ref"]},
            "current": None if not acc else {
                "value": acc["accepted_value"], "timestamp": acc["timestamp"], "age_s": round(acc_age, 1),
                "is_current": bool(fresh and acc_age <= settings.stale_after_s)},
            "alert": {"name": settings.alert_name, "active": bool(alert_row and alert_row["active"]),
                      "since": alert_row["since"] if alert_row else None,
                      "median": alert_row["last_median"] if alert_row else None,
                      "on_c": settings.alert_on_c, "off_c": settings.alert_off_c, "data_fresh": fresh},
            "window_5min": comp,
            "status_counts_last_30": dict(Counter(r["status"] for r in recent)),
            "recent": [{k: r[k] for k in ("sample_number", "timestamp", "raw_value", "accepted_value", "status",
                                          "status_reason", "source_ref")} for r in recent[-12:][::-1]],
            "events": db.recent_events(c, 15),
        }

    @app.get("/api/status")
    def api_status():
        return jsonify(build_status())

    @app.get("/healthz")
    def healthz():
        s = build_status()
        return jsonify({"web": "ok", "health": s["health"]["state"], "reason": s["health"]["reason"],
                        "heartbeat_age_s": s["heartbeat_age_s"], "alert_active": s["alert"]["active"],
                        "server_time": s["server_time"]})

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html", s=build_status(), settings=settings)

    # ------------------------------------------------------------ charts
    @app.get("/chart/<dataset>.svg")
    def chart(dataset):
        c = conn()
        info = c.execute("SELECT * FROM datasets WHERE name = ?", (dataset,)).fetchone()
        if not info:
            abort(404)
        sensor = request.args.get("sensor", "temperature")
        if dataset == settings.live_dataset and "minutes" in request.args:
            minutes = max(1, min(int(request.args.get("minutes", 5)), 24 * 60))
            end = datetime.fromtimestamp(clock())
            start = end - timedelta(minutes=minutes)
            first = c.execute("SELECT MIN(timestamp) FROM sensor_readings WHERE dataset = ?", (dataset,)).fetchone()[0]
            if first:      # a young system: start the axis at its first reading, not at empty time
                start = max(start, parse_ts(first) - timedelta(seconds=info["interval_seconds"]))
            rows = db.readings_since(c, dataset, sensor, db.fmt_ts(start))
            svg = timeseries_svg(rows, info["interval_seconds"], thresholds=thresholds(), start=start, end=end,
                                 stale_after_s=settings.stale_after_s, title=f"live {sensor}, last {minutes} min")
        else:
            window = request.args.get("window", "full")
            hours = WINDOWS.get(window, WINDOWS["full"])[1]
            latest = db.parse_ts(c.execute("SELECT MAX(timestamp) FROM sensor_readings WHERE dataset = ?",
                                           (dataset,)).fetchone()[0])
            rows = query_window(c, dataset, hours, sensor)
            start = latest - timedelta(hours=hours) if hours else None
            first = c.execute("SELECT MIN(timestamp) FROM sensor_readings WHERE dataset = ?", (dataset,)).fetchone()[0]
            if start is not None and first:   # same rule as the completeness table: not before the data began
                start = max(start, parse_ts(first) - timedelta(seconds=info["interval_seconds"]))
            th = thresholds() if sensor == "temperature" else ()
            svg = timeseries_svg(rows, info["interval_seconds"], thresholds=th, start=start, end=latest,
                                 unit=rows[0]["unit"] if rows else "", title=f"{dataset} {sensor} {window}")
        return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ history
    @app.get("/history")
    def history():
        c = conn()
        datasets = [dict(r) for r in c.execute("SELECT * FROM datasets ORDER BY name")]
        names = [d["name"] for d in datasets]
        dataset = request.args.get("dataset", "practice")
        if dataset not in names:
            abort(404)
        window = request.args.get("window", "day")
        if window not in WINDOWS:
            abort(404)
        has_rows = c.execute("SELECT 1 FROM sensor_readings WHERE dataset = ? LIMIT 1", (dataset,)).fetchone()
        summary = window_summary(c, dataset, WINDOWS[window][1]) if has_rows else None
        sensors = sorted(summary["sensors"]) if summary else []
        sensor = request.args.get("sensor", "temperature" if "temperature" in sensors else (sensors[0] if sensors else ""))
        return render_template("history.html", datasets=datasets, dataset=dataset, window=window, windows=WINDOWS,
                               summary=summary, sensors=sensors, sensor=sensor, settings=settings)

    # ------------------------------------------------------------ logs
    @app.get("/logs")
    def logs():
        tails = {}
        for name in LOG_FILES:
            path = settings.log_dir / f"{name}.log"
            if path.exists():
                with open(path, encoding="utf-8", errors="replace") as f:
                    tails[name] = list(deque(f, maxlen=120))
            else:
                tails[name] = []
        return render_template("logs.html", tails=tails, events=db.recent_events(conn(), 120),
                               log_dir=settings.log_dir, settings=settings)

    return app


def main():
    import os
    import signal
    if os.environ.get("MONITOR_SUPERVISED"):
        signal.signal(signal.SIGINT, signal.SIG_IGN)         # Ctrl+C is handled by the supervisor
    settings = load_settings()
    log = configure("web", settings)
    db.init_db(settings)
    app = create_app(settings)
    c = db.connect(settings.db_path)
    db.record_event(c, "INFO", "dashboard", "STARTUP",
                    f"dashboard listening on http://{settings.web_host}:{settings.web_port}", logger=log)
    c.close()
    from monitor import guard

    def orphaned():
        log.error("ORPHANED supervisor is gone; dashboard exits and frees port %s", settings.web_port)
        os._exit(3)
    guard.start_watchdog_thread(orphaned)
    from waitress import serve
    serve(app, host=settings.web_host, port=settings.web_port, threads=6, ident="sensor-monitor")


if __name__ == "__main__":
    main()
