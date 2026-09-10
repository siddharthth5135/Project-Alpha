"""Verify IC / CRO conditions that are enforceable in code.

    python scripts/verify_ic_conditions.py

Does not replace HoQR actions (signature, contract note, vendor purchase).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from alpha_desk.config.settings import (  # noqa: E402
    load_instrument_master,
    load_risk_config,
)
from verify_foundation import Check  # noqa: E402


def main() -> int:
    print("\nProject Alpha - IC condition verification (code-enforceable)")
    print("=" * 70)
    risk = load_risk_config()
    master = load_instrument_master()
    checks = Check()

    checks.add("NIFTY lot is 65", master.spec("NIFTY").lot_size == 65, str(master.spec("NIFTY").lot_size))
    checks.add("BANKNIFTY lot is 30", master.spec("BANKNIFTY").lot_size == 30)
    checks.add("FINNIFTY lot is 60", master.spec("FINNIFTY").lot_size == 60)
    checks.add(
        "margins tagged non-authoritative",
        not master.margins_are_authoritative,
        master.margin_source,
    )
    checks.add(
        "operating margin cap is 20%",
        risk.portfolio.max_margin_utilisation_pct == 20.0,
        str(risk.portfolio.max_margin_utilisation_pct),
    )
    checks.add(
        "stress margin cap is 30%",
        risk.portfolio.stress_margin_utilisation_pct == 30.0,
        str(risk.portfolio.stress_margin_utilisation_pct),
    )
    checks.add(
        "single concurrent position",
        risk.portfolio.max_concurrent_positions == 1,
    )
    checks.add(
        "single correlated cluster slot",
        risk.portfolio.max_correlated_cluster_positions == 1,
    )
    checks.add(
        "per-trade risk is 1% / Rs 10k",
        risk.per_trade.max_risk_per_trade_pct == 1.0
        and risk.per_trade.max_rupee_risk_per_trade == 10_000,
    )
    checks.add(
        "daily loss operating 3%",
        risk.daily_loss.max_daily_loss_pct == 3.0,
        f"{risk.daily_loss.max_daily_loss_pct}% (ceiling {risk.daily_loss.hard_ceiling_pct}%)",
    )
    checks.add("overnight forbidden", risk.session.allow_overnight is False)
    checks.add(
        "max one lot per order",
        risk.per_trade.max_lots_per_order == 1,
    )
    checks.add(
        "DATA_VENDORS.md present",
        (REPO / "docs" / "DATA_VENDORS.md").exists(),
    )
    checks.add(
        "IC_CONDITIONS.md present",
        (REPO / "docs" / "IC_CONDITIONS.md").exists(),
    )

    # HoQR items are warnings, not mandatory for this script
    checks.add(
        "risk config signed by HoQR",
        risk.is_signed,
        "still empty — HoQR action",
        mandatory=False,
    )

    print(checks.render())
    print("=" * 70)
    if checks.mandatory_failures:
        print(f"RESULT: {checks.mandatory_failures} code condition(s) FAILED.\n")
        return 1
    print("RESULT: code-enforceable IC conditions PASS.")
    print("Still required from HoQR: signature, contract note, futures data vendor.")
    print("Live Rs 10L capital remains NO-GO. Phase 1.2 is GO WITH CONDITIONS.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
