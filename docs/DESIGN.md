# Design and Evidence (Module 7 and the Failure Report)

This is the technical design behind the operating guide in `README.md`: how the restart layers work, the exact stale-data and alert rules, validation, the database and the Module 7 evidence. An operator does not need it to run the system.

## How Automatic Startup and Restart Work
Two layers, so no Python file is ever started by hand:

| Layer | What it does | Where |
|---|---|---|
| **Process manager** (`supervisor.py`) | Starts the collector and the dashboard. If either exits or is killed, it restarts it after 1, 2, 4, 8 or 16 s. If one fails more than 5 times in 60 s, it stops trying (see below). | `supervisor.py` |
| **Operating-system service** | Starts the supervisor at boot or logon, and restarts the supervisor itself if it dies. | `deploy/` |

Install the OS layer once (from the project folder, after creating `.venv`):

| OS | Command | Mechanism |
|---|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1` | Task Scheduler. It starts at logon, and a 1-minute watchdog trigger starts the supervisor again if it is not running (`MultipleInstances IgnoreNew`). Uses `pythonw.exe`, so no window appears. |
| macOS | `bash deploy/install_macos.sh` | launchd LaunchAgent with `RunAtLoad` + `KeepAlive`. |
| Linux / Raspberry Pi | `sudo bash deploy/install_linux.sh` | systemd unit with `Restart=always` and `KillMode=mixed`. |

Each script has a `--remove` / `-Remove` option to uninstall.

**Orphan guard.** If only the supervisor is killed, the collector and the dashboard notice within about 2 s and exit (`monitor/guard.py`). A restarted supervisor also stops any leftovers recorded in `run/pids.json`. This prevents two collectors writing at once, or an old dashboard holding the port.

## Sensor / Input Source
The **Module 6 SQLite history** (`data/module6_sensor_history.db`) is replayed in real time:

- **Source data:** the instructor practice temperature sensor, 1,756 readings, one every 60 s, covering 2026-10-01 08:00 to 2026-10-02 14:00.
- **Real gaps kept:** the data has two real gaps of 25 and 20 minutes. The replay keeps them, so data really stops arriving.
- **Replay speed:** `REPLAY_SPEED=30` by default, so one reading reaches the monitor **every 2 s**. After the last row the replay loops, so the system can run indefinitely.
- **Rows stored:** each live reading is stored as a new row in dataset `live`, with `timestamp_source = 'received'` (the time the collector received it) and a `source_ref` naming the replayed Module 6 row and its original time. Timestamps are local time.
- **After a crash:** readings that fell due while the collector was down are **skipped, not backfilled**, just as a real sensor's readings during an outage are lost. The skip is logged as `DATA_LOST`, and the gap stays in the history.

| Setting (environment variable) | Default | Demo used in `evidence/` |
|---|---|---|
| `REPLAY_SPEED` | 30 (one reading every 2 s) | 120 (one reading every 0.5 s) |
| `REPLAY_START_SAMPLE` | 1 | 200 (2026-10-01 11:19) |
| `WEB_PORT` | 5000 | 5000 |

## Expected Sampling Interval
**2 s** by default: the source interval (60 s) divided by `REPLAY_SPEED` (30). Every time rule below is a multiple of it. For the 0.5-s demo, every limit scales the same way.

## Stale-Data Definition
| Data state | Rule (default 2 s interval) | Health |
|---|---|---|
| FRESH | last reading ≤ 3 intervals old (≤ 6 s) | as below |
| STALE | no reading for more than 3 intervals (> 6 s) | **DEGRADED** |
| LOST (missing) | no reading for more than 15 intervals (> 30 s) | **FAILED** |
| Collector dead | collector heartbeat older than max(10 s, 5 intervals) | **FAILED** |

**How the collector checks:** it re-checks freshness every loop, even when nothing arrives, so it notices when data **stops** arriving.

**How the dashboard checks:** it re-checks freshness on its own and can only make the collector's verdict worse, never better.

**When data is stale:**
- **Current value:** the reading is struck through and labelled *"Last known value - STALE, not current"*.
- **Chart:** the missing period is shaded *no data*.
- **Alert:** shown as *"data stale - cannot confirm"*.

**Missing readings:**
- **No value:** a reading that arrives without a value is stored as `MISSING`, with no accepted value.
- **Never filled:** nothing is filled, interpolated or averaged across a gap.

## Threshold Rule
**HIGH_TEMP (warm room):**
- **Activates** when the median of the last 5 accepted readings is **≥ 24.0 °C**.
- **Clears** when that median is **≤ 23.5 °C**.

Why 24.0 °C? I assume the lab's target band is 20-24 °C. In the Module 6 history, temperature crosses 24 °C every afternoon (12:15 on day 1, 12:03 on day 2), so the alert is meaningful and is exercised by real data. All thresholds can be changed with `ALERT_ON_C` / `ALERT_OFF_C`.

## Alert-Control Method
Two techniques are used, so one noisy reading can never raise or clear the alert:

1. **Rolling threshold (median of 5 accepted readings).**
   - **How it decides:** at least 3 of the last 5 readings must be over the limit.
   - **What it ignores:** a single spike changes nothing. Rejected readings (spikes, out-of-range, missing) never enter the window.
2. **Hysteresis.**
   - **The rule:** the alert activates at 24.0 °C but clears only at 23.5 °C.
   - **Why that band:** the band is 0.5 °C, about 3 times the sensor's normal sample-to-sample change. Readings hovering around 24 °C therefore do not make it flap.
   - **Checked on real data:** over the whole Module 6 history the alert changes exactly 3 times (on, off, on), confirmed by `test_alert_on_module6_history`.

**After a data gap:**
- **Window reset:** the window is emptied, so the next decision uses only 5 new, contiguous readings.
- **State kept:** the alert keeps its state during the gap. It is stored in SQLite (`alert_state`), so a restart neither clears nor re-raises it.

## Validation (before anything is stored or used)
| Status | Rule |
|---|---|
| MISSING | no value, or not a finite number |
| FAULT | outside what the sensor can report, -40..125 °C |
| OUT_OF_RANGE | outside the supported room range, 0..50 °C (for example the 58.8 °C spike) |
| SUSPICIOUS | more than 5 °C from the last accepted value, unless the previous reading confirms the new level (Module 5 rule) |
| OK | everything else; `accepted_value = raw_value` |

**Every reading is kept.** Each one is stored with its raw value, status and reason. The database itself refuses an accepted value on a non-OK row (a CHECK constraint carried over from Module 6).

## System-Health States
| State | Meaning |
|---|---|
| **STARTING** | clean start; waiting for the first 5 fresh accepted readings |
| **HEALTHY** | fresh data, accepted readings, under 20 % rejected/missing among the last 30 |
| **DEGRADED** | data stale, or readings arrive but none is accepted, or ≥ 20 % of the last 30 rejected/missing |
| **FAILED** | expected data stopped arriving (> 15 intervals), or the collector process is not running |
| **RECOVERING** | after an unexpected stop, or after FAILED, until 5 fresh accepted readings arrive |

Every change is logged as `HEALTH_CHANGE old -> new: reason`.

## SQLite Integration
`data/monitor.db` is generated by `init_db.py`, or automatically on first start, and is a superset of the Module 6 database.

**Module 6 history, unchanged:**
- **What is imported:** all 8,900 Module 6 rows, with their timestamps, raw and accepted values, status and reason. A test compares them row by row.
- **Where to see it:** the dashboard's **History** page reads them with the Module 6 window and gap code (`monitor/history_m6.py`, copied unchanged): last hour, last day, full history.

**Live data and system state:**
- **Live readings:** go to the same `sensor_readings` table (dataset `live`). The extra `source_ref` column names the replayed Module 6 row.
- **New tables:** `events` (everything shown on the Logs page), `kv` (heartbeats, replay position, health) and `alert_state`.
- **Concurrent access:** WAL mode lets the dashboard read while the collector writes.
- **Not committed:** `data/monitor.db` is generated, so it is not in Git. The database from the evidence run is included as `evidence/monitor_after_evidence_run.db`.

## Log Location
| File | Written by |
|---|---|
| `logs/supervisor.log` | process manager: startup, child exits, restarts, crash loops, clean stop |
| `logs/worker.log` | collector: every reading, rejections, alert on/off, stale/missing data, health changes, recovery |
| `logs/web.log` | dashboard: startup, request errors |
| `logs/*.stderr.log` | anything a child prints before logging starts (tracebacks) |
| SQLite `events` table | the same important events, shown on the dashboard and the **Logs** page |

**Rotation:** each file rotates at 2 MB and keeps 5 old files.

**What gets logged:**
- **Startup:** `SUPERVISOR_START`, `STARTUP`.
- **Readings:** `READING ... status=OK`.
- **Alerts:** `ALERT_ACTIVE`, `ALERT_CLEARED`.
- **Missing or stale data:** `DATA_STALE`, `DATA_MISSING`, `DATA_LOST`.
- **Errors:** `APPLICATION_ERROR`, `READING_ERROR` (one reading failed unexpectedly; skipped, collector keeps running), `CHILD_EXIT` (with `exit n of 6 allowed in 60 s`), `CRASH_LOOP`.
- **Dashboard health:** `DASHBOARD_HEALTH` (the dashboard logs every change of the health it shows, e.g. `HEALTHY -> FAILED`).
- **Recovery and restarts:** `RECOVERY`, `CHILD_RESTART`, `RECOVERED`.

## Recovery Test (evidence)
`python scripts/evidence_run.py` ran the real system and **passed 25 of 25 checks**. Results are in `evidence/EVIDENCE.md`, along with screenshots, logs and a timeline.

| Test | What was done | Result |
|---|---|---|
| A | `kill -9` the collector and the dashboard | Supervisor restarted both. Dashboard back in **1.6 s**, health **HEALTHY in 3.0 s** (via RECOVERING). New PIDs. Live numbering continued (sample 317 → 322). Module 6 history intact (last day 1,420 of 1,440). |
| B | `kill -9` the supervisor | Collector and dashboard exited on their own in **2.4 s** (no orphans). Supervisor started again (the step systemd / launchd / Task Scheduler performs), logged `RECOVERY`, **HEALTHY in 4.3 s**. |

The same two tests also run automatically in `tests/test_supervisor_recovery.py`.

| Required evidence | File in `evidence/` |
|---|---|
| Dashboard running + current reading + timestamp | `01_dashboard_healthy_current_reading.png` |
| Historical view | `11_history_module6_last_day.png`, `12_history_live_last_hour.png`, `13_history_module6_full.png`, live chart on every dashboard shot |
| Alert active | `02_alert_active.png` |
| Alert cleared | `06_alert_cleared.png` |
| Stale / missing data | `03_data_stale.png` (DEGRADED), `04_missing_data_failed.png` (FAILED), `05_recovering_after_data_gap.png` |
| Logs | `10_logs.png`, `logs/*.log` |
| Restart / recovery | `07_dashboard_unreachable_during_kill.png`, `08_recovering_after_restart.png`, `EVIDENCE.md` |
| Final healthy state | `09_final_healthy.png` |

## Failure Report (malformed input)
Full report: **`FAILURE_REPORT.md`**. In short:

- **Failure:** the replay feed sends `'ERR'`, `''`, `'23.9C'`, `'nan'` for samples 400-459.
- **Original code (git `23b199e`):** the collector crashed on the first `'ERR'` (`ValueError` formatting a string with `.2f`), the same reading was re-delivered after every restart, the supervisor hit `CRASH_LOOP` after 6 exits and stopped restarting, and the dashboard returned HTTP 500. The dashboard also showed HEALTHY for about 7 s while the collector was down. A manual restart was needed.
- **Cause 1 (malformed input) confirmed, cause 2 (database) rejected:** `evidence/failure_run/diagnosis_tests.txt`.
- **Improved code:** the same feed gives no crash, page and chart stay at 200, health goes DEGRADED and then back to HEALTHY by itself. All 15/15 checks pass.

Reproduce:
```bash
python -m scripts.make_malformed_feed          # build the malformed feed
python scripts/failure_run.py verify           # improved code, evidence -> evidence/verify_run/
SOURCE_DATASET=practice_malformed REPLAY_START_SAMPLE=370 REPLAY_SPEED=60 python supervisor.py   # watch it live
python -m pytest tests/test_failure_malformed.py -v
```
`raw_value` now only ever holds a number; whatever text the sensor sent is stored in the new `raw_text` column (old databases are migrated automatically on start).

## What the System Recovers From Automatically
- **Collector or dashboard crash, or kill:** restarted by the supervisor within 1-16 s.
- **Supervisor crash or kill, reboot, logout/login:** restarted by systemd / launchd / Task Scheduler. Leftover children exit by themselves.
- **Power loss or unclean stop:** detected at the next start and logged as `RECOVERY`. The replay position, alert state and full history come back from SQLite.
- **Data gaps (sensor stops sending):** detected as STALE, then FAILED. When data returns: RECOVERING, then HEALTHY. The gap stays visible and is never filled.
- **Short database lock or error:** the collector retries up to 10 times, then exits and is restarted.
- **Bad readings:** stored with a reason and excluded from the alert and from "current value".
- **Malformed input** (`'ERR'`, `'23.9C'`, `''`, `'nan'`): classified as `FAULT` / `MISSING`, the exact text kept in `raw_text`, health DEGRADED while it lasts, HEALTHY again by itself when numbers return. See `FAILURE_REPORT.md`.

## What Still Requires Human Intervention
- **Crash loop.** If a component fails more than 5 times in 60 s (`CRASH_LOOP` in `logs/supervisor.log`), it is not restarted again. The dashboard then shows FAILED "collector stopped by the crash-loop guard … human intervention required". A person must read `logs/<name>.stderr.log`, fix the cause and restart the supervisor.
- **A sensor that stays down.** The system reports FAILED but cannot fix the sensor.
- **Readings lost during an outage.** They are not recovered (by design).
- **Responding to an alert.** The system raises and logs it, but nobody is notified outside the dashboard; there is no email or SMS.
- **Database problems.** A corrupted or deleted `data/monitor.db`, or a full disk, needs a person. They can rebuild it with `python init_db.py --reset`, which loses live history, events and alert state.
- **Changing the expected interval.** Changing `REPLAY_SPEED` changes the interval; older live rows are then judged with the new one. This is logged as `CONFIG_CHANGE`.
- **Port 5000 in use.** If another program holds the port, the dashboard crash-loops. Set `WEB_PORT`.

## Known Limitations
- **Replay, not hardware.** The input is a replay of recorded data. A live serial sensor would replace `monitor/replay.py` only.
- **One sensor, one alert.** The alert is local to the dashboard.
- **Local, single-user dashboard.** It listens on 127.0.0.1, with no login. To expose it on a network, set `WEB_HOST` and add authentication first.
- **Local time.** Timestamps are local time without a time zone (the Module 6 convention). A daylight-saving change can create an apparent 1-hour gap or overlap.
- **OS-level restart not tested in the sandbox.** It was tested here by starting the supervisor again, as the OS would. Run the install script on your own machine and kill the supervisor (Task Manager / `kill -9`) to confirm it there.

## Repository Layout
```
supervisor.py            process manager (the one command to run)
init_db.py               reproducible database generation
monitor/                 collector (worker.py), dashboard (web.py, templates/), validation, alerts,
                         health, replay, charts, db, logging_setup, guard, history_m6 (Module 6, unchanged)
deploy/                  systemd unit + Linux, macOS (launchd) and Windows (Task Scheduler) installers
data/                    module6_sensor_history.db (input); monitor.db is generated
scripts/evidence_run.py  automated evidence + recovery test
scripts/make_malformed_feed.py  builds the 'practice_malformed' replay feed (samples 400-459 malformed)
scripts/failure_run.py   failure experiment: `failure` (original code) and `verify` (improved code)
FAILURE_REPORT.md        failure report: malformed input (also .docx)
evidence/                screenshots, EVIDENCE.md, logs, timeline.csv, database snapshot
monitor/preflight.py     pre-start checks (python supervisor.py --check)
handoff/                 8.1 silent handoff: task sheet, observation sheet, dry run, report
tests/                   78 tests (test_failure_malformed.py = failure report, test_handoff.py = 8.1)
```

