# Assignment 8.1: The Silent Handoff Challenge

**Author:** Felipe D'Alvia   **Course:** EGN 321
**Repository:** https://github.com/pipedalvia21-boop/EGN321-Module7
**Application:** Lab Temperature Monitor (Modules 5–7): replay collector → validation → SQLite → alert → Flask dashboard, with automatic restart.
**Operating guide:** [`README.md`](../README.md), which has the 16 required sections in order.

**Files in this folder:**

| File | Purpose |
|---|---|
| `OPERATOR_TASKS.md` | the sheet given to the partner: the 12 handoff tasks, with no hints |
| `OBSERVATION_SHEET.md` | the author's silent-observation sheet |
| `dry_run/before.txt` | the author's cold-start dry run on the code before this assignment (git `94392ff`) |
| `dry_run/after.txt` | the same dry run after the improvements |
| `evidence/` | photos and screenshots from the handoff test with the partner |

---

## Part 1: Operating Documentation

`README.md` was rewritten as an operating guide for someone who has never seen the project. Its sections are:

1. Project Overview
2. System Requirements
3. Installation
4. Quick Start
5. Normal Operation
6. Inputs and Units
7. What the Tool Refuses
8. Expected Output
9. System Health and Alerts
10. Logs
11. Troubleshooting
12. Recovery Procedures
13. Assumptions
14. Known Limitations
15. Testing
16. AI Usage

The previous README was a technical design document. It is kept, unchanged, as `docs/DESIGN.md`.

## Before the Handoff: Author's Cold-Start Dry Run

Before giving the system to my partner, I did what a new user would do on a clean machine:

- a fresh `git clone` from GitHub;
- a new virtual environment;
- following the README literally;
- then the mistakes a new user is likely to make.

This was **not** the silent handoff, which is reported below. Its purpose was to find the problems I could find myself, so the handoff would test what I could not see. The full outputs are in `dry_run/before.txt` and `dry_run/after.txt`.

| # | What a new user does | What happened before (`before.txt`) | Problem |
|---|---|---|---|
| D1 | Starts the system while port 5000 is in use: a second copy, or **AirPlay Receiver on macOS**, which uses port 5000 | The console showed `SUPERVISOR_START`, then 6 restarts of the dashboard over 34 s, then `CRASH_LOOP`. The real reason (`OSError: Address already in use`) was only inside `logs/web.log`. | The user sees "site can't be reached" and a wall of restarts, with no reason on screen |
| D2 | Mistypes a setting (`REPLAY_SPEED=abc`) | A Python traceback ending in `ValueError: could not convert string to float: 'abc'` | It does not say which setting is wrong, what is allowed, or how to fix it |
| D3 | The input data file is missing (deleted or moved) | `FileNotFoundError` traceback | A traceback instead of "restore this file with this command" |
| D4 | Selects the malformed test feed before building it | The collector crash-looped (6 exits); the reason was only in `worker.log` | Same as D1 |
| D5 | Follows the README on Windows | The README only showed `source .venv/bin/activate` and macOS/Linux-style `VAR=value python …` settings, which do not work in PowerShell | Windows users cannot change any setting |
| D6 | Wants to know "is my installation OK?" | There was no way to check without starting the whole system | No verify step for the "install or verify dependencies" task |
| D7 | Reads the README for "how do I use it" | The README began with architecture and design rules (median, hysteresis, WAL). Startup was 4 lines; there was no troubleshooting table and no Windows instructions. | Written for a grader, not for an operator |

### Improvements Made After the Dry Run

| | Before | Change | After |
|---|---|---|---|
| **1. Pre-start checks and `--check`** | D1, D3, D4, D6: problems showed up as crash loops or tracebacks, and there was no way to verify an install | New `monitor/preflight.py` checks: Python ≥ 3.10, packages, input file, writable folders, database `quick_check`, replay dataset, port free. `python supervisor.py --check` prints PASS/FAIL with a `-> fix:` line, and the normal start runs the same checks first. | `after.txt` §0 and §B–D: one clear `REFUSED TO START` message with the exact fix, exit code 2, nothing started, and `STARTUP_REFUSED` logged. A busy port names AirPlay and gives `--port 5050`. |
| **2. Clear invalid-setting messages** | D2: a traceback | `ConfigError` names the setting, the value and what is allowed. Ranges were added for speed, port, start sample and the alert thresholds (0–50 °C, OFF < ON). | `REPLAY_SPEED = 'abc'` · `expected: a number above 0 and at most 1000 (default 30)`, with no traceback |
| **3. Options that work on every OS** | D5: settings needed bash syntax | `--port`, `--speed`, `--start-sample`, `--dataset`, `--alert-on`, `--alert-off`, plus `-h` | The same command works in PowerShell, cmd, macOS and Linux |
| **4. README rewritten as an operating guide** | D7 | 16 sections; Windows and macOS installation side by side with the expected output; a 7-step Quick Start; a 1-minute demo that shows an alert and a data gap; a 12-row troubleshooting table; two recovery procedures with verification steps. The design detail moved to `docs/DESIGN.md`. | An operator can go from clone to dashboard without reading any design detail |
| **5. Start-up banner** | After starting, the console showed only log lines | It prints `Sensor monitor running. Open the dashboard: http://127.0.0.1:5000 · Logs: … · Stop: press Ctrl+C` | The user knows where to go and how to stop |

**Bug found while testing improvement 1:** the first version of the port check refused to restart after a crash, because closed connections in `TIME_WAIT` made the port look busy. The existing kill-and-restart test (`test_supervisor_recovery.py`) caught it. It was fixed by binding the same way the web server does (`SO_REUSEADDR`).

**Verification:**

- `tests/test_handoff.py` adds 11 tests: every refusal, plus `--check` on a fresh clone.
- `python -m pytest` gives **78 passed** on Python 3.10, 3.11, 3.12 and 3.13.

---

## Part 2: The Silent Handoff Test

> **To be completed with the assigned partner.** Give them only the repository link and `OPERATOR_TASKS.md`, and fill in `OBSERVATION_SHEET.md` silently.

- **Partner:** ______________________
- **Date and time:** ______________________
- **Operator's computer:** ______________________
- **Commit handed off:** ______________________

## Part 3: Handoff Observation Table

| Step | Operator Action | Success? | Confusion Observed | Improvement Needed |
|---|---|---|---|---|
| Locate application | | | | |
| Understand purpose | | | | |
| Install / verify dependencies | | | | |
| Start application | | | | |
| Normal workflow | | | | |
| Valid input | | | | |
| Interpret result | | | | |
| Health / status | | | | |
| Logs | | | | |
| Failure condition | | | | |
| Troubleshooting / recovery | | | | |
| Known limitation | | | | |

**Problems discovered during the handoff:**

1. 
2. 
3. 

**Evidence:** `evidence/` (photos of both sheets, the operator's terminal and dashboard, and the operator's Logs page).

## Part 4: Improvements After the Handoff

| | Before (problem the operator hit) | Change | After |
|---|---|---|---|
| 1 | | | |
| 2 | | | |

## Part 5: Reflection

**1. What did your partner struggle with that seemed obvious to you while developing the application?**

&nbsp;

**2. What was the most important improvement you made after the handoff test?**

&nbsp;

**3. If you could no longer support this application personally, what additional documentation or automation would you add?**

&nbsp;
