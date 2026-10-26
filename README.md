# Lab Temperature Monitor: Operating Guide

**EGN 321 · Felipe D'Alvia.** This guide is for someone who has **never seen this project**. Follow it from top to bottom; you should not need to ask the author anything.

**Contents**

1. [Project Overview](#1-project-overview)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [Quick Start](#4-quick-start)
5. [Normal Operation](#5-normal-operation)
6. [Inputs and Units](#6-inputs-and-units)
7. [What the Tool Refuses](#7-what-the-tool-refuses)
8. [Expected Output](#8-expected-output)
9. [System Health and Alerts](#9-system-health-and-alerts)
10. [Logs](#10-logs)
11. [Troubleshooting](#11-troubleshooting)
12. [Recovery Procedures](#12-recovery-procedures)
13. [Assumptions](#13-assumptions)
14. [Known Limitations](#14-known-limitations)
15. [Testing](#15-testing)
16. [AI Usage](#16-ai-usage)

Other documents: [`docs/DESIGN.md`](docs/DESIGN.md) (technical design and Module 7 evidence), [`FAILURE_REPORT.md`](FAILURE_REPORT.md) (failure investigation), [`handoff/`](handoff/) (the silent-handoff test).

---

## 1. Project Overview

**What it does.** It watches a lab room's temperature sensor **unattended**:

- It reads a new temperature every few seconds.
- It checks each reading before using it.
- It stores every reading in a SQLite database.
- It shows the current temperature, a chart and the system's health on a web page (the **dashboard**).
- It raises a **warm-room alert** when the room is above 24 °C.
- It writes everything that happens to log files.
- If one of its parts crashes, it restarts that part by itself.

**What problem it solves.** Before this, someone had to open the sensor data and check the numbers by hand. Nobody noticed a warm room, or a sensor that had **stopped sending data**, until they happened to look. This system notices both, and it says when it *cannot* trust the data instead of showing an old number as if it were current.

**What it replaced.** It replaces the manual process from Modules 4–6: opening the instructor's sensor CSV export (the practice temperature workbook), reading values by eye and judging gaps by hand. The Module 6 SQLite history of that sensor is the input here.

**Intended user.** A lab technician or student who needs to know *"is the room OK, and can I trust that answer right now?"* No programming knowledge is needed beyond copying the commands below.

**About the sensor.** There is no physical sensor attached. The system **replays** the real recorded sensor data (1,756 readings taken one minute apart on 1–2 Oct 2026) at real-time pace. It replays faster when asked. See [Assumptions](#13-assumptions).

## 2. System Requirements

| Requirement | Details |
|---|---|
| Operating system | Windows 10/11, macOS 12 or newer, or Linux |
| Python | **3.10 or newer**; tested on 3.10, 3.11, 3.12 and 3.13. Check with `python --version` (Windows) or `python3 --version` (macOS/Linux). |
| Python libraries | `flask`, `waitress`, `psutil`, `pytest`. They are listed in `requirements.txt` and installed in step 3. |
| Browser | any current browser (Chrome, Edge, Firefox, Safari) |
| Git | to download the repository; alternatively download the ZIP from GitHub |
| Required file | `data/module6_sensor_history.db`, the input data. It is **included in the repository**; do not delete it. |
| Database | SQLite, built into Python, so there is nothing to install. `data/monitor.db` is created automatically on first start. |
| Disk | about 20 MB |
| Network | internet only to install the libraries. The dashboard runs on your own computer at `http://127.0.0.1:5000` and needs **port 5000 free** (another port can be chosen). |
| Environment variables | none required; see [Inputs and Units](#6-inputs-and-units) for optional settings |

## 3. Installation

Run these commands **one at a time** in a terminal. On Windows use PowerShell; on macOS use Terminal.

**Windows (PowerShell)**

```powershell
git clone https://github.com/pipedalvia21-boop/EGN321-Module7.git
cd EGN321-Module7
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python supervisor.py --check
```

**macOS / Linux (Terminal)**

```bash
git clone https://github.com/pipedalvia21-boop/EGN321-Module7.git
cd EGN321-Module7
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python supervisor.py --check
```

**What each step does:**

1. **Download:** `git clone` downloads the project.
2. **Enter the folder:** `cd` moves into it. Every later command must be run **inside this folder**.
3. **Create the environment:** `python -m venv .venv` creates a private Python environment.
4. **Activate it:** `Activate.ps1` / `source` turns the environment on. Your prompt now starts with **`(.venv)`**.
5. **Install:** `pip install` installs the libraries.
6. **Verify:** `python supervisor.py --check` checks that everything is ready **without starting anything**.

**Expected result of the last command:**

```
Pre-start checks:
  [PASS] Python version: 3.13.x
  [PASS] Required packages: flask, waitress, psutil installed
  [PASS] Input data (Module 6 history): module6_sensor_history.db: 8900 rows
  [PASS] Database folder writable: ...
  [PASS] Log folder writable: ...
  [PASS] Monitor database: monitor.db will be created on first start
  [PASS] Replay source dataset: practice/temperature: 1756 readings
  [PASS] Dashboard port 5000 free: http://127.0.0.1:5000

RESULT: ready - start it with:  python supervisor.py
```

Any `[FAIL]` line is followed by a `-> fix:` line that tells you exactly what to do. Common problems are covered in [Troubleshooting](#11-troubleshooting).

> **No Git?** On the GitHub page click **Code → Download ZIP**, unzip it, open a terminal in the unzipped `EGN321-Module7` folder and continue from `python -m venv .venv`.
>
> **Opening a new terminal later?** Go back into the folder and activate the environment again (`cd EGN321-Module7`, then step 4). Skip the other steps.

## 4. Quick Start

1. Install as in [section 3](#3-installation). `python supervisor.py --check` must say **ready**.
2. Start the system: **`python supervisor.py`**. Leave this window open; the system runs as long as it is open.
3. Open **http://127.0.0.1:5000** in your browser.
4. Wait about 10 seconds. The top banner turns green: **HEALTHY**.
5. Read the **Current temperature**, the **Warm-room alert** box and the chart.
6. Click **Logs** (top of the page) to see what the system recorded.
7. Stop it: click the terminal window and press **Ctrl+C**. It prints `supervisor stopped cleanly`.

There is no data to load and no button to press. The system feeds itself from the included sample data as soon as it starts.

**Fast demo (about 1 minute; shows an alert and a data gap):**

```
python supervisor.py --speed 120 --start-sample 240
```

It plays one reading every 0.5 s, starting just before the afternoon warm period. The expected sequence is:

| Time after start | What happens |
|---|---|
| ~3 s | **HEALTHY** |
| ~8 s | alert **ACTIVE** |
| ~35 s | the recorded data has a real 26-minute gap. Health goes **DEGRADED**, then **FAILED** at about 41 s, then **RECOVERING** and **HEALTHY** again at about 48 s. |

## 5. Normal Operation

There is nothing to type into the application while it runs. The normal workflow is to **start → watch → check → stop**.

| Step | What to do | What you should see |
|---|---|---|
| 1. Start | In the project folder, with `(.venv)` active: `python supervisor.py` | The terminal prints `Sensor monitor running. Open the dashboard: http://127.0.0.1:5000`, then lines such as `SUPERVISOR_START` and `CHILD_START started worker` / `started web`. |
| 2. Open | Go to **http://127.0.0.1:5000** | The **Live** page. The banner says **STARTING** for the first ~10 s (waiting for 5 good readings), then **HEALTHY**. |
| 3. Read the temperature | Look at **Current temperature** | A value such as **23.68 °C**, a green **Current** badge, the reading time and its **Age** (a few seconds). The page updates itself every second; you do not need to reload it. |
| 4. Check the alert | Look at **Warm-room alert (HIGH_TEMP)** | **CLEAR** (green) or **ACTIVE** (red), the rolling median, and since when. |
| 5. Check the data | Look at the **Data** box and the **Recent readings** table | Each reading with its raw value, accepted value and status (`OK`, or the reason it was rejected). |
| 6. Look at history | Click **History** at the top | The full recorded Module 6 history and the live history. Use the buttons for the last hour, last day or everything. Gaps are shaded, never joined with a line. |
| 7. Check the logs | Click **Logs** at the top | Recent events, plus the last lines of each log file. See [Logs](#10-logs). |
| 8. Stop | Press **Ctrl+C** in the terminal | `SUPERVISOR_STOPPING`, then `supervisor stopped cleanly`. Starting it again later continues where it stopped. |

**Options** (only if you need them; they work the same on every operating system):

| Option | Meaning | Example |
|---|---|---|
| `--port N` | use another dashboard port | `python supervisor.py --port 5050` → open http://127.0.0.1:5050 |
| `--speed X` | replay speed (30 = one reading every 2 s) | `--speed 120` = one reading every 0.5 s |
| `--start-sample N` | start the replay at recorded sample N (1–1801) | `--start-sample 240` |
| `--alert-on C` / `--alert-off C` | alert thresholds in °C | `--alert-on 25 --alert-off 24.5` |
| `--dataset NAME` | replay source: `practice` (default) or `practice_malformed` (a test feed with broken readings; build it first with `python -m scripts.make_malformed_feed`) | `--dataset practice_malformed --start-sample 370 --speed 60` |
| `--check` | only check that the computer is ready | `python supervisor.py --check` |
| `-h` | list all options | `python supervisor.py -h` |

## 6. Inputs and Units

**Required input.** The temperature readings in `data/module6_sensor_history.db`. They are included, so you provide nothing.

| Item | Value |
|---|---|
| Quantity | air temperature |
| Unit | **degrees Celsius (°C)**; no other unit is accepted or converted |
| Source interval | one reading every 60 s in the recording; at the monitor every **60 s ÷ speed** (2 s by default) |
| Accepted file format | SQLite database with the Module 6 schema (`sensor_readings` table: `dataset`, `sensor_name`, `sample_number`, `timestamp`, `raw_value`, …). CSV is **not** read directly; Module 6 converted the CSV into this database. |
| Timestamps | local time, ISO format `YYYY-MM-DDTHH:MM:SS.mmm` |

**Ranges every reading is checked against:**

| Range | Limits | A reading outside it is… |
|---|---|---|
| What the sensor can physically report | −40 to 125 °C | `FAULT` |
| Supported room range | **0 to 50 °C** | `OUT_OF_RANGE` |
| Largest believable change between readings | 5 °C | `SUSPICIOUS` (unless the next reading confirms it) |

**Optional inputs** (command-line options from section 5, or the environment variables of the same name):

| Setting | Default | Allowed |
|---|---|---|
| `--port` / `WEB_PORT` | 5000 | 1–65535 |
| `--speed` / `REPLAY_SPEED` | 30 | above 0, at most 1000 |
| `--start-sample` / `REPLAY_START_SAMPLE` | 1 | whole number ≥ 1 |
| `--alert-on` / `ALERT_ON_C` | 24.0 °C | 0–50 °C |
| `--alert-off` / `ALERT_OFF_C` | 23.5 °C | 0–50 °C and **lower than** the alert-on value |
| `--dataset` / `SOURCE_DATASET` | `practice` | `practice`, or `practice_malformed` once built |

## 7. What the Tool Refuses

The system would rather say **"I cannot safely do this"** than show a wrong answer.

**It refuses to start.** You see a `REFUSED TO START` message with the fix, nothing is started, and a `STARTUP_REFUSED` line is written to `logs/supervisor.log`. This happens when:

| Condition | Example message |
|---|---|
| Invalid setting (not a number, out of range, alert-off ≥ alert-on) | `REPLAY_SPEED = 'abc'` · `expected: a number above 0 and at most 1000 (default 30)` |
| Port already in use (or the monitor is already running) | `another program is already using port 5000` → `python supervisor.py --port 5050` |
| Input data file missing or unreadable | `[FAIL] Input data … not found` → `git checkout -- data/module6_sensor_history.db` |
| Database folder or log folder not writable | `[FAIL] Log folder writable: …` |
| Database damaged | `[FAIL] Monitor database: quick_check …` → see [Recovery B](#12-recovery-procedures) |
| Replay dataset does not exist | `dataset 'practice_malformed' has no temperature readings` → `python -m scripts.make_malformed_feed` |
| Python too old or libraries missing | `[FAIL] Required packages: missing: Flask` → `pip install -r requirements.txt` |

**It refuses to use a reading.** The reading is stored with its reason, but it is never shown as the current temperature and never used for the alert:

| Status | Meaning |
|---|---|
| `MISSING` | no value arrived (empty, `nan`) |
| `FAULT` | impossible for the sensor (below −40 or above 125 °C), or not a number at all (`'ERR'`, `'23.9C'`) |
| `OUT_OF_RANGE` | outside the supported room range of 0–50 °C (the recording contains real 58.8 °C and 55.5 °C spikes) |
| `SUSPICIOUS` | jumped more than 5 °C from the last good reading |

**It refuses to call old data current.** If no reading arrives for 3 intervals, the value is struck through and labelled *"Last known value – STALE, not current"*, and the alert box says *"data stale – cannot confirm"*. Missing readings are never filled in, averaged or drawn across.

## 8. Expected Output

![Dashboard in normal operation](evidence/01_dashboard_healthy_current_reading.png)

| Part of the Live page | What it shows | What is normal |
|---|---|---|
| **Health banner** (top) | the overall state and the reason, e.g. `HEALTHY · data fresh: last reading 0.4 s ago` | green **HEALTHY** |
| **Current temperature** | the latest **accepted** reading in °C, its time and age, and a `Current` badge | about 21–25 °C, age below 6 s |
| **Data** box | FRESH / STALE / LOST, the latest reading and its status, the expected interval and limits, how many readings arrived in the last 5 min, and the collector heartbeat | **FRESH**, "11 of 11 received (100 %)" |
| **Warm-room alert (HIGH_TEMP)** | CLEAR or ACTIVE, since when, the **rolling median** of the last 5 accepted readings, and the rule | matches the median: ACTIVE at ≥ 24.0 °C, CLEAR again at ≤ 23.5 °C |
| **Chart** (last 5 minutes) | accepted readings as a line, rejected readings as ×, alert thresholds as dashed lines, gaps shaded | a continuous line |
| **Recent readings** table | time, raw °C, accepted °C, status, and which recorded row it replays | mostly `OK`; an occasional `OUT_OF_RANGE` spike is normal |
| **Events** | the latest important events (startup, alerts, health changes) | — |

**Other pages and outputs:**

- **History** page: the full recorded history and the live history, with gaps shaded.
- **Logs** page: see [Logs](#10-logs).
- **Machine-readable status:** http://127.0.0.1:5000/healthz (short) and http://127.0.0.1:5000/api/status (everything on the Live page, as JSON).

**Calculations performed:**

- **Validation** of each reading ([section 7](#7-what-the-tool-refuses)).
- **Rolling median** of the last 5 accepted readings, for the alert.
- **Data age** compared with the expected interval, for stale/missing data.
- **Share of rejected readings** among the last 30, for DEGRADED.

## 9. System Health and Alerts

**Health states** (the coloured banner at the top of the Live page):

| State | Colour | Meaning | What you should do |
|---|---|---|---|
| **STARTING** | blue | just started; waiting for the first 5 good readings (~10 s) | wait |
| **HEALTHY** | green | data is fresh and readings are being accepted | nothing |
| **DEGRADED** | amber | data is late (no reading for more than 3 intervals, 6 s by default), **or** readings arrive but are being rejected (≥ 20 % of the last 30, or none accepted recently), **or** the collector is restarting | watch it; it usually returns to HEALTHY by itself. If it lasts, read the reason in the banner and the logs. |
| **FAILED** | red | no data for more than 15 intervals (30 s by default), **or** the collector process is not running / was stopped by the crash-loop guard | follow [Recovery](#12-recovery-procedures) |
| **RECOVERING** | blue | data came back after a failure or a restart; waiting for 5 good readings | wait |

The banner always gives the **reason**, for example `data stale: last reading 7.2 s ago (stale after 6 s)`. Every change is logged as `HEALTH_CHANGE old -> new: reason`.

**Alert: HIGH_TEMP (warm room)**

| | Rule |
|---|---|
| Activates | when the **median of the last 5 accepted readings is ≥ 24.0 °C** |
| Clears | when that median is **≤ 23.5 °C** |

- **Why a median and a gap:** one spike can never trigger or clear the alert, and a temperature hovering at 24 °C does not make it flap on and off.
- **During stale data:** the alert box says *"data stale – cannot confirm"*. The alert keeps its last state, because there is no new evidence either way.
- **Survives restarts:** the alert state is saved in the database.
- **Where it shows:** only on the dashboard and in the logs (`ALERT_ACTIVE`, `ALERT_CLEARED`). No email is sent.

## 10. Logs

**Where.** In the **`logs/`** folder inside the project. It is created on first start.

| File | Written by | Contains |
|---|---|---|
| `logs/supervisor.log` | the process manager | start/stop, `STARTUP_REFUSED`, a part stopping (`CHILD_EXIT … exit n of 6 allowed`), restarts, `CRASH_LOOP` |
| `logs/worker.log` | the collector | **every reading** (`READING … status=OK`), rejected readings with the exact value and reason, `ALERT_ACTIVE` / `ALERT_CLEARED`, `DATA_STALE` / `DATA_MISSING` / `DATA_LOST`, `HEALTH_CHANGE`, `RECOVERY` |
| `logs/web.log` | the dashboard | startup, `DASHBOARD_HEALTH` changes, request errors |
| `logs/worker.stderr.log`, `logs/web.stderr.log` | Python itself | crash tracebacks printed before logging started |

Each line has the format `2026-10-25 21:16:03,323 INFO     worker     EVENT_NAME message`: date and time, level (INFO / WARNING / ERROR / CRITICAL), component, event, details.

**How to view them:**

- **Easiest:** the **Logs** page of the dashboard (http://127.0.0.1:5000/logs) shows the important events and the last lines of each file.
- **Any text editor:** open the files in the `logs/` folder.
- **Live in a second terminal:** on Windows, `Get-Content logs\worker.log -Wait -Tail 20`; on macOS/Linux, `tail -f logs/worker.log`.

**Using logs to diagnose a problem:**

1. Note the time the problem was seen (banner `since …`).
2. In `supervisor.log`, look for `ERROR` or `CRITICAL` near that time. It names the component that stopped.
3. Open that component's log (`worker.log` or `web.log`) and find the **first** `WARNING` or `ERROR` before that time. That is usually the cause; later lines are consequences.
4. Search for a word: `ERROR`, `REFUSED`, `CRASH_LOOP`, `HEALTH_CHANGE`.

Files rotate at 2 MB and keep 5 old copies, so they never fill the disk.

## 11. Troubleshooting

| Problem | Possible cause | What to check / do |
|---|---|---|
| `python` is not recognized (Windows) or `command not found` | Python not installed, or not on PATH | Windows: use **`py`** instead of `python` (`py -m venv .venv`), or reinstall Python with **"Add python.exe to PATH"** ticked. macOS: use `python3`. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` (Windows) | PowerShell's script policy | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer **Y**, then activate again. Or use Command Prompt: `.venv\Scripts\activate.bat`. |
| `ModuleNotFoundError: No module named 'flask'`, or `[FAIL] Required packages` | the environment is not active, or the install step was skipped | The prompt must start with `(.venv)`. Activate (section 3, step 4), then `pip install -r requirements.txt`. |
| `can't open file … supervisor.py` | the terminal is not in the project folder | `cd` into the `EGN321-Module7` folder. `dir` (Windows) or `ls` must list `supervisor.py`. |
| `REFUSED TO START … already using port 5000` | the monitor is already running in another window, or another program uses port 5000 (on **macOS, AirPlay Receiver** uses 5000) | Close the other monitor window (Ctrl+C), or run `python supervisor.py --port 5050` and open http://127.0.0.1:5050 |
| Browser: "This site can't be reached" | the system is not running, or the wrong port | Is the terminal window still open with no `stopped` line? Use the port shown in its `Open the dashboard:` line. |
| Banner stays **DEGRADED** or **FAILED**, "data stale" | the replay reached a real gap in the recording (normal: about 26 min and 21 min of recorded time), or the collector stopped | Wait one minute: a gap ends by itself (→ RECOVERING → HEALTHY). If it stays FAILED, look in `logs/supervisor.log` for `CRASH_LOOP` and use [Recovery A](#12-recovery-procedures). |
| Banner **FAILED: "collector stopped by the crash-loop guard … human intervention required"** | the collector crashed more than 5 times in 60 s | [Recovery A](#12-recovery-procedures) |
| Temperature shows struck through, "STALE, not current" | no reading for more than 3 intervals | This is intentional, not a bug. See the banner reason. |
| A reading shows `OUT_OF_RANGE` / `FAULT` | the recorded data contains real bad readings (58.8 °C spike) or a broken value | This is intentional: the reading is kept but not used. |
| `[FAIL] Input data … not found` | `data/module6_sensor_history.db` was deleted or moved | `git checkout -- data/module6_sensor_history.db`, or download the project again |
| `[FAIL] Monitor database`, or `database disk image is malformed` in the logs | `data/monitor.db` damaged (for example the disk was full, or the file was copied while running) | [Recovery B](#12-recovery-procedures) |

## 12. Recovery Procedures

**Rule: never delete the logs or the database to "fix" a problem before you have copied them.** They are the evidence of what went wrong.

### Recovery A: the system shows FAILED or a part keeps crashing

1. **Read the reason** in the red banner and the last `ERROR` / `CRITICAL` lines in `logs/supervisor.log` (or on the **Logs** page).
2. **Keep the evidence:** copy the `logs` folder to `logs_backup_<date>`.
3. **Fix the cause** named in the log, if there is one: a busy port, a setting, a missing file. See [Troubleshooting](#11-troubleshooting).
4. **Restart:** press **Ctrl+C** in the terminal, wait for `supervisor stopped cleanly`, then run `python supervisor.py` again. If it was installed as an operating-system service, use `sudo systemctl restart sensor-monitor` on Linux; on Windows/macOS, log off and on.
5. **Verify** within about 30 s:
   - The banner goes **RECOVERING → HEALTHY**.
   - **Current temperature** shows a reading only seconds old.
   - `logs/worker.log` contains `RECOVERY …` and later `HEALTH_CHANGE … -> HEALTHY`.
   - The alert box shows the same state as before (it is restored, not reset).

Data that should have arrived while it was down is **not** invented. It is logged as `DATA_LOST n readings` and shown as a gap.

### Recovery B: the database is damaged

1. Stop the system (Ctrl+C).
2. Keep a copy: copy `data/monitor.db` to `data/monitor_damaged_<date>.db`.
3. Rebuild: `python init_db.py --reset`. This recreates `data/monitor.db` from the included Module 6 history. The **live** readings, events and alert state collected so far are lost; the Module 6 history is not.
4. Run `python supervisor.py --check` (all PASS), then `python supervisor.py`.

### Automatic recovery (no action needed)

| What happens | How the system recovers |
|---|---|
| The collector or dashboard crashes or is killed | restarted within 1–16 s |
| A data gap | DEGRADED → FAILED, then RECOVERING → HEALTHY when data returns |
| Broken readings (`'ERR'`, `''`, `nan`) | rejected, health DEGRADED while they last |

Unattended start at boot or logon is optional: `deploy/install_windows.ps1`, `deploy/install_macos.sh` or `deploy/install_linux.sh` (see `docs/DESIGN.md`).

## 13. Assumptions

- All temperatures are in **degrees Celsius**. Nothing is converted.
- The sensor is the instructor's practice temperature sensor, **replayed** from the Module 6 history. Its readings are assumed to be correctly time-stamped, one per minute.
- The room's normal range is **20–24 °C**, which is why the warm-room alert is set at 24.0 °C. Anything outside **0–50 °C** cannot be a real room reading.
- A change of more than **5 °C** between consecutive readings is not physically believable unless the next reading confirms it.
- Data is **stale** after 3 missed intervals and **lost** after 15. With the default speed that is 6 s and 30 s.
- Timestamps are the computer's **local time**.
- Only one copy of the monitor runs on a computer at a time, and only the person at that computer views the dashboard.

## 14. Known Limitations

- **No real sensor:** the input is a replay of recorded data. A live sensor would replace `monitor/replay.py`.
- **No calibration check:** the application does not independently verify physical sensor calibration. A sensor that reads 2 °C too high, but steadily, looks healthy.
- **Alerts stay local:** they appear only on the dashboard and in the logs. Nobody is emailed or paged, so someone has to look.
- **Readings lost during an outage stay lost:** they are shown as a gap, never filled in.
- **Crash loops need a person:** after more than 5 crashes in 60 s the system stops restarting that part (Recovery A).
- **One sensor, one alert, one user:** the dashboard has no login and listens only on this computer (127.0.0.1).
- **Daylight-saving time:** because timestamps are local time, a clock change can show a 1-hour gap or overlap.
- **Tested platforms:** the automatic start-at-boot installers for Windows and macOS were written but only the Linux path was tested.

## 15. Testing

With the environment active, in the project folder:

```
python -m pytest
```

Expected: **78 passed** (about 30–60 s; a few tests start and kill the real processes).

| Test file | What it checks |
|---|---|
| `tests/test_validation_alerts.py` | validation rules, alert median and hysteresis, the alert on the whole Module 6 history |
| `tests/test_health_replay.py`, `tests/test_worker.py` | stale/missing detection, health states, the replay schedule, the collector |
| `tests/test_db_logging.py`, `tests/test_web.py` | database, logging, dashboard pages |
| `tests/test_supervisor_recovery.py` | kills the real processes and checks they come back |
| `tests/test_failure_malformed.py` | the Failure Report: broken readings do not take the system down |
| `tests/test_handoff.py` | the refusals in section 7 (bad setting, busy port, missing file) |

To run one file: `python -m pytest tests/test_handoff.py -v`.

## 16. AI Usage

AI (Claude, Anthropic) was used in development. What it did and how each part was verified is recorded in **[`AI_LOG.md`](AI_LOG.md)**.
