# Feed Parity Gate (compliance C1 / train=serve)

**Rule:** The series you **train** on must be the same recipe you **paper** on, and later the same recipe you would use for live marks.

## Current Phase 1 SoR

| Role | Source |
|------|--------|
| Market-data capture | **Angel One** (`MARKET_DATA_PROVIDER=angelone`) |
| TrueData | **OFF** (`TRUEDATA_ENABLED=false`) — trial over; do not mix |
| Execution | **PaperBroker only** (live orders disabled) |

## Do

1. Capture with `ao_record_live` / `ao_backfill` → store under `source=angelone`.
2. Sync to warehouse: `sync_store_to_warehouse.py --source angelone ...`
3. Train: `train_from_store.py --source angelone ...`
4. Paper: `ao_paper_day.py --source angelone ...`

## Do not

- Train on `truedata` and paper/live-mark on `angelone` (or reverse) without a written parity study.
- Claim promote while SoR is mixed or history is only days/weeks deep.

## Gate question (before any future live orders)

> “If I replay yesterday’s Angel bars through the paper path, do features/scores match what the live mark path would have seen?”

Until that is yes in writing, live orders stay off.
