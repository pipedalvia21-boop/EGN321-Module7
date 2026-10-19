# Failure Report: Malformed Sensor Input Takes Down the Collector

**Course:** EGN 321, Module 7 Failure Report · **Author:** Felipe D'Alvia · **Date:** 2026-10-18 (all times America/New_York)
**System:** the Module 7 unattended temperature monitor (replay collector → validation → SQLite → alert → Flask dashboard, run by `supervisor.py`)
**Scenario chosen:** *provide malformed input*
**Code under test:** git `23b199e` (before the fix). The improved code is in commit `7c33913`.

Evidence folders:

| Folder | Contents |
|---|---|
| `evidence/failure_run/` | the failure, on the original code: logs, timeline, screenshots, the database copied **before** recovery, diagnosis output |
| `evidence/verify_run/` | the same malformed feed replayed on the **improved** code |

Each run can be reproduced with `python scripts/failure_run.py failure` or `python scripts/failure_run.py verify`.

---

## System Before Failure

Recorded at **22:58:18.693**, from `evidence/failure_run/run_log.md`, `status_1_normal.json` and screenshot `01_normal_state.png`.

| Item | Value |
|---|---|
| System status | **HEALTHY** ("data fresh: last reading 0.0 s ago"), entered at 22:58:16.340 |
| Latest sensor reading | **24.35 °C** at 22:58:18.354 (source sample 376), status OK |
| Dashboard availability | `GET /` → HTTP 200; `GET /chart/live.svg` → HTTP 200 |
| Database availability | `PRAGMA integrity_check` = ok; 7 live rows; Module 6 history intact (8,900 rows) |
| Alert state | **HIGH_TEMP ACTIVE** since 22:58:16.337 (median of 5 readings was 24.12 °C, threshold ≥ 24.0 °C) |

Run settings:

- Replay at 60× speed, so one reading per second.
- Start at source sample 370.
- Supervisor pid 219.

## Failure Introduced

**Exact action.** I ran `python -m scripts.make_malformed_feed` to build a replay dataset called `practice_malformed`. It is a copy of the Module 6 `practice` temperature series, except that samples **400–459** were replaced with what a broken serial sensor sends: `'ERR'`, `''`, `'23.9C'` and `'nan'`. The collector was then started with `SOURCE_DATASET=practice_malformed`. This is the same as plugging in a sensor that begins sending error strings partway through operation.

**Time of failure.** The first malformed reading (sample 400, value `'ERR'`) was delivered at **22:58:42.46**. The bad block lasted 60 readings, until about 22:59:42. From sample 460 onward the input was numeric again.

**Expected effect.** Validation should reject each bad reading and store it with a reason. The health state should become DEGRADED, because many readings are rejected. The dashboard should keep working, and the collector should keep running. When numeric input returns, the system should become HEALTHY again by itself.

**Actual effect.** The collector crashed on the first `'ERR'` value. The supervisor restarted it five times, and each restart crashed again. The crash-loop guard then **stopped restarting it permanently** at 22:59:16.247. The dashboard page and the chart returned **HTTP 500**. For about 7 seconds the dashboard still showed **HEALTHY** while the collector was dead. When the sensor recovered at 22:59:42, the system **did not recover**: an operator had to restart the service. In total, 61 + 32 + 16 + 7 + 3 readings fell due while the collector was down and were never recorded.

## First Observable Symptom

| | What | Evidence |
|---|---|---|
| **Root cause** | The collector assumed that every raw value is a number. Validation (`validate`) treated *any* value it could not compare as "missing", so `'ERR'` passed through classified as MISSING. Then the log line in `Worker.process` formatted it with `f"{row.raw_value:.2f}"`, which raises `ValueError` on a string. Nothing caught the exception at the level of a single reading, so the whole process exited. | Traceback in `logs/worker.log` (22:58:42,463); reproduced by the Cause 1 test |
| **First observed symptom** | **22:58:42,463 `ERROR worker APPLICATION_ERROR collector crashed`**, with `ValueError: Unknown format code 'f' for object of type 'str'` at `worker.py` line 171. This is the first warning or error in any log after normal operation. | `evidence/failure_run/logs/worker.log` |
| **Downstream symptoms** | 1. **22:58:42,692**: the supervisor logs `CHILD_EXIT … exit code 1`.<br>2. **22:58:42,695**: the dashboard `GET /` returns 500 (`TypeError: must be real number, not str`) and the chart returns 500 (`'>' not supported between 'str' and 'float'`). This happens because the text `'ERR'` had already been stored in the numeric `raw_value` column. There were 518 tracebacks in `web.log`.<br>3. The same poison row is re-delivered after every restart (sample 400 is stored twice), because the replay cursor was saved *after* processing.<br>4. DATA_LOST gaps of 3, 7, 16 and 32 readings.<br>5. **22:58:49**: health becomes DEGRADED (stale data).<br>6. **22:58:52**: health becomes FAILED (heartbeat 35 s old). This change was never written to a log.<br>7. **22:59:16,247**: `CRASH_LOOP`, and automatic restarts stop.<br>8. The system stays down after the input becomes valid again. | supervisor.log, web.log, timeline.csv, db_before_recovery.db |

A hidden symptom in the database: the six stored bad rows all say `status=MISSING, reason='no sensor value received'`. That is false, because a value *was* received; it simply was not a number. You can check this with `sqlite3 db_before_recovery.db "SELECT … WHERE typeof(raw_value)='text'"`.

## Log Evidence

All of the following is copied unchanged from `evidence/failure_run/logs/`. That folder holds the full logs after recovery; `logs_before_recovery/` holds them exactly as they were at 22:59:17.

```
worker.log
22:58:16,340 INFO     worker     HEALTH_CHANGE STARTING -> HEALTHY: data fresh: last reading 0.0 s ago
22:58:42,463 ERROR    worker     APPLICATION_ERROR collector crashed; the supervisor will restart it
Traceback (most recent call last):
  File ".../monitor/worker.py", line 253, in main
    worker.step()
  File ".../monitor/worker.py", line 142, in step
    self.process(row, cycle, now)
  File ".../monitor/worker.py", line 171, in process
    raw = "none" if row.raw_value is None else f"{row.raw_value:.2f}"
ValueError: Unknown format code 'f' for object of type 'str'
22:58:43,771 WARNING  worker     RECOVERY previous collector run ended unexpectedly (last heartbeat 22:58:41.708; down about 2 s)
22:58:43,794 ERROR    worker     APPLICATION_ERROR collector crashed; the supervisor will restart it
22:58:46,303 WARNING  worker     DATA_LOST 3 readings fell due while the collector was not running ...
...
22:59:15,843 WARNING  worker     DATA_LOST 32 readings fell due while the collector was not running ...
22:59:15,844 ERROR    worker     APPLICATION_ERROR collector crashed; the supervisor will restart it

supervisor.log
22:58:42,692 ERROR    supervisor CHILD_EXIT worker (pid 222) stopped unexpectedly: exit code 1
22:58:42,693 INFO     supervisor RESTART_SCHEDULED worker will restart in 1 s
...                   (restart in 2, 4, 8, 16 s)
22:59:16,246 ERROR    supervisor CHILD_EXIT worker (pid 309) stopped unexpectedly: exit code 1
22:59:16,247 CRITICAL supervisor CRASH_LOOP worker stopped 6 times in 60 s; automatic restart disabled - human intervention required

web.log
22:58:42,695 ERROR    web        APPLICATION_ERROR request / failed
22:58:42,699 ERROR    web        APPLICATION_ERROR /: TypeError: must be real number, not str
22:58:42,704 ERROR    web        APPLICATION_ERROR request /chart/live.svg failed
```

How the logs answer the required questions:

| Question | Answer |
|---|---|
| First error | `APPLICATION_ERROR` in worker.log at 22:58:42,463 |
| Sequence | crash → exit → backoff restart (×5) → `CRASH_LOOP` → supervisor gives up |
| Component affected | the **collector (worker)** first, then the **dashboard (web)** through the shared database |
| Resulting state | collector stopped, dashboard FAILED, page and chart returning 500, alert frozen at ACTIVE |

## Timeline of Events

The full timeline, with a poll every 0.5 s, is in `evidence/failure_run/timeline.csv`. The evidence table:

| Time | Component | Event | Evidence | Interpretation |
|---|---|---|---|---|
| 22:58:12.150 | Supervisor | SUPERVISOR_START pid 219 | supervisor.log | Service started |
| 22:58:16.338 | Alert | ALERT_ACTIVE HIGH_TEMP (median 24.12 °C) | worker.log | Normal alert behaviour; median ≥ 24.0 |
| 22:58:16.340 | Health | STARTING → HEALTHY | worker.log HEALTH_CHANGE | Normal operation established |
| 22:58:18.693 | All | Normal state recorded | run_log.md, `01_normal_state.png` | Baseline: 24.35 °C, HTTP 200, DB ok |
| 22:58:42.46 | Sensor | Sample 400 delivers `'ERR'` | timeline.csv `latest_raw='ERR'` | **Failure introduced** |
| 22:58:42.463 | Collector | APPLICATION_ERROR, ValueError `.2f` on str | worker.log traceback | **First observable failure**; the collector cannot handle text |
| 22:58:42.692 | Supervisor | CHILD_EXIT exit code 1, restart in 1 s | supervisor.log | Process-level failure detected |
| 22:58:42.695 | Dashboard | `GET /` 500, chart 500 | web.log TypeError | The text `'ERR'` stored in the numeric column breaks the web too |
| 22:58:42.7 → 22:58:48.9 | Health | Dashboard still shows **HEALTHY** | timeline.csv | **False health**: the heartbeat is only seconds old, so the restarts are invisible |
| 22:58:43.771 | Collector | RECOVERY, then crashes again on sample 400 | worker.log; DB has sample 400 twice | The cursor was not saved, so the poison row is re-delivered |
| 22:58:46.303 | Collector | DATA_LOST 3 (then 7, 16, 32) | worker.log | Backoff downtime turns into unrecorded data |
| 22:58:49.461 | Health | → DEGRADED (data stale 3.1 s) | timeline.csv | Dashboard notices missing data |
| 22:58:52.053 | Health | → FAILED (heartbeat old) | timeline.csv; **no log line** | Correct state, but nothing recorded *when* it happened |
| 22:59:16.247 | Supervisor | CRASH_LOOP: restarts disabled | supervisor.log CRITICAL | Automatic recovery has given up |
| 22:59:17.829 | Operator | Logs and DB copied (`logs_before_recovery/`, `db_before_recovery.db`) | run_log.md, `04_failed_state.png` | Evidence preserved before any change |
| 22:59:42 | Sensor | Sample 460: numeric again | timeline.csv | Input restored, but the collector is still down (FAILED, "heartbeat 63 s ago") |
| 22:59:44.769 | Supervisor | SUPERVISOR_STOP (clean), then SUPERVISOR_START pid 314 | supervisor.log | Operator restart (manual recovery) |
| 22:59:44.952 | Collector | RECOVERY (down about 63 s); ALERT_RESTORED ACTIVE | worker.log | State resumed from SQLite, alert not reset |
| 22:59:44.974 | Collector | DATA_LOST 61 | worker.log | The outage gap is recorded honestly (no backfill) |
| 22:59:44.978 | Health | (none) → DEGRADED (6 of last 30 rejected) | worker.log | Recent bad data still counted |
| 22:59:47.498 | Collector | RECOVERED: 5 fresh accepted readings | worker.log | Input confirmed flowing |
| 22:59:54.4 | Dashboard | `GET /` 200 again; chart still 500 | timeline.csv | The page is back; the chart is still broken by the `'ERR'` rows in its 5-minute window |
| 23:00:07.389 | Health | DEGRADED → HEALTHY | worker.log HEALTH_CHANGE | **Recovery verified**, 22.7 s after restart |
| 23:00:59.532 | Alert | ALERT_CLEARED (median 23.49 ≤ 23.5) | worker.log | The alert still evaluates correctly after recovery |

## Possible Cause 1

**The collector cannot handle a non-numeric raw value (malformed input), and the crash repeats because the same reading is re-delivered.**

**Supporting evidence:**

- The traceback names `f"{row.raw_value:.2f}"` and `ValueError … type 'str'`.
- The crash happens on the exact second sample 400 (`'ERR'`) is due.
- Every restart crashes within 20 ms of starting.
- The database holds sample 400 **twice**, plus `''`, `'nan'` and more `'ERR'` rows with `typeof(raw_value)='text'`.
- The web errors are type errors (`str` vs `float`).

**Missing evidence:**

- The log never says *which reading* or *what value* caused the crash. There is no sample number and no raw value in the error, so the link to sample 400 had to be made by matching timestamps against `timeline.csv` and the DB.
- The log does not say that validation misclassified `'ERR'` as "missing".

**Deterministic test:** feed `24.1`, `'ERR'`, `''` and `'23.9C'` directly to `Worker.process` on the original code (git worktree of `23b199e`). If the cause is right, every text value raises the same `ValueError` and every number succeeds, regardless of timing or the database.

**Result: CONFIRMED** (`evidence/failure_run/diagnosis_tests.txt`):

```
Worker.process(raw=24.1)    -> OK, no exception
Worker.process(raw='ERR')   -> ValueError: Unknown format code 'f' for object of type 'str'
Worker.process(raw='')      -> ValueError: Unknown format code 'f' for object of type 'str'
Worker.process(raw='23.9C') -> ValueError: Unknown format code 'f' for object of type 'str'
validate('ERR') -> Decision(status='MISSING', ..., reason='no sensor value received')
rows stored with TEXT in the numeric raw_value column before the exception: 3
```

## Possible Cause 2

**A database problem (locked, corrupted or not writable) killed the collector.** This is plausible because the dashboard broke at the same moment, `CHILD_EXIT` only says "exit code 1", and both processes share one SQLite file.

**Supporting evidence:**

- Both the collector and the dashboard failed within 0.3 s of each other.
- Both read and write the same `monitor.db`.
- The supervisor log alone does not show a reason.

**Missing evidence:**

- There is no `sqlite3.OperationalError`, no "database is locked" and no "disk I/O error" anywhere in the logs.
- The traceback does not pass through any database call.

**Deterministic test**, run on a copy of `db_before_recovery.db` (the database exactly as it was during the failure):

1. `PRAGMA integrity_check` and `quick_check`.
2. A write test (insert, then rollback).
3. A count of SQLite errors in worker.log.
4. The **same original code** run against the **same database** with the clean `practice` feed for 30 simulated seconds.

If the database were the cause, step 1, 2 or 4 would fail.

**Result: REJECTED:**

```
PRAGMA integrity_check -> ok
PRAGMA quick_check     -> ok
write test (insert + rollback) -> OK
sqlite3 errors in worker.log: 0 | 'database is locked': 0
original collector on the SAME database with the clean 'practice' feed: 30 s, no exception, 31 readings written, health HEALTHY
```

The database was a *victim*, because it held text in a numeric column. It was not the cause. Its own state was healthy.

## Recovery Procedure

Recovery did **not** delete evidence or start over:

1. **Preserved the evidence** at 22:59:17:
   - Copied `logs/` to `logs_before_recovery/`.
   - Made an online SQLite backup of `monitor.db` to `db_before_recovery.db`.
   - Saved `status_3_failed.json` and screenshot `04_failed_state.png`.
2. **Confirmed the input was valid again.** The feed delivered numeric samples from 460 on (22:59:42). The collector stayed down, which proves the guard does not self-heal.
3. **Restarted the service** the documented way:
   - Linux: `sudo systemctl restart sensor-monitor`
   - Windows/macOS: Ctrl+C, then `python supervisor.py`

   At 22:59:44.769 the supervisor stopped cleanly; at 22:59:44.880 it started again (pid 314). Starting the supervisor clears the crash-loop guard.
4. **Did not touch the database.** The collector resumed from the replay cursor and the alert state stored in SQLite (`RECOVERY`, `ALERT_RESTORED ACTIVE since 22:58:16.337`). It logged the 61-reading gap as `DATA_LOST` instead of inventing values.

Lasting recovery needed the code fix (see Improvement Made). With the improved code, the restart is no longer needed at all.

## Verification of Recovery

**Original code, after the operator restart** (`evidence/failure_run/checks.md`, screenshot `05_recovered_healthy.png`):

| Requirement | Result |
|---|---|
| Sensor/replay input resumed | ✅ samples 460 → 485 stored, RECOVERED at 22:59:47.498 |
| Database available | ✅ `integrity_check` ok; live rows 7 → 61; Module 6 history still 8,900 rows |
| Dashboard works | ⚠️ page 200 from 22:59:54; **chart still 500** (the `'ERR'` rows in its window); the only FAIL, 14/15 |
| Alerts behave correctly | ✅ restored as ACTIVE (not reset), then ALERT_CLEARED at 23:00:59 with median 23.49 ≤ 23.5 |
| Health back to expected state | ✅ DEGRADED → HEALTHY at 23:00:07, 22.7 s after restart |

**Improved code, same malformed feed** (`evidence/verify_run/checks.md`, **15/15 PASS**):

| Requirement | Result |
|---|---|
| Collector never crashed | ✅ 0 CHILD_EXIT, 0 tracebacks in any log |
| Malformed readings handled | ✅ each stored once as `FAULT` with the exact text, e.g. `READING_REJECTED sample=31 raw='ERR' status=FAULT: malformed value 'ERR': not a number (source practice_malformed#400 …)` |
| Health during the block | ✅ HEALTHY → **DEGRADED** at 23:08:14 ("readings arrive but none was accepted recently"), never FAILED |
| Dashboard during the block | ✅ page **200**, chart **200**; screenshots `03_…` and `04_during_malformed_block.png` |
| Recovery without intervention | ✅ DEGRADED → HEALTHY at 23:09:36 (worker log and dashboard `DASHBOARD_HEALTH`) |
| Database | ✅ integrity ok, live rows 7 → 115, Module 6 history intact |
| Alert | ✅ stayed ACTIVE through the incident, cleared at 23:10:29 (median 23.49) |

Automated tests: `tests/test_failure_malformed.py` adds 17 tests, and all **67 tests pass**. The tests check that:

- Every malformed form is classified correctly.
- The database refuses text in `raw_value`.
- The collector survives the whole 60-row block with no duplicates and no FAILED state.
- Rejected-reading logs show the exact text.
- An unexpected error is isolated to one reading and that reading is not re-delivered.
- The cursor is saved first.
- The dashboard reports a crash loop and a restart.
- `DASHBOARD_HEALTH` is logged.
- **The real failure database is migrated and renders HTTP 200.**

## What the Logs Told Me

- **Which component failed first, and when:** the collector, at 22:58:42,463. Then the supervisor (CHILD_EXIT) and the web (500), within 0.3 s.
- **The exact line and exception:** `worker.py:171`, `ValueError: Unknown format code 'f' for object of type 'str'`. This pointed straight at a type problem.
- **How the supervisor reacted:** each restart and its backoff (1, 2, 4, 8, 16 s), and that it gave up (`CRASH_LOOP`, CRITICAL), with "human intervention required".
- **The cost of the outage:** `DATA_LOST` counts after each restart (3, 7, 16, 32, 61), and the last good heartbeat (22:58:41.708) and downtime in every `RECOVERY` line.
- **That the alert state survived:** `ALERT_RESTORED … ACTIVE since 22:58:16.337` on every restart.

## What the Logs Did Not Tell Me

1. **Which reading caused the crash.** The error had no sample number, no source reference and no raw value. I had to match timestamps against the replay schedule and the DB.
2. **That the same reading was being retried.** Nothing said "sample 400 again". The duplicate only showed up in the DB.
3. **That validation misclassified the value.** The DB said "no sensor value received" for `'ERR'`, which is the opposite of the truth.
4. **When the dashboard showed FAILED, or that it showed HEALTHY while the collector was crash-looping.** The dashboard computes its own health and never logged it. The 7 s of false HEALTHY and the switch to FAILED at 22:58:52 are only visible in my external poller (`timeline.csv`).
5. **How close the supervisor was to giving up.** `CHILD_EXIT` did not say "exit 3 of 6".
6. **Why the web failed, in a usable way.** 518 identical tracebacks in `web.log` buried the useful first one.
7. **The database path** in the crash message. This matters when several copies exist.

**Fields and events that would have made diagnosis easier:**

- In the collector error: the `sample` / `source_ref`, the raw value as `repr()` (so `'ERR'`, `''` and `None` can be told apart), the exception type and the DB path.
- An exit counter on `CHILD_EXIT`.
- A `DASHBOARD_HEALTH` event whenever the dashboard's own health changes.
- Rate-limiting of repeated web tracebacks.

## Improvement Made

All in commit `7c33913`, tested by `tests/test_failure_malformed.py`:

| # | Improvement | File | Effect |
|---|---|---|---|
| 1 | **Malformed input is a `FAULT`, not "missing".** `parse_raw()` separates *missing* (`None`, `''`, `'nan'`) from *malformed* (`'ERR'`, `'23.9C'`, `'--'`, bools). The reason quotes the text: `malformed value 'ERR': not a number`. | `monitor/validation.py` | The root cause is fixed; the DB tells the truth |
| 2 | **New `raw_text` column**, with an automatic migration. `raw_value` only ever holds a number; `insert_reading` raises `ValueError` if text is passed. | `monitor/db.py` | Text can no longer poison numeric code in the web or the chart |
| 3 | **Per-reading error isolation:** an unexpected error in one reading logs `READING_ERROR component=collector source practice_malformed#395 … raw=…: RuntimeError: … - reading skipped, collector continues` with the traceback. Database errors still stop the process, so the supervisor handles those. | `monitor/worker.py` | One bad reading cannot take the service down |
| 4 | **Cursor saved before processing** (at-most-once delivery). | `monitor/worker.py` | No poison-row re-delivery and no duplicate rows |
| 5 | **Log fields:** the sample number, the source ref, the raw value as `repr`, the exception type, "while processing …", and the DB path in the crash message. | `monitor/worker.py` | What was missing in this investigation |
| 6 | **Retry count:** `CHILD_EXIT … [exit 3 of 6 allowed in 60 s; details in logs/worker.log]` and `RESTART_SCHEDULED … restart 3 of 5`. On a crash loop the supervisor writes `crash_loop` to the database, along with recovery instructions. | `supervisor.py` | You can see how close it is to giving up |
| 7 | **Dashboard health uses the supervisor's information:** FAILED "collector stopped by the crash-loop guard … NOT restarting automatically – human intervention required"; DEGRADED "collector restarting" / "collector unstable". It is never HEALTHY while the collector is restarting. | `monitor/health.py` | Removes the 7 s of false HEALTHY |
| 8 | **`DASHBOARD_HEALTH` events:** the dashboard logs its own state changes. | `monitor/web.py` | The FAILED moment is now in the logs |
| 9 | **Rate-limited web errors:** one full traceback per minute per error, plus a count of suppressed repeats. Displays and charts use only numeric values; the table shows `'ERR'` safely. | `monitor/web.py`, `charts.py`, `dashboard.html` | Logs stay readable; old databases still render |

## Remaining Limitations

- **The crash-loop guard still needs a human** for errors that are *not* per-reading, such as a broken database or a code bug at startup. This is on purpose: endless restarts hide problems. But the system cannot fix itself in that case.
- **At-most-once delivery:** if the process is killed between saving the cursor and storing the row, that one reading is lost. It shows up as a gap, not as a duplicate. I chose this over a crash loop.
- **No alarm leaves the machine.** CRASH_LOOP and FAILED appear on the dashboard and in the logs, but nobody is emailed or paged.
- **During a long malformed block, the alert freezes** at its last value. This is correct, because no accepted readings means no new evidence, and the dashboard shows DEGRADED. But the alert does not say that it is based on old data.
- **Only one kind of malformed input was tested:** text in a single value field. Malformed *timestamps*, truncated CSV lines or binary garbage are not covered.
- Existing text rows in old databases are displayed safely but are **not rewritten**. Their original reason ("no sensor value received") stays as historical evidence.
- The tests ran on Linux (Python 3.13). The Windows and macOS startup paths were not run in this investigation.
