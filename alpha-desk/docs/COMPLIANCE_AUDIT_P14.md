# Compliance Audit — alpha-desk (P1.4 re-audit)

**Role:** Strict Compliance Monitoring Agent (independent review — not implementation)  
**Date:** 2026-09-20  
**Prior audit:** 2026-07-26 scored **54 / 100**  
**Subject:** Project Alpha / `alpha-desk` after TrueData trial ingest, Angel MD adapter (gated), ops/console/Telegram, **paper-only live-order lock**  
**Bar:** Institutional production readiness (pass ≥ **95**)  
**Evidence:** `verify_g4.py` PASS · `kill_switch_drill.py` PASS · providers `angelone,truedata,truedata_mock` · TrueData partitions present · `AngelOneBroker` construct refused · `ALPHA_MODE=live` refused · offline TrueData train+paper ran  
**Interactive summary:** Cursor canvas `compliance-audit-p14.canvas.tsx`

---

## Overall Compliance Score

# **68 / 100 — REJECT for production / live capital**

| Lens | Prior | Now | Notes |
| --- | ---: | ---: | --- |
| Architecture | 68 | **78** | Vendor-agnostic MD port + TrueData + Angel adapters |
| Data engineering | 48 | **70** | TrueData warehouse/store real; Angel coded but OFF; depth still shallow for promote |
| Machine learning | 55 | **64** | Real TrueData trains exist; still `promote_eligible=false` |
| Security | 72 | **86** | Live ORDER path hard-disabled; live mode settings refused |
| Risk management | 62 | **66** | Kill drill PASS; equity-from-fills / session flatten actuators still weak |
| Reliability / FT | 32 | **60** | Scheduler, holiday skip, heartbeat, journal, Telegram |
| Software engineering | 70 | **76** | Ops scripts + tests; G0 suite must stay green |
| Observability | 64 | **80** | Console + journal + daily Telegram HTML report |
| Performance / scale | 50 | **52** | Mid-freq paper OK; not load-proven |
| Disaster recovery | 28 | **38** | Friend-PC zip runbook verbal; no broker reconcile yet |

**Paper research (gated):** **CONDITIONAL ACCEPT** — paper-only, TrueData/Angel-gated, HoQR aware of remaining Criticals.  
**Live capital / live orders:** **BLOCKED** (by design this phase).

**Delta vs Jul 2026:** **+14 points**. Still ≪ 95. Not production-ready.

---

## Critical Issues (updated)

| ID | Status | Finding |
| --- | --- | --- |
| **C1** | **PARTIAL** | TrueData ingest/store exists (trial depth). Multi-year promote-grade FUT still missing. Angel MD adapter exists but `ANGELONE_ALLOW_MARKETDATA=false`. |
| **C2** | OPEN | Trading PnL → `RiskState` equity / daily-loss from fills still incomplete. |
| **C3** | OPEN | `force_flatten_at` / feed-loss policies still do not fully actuate flatten loop in ops. |
| **C4** | OPEN | No broker reconciliation / persisted desk state across restart. |
| **C5** | OPEN | `Desk.broker` still publicly reachable (convention, not hard encapsulate). |
| **C6** | OPEN | No managed strategy exit engine (FLAT/ENTER-centric). |
| **C7** | OPEN | Promote evidence still not Fully GO (`promote=False` on latest trains). |
| **C8** | **REMEDIATED (stricter)** | Live Angel **orders** disabled outright; `ALPHA_MODE=live` refused. Re-enable only after paper gates + explicit human rebuild. |

---

## High-Risk Issues

- **H1** Kill engage/clear audit event coverage still incomplete vs ideal (drill proves refuse+flatten).
- **H2** Warehouse monthly rewrite / immutability story incomplete.
- **H3** Quarantine path exists in MarketStore ingest; warehouse path still weaker.
- **H4** Continuous futures: vendor-spliced CONT present in TrueData store; desk-roll Angel path is stand-in only.
- **H5** Train holdout ≠ full purged WF claim for promote.
- **H6** ~~GO signature alone enables live~~ → **closed** by paper-only lock (replaced by total LIVE refuse).
- **H7** Plumbing escape hatches still exist for research — must not be used for promote claims.
- **H8** `OrderPath.flatten` bypasses kernel by design (emergency) — keep rare + audited.
- **H9 (new)** Two writer PCs without sync can corrupt the store — ops must designate one SoR writer.
- **H10 (new)** Telegram/Angel secrets in local `.env` — never zip-share casually.

---

## Medium-Risk Issues

- Cost schedule still not contract-note verified (blocks promote).
- Instrument margins research_snapshot, not SPAN.
- Angel live MD uses quote-poll (not full WS binary) — fine for mid-freq store; document skew risk vs future live marks.
- Holiday Telegram fires at preopen only (good); weekday exchange holidays depend on calendar file currency.
- Train/paper SoR currently TrueData hist; future Angel live marks need Feed Parity Gate before any live orders (when re-added).

---

## Compliance Violations

| Standard | Status |
| --- | --- |
| Clean Architecture / vendor-agnostic MD | Improved — port + adapters present |
| Immutable versioned datasets | Partial |
| Independent risk enforcement | Partial — submit path strong; PnL/session flatten weak |
| Train/serve consistency | At risk if Angel live marks ≠ TrueData train without parity |
| Live capital controls | **Strong this phase** (orders disabled) |
| DR / reconcile | Still weak |
| Audit completeness | Better (ops journal); kill audit gaps remain |

---

## Security Findings

**Strengths:** `.env` gitignored; live mode refused; Angel order broker always raises; market-data dual-gated (`ALLOW` + `--i-allow-network`); Telegram reports use HTML escape.

**Weaknesses:** Local secrets concentration; zip-to-friend must not include `.env` in shared channels; rotate if ever leaked.

---

## Gate evidence (2026-09-20)

| Check | Result |
| --- | --- |
| `verify_g4.py` | PASS |
| `kill_switch_drill.py` | PASS |
| Desk broker class | `PaperBroker` |
| `live_enabled` | False |
| Angel orders construct | BrokerError DISABLED |
| Providers registered | angelone, truedata, truedata_mock |
| TrueData bar partitions | present |
| Offline TrueData train+paper | ran OK (promote=False) |

---

## Required Actions (prioritized)

| Pri | Action |
| --- | --- |
| **P0** | Keep **paper-only**; do not re-enable live orders until gates + written GO |
| **P0** | Decide Angel MD enable (`ANGELONE_ALLOW_MARKETDATA`) only with explicit approval — then daily capture |
| **P0** | Wire fill → equity/PnL; prove daily-loss halt from fills |
| **P0** | Actuate session flatten + feed-loss flatten in ops loop |
| **P1** | Persist/reconcile state; encapsulate broker |
| **P1** | Feed Parity Gate before any future live marks ≠ research SoR |
| **P1** | Deepen real FUT history (paid or self-accumulated) before promote claims |
| **HoQR** | Sign G4 kill-drill awareness; keep live capital BLOCKED |

---

## Enforcement statement

Passing paper tests and having TrueData trial bars is **necessary but not sufficient** for production. Score **68 ≪ 95**. This remains a **gated research desk**. Live order code stays out until the human green-flags after paper gates.

**Next human step:** run daily ops on trading days (holiday-aware); enable Angel MD only when ready; accumulate paper sample; do **not** request live order wiring yet.
