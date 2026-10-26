"""Process manager: starts the collector and the dashboard and restarts them when they stop.

    python supervisor.py              # the ONE command that runs the whole system
    python supervisor.py --check      # only check that this computer can run it (PASS / FAIL list)
    python supervisor.py --port 5050 --speed 120     # options work the same on Windows, macOS and Linux

* Each child (worker = collector, web = dashboard) is restarted automatically if it exits or is
  killed, with backoff 1, 2, 4, 8, 16 s.
* Crash-loop guard: more than 5 restarts of one child within 60 s -> it is NOT restarted again,
  a CRITICAL "CRASH_LOOP" event is logged and a person must fix the cause (see README).
* Ctrl+C / SIGTERM -> clean stop: run/stop.flag asks the collector to finish its loop and record a
  clean shutdown, then the dashboard is stopped.
* The supervisor itself is started at boot/logon and restarted by the operating system
  (systemd, Windows Task Scheduler or macOS launchd; see deploy/).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque

from monitor import db, guard, preflight
from monitor.config import PROJECT_ROOT, ConfigError, load_settings
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
        child.restarts.append(now)
        while child.restarts and now - child.restarts[0] > CRASH_WINDOW_S:
            child.restarts.popleft()
        n = len(child.restarts)
        self.event("ERROR", "CHILD_EXIT", f"{child.name} (pid {child.proc.pid}) stopped unexpectedly: {how} "
                   f"[exit {n} of {CRASH_LIMIT + 1} allowed in {CRASH_WINDOW_S} s; details in logs/{child.name}.log]")
        child.proc = None
        # 7.2 improvement: tell the dashboard about restarts, so it never shows HEALTHY while a child crash-loops
        exits = db.kv_get_json(self.conn, "child_exits", {}) or {}
        exits[child.name] = {"times": list(child.restarts), "last_exit": how, "limit": CRASH_LIMIT,
                             "window_s": CRASH_WINDOW_S}
        db.kv_set(self.conn, "child_exits", exits)
        if n > CRASH_LIMIT:
            child.given_up = True
            db.kv_set(self.conn, "crash_loop", {"child": child.name, "at": now, "exits": n, "last_exit": how})
            self.event("CRITICAL", "CRASH_LOOP",
                       f"{child.name} stopped {n} times in {CRASH_WINDOW_S} s (last: {how}); automatic restart "
                       f"disabled - human intervention required: read logs/{child.name}.log, fix the cause, then "
                       f"restart the service")
        else:
            delay = min(BACKOFF_MAX_S, 2 ** (n - 1))
            child.next_start = now + delay
            self.event("INFO", "RESTART_SCHEDULED", f"{child.name} will restart in {delay} s (restart {n} of "
                       f"{CRASH_LIMIT} before the crash-loop guard stops it)")
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
        db.kv_set(self.conn, "crash_loop", None)        # a fresh supervisor starts with a clean slate
        db.kv_set(self.conn, "child_exits", {})
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


OPTIONS = (  # command-line option -> environment variable, so the child processes see the same value
    ("--port", "WEB_PORT", "dashboard port (default 5000)"),
    ("--speed", "REPLAY_SPEED", "replay speed: 30 = one reading every 2 s (default), 120 = every 0.5 s"),
    ("--start-sample", "REPLAY_START_SAMPLE", "Module 6 sample number to start the replay at (default 1)"),
    ("--dataset", "SOURCE_DATASET", "replay source: practice (default) or practice_malformed"),
    ("--alert-on", "ALERT_ON_C", "alert activates when the median of 5 readings is >= this, in deg C (default 24.0)"),
    ("--alert-off", "ALERT_OFF_C", "alert clears when the median is <= this, in deg C (default 23.5)"),
)


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Start the unattended temperature monitor (collector + dashboard).")
    ap.add_argument("--check", action="store_true", help="only run the pre-start checks and exit")
    for flag, var, text in OPTIONS:
        ap.add_argument(flag, metavar=var, help=f"{text}; same as setting {var}")
    args = ap.parse_args(argv)
    for flag, var, _ in OPTIONS:
        value = getattr(args, flag[2:].replace("-", "_"))
        if value is not None:
            os.environ[var] = value
    return args


def refuse(title, lines, settings=None):
    print(f"\nREFUSED TO START: {title}\n" + lines + "\nNothing was started. See README > Troubleshooting.\n",
          file=sys.stderr)
    if settings is not None:
        try:
            log = configure("supervisor", settings, console=False)
            log.error("STARTUP_REFUSED %s | %s", title, " | ".join(x.strip() for x in lines.splitlines()))
        except OSError:
            pass
    sys.exit(2)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        refuse("invalid setting", f"  {exc.name} = {exc.value!r}\n  expected: {exc.expected}")
    if args.check:
        checks = preflight.run_checks(settings)
        print("Pre-start checks:\n" + preflight.report(checks))
        failed = [c for c in checks if not c.ok]
        print("\nRESULT: " + ("ready - start it with:  python supervisor.py" if not failed
                               else f"{len(failed)} problem(s) - fix them, then run the check again"))
        sys.exit(1 if failed else 0)
    checks = preflight.run_checks(settings, port_wait_s=10)
    failed = [c for c in checks if not c.ok]
    if failed:
        refuse(f"{len(failed)} pre-start check(s) failed", preflight.report(failed), settings)
    sup = Supervisor(settings)
    signal.signal(signal.SIGINT, sup.request_stop)
    signal.signal(signal.SIGTERM, sup.request_stop)
    if sys.stdout is not None:
        print(f"\n  Sensor monitor running.  Open the dashboard:  http://{settings.web_host}:{settings.web_port}\n"
              f"  Logs: {settings.log_dir}    Stop: press Ctrl+C in this window\n", flush=True)
    try:
        sup.run()
    except Exception:
        sup.log.exception("APPLICATION_ERROR supervisor crashed; the operating system service will restart it")
        sys.exit(1)


if __name__ == "__main__":
    main()
