"""Assignment 7.2 failure experiment: malformed sensor input.

    python scripts/failure_run.py failure   # run with the code as it was: observe, preserve, recover, verify
    python scripts/failure_run.py verify    # same malformed input after the improvement

Both phases run the real supervisor + collector + dashboard on the fault-injection feed
'practice_malformed' (scripts/make_malformed_feed.py): Module 6 practice temperature with samples
400-459 replaced by malformed serial output. Replay 60x (one reading per second) from sample 370,
so the malformed block arrives 30 s after start and lasts 60 s.

Everything observed is written to evidence/<phase>_run/:
  run_log.md          what was done and when (exact actions)
  timeline.csv        /healthz + page + chart status polled every 0.5 s
  status_*.json       full /api/status snapshots at key moments
  logs/               copies of supervisor/worker/web logs (+ stderr)
  db_*.db             SQLite backups (before recovery / after recovery)
  checks.md           verification checks with PASS/FAIL
  *.png               screenshots
Working files live in work/<phase>/ (not committed).
"""
import csv
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHASE = sys.argv[1] if len(sys.argv) > 1 else "failure"
assert PHASE in ("failure", "verify")
WORK = ROOT / "work" / PHASE
EVID = ROOT / "evidence" / f"{PHASE}_run"
PORT = 5000 if PHASE == "failure" else 5001
BASE = f"http://127.0.0.1:{PORT}"
SPEED, START, FIRST_BAD, LAST_BAD = 60, 370, 400, 459
ENV = dict(os.environ, MONITOR_DB=str(WORK / "monitor.db"), MONITOR_LOG_DIR=str(WORK / "logs"),
           MONITOR_RUN_DIR=str(WORK / "run"), SOURCE_DATASET="practice_malformed", REPLAY_SPEED=str(SPEED),
           REPLAY_START_SAMPLE=str(START), WEB_PORT=str(PORT))

log_lines, checks, rows = [], [], []
stop_poll = threading.Event()


def now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def note(text):
    line = f"{now()}  {text}"
    log_lines.append(line)
    print(line, flush=True)


def check(name, ok, detail=""):
    checks.append((name, bool(ok), str(detail)))
    print(f"   [{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


def http(path, timeout=2):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError:
        return None, b""


def api():
    code, body = http("/api/status")
    return json.loads(body) if code == 200 else None


def save_status(name):
    s = api()
    (EVID / f"status_{name}.json").write_text(json.dumps(s, indent=2))
    return s


def pids():
    try:
        return json.loads((WORK / "run" / "pids.json").read_text())
    except (OSError, ValueError):
        return {}


def poller():
    while not stop_poll.is_set():
        h_code, h_body = http("/healthz")
        p_code, _ = http("/")
        c_code, _ = http("/chart/live.svg?minutes=5")
        h = json.loads(h_body) if h_code == 200 else {}
        s = api() or {}
        lr = s.get("latest_row") or {}
        rows.append([now(), h.get("health", "UNREACHABLE"), h.get("reason", ""), h.get("alert_active", ""),
                     lr.get("source_ref", ""), repr(lr.get("raw_value")), lr.get("status", ""),
                     p_code, c_code, pids().get("worker")])
        time.sleep(0.5)


def wait(pred, timeout, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def log_text(name):
    p = WORK / "logs" / f"{name}.log"
    return p.read_text() if p.exists() else ""


def backup_db(dest):
    src = sqlite3.connect(WORK / "monitor.db")
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()


def copy_logs(sub):
    out = EVID / sub
    out.mkdir(parents=True, exist_ok=True)
    for f in (WORK / "logs").glob("*.log"):
        shutil.copy(f, out / f.name)


def start_supervisor():
    return subprocess.Popen([sys.executable, "supervisor.py"], cwd=ROOT, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def source_sample(s):
    ref = ((s or {}).get("latest_row") or {}).get("source_ref") or ""
    try:
        return int(ref.split("#")[1].split(" ")[0])
    except (IndexError, ValueError):
        return None


def db_checks(label):
    c = sqlite3.connect(WORK / "monitor.db")
    integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    m6 = c.execute("SELECT COUNT(*) FROM sensor_readings WHERE dataset IN ('practice','module5')").fetchone()[0]
    live = c.execute("SELECT COUNT(*) FROM sensor_readings WHERE dataset='live'").fetchone()[0]
    c.close()
    check(f"{label}: database integrity_check", integrity == "ok", integrity)
    check(f"{label}: Module 6 history intact", m6 == 8900, f"{m6} rows")
    return live


def main():
    from playwright.sync_api import sync_playwright
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(EVID, ignore_errors=True)
    EVID.mkdir(parents=True)
    WORK.mkdir(parents=True)
    subprocess.run([sys.executable, "init_db.py"], cwd=ROOT, env=ENV, check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, "-m", "scripts.make_malformed_feed"], cwd=ROOT, env=ENV, check=True)
    note(f"PHASE {PHASE}: code at git {subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}"
         f"; feed practice_malformed (samples {FIRST_BAD}-{LAST_BAD} malformed); replay {SPEED}x from sample {START}")

    sup = start_supervisor()
    t_start = time.time()
    note(f"supervisor started (pid {sup.pid})")
    threading.Thread(target=poller, daemon=True).start()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 1000})

        def shot(name, caption, fresh=False):
            if fresh:
                p2 = browser.new_page(viewport={"width": 1360, "height": 1000})
                p2.goto(BASE + "/")
                time.sleep(1.2)
                p2.screenshot(path=str(EVID / f"{name}.png"), full_page=True)
                p2.close()
            else:
                time.sleep(1.2)
                page.screenshot(path=str(EVID / f"{name}.png"), full_page=True)
            note(f"screenshot {name}.png - {caption}")

        # ---------------------------------------------------------- 1. normal state
        assert wait(lambda: (s := api()) and s["health"]["state"] == "HEALTHY" and s["alert"]["active"], 40), \
            "system never reached HEALTHY with the alert active"
        page.goto(BASE + "/")
        time.sleep(2)
        s = save_status("1_normal")
        code, _ = http("/")
        live = db_checks("normal")
        note(f"NORMAL STATE: health {s['health']['state']}; latest reading {s['current']['value']} C at "
             f"{s['current']['timestamp']} (source sample {source_sample(s)}); dashboard HTTP {code}; "
             f"database ok, {live} live rows; alert {s['alert']['name']} ACTIVE since {s['alert']['since']}")
        shot("01_normal_state", "Normal operation before the failure")

        # ---------------------------------------------------------- 2. failure arrives
        t_bad = t_start + (FIRST_BAD - START) / (SPEED / 60)
        note(f"FAILURE INTRODUCED: the feed delivers malformed sample {FIRST_BAD} ('ERR') at about "
             f"{datetime.fromtimestamp(t_bad):%H:%M:%S}; samples {FIRST_BAD}-{LAST_BAD} are all malformed")
        wait(lambda: (source_sample(api()) or 0) >= FIRST_BAD, 60, 0.1)
        note("first malformed reading stored (latest_row.source_ref = sample "
             f"{source_sample(api())}, raw {((api() or {}).get('latest_row') or {}).get('raw_value')!r})")
        time.sleep(4)
        save_status("2_during_failure")
        shot("02_open_page_during_failure", "Already-open dashboard while malformed input arrives")
        code, body = http("/")
        note(f"fresh load of / -> HTTP {code}; chart -> HTTP {http('/chart/live.svg?minutes=5')[0]}")
        shot("03_fresh_page_load_during_failure", "A NEW visit to the dashboard during the failure", fresh=True)

        if PHASE == "failure":
            loop = wait(lambda: "CRASH_LOOP" in log_text("supervisor"), 90, 0.5)
            note("supervisor log: CRASH_LOOP" if loop else "no CRASH_LOOP within 90 s")
            check("failure reproduced: collector crash loop", loop)
            failed = wait(lambda: (s := api()) and s["health"]["state"] == "FAILED", 30)
            s = save_status("3_failed")
            note(f"dashboard health: {s['health']['state']} - {s['health']['reason']}")
            shot("04_failed_state", "Collector stopped by the crash-loop guard: system FAILED")
            # preserve evidence BEFORE touching anything
            copy_logs("logs_before_recovery")
            backup_db(EVID / "db_before_recovery.db")
            note("evidence preserved: logs_before_recovery/ and db_before_recovery.db (nothing deleted)")
            # sensor fixed: wait until the malformed block is over in the feed
            t_good = t_start + (LAST_BAD + 1 - START) / (SPEED / 60) + 2
            while time.time() < t_good:
                time.sleep(0.5)
            note(f"input restored: feed sample {LAST_BAD + 1} onward is numeric again "
                 f"(due {datetime.fromtimestamp(t_good - 2):%H:%M:%S}); collector is still DOWN (crash-loop guard)")
            st = api()
            check("collector does NOT come back by itself after the input recovers",
                  st and st["health"]["state"] == "FAILED", st and st["health"]["reason"])
            # ------------------------------------------------------ 3. recovery procedure
            note("RECOVERY: operator restarts the service (Ctrl+C / systemctl restart sensor-monitor)")
            sup.send_signal(signal.SIGINT)
            sup.wait(timeout=30)
            t_restart = time.time()
            sup = start_supervisor()
            note(f"supervisor restarted (pid {sup.pid})")
        else:
            time.sleep((LAST_BAD - FIRST_BAD) / 2 / (SPEED / 60))
            s = save_status("3_mid_block")
            note(f"mid-block: health {s['health']['state']} - {s['health']['reason']}")
            shot("04_during_malformed_block", "Improved code, middle of the malformed block")
            check("no collector crash during malformed input", "CHILD_EXIT" not in log_text("supervisor"))
            check("dashboard page loads during malformed input", http("/")[0] == 200)
            check("chart loads during malformed input", http("/chart/live.svg?minutes=5")[0] == 200)
            check("health DEGRADED (not FAILED) while only malformed data arrives",
                  s["health"]["state"] == "DEGRADED", s["health"]["reason"])
            t_restart = time.time()

        # ---------------------------------------------------------- 4. verification
        ok = wait(lambda: (s := api()) and s["health"]["state"] == "HEALTHY" and (source_sample(s) or 0) > LAST_BAD, 90)
        t_ok = time.time()
        s = save_status("4_recovered")
        check("replay input resumed (numeric samples after the malformed block)",
              (source_sample(s) or 0) > LAST_BAD, f"latest source sample {source_sample(s)}")
        check("system health returned to HEALTHY", ok, f"{t_ok - t_restart:.1f} s after "
              f"{'restart' if PHASE == 'failure' else 'end of measurement'}")
        live2 = db_checks("after recovery")
        check("database available and growing", live2 > live, f"live rows {live} -> {live2}")
        page_ok = wait(lambda: http("/")[0] == 200, 60, 0.5)
        t_page = time.time()
        check("dashboard page loads (HTTP 200)", page_ok, f"{t_page - t_ok:.1f} s after HEALTHY")
        check("live chart loads (HTTP 200)", wait(lambda: http("/chart/live.svg?minutes=5")[0] == 200, 30, 0.5))
        wl = log_text("worker")
        check("alert state kept through the incident (ACTIVE, not reset)", s["alert"]["active"],
              f"since {s['alert']['since']}")
        if PHASE == "failure":
            check("restart logged as RECOVERY", "RECOVERY previous collector run ended unexpectedly" in wl)
            check("alert restored from SQLite at restart", "ALERT_RESTORED" in wl)
        alert_clear = wait(lambda: (s := api()) and not s["alert"]["active"], 150, 0.5)
        s = save_status("5_alert_cleared")
        check("alert still evaluated after recovery (clears normally with hysteresis)", alert_clear,
              s and f"median {s['alert']['median']}")
        page.goto(BASE + "/")
        shot("05_recovered_healthy", "Verified recovery: HEALTHY, numeric readings, page loads")
        page.goto(BASE + "/logs")
        shot("06_logs_page", "Logs page after recovery")
        browser.close()

    stop_poll.set()
    sup.send_signal(signal.SIGINT)
    sup.wait(timeout=30)
    note("supervisor stopped cleanly")
    copy_logs("logs")
    backup_db(EVID / "db_after_run.db")
    with open(EVID / "timeline.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "health", "reason", "alert_active", "latest_source_ref", "latest_raw", "latest_status",
                    "page_http", "chart_http", "worker_pid"])
        w.writerows(rows)
    (EVID / "run_log.md").write_text("# Run log - " + PHASE + "\n\n```\n" + "\n".join(log_lines) + "\n```\n")
    (EVID / "checks.md").write_text("| Result | Check | Detail |\n|---|---|---|\n" + "\n".join(
        f"| {'PASS' if ok else 'FAIL'} | {n} | {d} |" for n, ok, d in checks) + "\n")
    print(f"\n{sum(ok for _, ok, _ in checks)}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()
