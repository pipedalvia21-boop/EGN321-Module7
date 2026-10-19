"""Validation rule and threshold alert (rolling median + hysteresis)."""
import sqlite3

import pytest

from monitor.alerts import ACTIVATED, CLEARED, RollingMedianAlert
from monitor.validation import validate


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("raw, status", [(23.9, "OK"), (0.0, "OK"), (50.0, "OK"), (None, "MISSING"),
                                         (float("nan"), "MISSING"), (50.01, "OUT_OF_RANGE"), (-0.5, "OUT_OF_RANGE"),
                                         (58.8, "OUT_OF_RANGE"), (130.0, "FAULT"), (-273.15, "FAULT")])
def test_validation_limits(settings, raw, status):
    d = validate(raw, settings, last_accepted=None)
    assert d.status == status
    assert (d.accepted_value is not None) == (status == "OK")


def test_spike_is_suspicious_and_real_level_change_is_confirmed(settings):
    first = validate(30.5, settings, last_accepted=24.0, previous=("OK", 24.0))
    assert first.status == "SUSPICIOUS"
    second = validate(30.4, settings, last_accepted=24.0, previous=("SUSPICIOUS", 30.5))
    assert second.status == "OK" and "confirmed" in second.reason
    back = validate(24.1, settings, last_accepted=24.0, previous=("SUSPICIOUS", 30.5))
    assert back.status == "OK" and back.reason is None


# ---------------------------------------------------------------- alert
def feed(alert, values):
    return [alert.update(v, f"t{i}") for i, v in enumerate(values)]


def new_alert():
    return RollingMedianAlert("HIGH_TEMP", 24.0, 23.5, 5)


def test_single_noisy_reading_cannot_raise_alert():
    a = new_alert()
    assert feed(a, [23.0, 23.0, 23.0, 23.0, 26.0, 23.0]) == [None] * 6
    assert not a.active


def test_needs_three_of_five_above_threshold():
    a = new_alert()
    assert feed(a, [23.0, 23.0, 23.0, 24.5, 24.5]) == [None] * 5       # 2 of 5 -> no
    assert a.update(24.5, "t") == ACTIVATED                             # 3 of 5 -> yes


def test_threshold_boundary_is_inclusive():
    a = new_alert()
    feed(a, [24.0] * 5)
    assert a.active
    b = new_alert()
    feed(b, [23.99] * 5)
    assert not b.active


def test_hysteresis_holds_between_limits_and_clears_at_off_limit():
    a = new_alert()
    feed(a, [24.2] * 5)
    assert a.active
    assert feed(a, [23.8] * 5)[-1] is None and a.active                # 23.5 < 23.8 < 24.0: stays active
    assert CLEARED in feed(a, [23.5] * 5)                              # median reaches 23.5 -> clears
    assert not a.active


def test_noise_around_threshold_does_not_flap():
    a = new_alert()
    noisy = [24.0 + (0.3 if i % 2 else -0.3) for i in range(200)]       # 23.7 / 24.3 alternating
    changes = [c for c in feed(a, noisy) if c]
    assert len(changes) <= 1


def test_window_reset_after_gap_requires_five_new_readings():
    a = new_alert()
    feed(a, [24.5] * 5)
    a.reset_window()
    assert feed(a, [23.0] * 4) == [None] * 4 and a.active               # still active until 5 new readings
    assert a.update(23.0, "t") == CLEARED


def test_alert_on_module6_history(settings):
    """Whole Module 6 practice temperature history: exactly ON, OFF, ON - no flapping."""
    rows = sqlite3.connect(settings.module6_db).execute(
        "SELECT timestamp, raw_value, status FROM sensor_readings WHERE dataset='practice' "
        "AND sensor_name='temperature' ORDER BY timestamp").fetchall()
    a = new_alert()
    events = [(c, ts) for ts, v, st in rows if st == "OK" for c in [a.update(v, ts)] if c]
    assert [c for c, _ in events] == [ACTIVATED, CLEARED, ACTIVATED]
    assert events[0][1] == "2026-10-01T12:15:00.000" and events[1][1] == "2026-10-01T16:56:00.000"
