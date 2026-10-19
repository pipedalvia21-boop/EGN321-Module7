# AI Use Log

| Date | Tool / model | Purpose | Prompt summary | Used / Modified / Rejected | How it was verified |
|---|---|---|---|---|---|
| 2026-10-18 | Claude (Anthropic), Claude Opus 5.5 | Architecture: replay source → validation → SQLite → alerts → Flask → logs + recovery | "Build the Module 7 unattended monitoring system from my Module 6 history" | Used after review | 50 pytest tests; full evidence run passed 25/25 checks (`evidence/EVIDENCE.md`) |
| 2026-10-18 | Claude Opus 5.5 | Choice of alert threshold (24.0 / 23.5 °C) and noise control (median of 5 + hysteresis) | Same session | Used. Values chosen by replaying the whole Module 6 history | `test_alert_on_module6_history`: exactly ON 12:15, OFF 16:56, ON day 2; plus single-spike, 3-of-5, boundary, hysteresis and no-flap tests |
| 2026-10-18 | Claude Opus 5.5 | Stale/missing rules (3 and 15 intervals), health states, heartbeat timeout | Same session | Used | Boundary tests at exactly 1.5 s / 7.5 s in the demo setting; dashboard-only staleness override test |
| 2026-10-18 | Claude Opus 5.5 | Restart design: supervisor + systemd / launchd / Task Scheduler, orphan guard, no backfill after downtime | Same session | Used. The orphan guard was added after finding that killing only the supervisor left children running | `tests/test_supervisor_recovery.py` kills real processes; evidence run recovery tests A and B |
| 2026-10-18 | Claude Opus 5.5 | Dashboard design and evidence screenshots (Playwright) | Same session | Modified. The first screenshots captured the page before it refreshed, so the script was changed to wait for the page itself to show each state | Screenshots reviewed one by one; `timeline.csv` cross-checks the states |

The evidence screenshots, logs and timeline were produced by running the real system with `scripts/evidence_run.py` in Claude's cloud workspace (Linux, Python 3.13, America/New_York time zone). No values in the evidence were typed or edited. Re-running the script on another machine regenerates all of them.

The Windows Task Scheduler and macOS launchd installers were written but not executed in that workspace (no Windows/macOS there). The supervisor-level restart and the restart of a killed supervisor were tested for real.
