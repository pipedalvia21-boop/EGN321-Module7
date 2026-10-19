| Result | Check | Detail |
|---|---|---|
| PASS | normal: database integrity_check | ok |
| PASS | normal: Module 6 history intact | 8900 rows |
| PASS | failure reproduced: collector crash loop |  |
| PASS | collector does NOT come back by itself after the input recovers | collector process not responding: last heartbeat 63 s ago |
| PASS | replay input resumed (numeric samples after the malformed block) | latest source sample 485 |
| PASS | system health returned to HEALTHY | 22.7 s after restart |
| PASS | after recovery: database integrity_check | ok |
| PASS | after recovery: Module 6 history intact | 8900 rows |
| PASS | database available and growing | live rows 7 -> 61 |
| PASS | dashboard page loads (HTTP 200) | 0.1 s after HEALTHY |
| FAIL | live chart loads (HTTP 200) |  |
| PASS | alert state kept through the incident (ACTIVE, not reset) | since 2026-10-18T22:58:16.337 |
| PASS | restart logged as RECOVERY |  |
| PASS | alert restored from SQLite at restart |  |
| PASS | alert still evaluated after recovery (clears normally with hysteresis) | median 23.49 |
