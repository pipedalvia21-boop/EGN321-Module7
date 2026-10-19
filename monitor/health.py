"""System-health and data-freshness rules (pure functions, unit-tested).

Data states
  NO_DATA  nothing received since start
  FRESH    last reading is at most 3 expected intervals old
  STALE    no reading for more than 3 intervals            -> health DEGRADED
  LOST     no reading for more than 15 intervals           -> health FAILED

Health states (worst first)
  FAILED      collector not running (heartbeat too old) or no data for > 15 intervals
  DEGRADED    data stale, or readings arrive but none is accepted, or >= 20 % of the last
              30 readings were rejected/missing
  STARTING    clean start, waiting for the first 5 fresh accepted readings
  RECOVERING  after an unexpected stop or after FAILED, until 5 fresh accepted readings arrive
  HEALTHY     fresh data, accepted readings, low rejection rate
"""
from __future__ import annotations

from dataclasses import dataclass

HEALTHY, DEGRADED, FAILED, STARTING, RECOVERING = "HEALTHY", "DEGRADED", "FAILED", "STARTING", "RECOVERING"
NO_DATA, FRESH, STALE, LOST = "NO_DATA", "FRESH", "STALE", "LOST"
LEVEL = {HEALTHY: "INFO", STARTING: "INFO", RECOVERING: "INFO", DEGRADED: "WARNING", FAILED: "ERROR"}


@dataclass(frozen=True)
class Health:
    state: str
    reason: str
    data_state: str


def data_state(now, last_row_epoch, settings, started_epoch=None):
    if last_row_epoch is None:
        if started_epoch is not None and now - started_epoch > settings.failed_after_s:
            return LOST
        return NO_DATA
    age = now - last_row_epoch
    if age > settings.failed_after_s:
        return LOST
    if age > settings.stale_after_s:
        return STALE
    return FRESH


def evaluate(now, settings, *, last_row_epoch, last_accepted_epoch, recent_statuses,
             warmup=None, warm_count=0, started_epoch=None) -> Health:
    """Decide the health of the data pipeline at time `now` (epoch seconds)."""
    ds = data_state(now, last_row_epoch, settings, started_epoch)
    iv = settings.interval_s
    if ds == NO_DATA:
        return Health(warmup or STARTING, "waiting for the first reading", ds)
    if ds == LOST:
        if last_row_epoch is None:
            return Health(FAILED, f"no reading received since start ({now - started_epoch:.0f} s)", ds)
        return Health(FAILED, f"expected data stopped arriving: no reading for {now - last_row_epoch:.1f} s "
                              f"(expected every {iv:g} s)", ds)
    if ds == STALE:
        return Health(DEGRADED, f"data stale: last reading {now - last_row_epoch:.1f} s ago "
                                f"(stale after {settings.stale_after_s:g} s)", ds)
    if last_accepted_epoch is None or now - last_accepted_epoch > settings.stale_after_s:
        return Health(DEGRADED, "readings arrive but none was accepted recently", ds)
    recent = list(recent_statuses)[-settings.bad_window:]
    bad = sum(s != "OK" for s in recent)
    if len(recent) >= 10 and bad / len(recent) >= settings.bad_ratio:
        return Health(DEGRADED, f"{bad} of the last {len(recent)} readings were rejected or missing", ds)
    if warmup:
        return Health(warmup, f"{warm_count} of {settings.recovery_readings} fresh accepted readings "
                              f"since {'restart' if warmup == RECOVERING else 'start'}", ds)
    return Health(HEALTHY, f"data fresh: last reading {now - last_row_epoch:.1f} s ago", ds)


def dashboard_health(now, settings, worker_health: dict | None, heartbeat_epoch: float | None,
                     last_row_epoch: float | None = None, crash_loop: dict | None = None,
                     worker_exits: dict | None = None) -> Health:
    """What the dashboard shows: the collector's own verdict, made worse (never better) by what the
    dashboard sees itself - a crash loop, a dead or restarting collector, or data gone stale since."""
    from datetime import datetime
    if crash_loop and crash_loop.get("child") == "worker":
        at = datetime.fromtimestamp(crash_loop["at"]).strftime("%H:%M:%S")
        return Health(FAILED, f"collector stopped by the crash-loop guard at {at} after {crash_loop['exits']} exits "
                              f"in 60 s ({crash_loop['last_exit']}); NOT restarting automatically - human "
                              f"intervention required (see logs/worker.log)", LOST)
    if heartbeat_epoch is None:
        return Health(FAILED, "collector has never reported (not started)", NO_DATA)
    age = now - heartbeat_epoch
    recent = [t for t in (worker_exits or {}).get("times", []) if now - t <= 60]
    restarting = (f"; collector exited {len(recent)} time(s) in the last 60 s "
                  f"(last: {worker_exits['last_exit']})") if recent else ""
    if age > settings.heartbeat_timeout_s:
        return Health(FAILED, f"collector process not responding: last heartbeat {age:.0f} s ago{restarting}", LOST)
    if recent:
        ds = worker_health["data_state"] if worker_health else NO_DATA
        if heartbeat_epoch < max(recent):           # has not completed a loop since it last exited
            return Health(DEGRADED, f"collector restarting, not running yet{restarting}", ds)
        if len(recent) >= 2:                        # repeated exits: unstable even if it is up right now
            return Health(DEGRADED, f"collector unstable{restarting}", ds)
    if not worker_health:
        return Health(STARTING, "collector starting", NO_DATA)
    h = Health(worker_health["state"], worker_health["reason"], worker_health["data_state"])
    if last_row_epoch is not None and h.state != FAILED:
        ds = data_state(now, last_row_epoch, settings)
        data_age = now - last_row_epoch
        if ds == LOST:
            return Health(FAILED, f"expected data stopped arriving: no reading for {data_age:.1f} s "
                                  f"(expected every {settings.interval_s:g} s)", LOST)
        if ds == STALE and h.state != DEGRADED:
            return Health(DEGRADED, f"data stale: last reading {data_age:.1f} s ago "
                                    f"(stale after {settings.stale_after_s:g} s)", STALE)
    return h
