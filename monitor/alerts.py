"""Threshold alert with two noise-control techniques.

HIGH_TEMP ("lab air too warm"):
  * Rolling threshold - the alert looks at the MEDIAN of the last 5 ACCEPTED readings, never at a
    single reading. At least 3 of 5 readings must be at or above the limit, so one noisy or spiky
    reading cannot raise or clear it. Rejected readings never enter the window.
  * Hysteresis - it ACTIVATES when the median is >= 24.0 C and CLEARS only when the median is
    <= 23.5 C. Between 23.5 and 24.0 the state does not change, so values hovering near the limit
    do not make the alert flap.
  * After a data gap the window is emptied: a decision is made only from 5 new, contiguous readings.
    The alert keeps its state during the gap (it is shown as "data stale - cannot confirm").
"""
import statistics
from collections import deque

ACTIVATED = "ALERT_ACTIVE"
CLEARED = "ALERT_CLEARED"


class RollingMedianAlert:
    def __init__(self, name, on_threshold, off_threshold, window=5, active=False, since=None):
        if not off_threshold < on_threshold:
            raise ValueError("off threshold must be below on threshold")
        self.name = name
        self.on = on_threshold
        self.off = off_threshold
        self.window = deque(maxlen=window)
        self.active = bool(active)
        self.since = since
        self.median = None

    def reset_window(self):
        self.window.clear()
        self.median = None

    def update(self, value, ts):
        """Feed one ACCEPTED value. Returns ACTIVATED, CLEARED or None."""
        self.window.append(value)
        if len(self.window) < self.window.maxlen:
            return None
        self.median = statistics.median(self.window)
        if not self.active and self.median >= self.on:
            self.active, self.since = True, ts
            return ACTIVATED
        if self.active and self.median <= self.off:
            self.active, self.since = False, ts
            return CLEARED
        return None

    def describe(self, latest=None):
        m = f"{self.median:.2f}" if self.median is not None else "n/a"
        if self.active:
            return (f"{self.name} active: median of last {self.window.maxlen} accepted readings {m} C "
                    f">= {self.on:g} C" + (f" (latest {latest:.2f} C)" if latest is not None else ""))
        return (f"{self.name} cleared: median of last {self.window.maxlen} accepted readings {m} C "
                f"<= {self.off:g} C" + (f" (latest {latest:.2f} C)" if latest is not None else ""))
