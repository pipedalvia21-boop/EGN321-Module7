"""Reproducible database generation.

    python init_db.py            # create data/monitor.db from data/module6_sensor_history.db (if missing)
    python init_db.py --reset    # delete it and rebuild from scratch (live history, events, alert state are lost)

The supervisor and the collector also run this automatically on start, so a fresh clone works.
"""
import sys

from monitor import db
from monitor.config import load_settings


def main(argv):
    settings = load_settings()
    summary = db.init_db(settings, reset="--reset" in argv)
    conn = db.connect(settings.db_path)
    counts = conn.execute("SELECT dataset, COUNT(*) FROM sensor_readings GROUP BY dataset ORDER BY dataset").fetchall()
    print(f"Database: {settings.db_path}")
    print(f"Imported from Module 6 in this run: {summary['imported_module6_rows']} rows")
    for name, n in counts:
        print(f"  {name:10s} {n:6d} rows")


if __name__ == "__main__":
    main(sys.argv)
