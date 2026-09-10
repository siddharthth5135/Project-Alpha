# Project Alpha — 100% Completion Roadmap (Source of Truth)

**Status:** ACTIVE — follow this file for all future development  
**Created:** 2026-09-09  
**Plain-text twin (easy reading / sharing):** `docs/PROJECT_100_ROADMAP.txt`  
**Strategy / market-logic brief for analysts:** `docs/PROJECT_100_Strategies.txt`  
**Owner principle:** Consistency and accuracy over speed. Real-money readiness requires proof, not hope.  
**Capital rule:** Zero rupees of live capital until Gate **G-LIVE** is explicitly signed. Paper only until then.

---

## 0. How to use this document

1. Every development session starts by reading **this file**.
2. Do not skip gates. A later phase must not start until the prior gate is **PASS**.
3. “100%” means **deploy-ready for real money under written risk limits**, not “models trained once.”
4. Trial TrueData ends **2026-09-13**. That deadline governs **Phase T only**, not the whole project.

### Hard truths (do not soft-pedal)

| Claim | Reality |
| --- | --- |
| Finish real-money ready by 13 Sep | **Not possible with integrity.** Trial bar history is ~15 days; promote-grade needs years. |
| One training run is enough | **False.** Retrain + re-validate on a schedule forever. |
| Trial IC looks good → trade live | **False.** Short samples overfit. `promote_eligible=false` until gates pass. |
| Agent works while laptop sleeps | **False.** Laptop must be on; OS scheduler runs jobs; Cursor chat alone is not a cron. |

---

## 1. Definition of 100% (overall score)

Score is a **weighted checklist**. Do not claim 100 until every gate below is PASS.

| Gate | Name | Weight | Meaning |
| --- | --- | ---: | --- |
| G0 | Safety & compliance locked | 5% | Live blocked; audit; risk signed; paper-only |
| G1 | Market-data SoR proven | 15% | TrueData (or successor) ingest, store, quality, live capture reliable |
| G2 | Research dataset promote-grade | 15% | Enough clean history; calendar correct; quarantine honest |
| G3 | Models + purged walk-forward | 20% | Ridge / LGBM / loss pass reject→promote rules on real data |
| G4 | Strategy + paper path | 15% | Rules → OrderPath → PaperBroker; kill-switch drill |
| G5 | Paper sample | 15% | ≥60 sessions **or** ≥100 paper trades with written review |
| G6 | Ops automation | 5% | Unattended daily capture + weekly retrain + alerts |
| G7 | Broker parity & deploy | 5% | Angel (or chosen broker) paper→tiny live; feed parity if dual feed |
| G-LIVE | Real-money go | 5% | Signed go-signature, capital cap, kill switch armed |

**Current estimate (2026-09-09):** ~**35–40%**  
(G0 mostly done; G1 partial on trial; G2–G-LIVE incomplete.)

---

## 2. North-star architecture (never break)

```
TrueData / vendor feed
        ↓
Canonical contracts + quality gates + immutable parquet store
        ↓
Features + labels (same recipe for research and paper)
        ↓
Models (ridge_alpha, lgbm_alpha, loss_hit_stop) + versioned artifacts
        ↓
Purged walk-forward → REJECT or PROMOTE
        ↓
Strategy rules + risk kernel → OrderPath
        ↓
PaperBroker  ──(only after G-LIVE)──► Live broker (tiny size)
```

**Invariants**

- ML / strategy never import `vendors.truedata` directly.
- No silent gap-fill for promote datasets.
- Live orders require non-empty `ALPHA_LIVE_GO_SIGNATURE` (or successor).
- Credentials only in `.env` (never committed).

---

## 3. Phase map (accuracy-first order)

### Phase T — Trial harvest (now → 2026-09-13) — **do not skip**

**Goal:** Extract maximum learning + data + automation from Trial220 before expiry.  
**Not a goal:** Promote models or live trading.

| Day | Focus | Done when |
| --- | --- | --- |
| T0 (done) | Auth, smoke, probe, first backfill, first train | Smoke pass; train artifacts exist |
| T1 | Fix quality calendar gaps; promote more futures bars | Fewer false quarantines on known holidays |
| T2–T4 | **Scheduled live capture** every session; tick + touchline archive | Raw + promoted live partitions for each day |
| T2–T4 | Incremental backfill; options ladder (narrow ATM) | Store grows; trial report artifacts |
| T5 | Trial decision report + paid-plan quote checklist | Keep / upgrade / cancel TrueData written |

**Trial data limits (enforce in config, never pretend otherwise)**

| Dataset | Trial depth | Enough for promote? |
| --- | --- | --- |
| Ticks | 2 days | No |
| Bars 1m–60m | 15 days | No |
| EOD | ~3 years claimed | Maybe for EOD research only; mid-freq needs deeper 1m/15m |
| Live symbols | 50 concurrent | OK for paper research |

**13 Sep answer:** Possible to finish **Phase T** well. **Not** possible to finish project 100% on trial depth alone.

**Extra trial (another email/user)?**

| Option | Verdict |
| --- | --- |
| Second free trial under new email | Often against vendor ToS; do **not** rely on this for accuracy |
| Paid TrueData plan after trial | **Required** for G2 promote-grade mid-freq history |
| Short gap without feed | Acceptable if Phase T archives + automation scripts are solid |

---

### Phase A — Safety freeze (G0)

- [x] Risk config signed / paper-only enforced  
- [x] Live go-signature empty by default  
- [ ] Compliance note updated for Trial220 (dates, limits)  
- [ ] Kill-switch drill executed and logged (`scripts/kill_switch_drill.py`)  
- [ ] “No live capital” banner in runbooks  

**Exit:** G0 = PASS

---

### Phase B — Market-data system of record (G1)

- [x] Vendor-agnostic layer + TrueData adapter  
- [x] Store, ingest, smoke/probe/backfill/live scripts  
- [ ] Holiday calendar learned / verified for 2026 gaps that false-quarantine  
- [ ] Continuous futures resolution stable (`NIFTY-I` vs dated `NIFTY26SEPFUT`)  
- [ ] Index EOD quality rules (zero-volume indices) documented or relaxed with reason  
- [ ] Live recorder: reconnect, flush, 50-symbol cap, raw capture  
- [ ] Daily integrity script: row counts, last timestamp, gap report  

**Exit:** G1 = PASS when 5 consecutive sessions of live capture succeed without manual babysitting.

---

### Phase C — Promote-grade dataset (G2)

**Blocked on paid history depth for 1m/15m.**

- [ ] Paid plan history windows set in `.env` honestly  
- [ ] Futures backfill (priority: NIFTY then BANKNIFTY) to **≥3 years** 15m (prefer 5–7y if affordable)  
- [ ] 1m backfill for recent window used by labels (at least months, not 15 days)  
- [ ] Corporate actions / rolls documented for continuous series  
- [ ] Quarantine rate < agreed threshold; manifests audited  
- [ ] Dataset freeze tag: `DATASET_PROMOTE_vN` with hash  

**Exit:** G2 = PASS only with frozen promote dataset + written depth proof.

---

### Phase D — Models & validation (G3)

Models (fixed set until a written change-control):

1. `ridge_alpha` — linear alpha score  
2. `lgbm_alpha` — nonlinear alpha score  
3. `loss_hit_stop` — stop-hit probability  

Pipeline forever:

```
store → train_from_store.py → train_models.py → validate_models.py (purged WF) → REJECT|PROMOTE
```

- [ ] Training table built only from **promoted** store partitions  
- [ ] Embargo / purge respected; no leakage  
- [ ] Walk-forward report checked in  
- [ ] Promote eligibility true for at least one alpha + loss model **or** explicit reject with reason  
- [ ] Retrain schedule defined (see §5)  

**Exit:** G3 = PASS when promote artifacts exist **or** strategy is formally parked (no live path).

---

### Phase E — Strategy & paper plumbing (G4)

- [ ] `alpha_score_threshold` approved in `configs/strategy_spec_001.yaml`  
- [ ] Paper path uses TrueData-sourced scores (not synthetic)  
- [ ] Risk kernel limits enforced on every order  
- [ ] Kill switch + audit log verified (`verify_g4.py`)  
- [ ] Flatten-by-time / session rules tested  

**Exit:** G4 = PASS

---

### Phase F — Paper sample (G5) — longest honest gate

Minimum sample (from market-data requirements):

- ≥ **60** trading sessions of paper, **or**
- ≥ **100** paper trades with post-trade review

Track weekly:

- PnL (paper), max DD, hit rate, slippage vs model, kill-switch events, data outages

**Exit:** G5 = PASS only after written human review of the sample. No shortcuts.

---

### Phase G — Unattended ops (G6)

- [ ] Windows Task Scheduler (or equivalent) jobs installed (§5)  
- [ ] Heartbeat file / log proving jobs ran  
- [ ] Failure notification path (email/Telegram/local toast — pick one)  
- [ ] Weekly retrain + validation job  
- [ ] Disk / quota guard for parquet growth  

**Exit:** G6 = PASS when jobs run 5 consecutive market days without human click.

---

### Phase H — Deploy & tiny live (G7 + G-LIVE)

- [ ] Broker paper integration verified (Angel or chosen)  
- [ ] Feed parity gate if live marks ≠ research SoR  
- [ ] Capital cap (docs: start ≤ ₹50k notionally)  
- [ ] Written go checklist signed; `ALPHA_LIVE_GO_SIGNATURE` set deliberately  
- [ ] First live week: size floor, human on-call, kill switch tested  

**Exit:** 100% only when G-LIVE = PASS and first live week report archived.

---

## 4. Retraining doctrine (answer to “one train enough?”)

| Cadence | Action | Why |
| --- | --- | --- |
| Every market day | Live capture + incremental bars | New information arrives daily |
| After each capture | Integrity check | Catch feed holes same day |
| Weekly (weekend) | Rebuild table → retrain 3 models → walk-forward | Weights must track regime |
| After data vendor change / roll | Full backfill + retrain | Symbology / history shift |
| After promote reject | Root-cause, do not “tweak until green” blindly | Accuracy > vanity metrics |

**Same history is not “new data.”** Retrain without new bars mainly re-fits noise.  
**New live bars + deeper paid history are the real new fuel.**

---

## 5. Unattended automation plan (you away during market hours)

### What works without you at the keyboard

| Mechanism | Works if laptop… | Needs Cursor open? |
| --- | --- | --- |
| **Windows Task Scheduler** running `scripts/ops_daily.ps1` | On, awake, network OK | **No** |
| Cursor agent in this chat | On + chat session alive | Yes — fragile |
| Cursor Automations (Agents Window) | Cloud/agent runtime configured | Separate product setup |

**Recommended primary path:** Windows Task Scheduler + PowerShell wrappers.  
Cursor may supervise design/fixes when you are back; it must **not** be the only cron.

### Required machine setup (one-time, with you present)

1. Laptop power: AC + “never sleep” while plugged in during market days (or use a small always-on PC).  
2. Auto-login optional; prefer scheduled task “Run whether user is logged on” with stored vault creds for the venv only.  
3. Install tasks:

| Task name | Trigger (IST) | Script |
| --- | --- | --- |
| `AlphaDesk-PreOpen` | 09:00 Mon–Fri | Health: auth smoke, disk space |
| `AlphaDesk-LiveAM` | 09:14 Mon–Fri | `td_record_live.py` until 12:00 |
| `AlphaDesk-LivePM` | 12:05 Mon–Fri | `td_record_live.py` until 15:35 |
| `AlphaDesk-EOD` | 15:45 Mon–Fri | Incremental backfill + integrity + trial/daily report |
| `AlphaDesk-WeekendTrain` | Sat 10:00 | `train_from_store.py --then-train` + validate |

4. Heartbeat: each job appends `logs/ops/heartbeat.jsonl`.  
5. Fail loud: non-zero exit writes `logs/ops/FAIL_<date>.txt`.

### Scripts to implement (Phase G / late Phase T)

| Path | Purpose |
| --- | --- |
| `scripts/ops_daily.ps1` | Single entry: mode=preopen\|live\|eod |
| `scripts/ops_install_tasks.ps1` | Creates/updates Windows Scheduled Tasks |
| `scripts/ops_integrity.py` | Last bar age, gap %, symbol count |
| `scripts/ops_heartbeat.py` | Append heartbeat |

**Permission model you granted:** Agent may create these scripts and install tasks **when the machine is available**. Scheduler then runs without you. Agent cannot wake a sleeping laptop or run if Windows is off.

---

## 6. Money-safety rules (non-negotiable)

1. No live orders until G-LIVE.  
2. No “temporary” live for testing.  
3. No promote on Yahoo/synthetic/`truedata` trial-short samples.  
4. Quarantine > silent accept.  
5. Kill switch must work before any broker credentials for live.  
6. Risk limits are code, not comments.  
7. If data feed dies mid-session → flat / no new risk (paper and live).  

**“Not a single money loss” is not a guarantee markets can give.**  
This roadmap maximizes **process integrity** so losses (when they happen) are sized, logged, and stoppable — not accidental.

---

## 7. Exact time-to-100% / real-money (do not confuse “trial days”)

### Two different clocks

| Clock | What it measures | Stacking free trials help? |
| --- | --- | --- |
| **A. Vendor history depth** | How far back REST will return 1m/15m bars **on this plan** | **NO.** Each trial still returns only ~**15 days** of bars. Changing username does not add years. |
| **B. Calendar paper sample** | How many **trading sessions** you paper-trade after models promote | Partially — only if feed stays online; still need depth from (A) |

### Straight answer you asked for

| Question | Answer |
| --- | --- |
| How many **free trial days** until 100% / real money? | **None are enough.** Trials are for pipeline proof, not promote-grade history. |
| How many **extra Trial220-style trials** (change creds 2–3×)? | **0 recommended.** Does **not** unlock 3-year 1m history. Wastes time; may violate vendor ToS. |
| What do we actually need for G2 (promote dataset)? | **Paid** TrueData (or equal) with **≥3 years** of 15m (prefer 5–7y) + months of 1m — **one continuous paid plan**, not N trials. |
| What calendar time after paid data for G5 paper? | **≥60 trading sessions ≈ ~3 months** of Indian market days **or** ≥100 paper trades with review. |
| Earliest honest **real-money** window (if paid starts immediately after 13 Sep)? | **~3–4 months** of disciplined paper after G2–G4 pass — **not days**. Rushing = money risk. |

### Numeric plan (accuracy-first)

1. **Now → 13 Sep 2026:** finish Phase T on Trial220 (days left on **this** trial only).  
2. **Day after trial:** start **paid** feed (same project; update `.env` once).  
3. **Weeks 1–2 on paid:** deep backfill + calendar fix → G2.  
4. **Weeks 2–4:** train + purged walk-forward → G3 (or REJECT and iterate).  
5. **Next ~60 sessions:** paper sample → G5.  
6. **Only then:** G-LIVE tiny capital.

**If you buy more trials instead of paid depth:** score stays stuck near ~40%. Creds rotation is for account changes on a **real plan**, not a cheat code for history.

---

## 7b. What to finish before 13 Sep vs after

### Before 13 Sep (mandatory harvest)

1. Maximize live + history archive under Trial220.  
2. Install unattended scheduler (§5).  
3. Fix calendar / symbology defects that burn trial days.  
4. Write `artifacts/truedata_trial/DECISION.md` (keep paid / pause / switch).  
5. Freeze trial learnings into this roadmap (update §9 changelog).

### After 13 Sep (required for 100%)

1. Paid market-data depth for G2.  
2. Full walk-forward promote path G3.  
3. Multi-week paper sample G5.  
4. Broker deploy G7 / G-LIVE.

**Second free trial:** Prefer **paid continuity** over account hopping. If TrueData offers a legitimate extension on the same userid, take it. Do not build the plan on ToS-grey multi-accounts.

---

## 8. Session playbook for the agent (every future chat)

1. Open this file; note current phase + next unchecked item.  
2. Prefer accuracy fixes (calendar, quality, validation) over new features.  
3. Never enable live trading.  
4. After code changes: run targeted tests + relevant ops script dry-run.  
5. Append a short note to §9 when a gate moves.  
6. If user is away: rely on Scheduled Tasks; do not claim work ran unless heartbeat proves it.

---

## 9. Changelog

| Date | Change |
| --- | --- |
| 2026-09-09 | Roadmap created. Trial220 live. First TrueData train done (`promote_eligible=false`). Score ~35–40%. |
| 2026-09-09 | Unattended ops installed: `ops_daily.ps1`, Windows tasks AlphaDesk-*. Clarified §7: free trials cannot reach G-LIVE; need paid depth + ~60 paper sessions. |
| 2026-09-09 | Phase T all-in: NSE 2026 holidays verified; EOD expected stamps = midnight IST; index zero-volume tolerance; trial DECISION.md drafted. |

---

## 10. Immediate next actions (ordered)

1. Implement `scripts/ops_*.ps1` + `ops_integrity.py` + install Windows tasks.  
2. Soften/fix holiday calendar false quarantines for Aug/Sep 2026 dates observed in trial.  
3. Run live capture on next trading session via scheduler (no human needed if PC awake).  
4. Produce trial decision draft before 2026-09-13.  
5. Do **not** chase promote metrics on 15-day bars.

---

**End of source-of-truth roadmap.**  
Related: `docs/PROJECT_100_ROADMAP.txt`, `docs/PROJECT_100_Strategies.txt`, `docs/MARKET_DATA_REQUIREMENTS.md`, `docs/TRUEDATA_BACKEND.md`, `docs/HOQR_ACTION_CHECKLIST.md`, `docs/DECISIONS_LOCKED.md`
