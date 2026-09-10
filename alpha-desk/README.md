# alpha-desk

Phase 1 implementation of **Project Alpha** — an ML-driven, strategy-based trading desk
for NSE equities and F&O. Mid-frequency, paper-first, deterministic risk.

Governing documents live one level up: [`../README.md`](../README.md),
[`../PHASE1_ML_ARCHITECTURE.txt`](../PHASE1_ML_ARCHITECTURE.txt),
[`../PHASE1_EXECUTION_PLAYBOOK.txt`](../PHASE1_EXECUTION_PLAYBOOK.txt),
[`../STRATEGIES.txt`](../STRATEGIES.txt).

**Live trading is off.** It cannot be enabled without `ALPHA_LIVE_GO_SIGNATURE` in the
environment and a signed risk config, and the Angel One adapter refuses to construct
without both.

---

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env          # leave broker keys blank for now

.\.venv\Scripts\python.exe -m pytest -q                        # 183 tests
.\.venv\Scripts\python.exe scripts\verify_foundation.py        # Gate G0 checklist
.\.venv\Scripts\python.exe scripts\fetch_data.py --source yahoo --days 55
.\.venv\Scripts\python.exe scripts\build_dataset.py --source yahoo
.\.venv\Scripts\python.exe scripts\verify_g1.py --source yahoo  # Gate G1 checklist
```

## The order path

Every order takes exactly one route, and each stage can refuse:

```
Strategy intent
   -> Portfolio sizer      [code]  fixed rupee risk -> whole lots, trimmed to fit caps
   -> Loss gate            [ML]    skip / cut / hold, on expected value after costs
   -> Risk Kernel          [code]  28 hard checks, absolute veto
   -> Execution            [code]  paper today, Angel One behind a signed flag
```

Nothing else may call the broker. The kernel rejects any draft that arrives without a
loss advisory, so the gate cannot be bypassed by calling the kernel directly. Every
stage — including refusals — is written to a hash-chained audit log.

## Layout

```
configs/          risk_config, strategy_spec_001, costs_india, instruments  (YAML, owned by HoQR)
src/alpha_desk/
  config/         loading + fail-closed validation of desk invariants
  core/           value objects shared by every layer
  costs/          India cost model: brokerage, STT, exchange, SEBI, IPFT, stamp, GST, slippage
  data/           bar schema, quality reports, Parquet warehouse, MarketStore, providers
  marketdata/     vendor-neutral port: contracts, calendar, quality, registry
  vendors/        TrueData adapter (REST + websocket); reached only via the registry
  ingest/         backfill, live recorder, trial universe, ML bridge
  features/       lean/v0 feature set, freeze lock, point-in-time attacks
  labels/         forward return + triple-barrier stop/target labels
  datasets/       supervised table assembly, tagged with feature hash + label id
  loss/           loss-prediction interface + P1.0 heuristic stub
  portfolio/      deterministic sizer
  risk/           Risk Kernel, desk state, kill switch
  execution/      broker interface, paper broker, rate limiter, inert Angel One adapter
  pipeline/       the wired order path
  registry/       trial registry (append-only SQLite)
  audit/          hash-chained JSONL audit log
  desk.py         assembly
scripts/          G0/G1/G3/G4 gates, TrueData trial (td_preflight/smoke/probe/backfill/record/report)
tests/            pytest (market-data, adapter, store, ingest, risk, features, paper path)
docs/             DECISIONS_LOCKED.md, TRUEDATA_BACKEND.md, TRUEDATA_TRIAL.md
data/  logs/      gitignored; live on D:
```

## What is deliberately not here yet

Trained models and walk-forward validation are P1.2–P1.3. Strategy rules + paper
path wiring are P1.4 (`scripts/run_paper_path.py`, `scripts/kill_switch_drill.py`).
Live-data paper sessions are P1.5.
Deep learning, reinforcement learning, LLMs anywhere near the trading path, options
market-making and tick/L2 data are out of scope for Phase 1 by decision, not by omission.

## Three things to know before reading the code

**Costs are not a detail.** A one-lot NIFTY round turn costs roughly ₹1,250 — about 30%
of the rupee risk on a 40-point stop. Any edge clears that hurdle first, which is why the
cost model is wired into sizing rather than bolted onto reporting.

**The loss gate is relative, not absolute.** Thresholds scale with each trade's own
break-even probability, because a genuine 1:4 setup is supposed to hit its stop most of
the time. See [`docs/DECISIONS_LOCKED.md`](docs/DECISIONS_LOCKED.md) section 3.

**Leakage tests come with control experiments.** Deliberately leaky features are fed to the
same point-in-time attacks, and Gate G1 fails if they pass. A leakage check that cannot
detect leakage is worse than no check, because it manufactures confidence.

## Disclaimer

Trading F&O involves substantial risk of loss. Nothing here is financial advice, and no
part of this system predicts or promises returns. Correct pipelines prevent losses caused
by software. They do not prevent losses caused by markets.
