# Run log - verify

```
23:07:42.493  PHASE verify: code at git 7c33913; feed practice_malformed (samples 400-459 malformed); replay 60x from sample 370
23:07:42.494  supervisor started (pid 631)
23:07:49.055  NORMAL STATE: health HEALTHY; latest reading 24.35 C at 2026-10-18T23:07:48.757 (source sample 376); dashboard HTTP 200; database ok, 7 live rows; alert HIGH_TEMP ACTIVE since 2026-10-18T23:07:46.741
23:07:50.466  screenshot 01_normal_state.png - Normal operation before the failure
23:07:50.466  FAILURE INTRODUCED: the feed delivers malformed sample 400 ('ERR') at about 23:08:12; samples 400-459 are all malformed
23:08:12.785  first malformed reading stored (latest_row.source_ref = sample 400, raw None)
23:08:18.213  screenshot 02_open_page_during_failure.png - Already-open dashboard while malformed input arrives
23:08:18.219  fresh load of / -> HTTP 200; chart -> HTTP 200
23:08:19.755  screenshot 03_fresh_page_load_during_failure.png - A NEW visit to the dashboard during the failure
23:08:49.259  mid-block: health DEGRADED - readings arrive but none was accepted recently
23:08:50.665  screenshot 04_during_malformed_block.png - Improved code, middle of the malformed block
23:10:31.541  screenshot 05_recovered_healthy.png - Verified recovery: HEALTHY, numeric readings, page loads
23:10:33.351  screenshot 06_logs_page.png - Logs page after recovery
23:10:34.022  supervisor stopped cleanly
```
