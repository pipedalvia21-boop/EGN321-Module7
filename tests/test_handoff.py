"""Assignment 8.1 (handoff) tests: the system refuses to start with a clear fix instead of a traceback."""
import os
import socket
import subprocess
import sys

import pytest

from monitor import preflight
from monitor.config import PROJECT_ROOT, ConfigError
from tests.conftest import make_settings


def run_sup(tmp_path, *args, **env):
    e = dict(os.environ, MONITOR_DB=str(tmp_path / "m.db"), MONITOR_LOG_DIR=str(tmp_path / "logs"),
             MONITOR_RUN_DIR=str(tmp_path / "run"))
    e.update(env)
    return subprocess.run([sys.executable, "supervisor.py", *args], cwd=PROJECT_ROOT, env=e,
                          capture_output=True, text=True, timeout=60)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("var, value", [("REPLAY_SPEED", "abc"), ("REPLAY_SPEED", "0"), ("WEB_PORT", "99999"),
                                        ("ALERT_OFF_C", "25"), ("ALERT_ON_C", "200"), ("REPLAY_START_SAMPLE", "1.5")])
def test_invalid_setting_names_the_variable_and_the_expected_value(tmp_path, var, value):
    with pytest.raises(ConfigError) as err:
        make_settings(tmp_path, **{var: value})
    assert err.value.name == var and "expected" in str(err.value)


def test_check_passes_on_a_fresh_clone(tmp_path):
    r = run_sup(tmp_path, "--check", WEB_PORT=str(free_port()))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: ready" in r.stdout and "[FAIL]" not in r.stdout
    assert not (tmp_path / "m.db").exists()          # --check changes nothing


def test_bad_setting_refused_without_traceback(tmp_path):
    r = run_sup(tmp_path, "--speed", "abc")
    assert r.returncode == 2
    assert "REFUSED TO START: invalid setting" in r.stderr and "REPLAY_SPEED = 'abc'" in r.stderr
    assert "Traceback" not in r.stderr


def test_busy_port_refused_with_fix_and_logged(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        r = run_sup(tmp_path, "--port", str(port))
    assert r.returncode == 2
    assert f"another program is already using port {port}" in r.stderr and "--port 5050" in r.stderr
    assert "STARTUP_REFUSED" in (tmp_path / "logs" / "supervisor.log").read_text()
    assert not (tmp_path / "m.db").exists()          # nothing was started


def test_missing_input_file_refused_with_fix(tmp_path):
    r = run_sup(tmp_path, "--check", MODULE6_DB=str(tmp_path / "nope.db"), WEB_PORT=str(free_port()))
    assert r.returncode == 1
    assert "[FAIL] Input data" in r.stdout and "git checkout -- data/module6_sensor_history.db" in r.stdout


def test_malformed_feed_not_built_is_explained(tmp_path):
    r = run_sup(tmp_path, "--check", "--dataset", "practice_malformed", WEB_PORT=str(free_port()))
    assert r.returncode == 1
    assert "python -m scripts.make_malformed_feed" in r.stdout
