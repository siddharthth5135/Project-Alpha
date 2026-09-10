# TrueData backend — trial runbook

The market-data stack is complete except for API credentials. Application
code talks only to `alpha_desk.marketdata`. TrueData lives behind
`alpha_desk.vendors.truedata` and is reached through the registry
(`get_provider("truedata")`).

## What is already in place

| Layer | Where | What it does |
| --- | --- | --- |
| Port | `src/alpha_desk/marketdata/` | Canonical contracts, calendar, normalisation, quality gates, registry |
| Adapter | `src/alpha_desk/vendors/truedata/` | Auth, REST, websocket, symbology, plan-depth clamp, mock doubles |
| Store | `src/alpha_desk/data/store.py` | Versioned parquet, manifests, quarantine, ingest log |
| Ingest | `src/alpha_desk/ingest/` | Backfill, live recorder, universe, ML bridge |
| Features / labels / models | existing P1.2–P1.4 modules | Unchanged; they never import TrueData |

Mocks replay documented TrueData payloads. `scripts/td_preflight.py` must stay green
without credentials.

## Credential day (do in this order)

1. Confirm TrueData enabled **Market Data API** (not Velocity). Put userid/password in `.env`. Confirm `TRUEDATA_WS_PORT` with them — it is per account.
2. Set plan facts they quoted (`TRUEDATA_HISTORY_DAYS_M1`, `TRUEDATA_MAX_LIVE_SYMBOLS`, `TRUEDATA_ENTITLED_GREEKS`).
3. Smoke (read-only, no warehouse writes):

```powershell
.\.venv\Scripts\python.exe scripts\td_smoke.py
```

4. Probe (depth, entitlements, live latency, raw payload samples):

```powershell
.\.venv\Scripts\python.exe scripts\td_probe.py
```

5. Backfill in priority order. Runs are resumable.

```powershell
.\.venv\Scripts\python.exe scripts\td_backfill.py --scope options --interval 15m --days 30 --strikes 5
.\.venv\Scripts\python.exe scripts\td_backfill.py --scope futures --interval 15m --days 180
.\.venv\Scripts\python.exe scripts\td_backfill.py --scope index --interval 1d --days 1825
.\.venv\Scripts\python.exe scripts\td_backfill.py --scope equities --interval 1d --days 1825
```

6. Live capture during market hours:

```powershell
.\.venv\Scripts\python.exe scripts\td_record_live.py --minutes 30 --strikes 5 --capture-raw
```

7. Train from what landed (same feature pipeline as paper):

```powershell
.\.venv\Scripts\python.exe scripts\train_from_store.py --then-train
```

8. Decision document from stored evidence:

```powershell
.\.venv\Scripts\python.exe scripts\td_trial_report.py
```

## Offline rehearsal (no credentials)

```powershell
.\.venv\Scripts\python.exe scripts\td_preflight.py
.\.venv\Scripts\python.exe scripts\td_backfill.py --provider truedata_mock --scope futures --interval 15m --days 45 --data-root D:/tmp/td_mock
```

## Invariants the adapter will not violate

- TLS only (`https` / `wss`). Plain HTTP is refused at config load.
- Credentials never appear in `repr`, logs, or `redacted()`.
- A window older than the declared plan depth is `NotEntitledError`, not a short series.
- Normaliser rejects go to quarantine with a reason; they are not dropped.
- Quality `quarantine` verdict never reaches the training read path.
- Feature construction for training and live is the same `FeatureSet`.

## After credentials land

Reply in chat: **`TrueData API enabled`**. Do not paste the password.
