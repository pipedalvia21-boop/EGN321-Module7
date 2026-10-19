"""Real processes: kill the collector and the dashboard, the supervisor brings both back."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from monitor.config import PROJECT_ROOT

KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return json.loads(r.read())
    except OSError:
        return None


def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.25)
    return None


def test_supervisor_restarts_killed_processes(tmp_path):
    port = free_port()
    env = dict(os.environ, MONITOR_DB=str(tmp_path / "m.db"), MONITOR_LOG_DIR=str(tmp_path / "logs"),
               MONITOR_RUN_DIR=str(tmp_path / "run"), REPLAY_SPEED="120", REPLAY_START_SAMPLE="200",
               WEB_PORT=str(port))
    sup = subprocess.Popen([sys.executable, "supervisor.py"], cwd=PROJECT_ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        assert wait_for(lambda: (h := get(url)) and h["health"] == "HEALTHY", 30), "never became HEALTHY"
        pids = json.loads((tmp_path / "run" / "pids.json").read_text())
        os.kill(pids["worker"], KILL)
        os.kill(pids["web"], KILL)
        wait_for(lambda: get(url) is None, 5)                              # dashboard goes down
        back = wait_for(lambda: get(url), 20)
        assert back is not None, "dashboard did not come back"
        assert wait_for(lambda: (h := get(url)) and h["health"] == "HEALTHY", 30), "health did not return"
        new = json.loads((tmp_path / "run" / "pids.json").read_text())
        assert new["worker"] != pids["worker"] and new["web"] != pids["web"]
        sup_log = (tmp_path / "logs" / "supervisor.log").read_text()
        work_log = (tmp_path / "logs" / "worker.log").read_text()
        assert sup_log.count("CHILD_EXIT") == 2 and sup_log.count("CHILD_RESTART") == 2
        assert "RECOVERY previous collector run ended unexpectedly" in work_log
        assert "HEALTH_CHANGE RECOVERING -> HEALTHY" in work_log or "RECOVERED" in work_log
    finally:
        sup.send_signal(signal.SIGINT)
        sup.wait(timeout=20)
    assert "SUPERVISOR_STOP" in (tmp_path / "logs" / "supervisor.log").read_text()


def test_killed_supervisor_leaves_no_orphans_and_restart_recovers(tmp_path):
    """kill -9 on the supervisor itself (what systemd / Task Scheduler / launchd then restarts)."""
    import psutil
    port = free_port()
    env = dict(os.environ, MONITOR_DB=str(tmp_path / "m.db"), MONITOR_LOG_DIR=str(tmp_path / "logs"),
               MONITOR_RUN_DIR=str(tmp_path / "run"), REPLAY_SPEED="120", REPLAY_START_SAMPLE="200",
               WEB_PORT=str(port))
    url = f"http://127.0.0.1:{port}/healthz"
    sup = subprocess.Popen([sys.executable, "supervisor.py"], cwd=PROJECT_ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_for(lambda: (h := get(url)) and h["health"] == "HEALTHY", 30)
    pids = json.loads((tmp_path / "run" / "pids.json").read_text())
    os.kill(sup.pid, KILL)
    sup.wait(timeout=5)
    gone = wait_for(lambda: not psutil.pid_exists(pids["worker"]) and not psutil.pid_exists(pids["web"]), 10)
    assert gone, "children kept running after the supervisor was killed"
    # the operating-system service manager now starts the supervisor again:
    sup2 = subprocess.Popen([sys.executable, "supervisor.py"], cwd=PROJECT_ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: (h := get(url)) and h["health"] == "HEALTHY", 40)
        sup_log = (tmp_path / "logs" / "supervisor.log").read_text()
        assert "RECOVERY previous supervisor run did not stop cleanly" in sup_log
        assert "RECOVERY previous collector run ended unexpectedly" in (tmp_path / "logs" / "worker.log").read_text()
    finally:
        sup2.send_signal(signal.SIGINT)
        sup2.wait(timeout=20)
