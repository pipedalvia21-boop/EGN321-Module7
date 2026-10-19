"""Validation of each live reading before it is stored or used (same limits as Modules 5 and 6).

Order of checks:
  MISSING      no value: None, an empty line, or NaN ("sensor had no measurement")
  FAULT        malformed: text that is not a number ('ERR', '23.9C') - added after the 7.2 failure report
  FAULT        outside what the sensor can physically report (-40 .. 125 C)
  OUT_OF_RANGE outside the supported room range (0 .. 50 C)
  SUSPICIOUS   jumps more than 5 C from the last accepted reading
               -- unless the previous reading was SUSPICIOUS at the same new level
                  (two readings agree -> a real level change, accepted; Module 5 rule)
  OK           everything else

A non-OK reading is still stored (raw value + reason); it just has no accepted value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    status: str
    accepted_value: float | None
    reason: str | None
    raw_number: float | None = None     # the value as a number, stored in raw_value (numeric column)
    raw_text: str | None = None         # exactly what arrived, when it was text (stored in raw_text)


def parse_raw(raw):
    """Turn whatever the sensor sent into (number, text, problem).

    problem is None for a usable finite number, 'missing' for no measurement, 'malformed' otherwise."""
    if raw is None:
        return None, None, "missing"
    if isinstance(raw, bool):
        return None, repr(raw), "malformed"
    if isinstance(raw, (int, float)):
        return (float(raw), None, None) if math.isfinite(raw) else (None, repr(raw), "missing")
    text = str(raw)
    stripped = text.strip()
    if stripped == "":
        return None, text, "missing"
    try:
        number = float(stripped)
    except ValueError:
        return None, text, "malformed"
    if not math.isfinite(number):
        return None, text, "missing"
    return number, text, None


def validate(raw, settings, last_accepted=None, previous=None) -> Decision:
    """raw: what the sensor sent (number, text or None). last_accepted: last accepted value (float or None).
    previous: (status, raw_value) of the reading just before this one, or None."""
    number, text, problem = parse_raw(raw)
    if problem == "missing":
        why = "no sensor value received" if raw is None else f"sensor sent no measurement ({text!r})"
        return Decision("MISSING", None, why, None, text)
    if problem == "malformed":
        return Decision("FAULT", None, f"malformed value {text!r}: not a number", None, text)
    if not settings.sensor_min_c <= number <= settings.sensor_max_c:
        return Decision("FAULT", None, f"outside sensor range {settings.sensor_min_c:g}..{settings.sensor_max_c:g} C",
                        number, text)
    if not settings.supported_min_c <= number <= settings.supported_max_c:
        return Decision("OUT_OF_RANGE", None,
                        f"outside supported range {settings.supported_min_c:g}..{settings.supported_max_c:g} C",
                        number, text)
    prev_raw = previous[1] if previous else None
    if last_accepted is not None and abs(number - last_accepted) > settings.max_jump_c:
        if previous and previous[0] == "SUSPICIOUS" and isinstance(prev_raw, (int, float)) \
                and abs(number - prev_raw) <= settings.max_jump_c:
            return Decision("OK", number, f"level change of {number - last_accepted:+.2f} C confirmed by previous "
                                          f"reading", number, text)
        return Decision("SUSPICIOUS", None,
                        f"jump of {number - last_accepted:+.2f} C from last accepted value "
                        f"(limit {settings.max_jump_c:g} C)", number, text)
    return Decision("OK", number, None, number, text)
