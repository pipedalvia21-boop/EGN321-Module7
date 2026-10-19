"""Orphan guard: a child process exits by itself if the supervisor that started it is gone.

Without this, killing only the supervisor (kill -9, Task Manager) would leave an old collector
writing to SQLite and an old dashboard holding the port, and the restarted supervisor's new
children would collide with them. Works the same on Windows, macOS and Linux (psutil).
"""
import os
import threading
import time

import psutil


def supervisor_pid():
    pid = os.environ.get("MONITOR_SUPERVISOR_PID")
    return int(pid) if pid else None


def supervisor_alive(pid=None, created=None):
    pid = pid or supervisor_pid()
    if pid is None:
        return True                       # not supervised (run by hand): nothing to watch
    try:
        proc = psutil.Process(pid)
        return created is None or abs(proc.create_time() - created) < 1.0   # same process, not a reused PID
    except psutil.NoSuchProcess:
        return False


def supervisor_create_time():
    pid = supervisor_pid()
    try:
        return psutil.Process(pid).create_time() if pid else None
    except psutil.NoSuchProcess:
        return None


def start_watchdog_thread(on_orphan, interval=2.0):
    """Used by the dashboard process: call on_orphan() once the supervisor disappears."""
    created = supervisor_create_time()

    def watch():
        while True:
            time.sleep(interval)
            if not supervisor_alive(created=created):
                on_orphan()
                return
    t = threading.Thread(target=watch, name="orphan-guard", daemon=True)
    t.start()
    return t


def kill_leftover_children(pids: dict, logger=None):
    """Called by a new supervisor: stop children left behind by a supervisor that was killed."""
    stopped = []
    for name, module in (("worker", "monitor.worker"), ("web", "monitor.web")):
        pid = pids.get(name)
        if not pid:
            continue
        try:
            proc = psutil.Process(pid)
            if module in " ".join(proc.cmdline()):
                proc.kill()
                proc.wait(timeout=5)
                stopped.append(f"{name} (pid {pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass
    return stopped
