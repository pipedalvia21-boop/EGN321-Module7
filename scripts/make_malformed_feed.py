"""Failure injection for Assignment 7.2: build a sensor feed that contains malformed records.

    python -m scripts.make_malformed_feed            # samples 400-459 become malformed

Creates dataset 'practice_malformed' in data/monitor.db: an exact copy of the Module 6
'practice/temperature' feed, except that 60 consecutive readings (samples 400-459,
2026-10-01 14:19-15:18 in source time) are replaced by what a serial temperature sensor
sends when its probe is unplugged or the line is noisy: 'ERR', '' (empty line), '23.9C'
(value with unit glued on) and 'nan'. Everything else is untouched; the original
'practice' dataset is not modified.

Run the collector on it with SOURCE_DATASET=practice_malformed.
"""
import sys

from monitor import db
from monitor.config import load_settings

FIRST, LAST = 400, 459
PATTERN = ["ERR", "ERR", "ERR", "", "ERR", "23.9C", "ERR", "nan", "ERR", "ERR"]
NAME = "practice_malformed"


def build(conn, first=FIRST, last=LAST):
    conn.execute("DELETE FROM sensor_readings WHERE dataset = ?", (NAME,))
    conn.execute("INSERT OR REPLACE INTO datasets SELECT ?, ?, source_file, interval_seconds, timestamp_source, "
                 "timestamp_note FROM datasets WHERE name = 'practice'",
                 (NAME, f"FAULT INJECTION: copy of practice/temperature with samples {first}-{last} replaced by "
                        f"malformed serial output ({', '.join(repr(v) for v in dict.fromkeys(PATTERN))})"))
    rows = conn.execute("SELECT sample_number, timestamp, raw_value FROM sensor_readings WHERE dataset='practice' "
                        "AND sensor_name='temperature' ORDER BY timestamp").fetchall()
    changed = 0
    for sample, ts, raw in rows:
        if first <= sample <= last:
            raw, changed = PATTERN[changed % len(PATTERN)], changed + 1
        conn.execute("INSERT INTO sensor_readings (dataset, sample_number, timestamp, timestamp_source, sensor_name, "
                     "unit, raw_value, accepted_value, status, source_status, status_reason) "
                     "VALUES (?, ?, ?, 'original', 'temperature', 'C', ?, NULL, 'OK', 'OK', ?)",
                     (NAME, sample, ts, raw, "injected malformed value" if first <= sample <= last else None))
    return len(rows), changed


if __name__ == "__main__":
    settings = load_settings()
    db.init_db(settings)
    c = db.connect(settings.db_path)
    total, changed = build(c)
    print(f"{NAME}: {total} rows, {changed} malformed (samples {FIRST}-{LAST})")
    sys.exit(0)
