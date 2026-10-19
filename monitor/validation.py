"""Validation of each live reading before it is stored or used (same limits as Modules 5 and 6).

Order of checks:
  MISSING      no value, or not a finite number
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


def validate(raw, settings, last_accepted=None, previous=None) -> Decision:
    """raw: the value received. last_accepted: last accepted value (float or None).
    previous: (status, raw_value) of the reading just before this one, or None."""
    if raw is None or not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(raw):
        return Decision("MISSING", None, "no sensor value received")
    raw = float(raw)
    if not settings.sensor_min_c <= raw <= settings.sensor_max_c:
        return Decision("FAULT", None, f"outside sensor range {settings.sensor_min_c:g}..{settings.sensor_max_c:g} C")
    if not settings.supported_min_c <= raw <= settings.supported_max_c:
        return Decision("OUT_OF_RANGE", None,
                        f"outside supported range {settings.supported_min_c:g}..{settings.supported_max_c:g} C")
    if last_accepted is not None and abs(raw - last_accepted) > settings.max_jump_c:
        if previous and previous[0] == "SUSPICIOUS" and previous[1] is not None \
                and abs(raw - previous[1]) <= settings.max_jump_c:
            return Decision("OK", raw, f"level change of {raw - last_accepted:+.2f} C confirmed by previous reading")
        return Decision("SUSPICIOUS", None,
                        f"jump of {raw - last_accepted:+.2f} C from last accepted value (limit {settings.max_jump_c:g} C)")
    return Decision("OK", raw, None)
