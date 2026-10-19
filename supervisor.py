"""Process manager: starts the collector and the dashboard and restarts them when they stop.

    python supervisor.py          # the ONE command that runs the whole system

* Each child (worker = collector, web = dashboard) is restarted automatically if it exits or is
  killed, with backoff 1, 2, 4, 8, 16 s.
* Crash-loop guard: more than 5 restarts of one child within 60 s -> it is NOT restarted again,
  a CRITICAL "CRASH_LOOP" event is logged and a person must fix the cause (see README).
* Ctrl+C / SIGTERM -> clean stop: run/stop.flag asks the collector to finish its loop and record a
  clean shutdown, then the dashboard is stopped.
* The supervisor itself is started at boot/logon and restarted by the operating system
  (systemd, Windows Task Scheduler or macOS launchd; see deploy/).
"""
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque

from monitor import db, guard
from monitor.config import PROJECT_ROOT, load_settings
from monitor.logging_setup import configure

BACKOFF_MAX_S = 30
CRASH_WINDOW_S = 60
CRASH_LIMIT = 5


class Child:
    def __init__(self, name, module):
        self.name = name
        self.cmd = [sys.executable, "-m", module]
        self.proc = None
        self.restarts = deque()
        self.next_start = 0.0
        self.given_up = False
        self.starts = 0


class Supervisor:
    def __init__(self, settings):
        self.s = settings
        self.log = configure("supervisor", settings)
        self.stopping = False
        self.children = [Child("worker", "monitor.worker"), Child("web", "monitor.web")]
        settings.run_dir.mkdir(parents=True, exist_ok=True)
        settings.stop_flag.unlink(missing_ok=True)
        db.init_db(settings)
        self.conn = db.connect(settings.db_path)

    def event(self, level, name, message):
        db.record_event(self.conn, level, "supervisor", name, message, logger=self.log)

    def write_pids(self):
        pids = {"supervisor": os.getpid(), "updated": db.fmt_ts(__import__("datetime").datetime.now())}
        for c in self.children:
            pids[c.name] = c.proc.pid if c.proc and c.proc.poll() is None else None
        tmp = self.s.run_dir / "pids.json.tmp"
        tmp.write_text(json.dumps(pids, indent=2))
        os.replace(tmp, self.s.run_dir / "pids.json")

    def start_child(self, child):
        env = dict(os.environ, PYTHONUNBUFFERED="1", MONITOR_SUPERVISED="1", MONITOR_SUPERVISOR_PID=str(os.getpid()))
        err = open(self.s.log_dir / f"{child.name}.stderr.log", "a", encoding="utf-8")
        child.proc = subprocess.Popen(child.cmd, cwd=PROJECT_ROOT, env=env,
                                      stdout=subprocess.DEVNULL, stderr=err)
        err.close()
        child.starts += 1
        if child.starts == 1:
            self.event("INFO", "CHILD_START", f"started {child.name} (pid {child.proc.pid})")
        else:
            self.event("WARNING", "CHILD_RESTART",
                       f"restarted {child.name} (pid {child.proc.pid}, restart #{child.starts - 1})")
        self.write_pids()

    def on_exit(self, child, now):
        code = child.proc.returncode
        how = f"killed by signal {-code}" if code is not None and code < 0 else f"exit code {code}"
        self.event("ERROR", "CHILD_EXIT", f"{child.name} (pid {child.proc.pid}) stopped unexpectedly: {how}")
        child.proc = None
        child.restarts.append(now)
        while child.restarts and now - child.restarts[0] > CRASH_WINDOW_S:
            child.restarts.popleft()
        if len(child.restarts) > CRASH_LIMIT:
            child.given_up = True
            self.event("CRITICAL", "CRASH_LOOP",
                       f"{child.name} stopped {len(child.restarts)} times in {CRASH_WINDOW_S} s; automatic "
                       f"restart disabled - human intervention required (see logs/{child.name}.stderr.log)")
        else:
            delay = min(BACKOFF_MAX_S, 2 ** (len(child.restarts) - 1))
            child.next_start = now + delay
            self.event("INFO", "RESTART_SCHEDULED", f"{child.name} will restart in {delay} s")
        self.write_pids()

    def run(self):
        was_running = db.kv_get(self.conn, "supervisor_running") == "1"
        self.event("INFO", "SUPERVISOR_START", f"supervisor started (pid {os.getpid()}), python {sys.version.split()[0]}, "
                   f"dashboard http://{self.s.web_host}:{self.s.web_port}")
        if was_running:
            self.event("WARNING", "RECOVERY", "previous supervisor run did not stop cleanly "
                       "(power loss, kill or crash); restarting all components")
        pid_file = self.s.run_dir / "pids.json"
        if pid_file.exists():
            leftovers = guard.kill_leftover_children(json.loads(pid_file.read_text()))
            if leftovers:
                self.event("WARNING", "RECOVERY", "stopped leftover processes from the previous run: "
                           + ", ".join(leftovers))
        db.kv_set(self.conn, "supervisor_running", "1")
        for child in self.children:
            self.start_child(child)
        while not self.stopping:
            now = time.time()
            for child in self.children:
                if child.given_up:
                    continue
                if child.proc is not None and child.proc.poll() is not None:
                    self.on_exit(child, now)
                elif child.proc is None and now >= child.next_start:
                    self.start_child(child)
            time.sleep(0.5)
        self.shutdown()

    def request_stop(self, *_):
        self.stopping = True

    def shutdown(self):
        self.event("INFO", "SUPERVISOR_STOPPING", "stop requested: asking collector to finish, then stopping dashboard")
        self.s.stop_flag.touch()
        worker = self.children[0].proc
        if worker is not None:
            try:
                worker.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.kill()
        for child in self.children[1:]:
            if child.proc is not None and child.proc.poll() is None:
                child.proc.terminate()
                try:
                    child.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.proc.kill()
        self.s.stop_flag.unlink(missing_ok=True)
        db.kv_set(self.conn, "supervisor_running", "0")
        self.event("INFO", "SUPERVISOR_STOP", "supervisor stopped cleanly")
        (self.s.run_dir / "pids.json").unlink(missing_ok=True)


def main():
    sup = Supervisor(load_settings())
    signal.signal(signal.SIGINT, sup.request_stop)
    signal.signal(signal.SIGTERM, sup.request_stop)
    try:
        sup.run()
    except Exception:
        sup.log.exception("APPLICATION_ERROR supervisor crashed; the operating system service will restart it")
        sys.exit(1)


if __name__ == "__main__":
    main()
