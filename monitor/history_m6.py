"""SQLite sensor history: load, log, query time windows, detect gaps, report completeness.

Two datasets are stored in one database, each with its own sampling interval:
  practice  - instructor practice CSV, 5 sensors, original timestamps, 1 reading / 60 s
  module5   - my Module 5 Velxio NTC run, 1 reading / 0.5 s; offsets come from the
              board's millis(), the absolute start time is documented (reconstructed)

Rules (also in the README):
  * A reading is RECEIVED if it has a raw value. A row with no value is stored (status MISSING)
    but is not received.
  * accepted_value is set only when status = 'OK'. Nothing is filled or interpolated.
  * Gap: time between two consecutive received readings > GAP_MULTIPLIER x expected interval.
  * Windows are half-open (latest - window, latest], where latest is the newest timestamp
    stored for that dataset in the database.
  * Expected readings in a window = round((latest - effective_start) / interval), with
    effective_start = max(window start, first stored reading - interval), so a window is
    never charged for time before the dataset began.
"""
import csv
import math
import sqlite3
from datetime import datetime, timedelta

GAP_MULTIPLIER = 1.5
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"   # stored with milliseconds so text order = time order

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    name              TEXT PRIMARY KEY,
    description       TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    interval_seconds  REAL NOT NULL CHECK (interval_seconds > 0),
    timestamp_source  TEXT NOT NULL CHECK (timestamp_source IN ('original', 'reconstructed')),
    timestamp_note    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sensor_readings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset          TEXT    NOT NULL REFERENCES datasets(name),
    sample_number    INTEGER NOT NULL,
    timestamp        TEXT    NOT NULL,
    timestamp_source TEXT    NOT NULL CHECK (timestamp_source IN ('original', 'reconstructed')),
    sensor_name      TEXT    NOT NULL,
    unit             TEXT    NOT NULL,
    raw_value        REAL,
    accepted_value   REAL,
    status           TEXT    NOT NULL
                     CHECK (status IN ('OK', 'OUT_OF_RANGE', 'MISSING', 'SUSPICIOUS', 'FAULT')),
    source_status    TEXT,
    status_reason    TEXT,
    UNIQUE (dataset, sensor_name, sample_number),
    CHECK (accepted_value IS NULL OR status = 'OK'),
    CHECK (accepted_value IS NULL OR accepted_value = raw_value)
);
CREATE INDEX IF NOT EXISTS idx_dataset_sensor_time ON sensor_readings(dataset, sensor_name, timestamp);
"""

COLUMNS = ["dataset", "sample_number", "timestamp", "timestamp_source", "sensor_name", "unit",
           "raw_value", "accepted_value", "status", "source_status", "status_reason"]


def fmt_ts(dt):
    return dt.strftime(TS_FORMAT)[:-3]          # millisecond precision


def parse_ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f")


# ---------------------------------------------------------------- loading
PRACTICE_SENSORS = {   # sensor: (value column, status column, unit)
    "temperature":   ("temperature_c", "temperature_status", "C"),
    "humidity":      ("humidity_percent", "humidity_status", "%RH"),
    "light":         ("light_raw", "light_status", "raw counts"),
    "distance":      ("distance_cm", "distance_status", "cm"),
    "potentiometer": ("potentiometer_percent", "potentiometer_status", "%"),
}


def _float_or_none(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def load_practice(path, dataset="practice"):
    """Instructor practice CSV (wide format, original timestamps) -> long-form records."""
    records = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = fmt_ts(datetime.fromisoformat(row["timestamp"]))
            for sensor, (vcol, scol, unit) in PRACTICE_SENSORS.items():
                raw = _float_or_none(row.get(vcol))
                source_status = (row.get(scol) or "").strip().upper() or None
                status, reason = source_status or "MISSING", None
                if raw is None:
                    status, reason = "MISSING", "no value in source file"
                elif sensor == "light" and raw < 0:
                    # A raw ADC count cannot be negative, even though the file says OK.
                    status, reason = "OUT_OF_RANGE", "negative raw light count is impossible (source said OK)"
                elif status not in ("OK", "OUT_OF_RANGE", "MISSING", "SUSPICIOUS", "FAULT"):
                    status, reason = "SUSPICIOUS", f"unknown source status {source_status!r}"
                records.append(dict(
                    dataset=dataset, sample_number=int(row["sample_number"]), timestamp=ts,
                    timestamp_source="original", sensor_name=sensor, unit=unit, raw_value=raw,
                    accepted_value=raw if status == "OK" else None, status=status,
                    source_status=source_status, status_reason=reason))
    return records


MODULE5_STATUS = {"ACCEPT": "OK", "REVIEW": "SUSPICIOUS"}
MODULE5_REJECT_REASON = {"R1": "MISSING", "R2": "FAULT", "R3": "OUT_OF_RANGE"}


def load_module5(path, start, dataset="module5"):
    """Module 5 classified readings -> records. Timestamp = documented start + board millis()."""
    records = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            decision, reason = row["decision"], (row.get("reason") or "").strip()
            status = MODULE5_STATUS.get(decision) or MODULE5_REJECT_REASON.get(reason[:2], "SUSPICIOUS")
            raw = _float_or_none(row.get("temperature_c"))
            if raw is None:
                status = "MISSING"
            ts = start + timedelta(milliseconds=int(row["timestamp_ms"]))
            records.append(dict(
                dataset=dataset, sample_number=int(row["sample"]), timestamp=fmt_ts(ts),
                timestamp_source="reconstructed", sensor_name="velxio_ntc_temperature", unit="C",
                raw_value=raw, accepted_value=raw if status == "OK" else None, status=status,
                source_status=row.get("status") or None,
                status_reason=f"5.1 rule: {decision}" + (f" - {reason}" if reason else "")))
    return records


# ---------------------------------------------------------------- database
def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def register_dataset(conn, name, description, source_file, interval_seconds, timestamp_source, note):
    conn.execute("INSERT OR REPLACE INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                 (name, description, source_file, interval_seconds, timestamp_source, note))


def log_readings(conn, records):
    conn.executemany(
        f"INSERT INTO sensor_readings ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
        [tuple(r[c] for c in COLUMNS) for r in records])
    conn.commit()
    return len(records)


def dataset_info(conn, dataset):
    return dict(conn.execute("SELECT * FROM datasets WHERE name = ?", (dataset,)).fetchone())


def latest_timestamp(conn, dataset):
    return conn.execute("SELECT MAX(timestamp) FROM sensor_readings WHERE dataset = ?",
                        (dataset,)).fetchone()[0]


def first_timestamp(conn, dataset):
    return conn.execute("SELECT MIN(timestamp) FROM sensor_readings WHERE dataset = ?",
                        (dataset,)).fetchone()[0]


def query_window(conn, dataset, hours=None, sensor=None):
    """Rows in (latest - hours, latest] for one dataset; hours=None -> full history."""
    sql = "SELECT * FROM sensor_readings WHERE dataset = ?"
    params = [dataset]
    if hours is not None:
        cutoff = parse_ts(latest_timestamp(conn, dataset)) - timedelta(hours=hours)
        sql += " AND timestamp > ?"
        params.append(fmt_ts(cutoff))
    if sensor is not None:
        sql += " AND sensor_name = ?"
        params.append(sensor)
    sql += " ORDER BY sensor_name, timestamp"
    return [dict(r) for r in conn.execute(sql, params)]


# ---------------------------------------------------------------- gaps and completeness
def detect_gaps(timestamps, interval_s, multiplier=GAP_MULTIPLIER):
    """Gaps between consecutive received timestamps (datetime, sorted)."""
    gaps = []
    for a, b in zip(timestamps, timestamps[1:]):
        delta = (b - a).total_seconds()
        if delta > multiplier * interval_s:
            gaps.append({"after": a, "before": b, "gap_seconds": delta,
                         "missing_readings": max(round(delta / interval_s) - 1, 0)})
    return gaps


def completeness(rows, interval_s, window_start, window_end, dataset_first,
                 multiplier=GAP_MULTIPLIER):
    """Completeness for one sensor in one window. rows: sensor rows inside the window."""
    effective_start = max(window_start, dataset_first - timedelta(seconds=interval_s)) \
        if window_start is not None else dataset_first - timedelta(seconds=interval_s)
    expected = max(round((window_end - effective_start).total_seconds() / interval_s), 0)
    received_ts = sorted(parse_ts(r["timestamp"]) for r in rows if r["raw_value"] is not None)
    received = len(received_ts)
    accepted = sum(r["accepted_value"] is not None for r in rows)
    gaps = detect_gaps(received_ts, interval_s, multiplier)
    status_counts = {}
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    return {
        "expected": expected, "received": received, "missing": max(expected - received, 0),
        "accepted": accepted,
        "completeness_pct": 100.0 * received / expected if expected else float("nan"),
        "accepted_pct": 100.0 * accepted / expected if expected else float("nan"),
        "gap_count": len(gaps),
        "largest_gap_seconds": max((g["gap_seconds"] for g in gaps), default=0.0),
        "gaps": gaps, "status_counts": status_counts,
    }


def window_summary(conn, dataset, hours=None):
    """Per-sensor completeness + value statistics for a window."""
    info = dataset_info(conn, dataset)
    interval = info["interval_seconds"]
    latest = parse_ts(latest_timestamp(conn, dataset))
    first = parse_ts(first_timestamp(conn, dataset))
    start = latest - timedelta(hours=hours) if hours is not None else None
    rows = query_window(conn, dataset, hours)
    out = {}
    for sensor in sorted({r["sensor_name"] for r in rows}):
        srows = [r for r in rows if r["sensor_name"] == sensor]
        c = completeness(srows, interval, start, latest, first)
        vals = sorted(r["accepted_value"] for r in srows if r["accepted_value"] is not None)
        c.update(unit=srows[0]["unit"], rows=len(srows),
                 first=srows[0]["timestamp"], last=srows[-1]["timestamp"],
                 min=vals[0] if vals else None, max=vals[-1] if vals else None,
                 median=(vals[len(vals) // 2] if len(vals) % 2 else
                         (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2) if vals else None)
        out[sensor] = c
    return {"dataset": dataset, "hours": hours, "latest": fmt_ts(latest),
            "window_start": fmt_ts(start) if start else fmt_ts(first), "rows": rows,
            "sensors": out, "info": info}


def gap_broken_series(rows, interval_s, multiplier=GAP_MULTIPLIER, field="accepted_value"):
    """(x, y) lists for plotting where a None is inserted at every gap, so lines break there.

    Uses only received readings for gap detection; non-accepted readings are plotted as
    None (a hole), never replaced.
    """
    xs, ys, prev = [], [], None
    for r in sorted((r for r in rows if r["raw_value"] is not None), key=lambda r: r["timestamp"]):
        t = parse_ts(r["timestamp"])
        if prev is not None and (t - prev).total_seconds() > multiplier * interval_s:
            xs.append(prev + (t - prev) / 2)
            ys.append(None)
        xs.append(t)
        ys.append(r[field])
        prev = t
    return xs, ys
