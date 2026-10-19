| Result | Check | Detail |
|---|---|---|
| PASS | normal: database integrity_check | ok |
| PASS | normal: Module 6 history intact | 8900 rows |
| PASS | no collector crash during malformed input |  |
| PASS | dashboard page loads during malformed input |  |
| PASS | chart loads during malformed input |  |
| PASS | health DEGRADED (not FAILED) while only malformed data arrives | readings arrive but none was accepted recently |
| PASS | replay input resumed (numeric samples after the malformed block) | latest source sample 484 |
| PASS | system health returned to HEALTHY | 46.2 s after end of measurement |
| PASS | after recovery: database integrity_check | ok |
| PASS | after recovery: Module 6 history intact | 8900 rows |
| PASS | database available and growing | live rows 7 -> 115 |
| PASS | dashboard page loads (HTTP 200) | 0.0 s after HEALTHY |
| PASS | live chart loads (HTTP 200) |  |
| PASS | alert state kept through the incident (ACTIVE, not reset) | since 2026-10-18T23:07:46.741 |
| PASS | alert still evaluated after recovery (clears normally with hysteresis) | median 23.49 |
