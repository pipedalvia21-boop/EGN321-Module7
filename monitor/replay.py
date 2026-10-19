"""Replay source: plays the Module 6 history back in real time.

The source rows (Module 6 'practice' temperature, 1 reading per 60 s, with two real gaps of
25 and 20 minutes) are scheduled on the wall clock:

    wall_time(row) = anchor + (source offset of row - offset of start row) / speed

so a source gap becomes a real period with no data at the monitor (that is how stale and
missing data happen naturally). After the last row the replay loops (unattended operation).

The anchor is stored in SQLite. After a crash the collector keeps the same anchor, so readings
whose time passed while it was down are SKIPPED, not delivered late: a real sensor's readings
during an outage are lost, and the gap stays visible in the history.
"""
from __future__ import annotations

from dataclasses import dataclass

from monitor.db import epoch


@dataclass(frozen=True)
class SourceRow:
    sample: int
    source_ts: str
    raw_value: float | None
    source_status: str
    offset_s: float


def load_source(conn, dataset, sensor) -> list[SourceRow]:
    rows = conn.execute("SELECT sample_number, timestamp, raw_value, status FROM sensor_readings "
                        "WHERE dataset = ? AND sensor_name = ? ORDER BY timestamp, id", (dataset, sensor)).fetchall()
    if not rows:
        raise ValueError(f"replay source {dataset}/{sensor} has no rows")
    t0 = epoch(rows[0]["timestamp"])
    return [SourceRow(r["sample_number"], r["timestamp"], r["raw_value"], r["status"], epoch(r["timestamp"]) - t0)
            for r in rows]


class ReplaySchedule:
    def __init__(self, rows: list[SourceRow], speed: float, source_interval_s: float,
                 anchor_wall: float, start_index: int = 0):
        self.rows = rows
        self.speed = speed
        self.anchor_wall = anchor_wall
        self.start_index = start_index
        # one full cycle in source seconds (the step from the last row back to the first)
        self.span = rows[-1].offset_s - rows[0].offset_s + source_interval_s

    def wall_time(self, cycle: int, index: int) -> float:
        return self.anchor_wall + (cycle * self.span + self.rows[index].offset_s
                                   - self.rows[self.start_index].offset_s) / self.speed

    def advance(self, cycle: int, index: int) -> tuple[int, int]:
        return (cycle, index + 1) if index + 1 < len(self.rows) else (cycle + 1, 0)

    def due(self, cursor: tuple[int, int], now: float, late_tolerance_s: float):
        """Rows due at `now` from `cursor`. Returns (deliver list, skipped count, new cursor).

        A row more than `late_tolerance_s` overdue was due while the collector was not running:
        it is skipped (counted) instead of being delivered late with a wrong timestamp."""
        deliver, skipped = [], 0
        cycle, index = cursor
        while self.wall_time(cycle, index) <= now:
            if now - self.wall_time(cycle, index) > late_tolerance_s:
                skipped += 1
            else:
                deliver.append((cycle, index, self.rows[index]))
            cycle, index = self.advance(cycle, index)
            if skipped > 10 * len(self.rows):      # very long outage: jump ahead, do not spin
                break
        return deliver, skipped, (cycle, index)


def start_index_for(rows: list[SourceRow], sample: int) -> int:
    for i, r in enumerate(rows):
        if r.sample >= sample:
            return i
    return 0
