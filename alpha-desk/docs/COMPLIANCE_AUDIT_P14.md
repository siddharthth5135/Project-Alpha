# Compliance Audit — alpha-desk (P1.4)

**Role:** Strict Compliance Monitoring Agent (independent review — not implementation)  
**Date:** 2026-07-26  
**Subject:** Project Alpha / `alpha-desk` through Phase 1.4 scaffolding  
**Bar:** Institutional production readiness (pass ≥ **95**)  
**Interactive summary:** Cursor canvas `compliance-audit-p14.canvas.tsx`

---

## Overall Compliance Score

# **54 / 100 — REJECT for production deployment**

| Lens | Score |
| --- | ---: |
| Architecture | 68 |
| Data engineering | 48 |
| Machine learning | 55 |
| Security | 72 |
| Risk management | 62 |
| Reliability / fault tolerance | 32 |
| Software engineering | 70 |
| Observability / auditability | 64 |
| Performance / scalability | 50 |
| Disaster recovery | 28 |

**Paper research (gated):** CONDITIONAL ACCEPT — only with synthetic/Yahoo marked non-promote, live OFF, and HoQR aware of Critical gaps.  
**Live capital:** BLOCKED.

---

## Critical Issues

1. **C1 — No TrueData / promote-grade market-data ingest.** Providers are synthetic + yahoo only; `fetch_data.py` has no `truedata` path. Velocity trial ≠ API.
2. **C2 — Trading PnL does not update `RiskState` equity.** `register_fill` updates positions/trades only; daily-loss halt cannot fire from actual fills.
3. **C3 — Ops flatten policies do not flatten.** `flatten_on_data_feed_loss`, `force_flatten_at` reject new risk; no actuator calls `OrderPath.flatten`.
4. **C4 — No reconciliation / persisted desk state.** Restart uses `RiskState.fresh` → orphan risk vs broker book.
5. **C5 — `Desk.broker` is public.** Convention-only protection; `broker.place()` can skip kernel.
6. **C6 — No strategy exit path.** FLAT/ENTER only; stops/targets are sizing inputs, not managed exits.
7. **C7 — Promote evidence is still plumbing.** Artifacts `promote_eligible=false`; no vendor history yet.
8. **C8 — Angel live execution unfinished (correctly inert).** Blocks G5; when built must include timeouts/retries/idempotency.

---

## High-Risk Issues

- **H1** Kill switch engage/clear does not write `AuditEvent.KILL_SWITCH`.
- **H2** Warehouse monthly Parquet rewritten in place — no `schema_version` / immutability lock.
- **H3** Dirty bars still written; QA does not quarantine.
- **H4** Continuous futures not wired into dataset/warehouse path.
- **H5** Training `time_holdout` ≠ purged WF — easy to over-claim OOS.
- **H6** Any non-empty `ALPHA_LIVE_GO_SIGNATURE` enables `live_enabled`.
- **H7** `--force-plumbing` / `--skip-pit` escape hatches can mint dishonest promote paths.
- **H8** `OrderPath.flatten` bypasses kernel by design (acceptable for emergency; must stay audited and rare).

---

## Medium-Risk Issues

- Zero-volume bars counted but not raised as issues (Yahoo may look clean).
- No NSE session/holiday gap detection.
- Flat / no-trade bars not audited.
- `LossAction.FLATTEN` never emitted by loss gate.
- `max_order_age_seconds` unused.
- No Monte Carlo / parameter sensitivity / dual-feed parity.
- `verify_foundation` secret scan misses `TRUEDATA_*`.
- Report PnL is label/sign simulation, not full execution TCA.

---

## Low-Risk Suggestions

- Formal `MarketDataProvider` (stream + masters + calendar).
- Stricter GO signature format validation.
- Shadow Angel MD feed only after TrueData SoR + Feed Parity Gate.
- Redis last-bar cache after live paper exists.
- CI static analysis depth as team prefers.

---

## Compliance Violations

| Standard | Violation |
| --- | --- |
| Clean Architecture / DIP | Market-data SoR adapters missing; features OK |
| Immutable versioned datasets | Monthly overwrite without version/audit |
| Independent risk enforcement | Limits exist; PnL + session flatten incomplete |
| Vendor-agnostic MD | Protocol thin; TrueData/Angel MD absent |
| Train/serve consistency | Future Angel-live features risk skew vs TrueData train |
| DR / reconcile | Runbooks without code |
| Audit completeness | Kill/heartbeat/flat gaps vs paper_gate 100% claim |

---

## Security Findings

**Strengths:** `.env` gitignored; `.env.example` empty; live mode requires signature; Angel refuses non-live construct; place/connect inert.

**Weaknesses:** Local `.env` holds secrets (rotate if ever shared/pasted); GO signature format not validated; kill engage/clear not hash-chained in audit; foundation scanner incomplete for TrueData env vars.

---

## Architectural Findings

**Strengths:** Single `OrderPath` funnel; strategy → Intent only; loss advice mandatory at kernel; source-partitioned warehouse; Broker ABC; features/models free of vendor SDKs.

**Weaknesses:** Public `Desk.broker`; doc `MARKET_DATA_REQUIREMENTS.md` ahead of code; continuous futures lab-only; no exit engine; paper path ≠ production ops loop.

---

## Quantitative Finance Findings

**Strengths:** PIT + shift-future controls with failing controls; plumbing cannot promote without force; purged expanding WF; feature lock hash; cost-aware report caveats.

**Weaknesses:** No real NFO history yet; continuous not in train path; label PnL ≠ execution sim; no robustness suite (MC, sensitivity); survivorship/expired-contract coverage blocked until TrueData; regime dependency untested on real data.

---

## Required Actions (prioritized)

| Pri | Action |
| --- | --- |
| **P0** | Get **TrueData Market Data API** enabled (not Velocity); ingest → `data/bars/truedata/`; reply `TrueData API enabled` |
| **P0** | Wire fill → equity/PnL in `RiskState`; prove daily-loss halt |
| **P0** | Actuate `force_flatten_at` + feed-loss flatten |
| **P0** | Persist/reconcile positions on startup |
| **P1** | Encapsulate broker; only `OrderPath` may `place` |
| **P1** | Audit kill engage/clear; restrict force-plumbing |
| **P1** | Quarantine dirty data; `schema_version`; gap/session QA |
| **P1** | Wire continuous futures into dataset builder |
| **P2** | Exit engine before live; Angel retries/timeouts/idempotency |
| **P2** | Feed Parity Gate before any Angel-as-MD plan |
| **HoQR** | Approve `alpha_score_threshold`; run `kill_switch_drill.py`; sign G4 |

---

## Enforcement statement

Functioning paper tests are **insufficient** for production approval. This platform is a **gated research desk** with a strong decision-path skeleton. It is **not** institutionally production-ready until Critical items are remediated and score ≥ 95 with evidence.

**Next human step unchanged:** chase TrueData **Market Data API** enablement; then continue remediation in priority order above.
