# Run log - failure

```
22:58:12.071  PHASE failure: code at git 23b199e; feed practice_malformed (samples 400-459 malformed); replay 60x from sample 370
22:58:12.072  supervisor started (pid 219)
22:58:18.693  NORMAL STATE: health HEALTHY; latest reading 24.35 C at 2026-10-18T22:58:18.354 (source sample 376); dashboard HTTP 200; database ok, 7 live rows; alert HIGH_TEMP ACTIVE since 2026-10-18T22:58:16.337
22:58:20.085  screenshot 01_normal_state.png - Normal operation before the failure
22:58:20.085  FAILURE INTRODUCED: the feed delivers malformed sample 400 ('ERR') at about 22:58:42; samples 400-459 are all malformed
22:58:42.574  first malformed reading stored (latest_row.source_ref = sample 400, raw 'ERR')
22:58:47.961  screenshot 02_open_page_during_failure.png - Already-open dashboard while malformed input arrives
22:58:47.972  fresh load of / -> HTTP 500; chart -> HTTP 500
22:58:49.378  screenshot 03_fresh_page_load_during_failure.png - A NEW visit to the dashboard during the failure
22:59:16.402  supervisor log: CRASH_LOOP
22:59:16.409  dashboard health: FAILED - collector process not responding: last heartbeat 35 s ago
22:59:17.817  screenshot 04_failed_state.png - Collector stopped by the crash-loop guard: system FAILED
22:59:17.829  evidence preserved: logs_before_recovery/ and db_before_recovery.db (nothing deleted)
22:59:44.339  input restored: feed sample 460 onward is numeric again (due 22:59:42); collector is still DOWN (crash-loop guard)
22:59:44.344  RECOVERY: operator restarts the service (Ctrl+C / systemctl restart sensor-monitor)
22:59:44.810  supervisor restarted (pid 314)
23:01:01.208  screenshot 05_recovered_healthy.png - Verified recovery: HEALTHY, numeric readings, page loads
23:01:03.173  screenshot 06_logs_page.png - Logs page after recovery
23:01:03.675  supervisor stopped cleanly
```
