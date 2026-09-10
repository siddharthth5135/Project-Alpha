# IC / CRO conditions — status board

**Review date:** 2026-07-26  
**Capital question:** Approve ₹10L live? **NO** until later gates.  
**Phase 1.2 ML question:** **GO WITH CONDITIONS** — see checklist.

## What HoQR must still provide (cannot be coded)

| # | Need | Why blocked without it |
| --- | --- | --- |
| 1 | Sign `configs/risk_config.yaml` (`signed_by` / `signed_on`) | G0 / live path |
| 2 | Real Angel One **contract note** → set `verified_against_contract_note: true` | Cost expectancy |
| 3 | Choose & purchase **futures** history vendor (see `DATA_VENDORS.md`) | Promote-grade training |
| 4 | Confirm overnight policy stays **flat-by-close** (or write an approved gap model) | Embedded as `allow_overnight: false` |
| 5 | Confirm IC risk cuts: daily **3%**, per-trade **1% / ₹10k** (replacing 5% / 2%) | Already applied in YAML; reverse only with written HoQR note |
| 6 | Angel One API credentials in local `.env` when ready for broker history | Live master / history |
| 7 | Explicit go/no-go after paper gate for any live capital (start ≤ ₹50k) | Capital memo |

## Agent-implemented conditions (this change set)

| IC condition | Status |
| --- | --- |
| Fix NIFTY lot 65 / FINNIFTY 60 | Done in `instruments.yaml` |
| Tag margins non-authoritative; live snapshot ~11.5% | Done |
| Operating margin 20% / stress 30% for new vs reduce-only | Done in risk config + kernel |
| Single directional beta book (max 1 position / cluster) | Done |
| Per-trade risk 1% / ₹10k; max 1 lot | Done |
| Daily loss operating 3% / ₹30k; ceiling 5% | Done |
| Overnight forbidden in Phase 1 | Done (`allow_overnight: false`) |
| Yahoo forbidden for promote-grade datasets | Done (`--promote-grade` gate) |
| Document data vendors | Done (`DATA_VENDORS.md`) |

## Re-open Phase 1.2 when

1. This checklist’s **agent** rows stay green in CI, and  
2. HoQR has at least **ordered** a futures data source (or committed to Angel history depth that meets ≥2y), and  
3. Risk YAML signature is in progress (can trail code work but must precede paper promote).

Live ₹10L remains **NO-GO** regardless of Phase 1.2 progress.
