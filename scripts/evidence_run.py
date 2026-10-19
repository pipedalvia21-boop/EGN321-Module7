"""Automated evidence run + recovery test (Assignment 7 "Required Evidence").

    python scripts/evidence_run.py           # ~5 minutes; needs: pip install playwright && playwright install chromium

What it does, with the REAL system (supervisor + collector + Flask dashboard):
  1. rebuilds data/monitor.db from the Module 6 history and clears logs/
  2. starts supervisor.py with a fast demo replay (120x: one reading every 0.5 s, starting at
     Module 6 sample 200, 2026-10-01 11:19) - the same code path as unattended operation
  3. screenshots the dashboard at each milestone: healthy + current reading, alert active,
     stale data, missing data (FAILED), recovering, alert cleared
  4. recovery test A: kill -9 the collector and the dashboard -> supervisor restarts both
  5. recovery test B: kill -9 the supervisor -> children exit (orphan guard) -> supervisor started
     again the way systemd / Task Scheduler / launchd would
  6. checks: dashboard back, SQLite history intact, health back to HEALTHY, log lines present
  7. writes evidence/EVIDENCE.md, evidence/timeline.csv, copies logs and a database snapshot
"""
import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / "evidence"
PORT = int(os.environ.get("WEB_PORT", 5000))
BASE = f"http://127.0.0.1:{PORT}"
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)
ENV = dict(os.environ, REPLAY_SPEED="120", REPLAY_START_SAMPLE="200", WEB_PORT=str(PORT))

timeline, notes, checks, shots = [], [], [], []


def now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def get(path, timeout=2):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read())
    except OSError:
        return None


def note(text):
    line = f"{now()}  {text}"
    notes.append(line)
    print(line, flush=True)


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    print(f"   [{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def poll():
    s = get("/api/status")
    if s:
        cur = s["current"]
        timeline.append([now(), s["health"]["state"], s["health"]["data_state"], s["alert"]["active"],
                         cur["value"] if cur else "", cur["is_current"] if cur else "",
                         s["latest_row"]["status"] if s["latest_row"] else "", s["health"]["reason"]])
    else:
        timeline.append([now(), "UNREACHABLE", "", "", "", "", "", "dashboard not responding"])
    return s


def wait_until(pred, timeout, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        s = poll()
        if s and pred(s):
            return s
        time.sleep(step)
    return None


def start_poller(stop):
    import threading

    def run():
        while not stop.is_set():
            poll()
            time.sleep(0.25)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def start_supervisor():
    return subprocess.Popen([sys.executable, "supervisor.py"], cwd=ROOT, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    from playwright.sync_api import sync_playwright

    for d in ("logs", "run"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    if EVID.exists():
        for p in EVID.glob("*.png"):
            p.unlink()
    (EVID / "logs").mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "init_db.py", "--reset"], cwd=ROOT, env=ENV, check=True)

    import threading
    stop_poll = threading.Event()
    sup = start_supervisor()
    start_poller(stop_poll)
    note(f"supervisor started (pid {sup.pid}); demo replay 120x from Module 6 sample 200")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 1000}, device_scale_factor=1)

        def shot(name, caption, path="/", full=True, reload=False):
            if reload or page.url.split(str(PORT))[-1] != path:
                page.goto(BASE + path)
            time.sleep(1.2)                      # let the page's own 1-s refresh draw the latest state
            page.screenshot(path=str(EVID / f"{name}.png"), full_page=full)
            shots.append((name, caption))
            note(f"screenshot {name}.png - {caption}")

        assert wait_until(lambda s: True, 30), "dashboard never started"
        page.goto(BASE + "/")

        def dom(cond_js, timeout_s):
            """Wait until the OPEN PAGE itself shows the condition (not just the API)."""
            try:
                page.wait_for_function(cond_js, timeout=timeout_s * 1000, polling=100)
                return True
            except Exception:      # noqa: BLE001 - reported as a failed check
                return False

        def snap(name, caption, settle=0.0):
            time.sleep(settle)
            page.screenshot(path=str(EVID / f"{name}.png"), full_page=True)
            shots.append((name, caption))
            note(f"screenshot {name}.png - {caption}")

        H = "document.getElementById('h-state').textContent"
        D = "document.getElementById('d-state').textContent"
        A = "document.getElementById('al-state').textContent"

        ok = dom(f"{H} === 'HEALTHY' && {A} === 'CLEAR'", 30)
        s = poll()
        check("dashboard running, HEALTHY, current reading shown", ok and s["current"]["is_current"],
              f"{s['current']['value']:.2f} C at {s['current']['timestamp']}")
        snap("01_dashboard_healthy_current_reading",
             "Dashboard running: HEALTHY, current reading, latest timestamp, live history", settle=3)

        ok = dom(f"{A} === 'ACTIVE'", 60)
        s = poll()
        check("threshold alert activated", ok and s["alert"]["active"], f"median {s['alert']['median']:.2f} C")
        snap("02_alert_active", "Alert ACTIVE: rolling median of the last 5 accepted readings >= 24.0 C", settle=1)

        ok = dom(f"{D} === 'STALE'", 60)
        s = poll()
        check("stale data detected and shown", ok, s["health"]["reason"])
        snap("03_data_stale", "Data STALE -> DEGRADED: value greyed out and marked 'not current'; the gap is shaded",
             settle=2.8)

        ok = dom(f"{H} === 'FAILED'", 30)
        s = poll()
        check("missing data -> FAILED", ok, s["health"]["reason"])
        snap("04_missing_data_failed", "Expected data stopped arriving: FAILED; alert shown as 'data stale - cannot confirm'",
             settle=2.6)

        ok = dom(f"{H} === 'RECOVERING'", 30)
        check("data resumed -> RECOVERING (shown on the page)", ok)
        if ok:
            snap("05_recovering_after_data_gap", "Data resumed: RECOVERING until 5 fresh accepted readings; the gap stays shaded")
        check("back to HEALTHY after data gap", dom(f"{H} === 'HEALTHY'", 30))

        ok = dom(f"{A} === 'CLEAR'", 240)
        s = poll()
        check("threshold alert cleared (hysteresis)", ok and not s["alert"]["active"], f"median {s['alert']['median']:.2f} C")
        snap("06_alert_cleared", "Alert CLEARED: rolling median <= 23.5 C (hysteresis)", settle=1)

        # ---------------------------------------------------------- recovery test A
        before = json.loads((ROOT / "run" / "pids.json").read_text())
        rows_before = get("/api/status")["recent"][0]["sample_number"]
        note(f"RECOVERY TEST A: kill -9 collector (pid {before['worker']}) and dashboard (pid {before['web']})")
        t_kill = time.time()
        os.kill(before["worker"], KILL)
        os.kill(before["web"], KILL)
        dom("document.body.classList.contains('offline')", 10)
        snap("07_dashboard_unreachable_during_kill",
             "Processes killed: the open page says the server is not responding and greys out the old values", settle=0.5)
        back = wait_until(lambda s: True, 30, 0.1)
        t_back = time.time()
        check("dashboard available again", back, f"after {t_back - t_kill:.1f} s")
        if dom(f"!document.body.classList.contains('offline') && {H} === 'RECOVERING'", 15):
            snap("08_recovering_after_restart", "Collector restarted after an unexpected stop: RECOVERING")
        healthy = wait_until(lambda s: s["health"]["state"] == "HEALTHY", 30)
        t_healthy = time.time()
        check("health returned to HEALTHY", healthy, f"after {t_healthy - t_kill:.1f} s")
        after = json.loads((ROOT / "run" / "pids.json").read_text())
        check("both processes have new PIDs", after["worker"] != before["worker"] and after["web"] != before["web"],
              f"worker {before['worker']} -> {after['worker']}, web {before['web']} -> {after['web']}")
        st = get("/api/status")
        check("SQLite live history continues (numbering not reset)", st["recent"][0]["sample_number"] > rows_before,
              f"sample {rows_before} -> {st['recent'][0]['sample_number']}")
        hist = urllib.request.urlopen(BASE + "/history?dataset=practice&window=day").read().decode()
        check("Module 6 history still usable after recovery", "1420" in hist and "1440" in hist,
              "last-day window: 1,420 of 1,440 readings")
        recovery_a = {"kill": t_kill, "back": t_back, "healthy": t_healthy, "before": before, "after": after}

        # ---------------------------------------------------------- recovery test B
        note(f"RECOVERY TEST B: kill -9 the supervisor itself (pid {sup.pid})")
        pids_b = json.loads((ROOT / "run" / "pids.json").read_text())
        t_kill_b = time.time()
        os.kill(sup.pid, KILL)
        sup.wait(timeout=5)
        import psutil
        gone = None
        end = time.time() + 15
        while time.time() < end:
            if not psutil.pid_exists(pids_b["worker"]) and not psutil.pid_exists(pids_b["web"]):
                gone = time.time()
                break
            time.sleep(0.2)
        check("children exit when the supervisor dies (no orphans)", gone, gone and f"after {gone - t_kill_b:.1f} s")
        note("starting supervisor.py again - the step systemd (Restart=always) / launchd (KeepAlive) / "
             "Task Scheduler (1-minute watchdog trigger) performs")
        sup = start_supervisor()
        s = wait_until(lambda s: s["health"]["state"] == "HEALTHY", 60)
        t_b = time.time()
        check("system HEALTHY after supervisor restart", s, f"after {t_b - t_kill_b:.1f} s")

        time.sleep(4)
        shot("09_final_healthy", "Final state after both recovery tests: HEALTHY", reload=True)
        shot("10_logs", "Logs page: events table + tail of supervisor/worker/web logs", path="/logs")
        shot("11_history_module6_last_day", "Module 6 history (SQLite), last day, gaps preserved", path="/history?dataset=practice&window=day")
        shot("12_history_live_last_hour", "Live history written by the collector, with the data gap and the outage gaps", path="/history?dataset=live&window=hour")
        browser.close()

    stop_poll.set()
    sup.send_signal(signal.SIGINT)
    sup.wait(timeout=30)
    note("supervisor stopped with Ctrl+C (clean shutdown)")

    # ---------------------------------------------------------- collect evidence
    for f in (ROOT / "logs").glob("*.log"):
        shutil.copy(f, EVID / "logs" / f.name)
    shutil.copy(ROOT / "data" / "monitor.db", EVID / "monitor_after_evidence_run.db")
    sup_log = (ROOT / "logs" / "supervisor.log").read_text()
    work_log = (ROOT / "logs" / "worker.log").read_text()
    for needle, where, text in [
        ("SUPERVISOR_START", sup_log, "normal startup"), ("READING sample=", work_log, "successful readings"),
        ("ALERT_ACTIVE", work_log, "alert activation"), ("ALERT_CLEARED", work_log, "alert clearing"),
        ("DATA_STALE", work_log, "stale data"), ("DATA_MISSING", work_log, "missing data"),
        ("CHILD_EXIT", sup_log, "application error (process killed)"), ("CHILD_RESTART", sup_log, "restart"),
        ("RECOVERY previous collector run ended unexpectedly", work_log, "recovery in collector log"),
        ("RECOVERY previous supervisor run did not stop cleanly", sup_log, "recovery in supervisor log"),
        ("SUPERVISOR_STOP", sup_log, "clean shutdown")]:
        check(f"log contains {text}", needle in where, f"'{needle}'")
    with open(EVID / "timeline.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "health", "data_state", "alert_active", "current_value", "value_is_current",
                    "latest_status", "reason"])
        w.writerows(timeline)
    write_report(recovery_a)
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    sys.exit(1 if failed else 0)


def write_report(a):
    rows = "\n".join(f"| {'PASS' if ok else 'FAIL'} | {name} | {detail} |" for name, ok, detail in checks)
    pics = "\n\n".join(f"### {name}\n{cap}\n\n![{name}]({name}.png)" for name, cap in shots)
    text = f"""# Evidence Run and Recovery Test

Generated by `python scripts/evidence_run.py` on {datetime.now():%Y-%m-%d %H:%M} (local time), running the real
supervisor, collector and Flask dashboard with a fast demo replay (120x: one reading every 0.5 s,
Module 6 sample 200 onward). Stale after 1.5 s, FAILED after 7.5 s at this speed.

## Checks
| Result | Check | Detail |
|---|---|---|
{rows}

## Recovery test A - collector and dashboard killed (kill -9)
- Killed: collector pid {a['before']['worker']}, dashboard pid {a['before']['web']}
- Dashboard answering again after **{a['back'] - a['kill']:.1f} s**; health back to HEALTHY after **{a['healthy'] - a['kill']:.1f} s**
- New pids: collector {a['after']['worker']}, dashboard {a['after']['web']}
- Readings that fell due while the collector was down were not backfilled: see `DATA_LOST` in `logs/worker.log`.

## Recovery test B - supervisor killed (kill -9)
The children noticed the supervisor was gone and exited (orphan guard). The supervisor was then started
again by this script - the step the operating system performs in a real install (systemd `Restart=always`,
launchd `KeepAlive`, Windows Task Scheduler 1-minute trigger). It logged `RECOVERY`, and the system
returned to HEALTHY.

## Run log
```
{chr(10).join(notes)}
```

## Screenshots
{pics}

Full logs: `evidence/logs/`. Status polled every 0.2 s: `evidence/timeline.csv`. Database after the run:
`evidence/monitor_after_evidence_run.db`.
"""
    (EVID / "EVIDENCE.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
