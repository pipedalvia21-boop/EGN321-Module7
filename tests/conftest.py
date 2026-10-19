from pathlib import Path

import pytest

from monitor import db
from monitor.config import PROJECT_ROOT, load_settings


class FakeClock:
    """Deterministic clock for the collector and the dashboard."""

    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_settings(tmp_path: Path, **extra):
    env = {"MONITOR_HOME": str(PROJECT_ROOT), "MONITOR_DB": str(tmp_path / "monitor.db"),
           "MONITOR_LOG_DIR": str(tmp_path / "logs"), "MONITOR_RUN_DIR": str(tmp_path / "run"),
           "REPLAY_SPEED": "120", "REPLAY_START_SAMPLE": "250"}
    env.update({k: str(v) for k, v in extra.items()})
    return load_settings(env)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def conn(settings):
    db.init_db(settings)
    c = db.connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def clock():
    return FakeClock()
