bye# Market Data Requirements Specification
## Project Alpha / alpha-desk

**Document type:** Technical requirements + vendor RFQ input  
**Audience:** HoQR, Lead Quant Architect review, TrueData sales  
**Date:** 2026-07-26  
**Status:** Working specification (aligns with locked Phase 1 decisions)  
**Related:** `DATA_VENDORS.md`, `DECISIONS_LOCKED.md`, `PHASE1_ML_ARCHITECTURE.txt`

---

## 0. Executive verdict on the proposed hybrid architecture

### Proposal (as stated)

1. **Phase R&D:** TrueData for history + live + options/OI/Greeks/corp actions; warehouse everything locally.  
2. **Phase paper/live:** Prefer Angel One SmartAPI for real-time market data; keep TrueData only for gaps; drop TrueData if Angel is “enough” to cut cost.  
3. **Hard requirement:** Vendor-agnostic data layer — ML/features/risk/execution never bind to TrueData or Angel.

### Verdict

| Aspect | Assessment |
| --- | --- |
| Local warehouse of every consumed bar/tick | **Sound** — mandatory |
| Vendor abstraction (adapters + canonical schema) | **Sound** — already partially built; must be completed |
| TrueData as primary **research / history** source | **Sound** — matches locked decision |
| Angel as **execution** broker | **Sound** |
| Assuming Angel live feed can replace TrueData for production features | **Risky / often false** without a formal parity gate |
| Buying full options / Greeks / depth / corp-actions stack in Phase 1 | **Over-scoped for current strategy** — defer cost until Sleeve B |
| “After we own history we can cancel the vendor” | **Partially true for past bars; false for live, corrections, new expiries, and license constraints** |

**Recommended production posture (improved hybrid):**

```
TrueData (or successor)  = Market-data system of record (history + live bars)
Angel One SmartAPI       = Execution + account/margin + (optional) secondary live quote
Local Parquet warehouse  = Authoritative research store (vendor-tagged partitions)
Feature / ML / Risk      = Read ONLY canonical warehouse / bar bus — never vendor SDKs
```

Cancel or shrink TrueData **only after** a written **Feed Parity Gate** passes (see §7), not when paper “feels fine.”

---

## 1. Critical review of the hybrid plan (Lead Quant Architect)

### 1.1 Hidden assumptions that break desks

1. **Train/serve skew**  
   Models trained on TrueData OHLC/volume/timestamps will compute different features on Angel candles (bar construction, missing minutes, volume semantics, session tagging). Mid-frequency desks die quietly from this, not from bad models.

2. **Angel historical API ≠ archive**  
   Already established: Angel typically serves **currently listed** NFO contracts. Expired months needed for continuous futures usually are not available. Local DB built from live Angel going forward does **not** recreate 2019–2025 history.

3. **“Own the history → cancel vendor” underestimates:**
   - Vendor **restatements / corrections**
   - **New** symbols, lot-size changes, strike introductions
   - **Live** feed still required every session
   - License may restrict how history may be stored/reused (confirm in contract)
   - Corporate-action and master-file maintenance

4. **Options/OI/Greeks in “research Phase 1”**  
   Locked strategy **A1** is **index futures**, 15m, lean features, no options path yet. Paying for full chain history now does not improve P1 promote odds; it increases bill and integration surface.

5. **Live paper on Angel while research warehouse is TrueData**  
   Acceptable only if paper features are built from the **same canonical bar series** you will use in live — or you run dual-feed parity and freeze the paper feed choice before Gate G4/G5.

6. **Cost optimization via “drop TrueData”**  
   Industry practice for small algo firms: keep one **reliable SoR** for market data through first live year; cut spend by narrowing **symbols / history depth / tick vs 1m**, not by mixing feeds mid-flight.

### 1.2 What is worth keeping from the proposal

- Centralized historical warehouse, continuously appended from live.  
- Broker ≠ data vendor mentally (and in code).  
- Retain TrueData **à la carte** later for options/OI if Angel lacks them.  
- Vendor-agnostic interfaces.

### 1.3 Better alternative (recommended)

**Two interfaces, never one “broker does everything” blob:**

| Interface | Responsibility | Phase 1 impl |
| --- | --- | --- |
| `MarketDataProvider` | History + live bars/ticks + masters | TrueData primary; Angel optional secondary |
| `Broker` | Orders, positions, margins, fills | Angel (paper flag / later live) |

**Feed policy:**

| Period | Market data SoR | Execution |
| --- | --- | --- |
| Research + train + backtest | TrueData → warehouse | N/A |
| Paper (P1.5) | Prefer **same SoR as train** (TrueData live → warehouse → features) | PaperBroker / Angel paper if available |
| Early live | TrueData live → warehouse → features | Angel live orders |
| Cost review (after ≥6–12 months paper/live) | Re-quote TrueData; consider Angel live **only if Feed Parity Gate passed** | Angel |

---

## 2. Vendor-agnostic software architecture

### 2.1 Layering (target)

```
┌─────────────────────────────────────────────────────────────┐
│  Strategy / Models / Features / Labels / Validation / Risk  │
│  (no vendor imports)                                        │
└────────────────────────────┬────────────────────────────────┘
                             │ reads
┌────────────────────────────▼────────────────────────────────┐
│  Canonical Market Store                                     │
│  - bars, ticks (optional), contracts, calendars, corp acts  │
│  - every row tagged: source, ingest_ts, schema_version      │
└────────────────────────────┬────────────────────────────────┘
                             │ written by
┌────────────────────────────▼────────────────────────────────┐
│  Ingest + Normalize + Validate                              │
│  - gap detect, dedupe, OHLC invariants, session align       │
└───────┬────────────────────┴────────────────────┬───────────┘
        │                                         │
┌───────▼──────────┐                    ┌─────────▼──────────┐
│ TrueDataAdapter  │                    │ AngelMdAdapter     │
│ (history+live)   │                    │ (live secondary)   │
└──────────────────┘                    └────────────────────┘

┌──────────────────┐
│ AngelBroker      │  ← execution only (separate package)
└──────────────────┘
```

### 2.2 Required interfaces (conceptual)

```text
MarketDataProvider
  list_instruments(segment, asof) -> InstrumentMasterRows
  fetch_bars(contract_id, timeframe, start, end) -> BarFrame
  stream_bars(contract_ids, timeframe) -> Iterator[Bar]
  stream_quotes(contract_ids) -> Iterator[Quote]   # optional L1
  get_calendar(exchange, year) -> SessionCalendar
  get_corporate_actions(symbol, start, end) -> list  # later

BarFrame columns (canonical):
  ts (Asia/Kolkata, tz-aware), open, high, low, close, volume
  + optional: oi, vwap, trade_count, bid, ask, bid_size, ask_size

Provenance columns (warehouse metadata, not model features):
  source, vendor_symbol, contract_id, schema_version, ingested_at
```

Existing alpha-desk pieces to extend (do not reinvent):

- `data/warehouse.py` — source-partitioned Parquet (`bars/<source>/<symbol>/<tf>/`)
- `data/schema.py` — normalize + QA
- `data/providers/*` — adapters
- `data/continuous.py` — continuous futures from **raw** contracts
- `execution/*` — broker, never used as history archive

### 2.3 Normalization strategy

1. Map every vendor symbol → internal `contract_id` (exchange + instrument + expiry + strike/opt-type if any).  
2. Convert all timestamps to **IST**, bar **close-time** convention documented once and frozen.  
3. Volume/OI units documented per source (shares vs lots).  
4. Never merge TrueData + Angel into one continuous series without an explicit `source=merged_*` and a parity report.  
5. Continuous futures are **derived artifacts** (`source=continuous_truedata_v1`), not vendor truth.

### 2.4 Validation layer (non-negotiable)

| Check | Rule |
| --- | --- |
| Dedupe | Unique `(contract_id, timeframe, ts)` |
| Order | Strictly increasing `ts` |
| OHLC | `high ≥ max(o,c)`, `low ≤ min(o,c)`, prices > 0 |
| Session | Bars only inside NSE session (+ documented auction rules) |
| Gaps | Flag missing 1m bars vs session calendar; severity by count |
| Stale live | Alert if last bar age > SLA |
| Cross-source | Daily parity job: TrueData vs Angel on overlapping live contracts |
| Promote gate | `--promote-grade` refuses yahoo/synthetic (already) |

### 2.5 Database / storage architecture

| Store | Use |
| --- | --- |
| **Parquet** under `data/bars/...` | Historical + research (primary) |
| **DuckDB / Polars** over Parquet | Research queries |
| **SQLite/Postgres** | Trials, audit, configs (already) |
| **Optional Redis** | Live last-bar cache only (Phase 2+) |
| **Do not** put multi-year ticks in Postgres | Cost/complexity without Phase 1 need |

**Backup:** versioned copy of `data/bars` + configs + artifact roots to encrypted offline/cloud weekly; retain raw vendor dumps for ≥90 days after ingest for dispute/replay.

**Archive:** monthly Parquet partitions; compress (snappy/zstd); never rewrite locked partitions without a new `schema_version` + audit note.

### 2.6 Continuously expanding local history

1. Live session: subscribe → normalize → append Parquet for **that source**.  
2. End of day: gap-fill from vendor REST history for the same source.  
3. Weekly: rebuild continuous futures from raw expired+front contracts.  
4. Monthly: storage report (rows, gaps, last_ts per symbol).  
5. Dependence on vendor **for past bars** falls over time; dependence for **live + corrections + new contracts** remains until you accept parity risk.

---

## 3. Complete market-data requirement specification

### Scope anchors (locked today)

- **Strategy Sleeve A (Phase 1):** NSE index **futures** — NIFTY, BANKNIFTY (FINNIFTY optional later).  
- **Bar primary:** 15m; **context:** 1d; **build from:** 1m preferred.  
- **Horizon:** 8 × 15m bars; ATR stops; no overnight.  
- **Options structures:** deferred (late Phase 1 / Phase 2).  
- **Capital regime:** paper → ≤ ₹50k first live; mid-frequency, not HFT.

Requirements below are tagged: **P1-MUST** / **P1-SHOULD** / **P2+** / **P3**.

---

### 3.1 Historical data requirements

#### Exchanges & segments

| Segment | Need | Priority |
| --- | --- | --- |
| NSE **NFO** (index futures) | Training + continuous series | **P1-MUST** |
| NSE **Index** cash indices (NIFTY 50, NIFTY BANK) | Regime / sanity / features | **P1-SHOULD** |
| NSE **NFO** index options | Options sleeve | **P2+** |
| NSE CM equities | Stock sleeve | **P3** / defer |
| BSE / MCX / CDS | Out of scope now | Omit |

#### Instruments (immediate)

- NIFTY futures — all expiries needed to build continuous series  
- BANKNIFTY futures — same  
- Optional: FINNIFTY futures (master + history if cheap add-on)  
- Underlying index levels: `NIFTY`, `BANKNIFTY` (or vendor equivalents)

#### Timeframes

| Timeframe | Priority | Notes |
| --- | --- | --- |
| **1-minute OHLCV** | **P1-MUST** | Source of truth; we aggregate 15m/1d |
| 15-minute OHLCV | P1-SHOULD | Convenience; can derive |
| Daily OHLCV | P1-SHOULD | Regime context |
| Tick / trade tape | P2+ | Not required for lean/v0 |
| Snapshot option chain history | P2+ | Options sleeve |

#### History depth

| Use | Minimum | Preferred |
| --- | --- | --- |
| Promote-grade walk-forward (futures) | **3 years** | **5–7 years** if priced reasonably |
| Index levels | Match futures span | Same |
| Options (later) | 2+ years chain snapshots | 3+ years |

#### Continuous futures

- **P1-MUST:** Raw **expired + live** futures contracts (not only continuous).  
- Continuous series built **in-house** (volume or days-to-expiry roll — already coded path).  
- Vendor-provided continuous is optional bonus; never sole input.

#### Expired contracts

- **P1-MUST:** Historical bars for **expired** NFO futures months covering the full lookback.  
- Without this, continuous futures and honest WF are impossible (Angel gap).

#### Corporate-action-adjusted data

- Index futures: corp-actions on constituents matter less than for stocks; still need **lot-size / freeze / symbol-change** history.  
- Full equity corp-action adjusted prices: **P3** (stock sleeve).  
- For indices: vendor should document whether index history is official NSE series.

#### Sector / macro / alt

| Dataset | Priority |
| --- | --- |
| India VIX (if available) | P1-SHOULD |
| Sector indices | P2+ |
| FII/DII, news, alt data | P2+ / architecture deferred |

---

### 3.2 Real-time data requirements

Phase 1 paper/live is **bar mid-freq**, flatten by ~15:15. We do **not** need exchange co-lo tick-to-trade latency.

| Feed | Priority | Spec |
| --- | --- | --- |
| Live **1m** OHLCV (or tick→1m we build) for front NIFTY/BN futures | **P1-MUST** | WebSocket preferred; REST poll ≤5–15s acceptable for v0 |
| LTP | **P1-MUST** | For marks / paper fills |
| Session OHLC / day volume | P1-SHOULD | |
| Bid/Ask (L1) | P1-SHOULD | Better paper fill model; not required for first feature set |
| Market depth L2 | P2+ | Omit initially |
| Live OI | P2+ (futures OI P1-SHOULD if cheap) | |
| Full live option chain | P2+ | |

**Transport:** WebSocket for live; REST for history backfill and gap-fill.  
**Update frequency:** 1m bar close within **≤ 2–5 seconds** of exchange minute end is enough for 15m strategy; alert if **> 15–30 seconds** stale during session.  
**Ticks:** defer storage unless needed for TCA later.

---

### 3.3 Options & derivatives requirements

| Item | Priority | Rationale |
| --- | --- | --- |
| Continuous / raw index **futures** | **P1-MUST** | Sleeve A |
| Historical **futures** OI | P1-SHOULD | Feature candidate; not in lean/v0 yet |
| Full live option chain | P2+ | Options sleeve |
| Historical option chain snapshots | P2+ | Expensive; buy when Sleeve B starts |
| Greeks (Δ Γ Θ ν) | P2+ | Prefer compute ourselves from chain + rate model when needed |
| IV surface | P2+ | |
| PCR / Max Pain | P2+ | Easy derived metrics; don’t buy as primary product |
| Expired options history | P2+ | |

**Cost rule:** Do **not** subscribe TrueData options packages until a written options trial_id exists.

---

### 3.4 Reference & corporate data

| Item | Priority |
| --- | --- |
| Symbol / instrument master (NFO) daily | **P1-MUST** |
| Lot size, tick size, freeze qty, expiry date | **P1-MUST** |
| Expiry calendar | **P1-MUST** |
| Trading holidays + session times (NSE) | **P1-MUST** |
| Contract add/delist events | **P1-MUST** |
| Equity corp actions (split/div/bonus/rights) | P3 |
| Delisted equity handling | P3 |

Masters must be dated (`as_of`) so backtests use point-in-time contract specs.

---

### 3.5 Data quality requirements

| Topic | Requirement |
| --- | --- |
| Latency (live 1m) | Target p95 < 5s after minute close; alert > 30s |
| Missing-data tolerance | Research: flag gaps; **no silent fill** for promote datasets. Live: gap-fill from REST within session if WS drops |
| Timestamp precision | Second or better; store tz-aware IST |
| Gap detection | vs NSE calendar × expected 1m bars |
| Validation | OHLC invariants + dedupe + monotonic ts (see §2.4) |
| Duplicates | Drop keep-last by ingest policy; log count |
| Time sync | NTP on research/trading host; log clock skew |
| Exchange consistency | One exchange clock; no mix of naive UTC/IST |
| Cleaning | Quarantine bad partitions; never train on `is_clean=false` without override |
| Storage | Parquet, monthly partitions, zstd/snappy |
| Versioning | `schema_version` + `source` + feature-set hash (already) |
| Backup | Weekly full + daily incremental of new partitions |
| Archive | Keep raw vendor extracts ≥ 90 days; curated bars indefinite |

---

## 4. Prioritized TrueData services (buy vs defer)

### 4.1 Buy now (quotation must include)

1. **NSE NFO historical** — NIFTY + BANKNIFTY **futures**, **all expiries / expired months**, **≥ 3 years**, **1-minute OHLCV** (OI if included).  
2. **NSE index historical** — NIFTY 50 + NIFTY BANK daily and/or 1m (as available).  
3. **Live NFO** — WebSocket (or equivalent) for front-month (and near) NIFTY + BANKNIFTY futures: LTP + 1m bars (or ticks we aggregate).  
4. **Instrument master / contract list** API with lot/tick/expiry.  
5. **Algo / commercial license** allowing: automated trading research, local storage, historical backfill, paper + eventual live use.  
6. **Python / REST + streaming** access suitable for unattended ingest jobs.

### 4.2 Should-have if marginal cost is low

- India VIX history + live  
- Futures OI history + live  
- FINNIFTY futures history (same shape)  
- Trading holiday calendar endpoint  

### 4.3 Defer (omit from initial quote to control cost)

- Full options chain live + history  
- Vendor Greeks / IV surfaces  
- Market depth L2/L3  
- Tick-level long archive  
- Equity CM history / corp-action adjusted stock database  
- PCR/Max Pain products (derive later)  
- Global / non-NSE markets  

### 4.4 Explicitly clarify with sales (contract terms)

- Storage rights for local Parquet warehouse  
- Redistribution: internal only (expected)  
- Correction / restatement policy  
- Whether expired futures history is complete for ≥3y  
- Trial credentials for **Market Data API** (not Velocity terminal alone)  
- Rate limits, concurrency, historical download caps  
- SLA for live feed and support hours (IST)  

---

## 5. Phase roadmap (cost-optimized)

### Phase 1 — Immediate (research / backtest / ML train) — **NOW**

- TrueData: NFO futures history 1m ≥3y + masters + index  
- Ingest → warehouse → continuous → features → models → WF  
- **No** options package  
- Angel: execution scaffolding only; not promote history  

### Phase 2 — Paper trading & validation (P1.5)

- TrueData **live** 1m/LTP for same symbols → warehouse → paper engine  
- Optional Angel live as **shadow feed** for parity metrics  
- Kill-switch / risk path already built  
- Do **not** cancel TrueData during paper sample (60 sessions / 100 trades gate)

### Phase 3 — Production / limited live

- Same binaries; TrueData remains MD SoR until Feed Parity Gate  
- Angel: live orders + SPAN/margin truth  
- Monitoring: stale feed, gap rate, shortfall vs backtest  
- Retrain cadence on **warehouse** data (vendor-tagged)  
- Re-bid TrueData annually; narrow symbols/timeframes before switching SoR  

### Feed Parity Gate (required before dropping TrueData live)

Must all pass for ≥20 sessions:

1. 1m OHLC correlation / max abs diff within tolerance on front futures  
2. Feature distribution drift (PSI / KS) below threshold  
3. Zero unexplained session gaps on Angel relative to TrueData  
4. Written HoQR approval  

Until then: **TrueData live + Angel execution**.

---

## 6. Architecture improvements checklist (engineering)

- [ ] Formal `MarketDataProvider` protocol; TrueData + Angel adapters  
- [ ] Keep `source=` partitions forever; forbid silent merges  
- [ ] EOD gap-fill job + gap report  
- [ ] Live→Parquet appender (P1.5)  
- [ ] Dual-feed parity dashboard (even if Angel is shadow-only)  
- [ ] PIT instrument master snapshots  
- [ ] License/ToS review before bulk download  
- [ ] Do not wire features to Angel SDK calls  

---

## 7. Concise checklist for TrueData sales (copy/paste)

**Project:** Algorithmic / ML mid-frequency trading research & paper trading on NSE index futures (India).  
**Broker (execution only):** Angel One SmartAPI — separate from market data.  
**We need a quotation + API trial (Market Data API, not only Velocity UI).**

### Must quote

| # | Requirement |
| --- | --- |
| 1 | Segment: **NSE F&O (NFO)** |
| 2 | Symbols: **NIFTY & BANKNIFTY futures** (all contracts / expired months required) |
| 3 | History: **≥ 3 years** (quote 5y if available) |
| 4 | Resolution: **1-minute OHLCV** (OI if available) |
| 5 | Delivery: **REST/API historical download** suitable for bulk backfill |
| 6 | Live: **streaming** LTP and/or 1-minute bars for front + near futures |
| 7 | **Instrument master** (lot size, tick, expiry, symbol mapping) |
| 8 | License: **algo / automated systems**, **local storage** of history for research & trading |
| 9 | Access: **Python**-friendly; document rate limits & concurrency |
| 10 | Optional add-ons (price separately): NIFTY/BANKNIFTY **index** history, **India VIX**, **FINNIFTY** futures |

### Please price separately / do not bundle unless cheap

- NSE options chain (live + historical)  
- Vendor Greeks / IV  
- Market depth  
- Tick archive  
- Equity cash market  

### Questions we need answered in writing

1. Confirm **expired futures** history availability for NIFTY/BANKNIFTY for ≥3 years at 1m.  
2. Bar timestamp convention (interval start vs end) and timezone.  
3. Live feed protocol (WebSocket URL/API) and typical latency.  
4. Historical download limits (rows/day, parallel requests).  
5. Trial: duration, symbols enabled, historical depth enabled.  
6. Commercial terms for **storing** data in our private warehouse.  
7. Support contact and incident SLA (IST market hours).  

### Out of scope for this quote

HFT colocation, BSE/MCX, full options analytics suite (unless itemized separately for a later phase).

---

## 8. Deliverables map (this document)

| # | Deliverable | Section |
| --- | --- | --- |
| 1 | Complete technical requirements | §3 |
| 2 | Prioritized TrueData services | §4.1–4.2 |
| 3 | Optional / deferred APIs | §4.3 |
| 4 | Vendor-agnostic architecture | §2 |
| 5 | Cost-optimized roadmap | §5 |
| 6 | Sales checklist | §7 |
| — | Architecture challenge / improvements | §0–§1 |

---

## 9. Bottom line

- **Do** make TrueData the market-data system of record for Phase 1 research and paper.  
- **Do** keep Angel as execution-only until a formal feed parity gate.  
- **Do not** buy the full options/Greeks/depth catalog for Sleeve A.  
- **Do not** plan to “cancel TrueData after we have history” without retaining a live SoR and legal storage rights.  
- **Do** invest in adapters + canonical warehouse + gap/parity validation — that is what makes vendor switching cheap later, not hoping Angel can replace an archive vendor.
