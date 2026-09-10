# Decisions locked for Phase 1

**Date:** 2026-07-25, revised **2026-07-26 (IC/CRO audit)** · **Status:** agent-applied IC
resolutions; awaiting HoQR signature at Gate G0

HoQR answers and an Independent Investment Committee review jointly govern this file.
Where they conflicted, the IC review wins until HoQR explicitly overrides in writing.

See also: [`IC_CONDITIONS.md`](IC_CONDITIONS.md), [`DATA_VENDORS.md`](DATA_VENDORS.md).

---

## 1. What HoQR specified

| Item | HoQR answer | Where it lives |
| --- | --- | --- |
| Broker | Angel One (SmartAPI), credentials shared later | `.env.example`, `execution/angelone.py` |
| Market data now | Yahoo Finance, broker API later | `data/providers/yahoo.py` — **plumbing only** |
| Instruments | No fixed instrument; architecture must not depend on one | `configs/instruments.yaml` |
| Paper first | Paper until the strategy is genuinely tested | `configs/risk_config.yaml` → `paper_gate` |
| Live | Only after HoQR is satisfied | `ALPHA_LIVE_GO_SIGNATURE`, unset |
| Capital | ₹10,00,000 planning | `risk_config.yaml` |
| Location | New folder under `D:\Project Alpha` | `D:\Project Alpha\alpha-desk` |

## 2. Locked values after IC audit (2026-07-26)

| Item | Locked value | Reasoning |
| --- | --- | --- |
| **NIFTY lot** | **65** (was wrongly 75) | NSE circular Jan 2026; Zerodha calculator |
| **BANKNIFTY lot** | 30 | Confirmed |
| **FINNIFTY lot** | **60** (was wrongly 65) | NSE circular Jan 2026 |
| **Margin % in YAML** | ~11.5% research snapshot | Live calm NRML ~11.3%; **not authoritative**; SPAN API required before live |
| **Position sizing primary** | ₹ stop-risk | Margin is capacity only |
| **Operating margin cap** | **20%** | Live one-lot ~17–19% of ₹10L; funds one lot |
| **Stress margin cap** | **30%** | Existing/reduce-only only; **not** for new risk |
| **Max positions / cluster** | **1 / 1** | NIFTY+BN = one India beta book |
| **Max lots** | 1 per order & instrument | Discrete lot already ≈ full book |
| **Risk per trade** | **1% / ₹10,000** | Bottom of HoQR’s 10–20k band until edge proven |
| **Daily loss** | **3% / ₹30,000** operating; 5% hard ceiling | IC cut from 5% operating as too aggressive |
| **Max drawdown halt** | 12% | Unchanged; resume needs written signoff |
| **Overnight** | **Forbidden** (`allow_overnight: false`) | No gap model yet; flatten by 15:15 |
| **Min R:R after costs** | 2.0 (1:6 = premium tag) | Unchanged |
| **Paper gate** | 60 sessions & 100 trades, zero breaches | Unchanged |
| **First live ceiling** | ₹50,000 | Unchanged; separate capital memo still required |
| **Promote-grade data** | Futures vendor / broker history only | Yahoo/synthetic refused under `--promote-grade` |

### Overturned error (keep visible)

The claim “20% margin cannot fund one index futures lot” was **false**. It used stale lot 75 and inflated 15%/18% margins. Live: NIFTY ~17.5% and BANKNIFTY ~19.3% of ₹10L equity at ~11.3% NRML. Permanent 30% as *operating* policy is rejected; 30% remains *stress-only*.

## 3. Loss gate (unchanged design)

Thresholds are relative to each trade’s cost-aware break-even probability. The Risk Kernel independently vetoes negative expected value.

## 4. Capital / product constraints (still true)

* ₹10L futures = **single-bet** economics, not a diversified desk.
* Round-turn costs remain large vs small stops — verify on a contract note.
* Yahoo cannot support promote decisions (~826 labels/symbol after warmup).
* Live ₹10L capital remains **NO-GO** until paper + cost + data + governance gates pass.

## 5. Leakage prevention

Truncation + shift-future attack, each with control experiments that must fail. Labels must react to a corrupted future.

## 6. Still owned by HoQR

1. Sign `configs/risk_config.yaml`.
2. Reconcile Angel One fees → `verified_against_contract_note: true`.
3. Purchase / approve futures history vendor (`DATA_VENDORS.md`).
4. Confirm IC risk cuts (3% daily, 1% per trade) or issue a written override.
5. Confirm flat-by-close overnight policy or approve a gap model.
6. Share Angel credentials locally when ready.
7. Sign gates G0→G5; live stays off without `ALPHA_LIVE_GO_SIGNATURE`.
