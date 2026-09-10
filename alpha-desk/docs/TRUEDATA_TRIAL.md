# TrueData trial — what you actually received

**Date:** 2026-07-26  
**Trial user:** stored in local `.env` as `TRUEDATA_USER` (not repeated here)  
**Ends:** originally 2026-08-02 (Velocity). A **7-day Market Data API** trial was applied for separately.

## Important finding

The first welcome email was **TrueData Velocity 2.0** (desktop charting + Excel plugin).

Login against the **Market Data API** auth endpoint (`https://auth.truedata.in/token`)
returned **`invalid_grant`**. **Velocity trial ≠ Market Data API access.**

Paper trading remains on **PaperBroker**. Angel/TrueData are **not** placing any real orders.

## Backend status (2026-08-15)

The vendor-agnostic market-data stack, TrueData adapter, store, ingest, live
recorder and ML bridge are implemented and tested against documented mock
payloads. See `docs/TRUEDATA_BACKEND.md`.

Until Market Data API credentials are in `.env`, do **not** start the 7-day
clock. Rehearse with:

```powershell
.\.venv\Scripts\python.exe scripts\td_preflight.py
```

## Typical trial limits (confirm with TrueData for your userid)

| Limit | Typical trial | Our handling |
| --- | --- | --- |
| Concurrent live symbols | ~30 | `TRUEDATA_MAX_LIVE_SYMBOLS`; watchlist capped in `td_record_live.py` |
| REST historical rate | ~5 req/s | `TRUEDATA_REST_RPM=300` (built-in limiter + backoff on 429) |
| 1-minute history depth | ~2 months | `TRUEDATA_HISTORY_DAYS_M1=60`; chunked backfill, resumable |
| Tick history depth | ~2 days | `TRUEDATA_HISTORY_DAYS_TICK=2` |
| Duration | 7 days | Use calendar; keys revoke after expiry |
| Segments | NSE cash + F&O | `TRUEDATA_SEGMENTS=INDEX,EQ,FUT,OPT` |

**Compliance:** Trial data is internal R&D only — no redistribution, no public
display. Our **private** paper loop (Angel + internal logs, not a public mock
trading product) is a separate execution path; TrueData feeds **research**, not
order routing. Confirm with TrueData if unsure.

## Recommended 7-day usage (do not waste quota on day 1)

**Before day 1:** `td_preflight.py` on mocks (no clock).

**Day 1:** `td_smoke.py` → `td_probe.py` (measure real depth, port, latency; save
`artifacts/truedata_probe/`). Tune `.env` from probe output.

**Days 2–3:** Backfill **narrow first**: continuous futures 15m (60d), then ATM
option ladder (5 strikes each side, 1–2 expiries), not full chain.

**Days 4–5:** Market hours — `td_record_live.py --limit 30 --minutes 30` with
`--capture-raw`. Validate reconnect + latency in `td_trial_report.py`.

**Days 6–7:** `train_from_store.py --then-train` + walk-forward on **truedata**
source only. Export `td_trial_report.md` for buy/no-buy.

**Avoid on trial:** parallel backfill of hundreds of option legs at boot;
requesting >2 months of 1m for every symbol; subscribing >30 live names.

## After they enable API

1. Put userid/password in `.env`. Confirm `TRUEDATA_WS_PORT` with TrueData.
2. Set trial limits in `.env` (copy from `.env.example`: 30 symbols, 300 RPM, 60d 1m, 2d tick).
3. Reply in chat: **`TrueData API enabled`** (do **not** paste the password).
4. Run, in order: `td_smoke.py` → `td_probe.py` → `td_backfill.py` → `td_record_live.py` → `train_from_store.py` → `td_trial_report.py`.

## Security

Never paste passwords in chat — only say “updated in `.env`”. Rotate the trial
password after the trial if TrueData allows.
