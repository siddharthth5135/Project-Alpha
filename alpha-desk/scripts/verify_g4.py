"""Gate G4 checklist — decision path safe (P1.4).

    python scripts/verify_g4.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from alpha_desk.config.settings import load_settings, load_strategy_spec  # noqa: E402
from alpha_desk.desk import build_desk  # noqa: E402
from alpha_desk.strategy.rules import rules_from_strategy_spec  # noqa: E402
from verify_foundation import Check  # noqa: E402


def main() -> int:
    print("\nProject Alpha — Gate G4 decision-path checks")
    print("=" * 70)
    settings = load_settings()
    spec = load_strategy_spec()
    checks = Check()

    checks.add("ALPHA_MODE is paper (not live)", settings.mode.value == "paper", settings.mode.value)
    checks.add("live_enabled is False", not settings.live_enabled, str(settings.live_enabled))

    entry = getattr(spec, "entry_rules", {}) or {}
    checks.add(
        "alpha_score_threshold present in strategy_spec",
        "alpha_score_threshold" in entry,
        str(entry.get("alpha_score_threshold")),
    )
    cfg = rules_from_strategy_spec(spec)
    checks.add("rules config builds", cfg.strategy_id == spec.strategy_id, cfg.strategy_id)
    checks.add("stop_atr_multiple > 0", cfg.stop_atr_multiple > 0, str(cfg.stop_atr_multiple))
    checks.add("target_rr > 0", cfg.target_rr > 0, str(cfg.target_rr))

    desk = build_desk()
    checks.add("order_path wired on desk", desk.order_path is not None, "ok")
    checks.add(
        "kill switch dir exists",
        desk.kill_switch.path.parent.exists(),
        str(desk.kill_switch.path),
    )
    checks.add(
        "paper broker assembled (not live)",
        getattr(desk.broker, "inner_class_name", desk.broker.__class__.__name__) == "PaperBroker",
        getattr(desk.broker, "inner_class_name", desk.broker.__class__.__name__),
    )
    checks.add(
        "direct broker.place is guarded",
        desk.broker.__class__.__name__ == "BrokerGuard",
        desk.broker.__class__.__name__,
    )

    rules_mod = REPO / "src" / "alpha_desk" / "strategy" / "rules.py"
    paper_mod = REPO / "src" / "alpha_desk" / "pipeline" / "paper_engine.py"
    drill = REPO / "scripts" / "kill_switch_drill.py"
    checks.add("strategy/rules.py present", rules_mod.exists(), str(rules_mod))
    checks.add("pipeline/paper_engine.py present", paper_mod.exists(), str(paper_mod))
    checks.add("kill_switch_drill.py present", drill.exists(), str(drill))

    print(checks.render())
    print("=" * 70)
    if checks.mandatory_failures:
        print(f"RESULT: {checks.mandatory_failures} mandatory check(s) FAILED.\n")
        return 1
    print("RESULT: G4 code checks PASS.")
    print("HoQR must still run kill_switch_drill.py and sign “I will not bypass Loss/Risk”.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
