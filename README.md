# Project Alpha

**AI-accelerated mid-frequency systematic trading desk — Indian equity & F&O**  
*Internal product & engineering README for a full Software + AI engineering team building with Cursor / coding agents (vibe coding), under a multi-agent runtime hierarchy reporting to Head of Quant Research.*

| Field | Spec |
| --- | --- |
| **Operator model** | Full team: Software engineers + AI / GenAI engineers + Cursor coding agents |
| **Runtime control** | Specialist agents → **Desk Manager Agent** → **Head of Quant Research** |
| **Business shape (Year-1)** | Proprietary capital only |
| **Primary mandate** | Highest-probability survivable systematic India desk under hard risk + loss gates |
| **Market freeze** | **NSE only** — liquid equities + F&O (index & stock). No US / crypto live book in Y1 |
| **Optimize for** | Risk-adjusted performance; **minimize losses / prefer flat break-even over hope** |
| **Returns** | Outcomes of process and risk-taking — never control-loop targets |

This document is the operating brief for vision, markets, strategies, model training vs LLM APIs, tech stack, multi-agent army & hierarchy, build timeline, capital tiers, risk, paper-trading protocol, AI build process, difficulties, and compliance.

### Canonical docs (2-phase plan)

| Phase | File | Role |
| --- | --- | --- |
| Overview | [`README.md`](README.md) | Master operating brief (this file) |
| Phase 1 | [`PHASE1_ML_ARCHITECTURE.txt`](PHASE1_ML_ARCHITECTURE.txt) | What to build (ML-only decisions) |
| Phase 1 | [`PHASE1_EXECUTION_PLAYBOOK.txt`](PHASE1_EXECUTION_PLAYBOOK.txt) | How to build (micro-steps, gates, your role) |
| Phase 1 | [`STRATEGIES.txt`](STRATEGIES.txt) | What to trade first (A1/A2) |
| Phase 2 | [`AGENT_ARMY.txt`](AGENT_ARMY.txt) | AI/LLM boundaries when enhancers are added |
| **Code** | [`alpha-desk/`](alpha-desk/README.md) | **The implementation** (Phase 1 foundation, live OFF) |
| Code | [`alpha-desk/docs/DECISIONS_LOCKED.md`](alpha-desk/docs/DECISIONS_LOCKED.md) | Every open decision closed, with reasoning |
| Code | [`alpha-desk/docs/RUNBOOKS.md`](alpha-desk/docs/RUNBOOKS.md) | Flatten, halt, reconnect, kill-switch drill |

### Current build state — 2026-07-26 (IC remediation)

**P1.0–P1.1 code complete. Independent IC review applied: lots/margins/risk caps
corrected. Live ₹10L = NO-GO. Phase 1.2 ML = GO WITH CONDITIONS (futures data +
HoQR actions still required).**

See [`alpha-desk/docs/IC_CONDITIONS.md`](alpha-desk/docs/IC_CONDITIONS.md) and
[`alpha-desk/docs/DATA_VENDORS.md`](alpha-desk/docs/DATA_VENDORS.md).

| Component | State |
| --- | --- |
| Config layer with fail-closed desk invariants | Done |
| India cost model (brokerage/STT/exchange/SEBI/IPFT/stamp/GST/slippage) | Done — **rates unverified against a contract note** |
| Risk Kernel v0 + kill switch | Done — 32 unit tests |
| Loss Prediction gate (interface + heuristic stub) | Done — enforced in the order path |
| Portfolio sizer (deterministic, margin-aware) | Done |
| Paper broker + rate limiter; Angel One adapter | Done — adapter deliberately inert until G5 |
| Trial registry (append-only SQLite) | Done |
| Hash-chained audit log | Done |
| Parquet warehouse + Yahoo/synthetic providers | Done — sources partitioned so real and fake data cannot merge |
| Continuous futures / expiry roll builder | Done — returns proven invariant to later rolls |
| Feature set `lean/v0` (14 features) + freeze lock | Done — schema hash committed, mismatch fails closed |
| Point-in-time leakage tests + control experiments | Done — truncation and shift-future attacks, both proven to bite |
| Label pipeline (forward return + triple barrier) | Done — versioned `label_def_id`, ties resolved against us |
| Supervised dataset builder | Done — every row tagged with feature hash and label id |
| Models, walk-forward validation, paper loop | **Not started — steps P1.2 to P1.5** |

Total: 183 tests green. `alpha-desk/scripts/verify_foundation.py` prints the G0 checklist,
`alpha-desk/scripts/verify_g1.py` prints G1.

**Blocking HoQR at G0:** sign `configs/risk_config.yaml`, and reconcile the Angel One fee
schedule against a real contract note (promotion is blocked in code until then).

**Blocking a real G1:** a paid history source. Yahoo yields ~826 labelled rows per symbol
after warmup and horizon trimming — two orders of magnitude short of a trainable dataset,
and it is spot rather than futures with no real volume.

---

## Table of contents

1. [Vision — what we actually want to achieve](#1-vision--what-we-actually-want-to-achieve)
2. [What success looks like](#2-what-success-looks-like)
3. [Markets to target](#3-markets-to-target)
4. [Strategies — which, where, how](#4-strategies--which-where-how)
5. [Model training vs LLM APIs](#5-model-training-vs-llm-apis)
6. [Tech stack analysis](#6-tech-stack-analysis)
7. [Multi-agent architecture (agent army)](#7-multi-agent-architecture-agent-army)
8. [Build timeline](#8-build-timeline)
9. [Capital tiers & expected returns](#9-capital-tiers--expected-returns)
10. [Risk framework + Loss Prediction](#10-risk-framework--loss-prediction)
11. [Paper trading first protocol](#11-paper-trading-first-protocol)
12. [How the team will build with AI](#12-how-the-team-will-build-with-ai)
13. [Difficulties & mitigations](#13-difficulties--mitigations)
14. [Compliance & disclaimer](#14-compliance--disclaimer)

---

## 1. Vision — what we actually want to achieve

Project Alpha exists to build a **production-grade AI-driven systematic trading desk for the Indian stock market** — operated by a human Head of Quant Research and a full engineering team, with a **Manager Agent** coordinating specialist agents.

### Core product

- **Mid-frequency systematic book**: liquid NSE equities + index & stock futures & options (F&O).
- **Primary optimize**: risk-adjusted performance (net Sharpe / Sortino / Calmar after costs) under **hard risk ceilings** and a mandatory **Loss Prediction** checkpoint.
- **Returns are outcomes**, not directives. We do not chase a monthly floor. We do not scale risk because a dashboard “owes” us performance.
- **Hate losses**: prefer **skip / reduce / flatten to flat or break-even** over hopeful hold. Catch-up leverage after a loss is forbidden in code.

### Architectural thesis

Build a mini multi-strategy **research and ops factory** where:

1. **Classical quant models** own signal math and loss prediction (offline-trained, serialized, gated).
2. **Multi-agent AI** accelerates research throughput, engineering, documentation, and intelligence extraction — under a **single Manager** so work does not fork into conflicting actions.
3. A **deterministic risk kernel** has absolute veto — **no LLM may override, relax, or reinterpret risk limits**.
4. **Head of Quant Research** (human) owns policy, capital, and promote/kill decisions; Cursor models assist the team at build-time and can advise at review-time — they never sit inside the risk kernel.

### One-line thesis

> Engineers + Cursor build the factory; offline quant models produce alpha and loss forecasts; the Manager Agent orchestrates specialists; risk code decides what is allowed to trade; live capital only follows edges that survive costs, capacity, paper gates, and loss checks at *our* size.

### What we deliberately do not try to be

We do not try to win on sub-millisecond HFT, triangular FX, cross-exchange crypto races, or Year-1 options market-making. Our edge, if any, is **process, research throughput, cost honesty, hierarchical control, and operational reliability**.

---

## 2. What success looks like

Success is defined by **systems quality and survivability**, not by a promised return path.

### Operational success (must-haves)

| Capability | Meaning |
| --- | --- |
| **Automated pipeline** | Data → features → signals → portfolio proposal → **loss check** → risk check → execution → attribution |
| **Hierarchy integrity** | Specialists report only to Manager; Manager reports to HoQR; no sideways production orders |
| **Paper → live gates** | Written, testable promotion criteria; live capital only after paper passes |
| **Audit logs** | Every signal, order, risk decision, loss advisory, and override attempt is durable |
| **Kill switches** | Manual flatten works; automated halt on drawdown / daily loss / feed loss works |
| **Go / no-go clarity** | After a meaningful sample: scale, maintain, pause research, or shut down |

### Risk & loss success

- Drawdowns stay **within policy** (or trigger halt as designed — that is still success).
- Zero uncaught risk breaches in paper and early live.
- Loss Prediction Agent blocks or cuts size when predicted adverse path is unacceptable.
- No return-target-based leverage catch-up after a bad week.
- Flat / break-even days after blocked trades count as **good process**, not failure.

### Research success

- At least one strategy with honest cost-aware walk-forward evidence, capacity curve, and decay monitoring.
- A trial registry that records **rejected** ideas (rejection is a health metric).
- Live / paper congruence measured (implementation shortfall, missed signals, slippage vs model).

### Professional failure still counts as success

Stopping before ruin when evidence says there is no deployable edge is a successful outcome. Continuing with leverage because “we need to make it back” is failure.

---

## 3. Markets to target

### Geography rule: India only (locked)

**NSE liquid equities + F&O (index and stock).** Do not run India + US + crypto Day-1. Ops and compliance complexity compounds faster than diversification benefit until the India desk is boring.

| Sleeve | Role | Universe | Timing |
| --- | --- | --- | --- |
| **A — Core** | Primary PnL and learning surface | NSE equity + index F&O (Nifty / BankNifty / FinNifty) + liquid stock F&O | Build first; live first |
| **B — Intelligence** | Inputs to Sleeve A | News / filings / NSE calendar / corporate events | Parallel as **features**, never discretionary LLM trading |
| **C — Later (explicitly frozen)** | Optional diversifier | US / crypto | Only after Sleeve A is live-stable for a meaningful period **and** HoQR unlocks |

### In scope (Year-1)

- Index futures & options (Nifty, BankNifty, FinNifty as liquidity allows)
- Liquid stock futures & options in the F&O universe
- Cash equity where needed for factors / hedges
- India session clocks, brokerage, STT, expiry / rollover discipline
- SEBI / broker algo reality: static IP whitelist, rate limits, audit trail, India hosting preference

### Explicitly deprioritize (Year-1 and likely longer)

- Parallel US live book
- Crypto funding / basis as identity of the desk
- HFT / latency arbitrage / co-lo
- Options market-making (inventory + greeks + adverse selection = separate company)
- Illiquid smallcaps as the main book
- “AI predicts the next tick” end-to-end LLM trading

### Decision matrix

| Market | Priority | Notes |
| --- | --- | --- |
| NSE equity + index & stock F&O | **Locked highest** | Only live geography Y1 |
| US equities/futures | Frozen | Unlock only by HoQR written decision |
| BTC/ETH funding/basis | Frozen | Same |
| Stock options MM | Later | Not Year-1 |
| Cross-exchange crypto HFT / FX triangular | Avoid | Institutionally owned |

---

## 4. Strategies — which, where, how

### Sleeve A — core systematic (highest probability)

Start with **one or two** candidate edges, not twenty. Horizon: roughly **intraday to multi-day** (mid-frequency), not sub-second.

| Candidate | Where | How (sketch) |
| --- | --- | --- |
| **Cross-sectional short-horizon factors** | Liquid NSE equities | Value / quality / momentum / short-term reversal with beta hedge via index F&O |
| **Index F&O regime / mean-reversion** | Nifty / BankNifty (+ FinNifty if liquid) | Volatility / trend regime filters; mean-reversion only when regime allows; strict ₹ loss caps |
| **Careful event overlays** | Earnings / calendar / known corporate events | Binary or sparse features as **overlays** — not standalone discretionary LLM bets; extreme lookahead hygiene |
| **Defined-risk options structures (later within Y1 if ready)** | Liquid index options | Only after futures/equity path is stable; no naked MM inventory |

Avoid Year-1: options MM, microstructure HFT, illiquid names as alpha core.

### How we feed strategies (research factory)

1. **Strategy Research Agent** files a hypothesis into the **trial registry** (Manager accepts/rejects intake).
2. **Feature Engineering Agent** builds point-in-time features (corp actions / F&O continuous handled carefully).
3. **Model Training Agent** trains **offline**, serializes artifact (model + schema + metadata + hash).
4. **Backtest / Validation Agent** runs cost-aware walk-forward / purged CV; logs promote/reject.
5. **Paper Gate Agent** runs the **same code path** as live with paper flags.
6. **HoQR** signs written promote / kill / extend.
7. Live path: Portfolio → **Loss Prediction** → **Risk Kernel** → Manager commit → Execution.

### How models are trained

See [Section 5](#5-model-training-vs-llm-apis). Short version: **offline classical ML / statistical models for trading signals and loss prediction**; **LLM APIs for engineering and intelligence**, never for live sizing or risk veto.

### Objective function (non-negotiable)

- **Maximize**: net risk-adjusted performance after fees, slippage, impact, STT / taxes.
- **Subject to**: max drawdown, max daily loss, margin-risk ₹ ceilings, concentration limits, **Loss Prediction soft policy**.
- Period returns appear on dashboards as **KPIs only** — never as inputs that automatically increase risk.

---

## 5. Model training vs LLM APIs

This is a deliberate **hybrid**. Confusing the two layers is how desks blow up or build theater.

### Recommendation (best + cost-effective)

| Concern | Winner | Why |
| --- | --- | --- |
| Alpha / signals | **Offline batch training** | Reproducible artifacts, audit, cheap inference |
| Loss prediction | **Offline batch training** | Same — versioned ES / adverse-path models |
| Software build | **Online LLM APIs** (Cursor + Claude/OpenAI etc.) | Highest ROI for eng velocity |
| News / filings extract | **Online LLM + parsers + verification** | Only with source spans |
| Risk / sizing caps / halt | **Deterministic code** | Never an API model |
| Continuous online DL on ticks | **Avoid Y1** | Costly, drift-prone, hard to audit |

> **Quant models offline for alpha and loss forecasts; LLMs via API for engineering and intelligence; risk kernel never LLM.**

### Offline / local / batch model training — for actual trading signals & loss models

| Layer | Role | Examples |
| --- | --- | --- |
| **Classical ML** | Nonlinear tabular alpha on engineered features | LightGBM, XGBoost |
| **Linear / factor models** | Interpretable cross-sectional signals | Ridge / elastic net, factor portfolios |
| **Statistical models** | Regime, mean-reversion, volatility | AR/GARCH-family, simple state filters |
| **Loss / risk forecasting** | P(hit stop), expected shortfall, adverse path | Gradient boosting / calibrated classifiers on path features |

**Workflow**

1. Build historical feature store (point-in-time correct; corporate actions / continuous F&O handled carefully).
2. Train **offline** on historical windows (batch jobs).
3. Validate with walk-forward / purged cross-validation; measure **after costs**.
4. Serialize artifacts (model + feature schema + training metadata + hash).
5. Promote only through gates (paper → limited live) with Manager + HoQR.
6. **Re-train on a schedule** (e.g. weekly or monthly walk-forward refresh) — not continuous online “vibe updates.”

**Hard rules**

- No continuous online LLM fine-tuning driving live orders.
- No chat model “decides size” in production.
- Feature freeze before OOS evaluation; trial registry for multiplicity.

### Claude / OpenAI (and similar) APIs — for engineering & intelligence

| Allowed uses | Forbidden uses |
| --- | --- |
| Research assistants (literature, hypothesis framing) | Live position sizing |
| Code generation, refactoring, test scaffolding | Risk veto / limit relaxation |
| Docs, runbooks, design reviews | Unverified news → trade without parsers + checks |
| Feature ideation (human + backtest must confirm) | Autonomous “agent places order because the story felt good” |
| News / filings extraction **with source spans and verification** | Overriding broker / risk kernel decisions |

**Cost control**

- Budget tokens toward highest ROI: code review, test generation, data QA, docs.
- Cap monthly API spend; alert on spend spikes.
- Optional later: local open-weight models for privacy-sensitive docs / code assist — still not for risk kernel.

---

## 6. Tech stack analysis

Goal: **scalable enough for a full eng team**, **fast enough for mid-frequency India F&O**, **correct under SEBI/broker API constraints** — not a co-lo fantasy on Day-1.

### Recommended stack (clear choice)

| Layer | Choice | Why |
| --- | --- | --- |
| **Primary language** | **Python 3.11+** | Research, ML, agents, orchestration, backtests, broker SDKs — best team + Cursor velocity |
| **Optional hot path** | **Rust** (later) | Order gateways / state machine **when profiling proves need** — not Day-1 |
| **Data on disk** | **Parquet** | Columnar, cheap, reproducible research artifacts |
| **Analytics DB** | **DuckDB** (research) | Local/CI speed for walk-forward |
| **Ops DB** | **Postgres** | Trials, audit, configs, multi-engineer durability |
| **Live state** | **Redis** | Positions, heartbeats, rate limits, kill flags |
| **Research libs** | pandas/polars, numpy, scipy, statsmodels, scikit-learn, LightGBM/XGBoost | Standard quant toolkit |
| **Backtest** | Owned harness with **first-class India cost model** (brokerage, STT, slippage, lots) | You must own costs |
| **Orchestration** | Prefect (prefer) or Airflow | Retrain jobs, data pipelines, paper loops |
| **Containers** | Docker | Reproducible paper/live environments |
| **CI** | pytest + golden files + **risk unit tests** | Risk is the product |
| **Brokers (India)** | **One primary** among Zerodha / Dhan / Fyers | Mature APIs; dual-broker only after one adapter is solid |
| **Hosting** | India region (e.g. AWS `ap-south-1` / India VPS) + **static IP** | Broker whitelist / SEBI-oriented ops |
| **Agents** | Thin LangGraph / job-queue + prompts | Manager routes; specialists are roles |
| **Monitoring** | Structured logs + Telegram/email; Prometheus/Grafana as team matures | Alerts beat dashboards early |
| **Secrets** | Env / vault; never commit keys | Non-negotiable |

### Broker notes (pick one primary)

| Broker | Strength | Watch |
| --- | --- | --- |
| **Zerodha Kite Connect** | Docs, community, maturity | Paid API; lower OPS — fine for mid-freq if rate-limited |
| **DhanHQ** | Higher OPS, options baskets | Younger ecosystem — still viable |
| **Fyers API v3** | Competitive API / history options | Confirm compliance path with broker |

Implement a **hard rate limiter** (stay comfortably under broker/exchange OPS expectations). Mid-frequency does not need to hammer APIs.

### Python-first + Rust-when-needed

| Criterion | Python-first | Add Rust later |
| --- | --- | --- |
| Team + Cursor agents | Excellent ecosystem | Use when profiling proves bottleneck |
| Research → prod | Short path | Overkill Day-1 |
| Mid-frequency horizon | Seconds–minutes latency usually enough | Sub-ms not our game Y1 |
| Maintenance | One language until it hurts | Second language doubles ops surface |

**Decision:** ship the desk in **Python**. Introduce **Rust only** for measured hot paths after paper/live proves the Python path is the bottleneck.

### Non-functional requirements

- Idempotent jobs; replayable pipelines.
- Clock / timezone discipline (IST sessions, F&O expiry).
- Heartbeats for data feed and broker session; flatten-on-disconnect policy.
- Backups of configs, model artifacts, and audit logs.
- Algo tags / audit fields as required by broker compliance process.

---

## 7. Multi-agent architecture (agent army)

> **Full LLM online/offline map, Cursor Pro+ policy, and exact model/API picks:** see [`AGENT_ARMY.txt`](AGENT_ARMY.txt).

### Hierarchy (non-negotiable)

```
Head of Quant Research (human — policy, capital, promote/kill)
        │
        ▼
Desk Manager Agent (single orchestrator — reports upward only)
        │
        ├── Market Intelligence pod
        │     ├── Market Data Agent
        │     ├── News / Events Agent
        │     └── Regime Detection Agent
        │
        ├── Research pod
        │     ├── Strategy Research Agent
        │     ├── Feature Engineering Agent
        │     ├── Model Training Agent
        │     └── Backtest / Validation Agent
        │
        ├── Portfolio & Loss & Risk pod
        │     ├── Portfolio Construction Agent   ← CODE only (NO LLM at runtime)
        │     ├── Loss Prediction Agent          ← offline QUANT (NOT LLM)
        │     └── RISK KERNEL (deterministic code — NOT an LLM)
        │
        ├── Execution pod
        │     ├── Execution Agent
        │     └── TCA / Slippage Agent
        │
        └── Ops & Governance pod
              ├── Attribution Agent
              ├── Monitoring / Alert Agent
              ├── Compliance / Audit Agent
              └── Paper Gate Agent
```

**Rules that prevent flaws**

1. Specialists **do not** place broker orders or talk sideways into production.
2. Only **Execution Agent** may hit the broker — and only after Manager commit.
3. Every size proposal must pass **Loss Prediction** then **Risk Kernel**.
4. **Risk Kernel veto is absolute** — Manager cannot override; HoQR changes policy only via config + review, never mid-tick chat.
5. Build-time Cursor agents write the system; they are **not** inside the live risk kernel.
6. **Portfolio Construction has no runtime LLM** — optimizer code only. Cursor may *write* that code; it must not *decide* weights live.
7. **Offline LLM weights (Ollama/Llama/etc.) are not required Year-1** — alpha/loss use LightGBM/sklearn, not language models.

### LLM usage at a glance

| Layer | What | When |
| --- | --- | --- |
| **Build-time online LLM** | Cursor Pro+ (Composer / Auto / Claude–GPT inside Cursor) | Now — primary eng accelerator |
| **Run-time online LLM** | News/Events extract only (+ optional digests) | Later — after paper path works |
| **Offline QUANT (not LLM)** | LightGBM / Ridge / calibrated loss classifier | Now — signals + loss scores |
| **Offline local LLM** | Ollama / Llama / Mistral | **Skip Year-1** |
| **Separate OpenAI/Anthropic API key** | Batch news jobs outside Cursor | **Not needed Day-1** if Cursor Pro+ covers build |

### Full roster

| Agent / component | Expert in | Runtime engine | LLM runtime? | Reports to | Must not |
| --- | --- | --- | --- | --- | --- |
| **Head of Quant Research** | Policy, capital, promote/kill | Human | No | — | Micromanage every tick |
| **Desk Manager Agent** | Orchestration, priorities, escalation | Rules first (optional thin LLM router later) | Optional thin only | HoQR | Bypass risk kernel / size positions |
| Market Data Agent | Feeds, corp actions, F&O continuous | Code + broker/data API | No | Manager | Place orders |
| News / Events Agent | Calendar, filings, verified extract | Online LLM + parsers (Phase later) | Yes (extract only) | Manager | Discretionary LLM trades |
| Regime Detection Agent | Vol / trend / liquidity regimes | Rules / GARCH / later HMM | No | Manager | Size positions alone |
| Strategy Research Agent | Hypotheses, literature, candidates | Human + Cursor assist | Build / draft only | Manager | Self-promote to live |
| Feature Engineering Agent | PIT features, schemas, QA | Code | No | Manager | Leak future data |
| Model Training Agent | Offline train, serialize, version | Offline QUANT (LightGBM etc.) | No | Manager | Online vibe-retrain into live |
| Backtest / Validation Agent | Walk-forward, costs, capacity | Code | No | Manager | Hide failed trials |
| Portfolio Construction Agent | Weights under budgets | **Deterministic optimizer code** | **No** | Manager | Skip loss + risk; use LLM for weights |
| **Loss Prediction Agent** | Adverse path, ES, skip/cut/flatten | Offline QUANT classifier/GBDT | No | Manager | Override hard risk code |
| **Risk Kernel (code)** | Hard ceilings, kill, veto | Deterministic code | No | Manager (veto power) | Be an LLM |
| Execution Agent | Broker API, child orders | Code + broker SDK | No | Manager | Change thesis mid-flight |
| TCA / Slippage Agent | Shortfall, cost feedback | Code / stats | No | Manager | Relax limits to look good |
| Attribution Agent | PnL explain, decay alerts | Code (+ optional LLM digest) | Optional narrative | Manager | Optimize narrative |
| Monitoring / Alert Agent | Heartbeats, alerts, uptime | Code | No | Manager | Silent-fail reconnects |
| Compliance / Audit Agent | Logs, SEBI/broker tags, IP, OPS | Code | No | Manager | Trade without audit trail |
| Paper Gate Agent | Promotion criteria, congruence | Code metrics + human | Optional memo | Manager | Fast-track to live |

### Order path (construction against errors)

```
Promoted QUANT signal artifact
  → Portfolio Construction [CODE] (draft size)
  → Loss Prediction Agent [QUANT] (skip / cut / flatten advise)
  → Risk Kernel [CODE] (hard veto)
  → Desk Manager commit
  → Execution Agent [CODE]
  → TCA + Attribution + Loss feedback → trial registry
```

### Build-time vs run-time AI (critical distinction)

| Mode | Purpose | Tools |
| --- | --- | --- |
| **Build-time** | Design, implement, test, document the software | **Cursor Pro+** (primary) + optional later provider APIs |
| **Run-time** | Orchestrate research/ops loops inside the desk | Manager + specialists + **offline QUANT** + **deterministic risk** |

Build-time agents write the risk kernel; they do **not** sit inside the risk kernel at trade time. **You do not need a separate OpenAI/Anthropic key or a local LLM download to start Phase 0–2.**

---

## 8. Build timeline

### Ambition vs quality

**Ambition:** with a full team + Cursor, ship **foundation + research harness + risk + loss gate + paper path** on a compressed schedule via parallel pods.

**Quality bar:** do **not** compromise correctness for the calendar. If data integrity, cost model, risk tests, or loss-model validation are not ready — **extend**. There is no prize for early live capital with broken gates.

Paper trading is **mandatory** before real capital. Always.

### Phases

| Phase | Focus | Exit gate |
| --- | --- | --- |
| **0 — Foundation** | Accounts, data pipeline, Parquet/DuckDB/Postgres, risk kernel v0, loss stub, logging, kill switch, Docker, India static IP plan | Paper order path works; risk unit tests green |
| **1 — Research / backtest** | India cost model, walk-forward harness, first strategy candidates, trial registry, loss model v0 | Cost sensitivity done; ≥1 candidate with honest OOS or documented reject |
| **2 — Paper trading** | Live data → signals → loss → risk → paper orders; shortfall & uptime | Shortfall within band; **zero** uncaught risk breaches |
| **3 — Limited live** | Tiny real capital under full risk + loss policy | Congruence with paper; no policy breaches |
| **4 — Scale** | Scale Sleeve A along capacity curve | Written go/no-go; capacity & decay monitoring live |

### Pod-parallel sprint (team)

| Pod | Early deliverables |
| --- | --- |
| Data | OHLCV + corp actions + F&O continuous; Parquet layout; golden tests |
| Risk & Loss | Config ceilings, unit tests, kill switch, loss checkpoint interface |
| Research | Backtest harness, cost model, trial registry |
| Execution | One broker adapter (paper + live flag), rate limiter, TCA logs |
| Agents | Manager router + thin specialist role scaffolding |
| Ops | Docker, CI, alerts, audit schema, compliance checklist |

If quality gates fail → **extend**. Do not “go live to learn.” Learn on paper.

### Kill criteria (professional)

If after disciplined work (paper + limited live sample) **no** strategy passes promotion gates, **stop trading capital**. Restart research or shut down. Do not add leverage.

---

## 9. Capital tiers & expected returns

### Important framing (read before the tables)

- Figures below are **planning priors** for risk budgeting and expectations-setting — **not forecasts**, not promises, and not marketing claims.
- Many systems **lose money**. Edge after costs is rare. Small capital is especially hostile because **fixed costs and lot sizes eat edge**.
- Bad years can be **negative**. Favorable years are not a floor for the next year.
- Separate **(A) build/tooling investment**, **(B) monthly operating cost**, and **(C) trading capital**. Do not confuse them.

### Overall annualized return prior (if a real edge exists after costs)

| Scenario | Rough annualized net (planning prior) |
| --- | --- |
| Favorable year | Mid-single digit to low-double digit (illustrative ~**8–20%** on risk capital in good years) |
| Base / mixed year | Lower single digits to low teens, or flat |
| Bad year | **Negative** (drawdowns within policy still expected; halt if ceilings hit) |

Do **not** convert annual priors into hard monthly floors.

### Trading capital tiers (INR) — what is realistic

| Capital | Role | Realistic use | Constraints |
| --- | --- | --- | --- |
| **₹25,000 – ₹1,50,000** | Learning / micro | Paper primarily; tiny live only if gates pass | Lot sizes (esp. F&O) dominate |
| **₹5,00,000 (5L)** | First meaningful limited live (post-paper) | Liquid equity + careful index F&O with ₹ loss budgets | Still sensitive to slippage |
| **₹10,00,000 (10L)** | Early operating book | Process edge starts to matter more than lot-size noise | Do not overtrade |
| **₹25L – ₹50L** | Stable small prop book | Multiple names / hedges more workable | Correlation risk remains |
| **₹1Cr+** | Scale along capacity | Absolute INR outcomes matter | Capacity often **hurts** Sharpe |

### Build / tooling investment (team, separate from trading capital)

| Tier | Approx Year-1 tooling | What you get |
| --- | --- | --- |
| **Minimum** | ~₹50K–₹1.5L | Broker APIs, basic data, small VM, LLM credits |
| **Stable (recommended)** | ~₹3L–₹8L | Cleaner historical data, India VPS + static IP, better LLM budget, logging/backup |
| **Heavy tooling** | ~₹15L+ | Premium data, redundant feeds, legal/compliance consult — when capital justifies it |

### Monthly operating cost (steady state, stable tier)

| Item | Rough monthly band |
| --- | --- |
| Cloud / VPS (India) | Low tens of USD (or INR equivalent) upward |
| Market data | ₹0 to meaningful subscription |
| LLM / AI APIs (build) | Controlled budget (alert on spikes) |
| Broker / platform | Often low; watch turnover costs in PnL |
| Monitoring / misc | Small |
| **Total OpEx (order of magnitude)** | Roughly **₹7K–₹50K+ / month** depending on data + LLM appetite |

### Capital policy reminders

- Do **not** put all personal / firm net worth into the book.
- Cap trading capital at money you can lose a large fraction of (within policy DD) without existential damage.
- F&O is margin-efficient — easier to blow up. Express limits in **rupees of loss**, not “number of lots feels small.”

---

## 10. Risk framework + Loss Prediction

### Principles

1. **Hard ceilings** in config + code — not soft guidelines in a chat.
2. **Loss Prediction soft gate** before hard risk — mandatory on every size proposal.
3. **No return-target scaling** — never increase risk because performance “should” catch up.
4. **Kill switches** — daily loss, drawdown from peak, data-feed loss, broker disconnect.
5. **Risk kernel is deterministic** — agents propose; risk code disposes.
6. **Pre-trade enforcement** — rejected orders are success if limits say so.
7. **Prefer flat / break-even** over hopeful hold when Loss Agent flags path risk.

### Example starting policy (tune to capital; copy into config)

| Parameter | Example starting value | Notes |
| --- | --- | --- |
| `max_portfolio_drawdown_halt` | ~12% from peak of strategy equity | Halt / flatten per runbook |
| `max_daily_loss` | ~2% | Hard stop for the day |
| `max_gross_leverage` | ~2.0 equity book | For F&O prefer **margin-risk ₹ limits** |
| `max_net_beta` | ~0.3 if market-neutral intent | If applicable |
| `max_single_name` | ~5% | Concentration |
| `max_sector` | ~25% | Concentration |
| `loss_pred_block_threshold` | Tunable P(hit daily stop) / ES | Soft gate → skip or cut |
| `no_return_target_scaling` | `true` | Non-negotiable |
| `flatten_on_data_feed_loss` | `true` | Ops risk |

### Loss Prediction Agent — contract

| Must do | Must not |
| --- | --- |
| Score adverse path / ES / P(hit stop) on draft orders | Replace Risk Kernel |
| Recommend skip / reduce / flatten / hold | Be ignored when above threshold |
| Log advisories into audit + registry | “Hope” overrides |
| Retrain offline on schedule with validation | Continuous unverified online updates |

### Governance cadence

- **Daily**: Manager digest to HoQR (breaches attempted, loss blocks, uptime).
- **Weekly**: factor / cost / shortfall / loss-model false-alarm review.
- **Monthly**: written strategy memo (what worked, what decayed, what we refuse to do).
- **Anytime**: kill switch drills.

---

## 11. Paper trading first protocol

Paper trading is not optional theater. It is the **gate** between research fantasy and capital at risk.

### Mandatory sequence

1. Backtest with **conservative** India costs passes promotion heuristics.
2. Paper environment uses **same** code path as intended live (config flags, not a second brain).
3. Loss Prediction + Risk Kernel both active in paper.
4. Run paper for a **pre-agreed** minimum period / sample (do not stop early because PnL looks good).
5. Measure:
   - Implementation shortfall vs model
   - Missed / duplicate signals
   - Uptime and reconnect behavior
   - Risk breach attempts (should be caught)
   - Loss-agent block rate and subsequent outcomes
6. Written go/no-go for limited live (Manager summary → HoQR decision).
7. Limited live with **tiny** capital and full policy.
8. Scale only along capacity curve.

### Example paper pass bars (tune; do not ritualize)

| Metric | Example bar |
| --- | --- |
| Implementation shortfall vs model | Within ~20–30% relative band |
| Missed signals | Low single-digit % of intended |
| Uncaught risk breaches | **Zero** |
| Audit completeness | 100% of orders/signals/loss advisories logged |

Fail any critical bar → extend paper or return to research. **Never** skip to live to “feel progress.”

---

## 12. How the team will build with AI

### Team shape

- **Head of Quant Research** owns architecture constitution, risk/loss policy, go/no-go, and production responsibility.
- **Software engineers** own data, execution, infra, reliability, CI.
- **AI / GenAI engineers** own agent orchestration, prompts/tools contracts, evaluation harnesses, intelligence pipelines.
- **Cursor / coding agents (vibe coding)** accelerate implementation under human review — they do not own promote-to-live.

### Quality bars (non-negotiable)

| Bar | Why |
| --- | --- |
| Automated tests (incl. **risk + loss-gate unit tests**) | Risk is the product |
| Cost model in every backtest | Edge without costs is fiction |
| No lookahead / point-in-time correctness | Silent death of desks |
| Trial registry & promotion gates | AI can generate infinite hypotheses |
| Hierarchy contracts (Manager-only escalation) | Prevent conflicting agent actions |
| Audit logs & secrets hygiene | Ops and compliance |
| Code review (human + AI) | Agents ship bugs at high speed too |

### Build practices

- Thin vertical slices: paper order path before fancy UI.
- Prefer boring, readable Python over clever frameworks.
- Generate tests with agents; **verify** tests assert real invariants.
- Document runbooks as you go (flatten, halt, reconnect).
- Time-box experiments; kill strategies that fail gates without drama.
- Parallelize by pod; integrate through Manager contracts and shared schemas.

### What “AI-assisted end-to-end” means here

Cursor models and coding agents scaffold modules, write adapters, propose features, draft docs, and refactor. Humans set the risk/loss constitution, approve promotions, and refuse to ship when gates fail.

---

## 13. Difficulties & mitigations

| Difficulty | Why it hurts | Mitigation |
| --- | --- | --- |
| **Overfitting** | AI generates infinite hypotheses | Trial registry; deflated Sharpe; freeze features before OOS |
| **Cost blindness** | Backtests look rich | Conservative slippage; brokerage/STT; round-turn costs |
| **Regime change** | 2020 ≠ 2022 ≠ 2025 | Regime agent; reduce size when vol/liquidity shifts |
| **Broker/API fragility** | Silent failures | Heartbeats; max order age; flatten-on-disconnect |
| **Agent sprawl / conflicting actions** | Many agents ≠ many edges | Single Manager; no sideways orders; risk veto |
| **Return chasing** | Leverage after bad month | Code forbids target-based scaling; Loss Agent prefers flat |
| **Small capital economics** | Lots & costs dominate | Paper first; scale capital before complexity |
| **Dirty F&O continuous / corp actions** | Wrong history → fake alpha | Clean data + golden-file tests |
| **SEBI / broker algo compliance** | Static IP, OPS, audit | India hosting; rate limiter; Compliance Agent |
| **Emotional override** | “Just this once” | Written runbook + cool-off; kill switch drills |
| **Compliance creep** | Friends’ money / signal groups | Year-1 prop only |
| **Timeline pressure** | Ship broken live | Extend; quality over calendar ambition |

---

## 14. Compliance & disclaimer

### Year-1 business shape

- Trade **proprietary capital only**.
- Do **not** take friends’ money, run Telegram signal products, or brand this as a fund.
- Keep full audit logs (signals, orders, risk decisions, loss advisories).
- Treat tax as first-class (India F&O tax treatment — use a qualified accountant).
- Follow broker/exchange algo requirements applicable to your account (static IP, rate limits, strategy disclosure / Algo ID process as required).
- If external capital is ever considered later: obtain proper legal / regulatory counsel before any marketing or solicitation.

### Disclaimer

**This README is not financial, investment, legal, or tax advice.** Trading equities, futures, and options involves **substantial risk of loss**, including loss of principal. Planning priors and capital tables are **not guarantees** of performance. Past research results, paper results, and hypothetical returns do not predict future results. Many systematic strategies fail after costs. You are solely responsible for your capital, compliance, and decisions.

---

## Quick reference — strategic defaults

| Parameter | Default for Project Alpha |
| --- | --- |
| Team | SW + AI engineers + Cursor agents; HoQR owns promote/kill |
| Runtime hierarchy | Specialists → Desk Manager → HoQR |
| Markets | **NSE equity + F&O only** (Y1 lock) |
| Strategies | Sleeve A first (factors + index F&O regime); defined-risk options later if ready |
| Alpha + loss models | Offline LightGBM/factors/stats; scheduled retrain |
| LLMs | Engineering + intelligence via API; never live risk/sizing |
| Stack | Python-first; Parquet + DuckDB + Postgres + Redis; Docker; Prefect; India host + static IP |
| Loss control | Loss Prediction Agent + deterministic Risk Kernel |
| Timeline | Parallel pods; **extend for quality**; paper before live |
| Returns | Planning prior only; bad years negative; flat after blocks = good process |
| Risk | Hard ceilings; kill switches; no return-target scaling |

---

*End of Project Alpha README.*
