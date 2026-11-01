# TrueData trial report

Generated: 2026-09-12T09:07:41.260263+05:30
Warehouse: `D:\Project Alpha\alpha-desk\data`
Audit chain: intact

Rows stored by dataset: {"bars": 18497, "ticks": 689500, "masters": 128, "corporate_actions": 154}

## 1. Coverage

### bars

| partition | files | rows | first | last | verdicts |
| --- | --- | --- | --- | --- | --- |
| `truedata\NSE__FUT__BANKNIFTY__2026-09-01\15m` | 2 | 288 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__BANKNIFTY__2026-09-29\15m` | 3 | 341 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__BANKNIFTY__2026-09-29\1m` | 1 | 769 | 2026-09-10 09:15:00+05:30 | 2026-09-11 15:40:00+05:30 | accept_with_warnings |
| `truedata\NSE__FUT__BANKNIFTY__2026-10-01\15m` | 2 | 285 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept_with_warnings |
| `truedata\NSE__FUT__BANKNIFTY__2026-10-27\15m` | 3 | 337 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:30:00+05:30 | accept, accept_with_warnings |
| `truedata\NSE__FUT__BANKNIFTY__CONT-vendor_spliced\15m` | 5 | 625 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:30:00+05:30 | accept, accept_with_warnings |
| `truedata\NSE__FUT__NIFTY__2026-09-01\15m` | 2 | 289 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__2026-09-01\1m` | 2 | 4,243 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:40:00+05:30 | accept_with_warnings |
| `truedata\NSE__FUT__NIFTY__2026-09-29\15m` | 3 | 341 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__2026-09-29\1m` | 3 | 5,015 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:40:00+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__2026-10-01\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__2026-10-27\15m` | 3 | 338 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__2026-11-01\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__CONT-vendor_spliced\15m` | 5 | 627 | 2026-08-26 09:15:00+05:30 | 2026-09-11 15:30:00+05:30 | accept |
| `truedata\NSE__INDEX__BANKNIFTY\1d` | 25 | 497 | 2024-09-09 00:00:00+05:30 | 2026-09-09 00:00:00+05:30 | accept_with_warnings |
| `truedata\NSE__INDEX__NIFTY\1d` | 25 | 498 | 2024-09-09 00:00:00+05:30 | 2026-09-09 00:00:00+05:30 | accept_with_warnings |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__55800__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__55900__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56000__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56200__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56300__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56400__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56500__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56600__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56700__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56800__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23500__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23700__PE\15m` | 2 | 286 | 2026-08-26 09:15:00+05:30 | 2026-09-09 15:30:00+05:30 | accept |

### ticks

| partition | files | rows | first | last | verdicts |
| --- | --- | --- | --- | --- | --- |
| `truedata\NSE__EQ__RELIANCE` | 1359 | 12,936 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:55+05:30 | accept |
| `truedata\NSE__FUT__BANKNIFTY__2026-09-29` | 1376 | 17,752 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__FUT__BANKNIFTY__CONT-vendor_spliced` | 1376 | 17,734 | 2026-09-10 09:15:02+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__2026-09-29` | 1376 | 13,345 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__FUT__NIFTY__CONT-vendor_spliced` | 1376 | 13,370 | 2026-09-10 09:15:02+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__INDEX__BANKNIFTY` | 1379 | 89,059 | 2026-09-10 09:10:57+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__INDEX__NIFTY` | 1379 | 89,108 | 2026-09-10 09:10:57+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56000__CE` | 6 | 69 | 2026-09-10 09:15:02+05:30 | 2026-09-10 09:16:12+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56000__PE` | 6 | 74 | 2026-09-10 09:15:00+05:30 | 2026-09-10 09:16:12+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56100__CE` | 1375 | 18,124 | 2026-09-10 09:15:05+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56100__PE` | 1376 | 17,753 | 2026-09-10 09:15:03+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56200__CE` | 1375 | 17,757 | 2026-09-10 09:15:04+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56200__PE` | 1376 | 17,746 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56300__CE` | 1376 | 17,775 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56300__PE` | 1376 | 17,775 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56400__CE` | 1376 | 17,760 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56400__PE` | 1376 | 17,747 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56500__CE` | 1376 | 17,798 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56500__PE` | 1376 | 17,753 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56600__CE` | 1376 | 17,775 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56600__PE` | 1376 | 17,715 | 2026-09-10 09:15:01+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56700__CE` | 1370 | 17,683 | 2026-09-10 09:18:17+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__BANKNIFTY__2026-09-29__56700__PE` | 1370 | 17,694 | 2026-09-10 09:18:17+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23300__CE` | 1376 | 13,366 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23300__PE` | 1376 | 13,342 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23350__CE` | 1376 | 13,373 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23350__PE` | 1376 | 13,346 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23400__CE` | 1376 | 13,319 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:58+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23400__PE` | 1376 | 13,293 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:57+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23450__CE` | 1376 | 13,401 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:57+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23450__PE` | 1376 | 13,361 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:57+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23500__CE` | 1376 | 13,475 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23500__PE` | 1376 | 13,455 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23550__CE` | 1376 | 13,383 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23550__PE` | 1376 | 13,316 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23600__CE` | 1376 | 13,409 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |
| `truedata\NSE__OPT__NIFTY__2026-09-15__23600__PE` | 1376 | 13,359 | 2026-09-10 09:15:00+05:30 | 2026-09-10 15:23:59+05:30 | accept |

### masters

| partition | files | rows | first | last | verdicts |
| --- | --- | --- | --- | --- | --- |
| `truedata\constituents\NIFTY_50` | 2 | 100 | None | None | accept |
| `truedata\constituents\NIFTY_BANK` | 2 | 28 | None | None | accept |

### corporate_actions

| partition | files | rows | first | last | verdicts |
| --- | --- | --- | --- | --- | --- |
| `truedata\symbol\HDFCBANK` | 2 | 32 | None | None | accept |
| `truedata\symbol\RELIANCE` | 2 | 28 | None | None | accept |
| `truedata\symbol\TCS` | 2 | 94 | None | None | accept |

## 2. Data quality

Writes: 48334. Verdicts: {'accept': 48214, 'accept_with_warnings': 59, 'quarantine': 61}


#### Quality findings

- **NSE:FUT:BANKNIFTY:2026-09-01** (1m): 1.58% of expected bars missing
- **NSE:FUT:BANKNIFTY:2026-09-29** (1m): 1.58% of expected bars missing
- **NSE:FUT:BANKNIFTY:2026-10-01** (1m): 42.64% of expected bars missing
- **NSE:FUT:BANKNIFTY:2026-10-01** (1m): contiguous gap of 20 bars
- **NSE:FUT:BANKNIFTY:2026-10-27** (1m): 42.64% of expected bars missing
- **NSE:FUT:BANKNIFTY:2026-10-27** (1m): contiguous gap of 20 bars
- **NSE:FUT:BANKNIFTY:2026-11-01** (15m): 2.55% of expected bars missing
- **NSE:FUT:BANKNIFTY:2026-11-01** (1m): 66.16% of expected bars missing
- **NSE:FUT:BANKNIFTY:2026-11-01** (1m): contiguous gap of 32 bars
- **NSE:FUT:BANKNIFTY:CONT-vendor_spliced** (1m): 28.67% of expected bars missing
- **NSE:FUT:BANKNIFTY:CONT-vendor_spliced** (1m): 42.64% of expected bars missing
- **NSE:FUT:BANKNIFTY:CONT-vendor_spliced** (1m): 66.16% of expected bars missing
- **NSE:FUT:BANKNIFTY:CONT-vendor_spliced** (1m): contiguous gap of 20 bars
- **NSE:FUT:BANKNIFTY:CONT-vendor_spliced** (1m): contiguous gap of 32 bars
- **NSE:FUT:NIFTY:2026-10-01** (1m): 11.08% of expected bars missing
- **NSE:FUT:NIFTY:2026-10-27** (1m): 11.08% of expected bars missing
- **NSE:FUT:NIFTY:2026-11-01** (1m): 30.42% of expected bars missing
- **NSE:FUT:NIFTY:CONT-vendor_spliced** (1m): 11.08% of expected bars missing
- **NSE:FUT:NIFTY:CONT-vendor_spliced** (1m): 2.27% of expected bars missing
- **NSE:FUT:NIFTY:CONT-vendor_spliced** (1m): 30.42% of expected bars missing
- **NSE:INDEX:BANKNIFTY** (1d): 0.60% of expected bars missing
- **NSE:INDEX:BANKNIFTY** (1d): 100.00% of expected bars missing
- **NSE:INDEX:BANKNIFTY** (1d): contiguous gap of 503 bars
- **NSE:INDEX:BANKNIFTY** (1d): whole sessions missing: ['2024-11-20', '2025-07-10', '2026-01-15', '2026-03-03', '2026-03-26']
- **NSE:INDEX:BANKNIFTY** (1d): whole sessions missing: ['2024-11-20', '2025-07-10', '2026-01-15']
- **NSE:INDEX:BANKNIFTY** (1d): whole sessions missing: ['2024-11-20', '2025-07-10']
- **NSE:INDEX:NIFTY** (1d): 100.00% of expected bars missing
- **NSE:INDEX:NIFTY** (1d): contiguous gap of 503 bars
- **NSE:INDEX:NIFTY** (1d): whole sessions missing: ['2024-11-20', '2026-01-15', '2026-03-03', '2026-03-26', '2026-03-31']
- **NSE:INDEX:NIFTY** (1d): whole sessions missing: ['2024-11-20', '2026-01-15']
- **NSE:INDEX:NIFTY** (1d): whole sessions missing: ['2024-11-20']
- **NSE:OPT:BANKNIFTY:2026-09-29:55800:CE** (15m): 81.09% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:55800:CE** (15m): contiguous gap of 120 bars
- **NSE:OPT:BANKNIFTY:2026-09-29:55800:CE** (15m): whole sessions missing: ['2026-08-27', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04']
- **NSE:OPT:BANKNIFTY:2026-09-29:55900:CE** (15m): 81.09% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:55900:CE** (15m): contiguous gap of 67 bars
- **NSE:OPT:BANKNIFTY:2026-09-29:55900:CE** (15m): whole sessions missing: ['2026-08-26', '2026-08-28', '2026-09-04', '2026-09-07']
- **NSE:OPT:BANKNIFTY:2026-09-29:56000:CE** (15m): 1.09% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56100:CE** (15m): 60.00% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56100:CE** (15m): contiguous gap of 43 bars
- **NSE:OPT:BANKNIFTY:2026-09-29:56100:CE** (15m): whole sessions missing: ['2026-08-28']
- **NSE:OPT:BANKNIFTY:2026-09-29:56100:PE** (15m): 9.45% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56100:PE** (15m): contiguous gap of 26 bars
- **NSE:OPT:BANKNIFTY:2026-09-29:56100:PE** (15m): whole sessions missing: ['2026-08-26']
- **NSE:OPT:BANKNIFTY:2026-09-29:56200:CE** (15m): 40.36% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56300:CE** (15m): 53.45% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56300:CE** (15m): contiguous gap of 46 bars
- **NSE:OPT:BANKNIFTY:2026-09-29:56300:CE** (15m): whole sessions missing: ['2026-08-26']
- **NSE:OPT:BANKNIFTY:2026-09-29:56400:CE** (15m): 40.00% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56400:CE** (15m): contiguous gap of 20 bars
- **NSE:OPT:BANKNIFTY:2026-09-29:56500:CE** (15m): 0.73% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56600:CE** (15m): 19.64% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56700:CE** (15m): 8.00% of expected bars missing
- **NSE:OPT:BANKNIFTY:2026-09-29:56800:CE** (15m): 11.27% of expected bars missing
- **NSE:OPT:NIFTY:2026-09-15:23200:CE** (15m): 68.73% of expected bars missing
- **NSE:OPT:NIFTY:2026-09-15:23200:CE** (15m): contiguous gap of 132 bars
- **NSE:OPT:NIFTY:2026-09-15:23200:CE** (15m): whole sessions missing: ['2026-08-26', '2026-08-27', '2026-08-28', '2026-08-31', '2026-09-01']
- **NSE:OPT:NIFTY:2026-09-15:23200:PE** (15m): 45.45% of expected bars missing
- **NSE:OPT:NIFTY:2026-09-15:23200:PE** (15m): contiguous gap of 125 bars
- **NSE:OPT:NIFTY:2026-09-15:23200:PE** (15m): whole sessions missing: ['2026-08-26', '2026-08-27', '2026-08-28', '2026-08-31', '2026-09-01']

Missing-bar percentage across partitions: median 0.364%, worst 100.000%


## 3. Live feed stability and latency

No live capture sessions recorded.


## 4. Historical vs live parity (1m close)

No live bars captured, so historical/live parity was not measured.


## 5. Decision checklist

| Question | Evidence to check above |
| --- | --- |
| Is history deep enough for training? | Section 1 first/last per partition |
| Is history clean enough to promote? | Section 2 verdicts and missing-bar percentages |
| Is the live feed stable? | Section 3 disconnects, sequence gaps, unrouted keys |
| Is latency acceptable for 15m decisions? | Section 3 p95 latency |
| Do history and live agree? | Section 4 close differences |
| Are options, futures and equities all covered? | Section 1 partitions per segment |
