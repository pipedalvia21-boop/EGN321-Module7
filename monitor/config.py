"""Settings for the unattended monitor.

Every value has a documented default and can be overridden with an environment variable,
so the same code runs unattended (defaults) or in a fast demo (REPLAY_SPEED=120).
"""
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    """An environment variable / command-line option has a value the system refuses to run with."""

    def __init__(self, name, value, expected):
        self.name, self.value, self.expected = name, value, expected
        super().__init__(f"invalid configuration: {name}={value!r} - expected {expected}")


def _number(env, name, default, kind=float, expected="a number"):
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return kind(str(raw).strip())
    except ValueError:
        raise ConfigError(name, raw, expected) from None


@dataclass(frozen=True)
class Settings:
    home: Path
    db_path: Path
    module6_db: Path
    log_dir: Path
    run_dir: Path

    # Replay source: Module 6 history, played back in real time (gaps included)
    source_dataset: str = "practice"
    source_sensor: str = "temperature"
    source_interval_s: float = 60.0      # how far apart the source readings are
    replay_speed: float = 30.0           # 30x -> one reading every 2 s at the monitor
    replay_start_sample: int = 1

    live_dataset: str = "live"
    live_sensor: str = "temperature"

    # Stale / missing data (multiples of the expected interval)
    stale_intervals: float = 3.0         # no reading for > 3 intervals  -> DEGRADED (stale)
    failed_intervals: float = 15.0       # no reading for > 15 intervals -> FAILED (data lost)
    recovery_readings: int = 5           # fresh accepted readings needed to leave STARTING/RECOVERING
    bad_window: int = 30                 # look at the last 30 readings ...
    bad_ratio: float = 0.20              # ... DEGRADED if >= 20 % were rejected or missing

    # Threshold alert: rolling median + hysteresis
    alert_name: str = "HIGH_TEMP"
    alert_on_c: float = 24.0             # activate when median of last 5 accepted >= 24.0 C
    alert_off_c: float = 23.5            # clear when median of last 5 accepted <= 23.5 C
    alert_window: int = 5

    # Validation (same limits as Modules 5 and 6)
    sensor_min_c: float = -40.0
    sensor_max_c: float = 125.0
    supported_min_c: float = 0.0
    supported_max_c: float = 50.0
    max_jump_c: float = 5.0

    web_host: str = "127.0.0.1"
    web_port: int = 5000

    def __post_init__(self):
        if not 0 < self.replay_speed <= 1000:
            raise ConfigError("REPLAY_SPEED", self.replay_speed, "a number above 0 and at most 1000 (default 30)")
        if self.replay_start_sample < 1:
            raise ConfigError("REPLAY_START_SAMPLE", self.replay_start_sample, "a whole number >= 1 (default 1)")
        if self.recovery_readings < 1:
            raise ConfigError("RECOVERY_READINGS", self.recovery_readings, "a whole number >= 1 (default 5)")
        if not 1 <= self.web_port <= 65535:
            raise ConfigError("WEB_PORT", self.web_port, "a port number from 1 to 65535 (default 5000)")
        for name, value in (("ALERT_ON_C", self.alert_on_c), ("ALERT_OFF_C", self.alert_off_c)):
            if not self.supported_min_c <= value <= self.supported_max_c:
                raise ConfigError(name, value, f"a temperature in degrees C between {self.supported_min_c:g} "
                                               f"and {self.supported_max_c:g}")
        if not self.alert_off_c < self.alert_on_c:
            raise ConfigError("ALERT_OFF_C", self.alert_off_c,
                              f"a value below ALERT_ON_C ({self.alert_on_c:g}) - the hysteresis band needs OFF < ON")

    @property
    def interval_s(self) -> float:
        """Expected sampling interval at the monitor."""
        return self.source_interval_s / self.replay_speed

    @property
    def stale_after_s(self) -> float:
        return self.stale_intervals * self.interval_s

    @property
    def failed_after_s(self) -> float:
        return self.failed_intervals * self.interval_s

    @property
    def heartbeat_timeout_s(self) -> float:
        """Dashboard declares the collector dead if its heartbeat is older than this."""
        return max(10.0, 5 * self.interval_s)

    @property
    def tick_s(self) -> float:
        return min(0.25, self.interval_s / 4)

    @property
    def stop_flag(self) -> Path:
        return self.run_dir / "stop.flag"


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    home = Path(env.get("MONITOR_HOME", PROJECT_ROOT))
    return Settings(
        home=home,
        db_path=Path(env.get("MONITOR_DB", home / "data" / "monitor.db")),
        module6_db=Path(env.get("MODULE6_DB", home / "data" / "module6_sensor_history.db")),
        log_dir=Path(env.get("MONITOR_LOG_DIR", home / "logs")),
        run_dir=Path(env.get("MONITOR_RUN_DIR", home / "run")),
        source_dataset=env.get("SOURCE_DATASET", "practice"),
        replay_speed=_number(env, "REPLAY_SPEED", 30.0, float, "a number above 0 and at most 1000 (default 30)"),
        replay_start_sample=_number(env, "REPLAY_START_SAMPLE", 1, int, "a whole number >= 1 (default 1)"),
        recovery_readings=_number(env, "RECOVERY_READINGS", 5, int, "a whole number >= 1 (default 5)"),
        alert_on_c=_number(env, "ALERT_ON_C", 24.0, float, "a temperature in degrees C, e.g. 24.0"),
        alert_off_c=_number(env, "ALERT_OFF_C", 23.5, float, "a temperature in degrees C, e.g. 23.5"),
        web_host=env.get("WEB_HOST", "127.0.0.1"),
        web_port=_number(env, "WEB_PORT", 5000, int, "a port number from 1 to 65535 (default 5000)"),
    )
