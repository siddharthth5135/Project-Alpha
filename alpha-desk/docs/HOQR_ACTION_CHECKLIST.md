# What you need to do (developer checklist)

> **Source of truth for 100% completion:** [`PROJECT_100_ROADMAP.md`](PROJECT_100_ROADMAP.md) / [`PROJECT_100_ROADMAP.txt`](PROJECT_100_ROADMAP.txt)  
> **Strategy / market logic for analysts:** [`PROJECT_100_Strategies.txt`](PROJECT_100_Strategies.txt)  
> Follow the roadmap for phases/gates; give Strategies.txt to business/strategy reviewers. This checklist is a short pointer only.

| Step | Status |
| --- | --- |
| 1 Sign risk config | **DONE** |
| 2 Contract note | Skipped (OK for now) |
| 3 TrueData **Market Data API** | **Trial220 active until 2026-09-13** — smoke/train working |
| 4 `.env` | Trial220 configured locally (never commit) |
| 5 Roadmap | **ACTIVE** — `docs/PROJECT_100_ROADMAP.md` |

## Phase status

| Phase | Status |
| --- | --- |
| P1.2 models | First **TrueData** train done — `promote_eligible=false` (expected on short trial) |
| P1.3 validation | Scaffolding done — purged WF + reports + reject/promote |
| P1.4 strategy + paper path | Scaffolding done — rules → OrderPath; run `kill_switch_drill.py` before G4 |
| Paper only | Enforced |
| Fully GO / promote | **Blocked** until paid history depth + gates in roadmap (G2→G-LIVE) |

## TrueData trial commands (no live orders)

```powershell
.\.venv\Scripts\python.exe scripts\td_preflight.py
.\.venv\Scripts\python.exe scripts\td_smoke.py
.\.venv\Scripts\python.exe scripts\td_probe.py
.\.venv\Scripts\python.exe scripts\td_backfill.py --scope futures --interval 15m --days 15
.\.venv\Scripts\python.exe scripts\td_record_live.py --minutes 15
.\.venv\Scripts\python.exe scripts\train_from_store.py --then-train
.\.venv\Scripts\python.exe scripts\td_trial_report.py
```

## P1.4 commands (paper only)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_rules.py tests/test_paper_engine.py tests/test_order_path.py -q
.\.venv\Scripts\python.exe scripts\run_paper_path.py --source synthetic --symbol NIFTY --score 0.5
.\.venv\Scripts\python.exe scripts\kill_switch_drill.py
.\.venv\Scripts\python.exe scripts\verify_g4.py
```

Approve `alpha_score_threshold` in `configs/strategy_spec_001.yaml` before treating paper as real.
