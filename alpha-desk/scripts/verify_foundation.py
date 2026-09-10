"""Gate G0 verification.

Runs the foundation checklist and prints a PASS/FAIL table. This is what HoQR
should look at before signing "Foundation OK". Exit code is non-zero if any
mandatory check fails.

    python scripts/verify_foundation.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alpha_desk.config.settings import (  # noqa: E402
    load_cost_config,
    load_instrument_master,
    load_risk_config,
    load_settings,
    load_strategy_spec,
)
from alpha_desk.core.types import Mode  # noqa: E402
from alpha_desk.desk import build_desk  # noqa: E402
from alpha_desk.registry.trial_registry import TrialStatus  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


class Check:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str, bool]] = []

    def add(self, name: str, ok: bool, detail: str = "", mandatory: bool = True) -> None:
        self.rows.append((name, ok, detail, mandatory))

    def run(self, name: str, fn, mandatory: bool = True) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a failing check is the output
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        self.add(name, ok, detail, mandatory)

    @property
    def mandatory_failures(self) -> int:
        return sum(1 for _, ok, _, mandatory in self.rows if mandatory and not ok)

    def render(self) -> str:
        width = max(len(name) for name, *_ in self.rows) + 2
        lines = []
        for name, ok, detail, mandatory in self.rows:
            mark = "PASS" if ok else ("FAIL" if mandatory else "WARN")
            lines.append(f"  [{mark}] {name.ljust(width)} {detail}")
        return "\n".join(lines)


def _configs_load():
    load_risk_config()
    load_cost_config()
    load_instrument_master()
    load_strategy_spec()
    return True, "risk, costs, instruments, strategy spec all valid"


def _tests_green():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "no output"
    return proc.returncode == 0, tail


def _risk_test_count():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_risk_kernel.py", "-q", "--collect-only"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    count = sum(1 for line in proc.stdout.splitlines() if "::" in line)
    if count == 0:
        for line in proc.stdout.splitlines():
            if line.strip().startswith("tests/test_risk_kernel.py:"):
                count = int(line.rsplit(":", 1)[1])
    return count >= 20, f"{count} risk kernel tests (gate requires 20)"


def _secrets_safe():
    env = REPO / ".env"
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    ignored = ".env" in gitignore
    leaked = []
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip().startswith(("ANGELONE", "TELEGRAM")) and value.strip():
                leaked.append(key.strip())
    detail = ".env is gitignored"
    if leaked:
        detail += f"; populated secrets present locally: {', '.join(leaked)} (never commit)"
    return ignored, detail


def _live_is_off():
    settings = load_settings()
    return (
        settings.mode is not Mode.LIVE or not settings.live_enabled,
        f"mode={settings.mode.value}, live_enabled={settings.live_enabled}",
    )


def _kill_switch_works(desk):
    desk.kill_switch.engage("G0 verification drill", engaged_by="verify_foundation")
    engaged = desk.kill_switch.is_engaged()
    desk.kill_switch.clear(cleared_by="verify_foundation", written_reason="drill complete")
    cleared = not desk.kill_switch.is_engaged()
    return engaged and cleared, "engage and clear both work"


def _registry_works(desk):
    with desk.registry() as reg:
        tid = reg.create_trial(
            strategy_id="G0_SMOKE", hypothesis="registry smoke test", feature_set_hash="-"
        )
        reg.record_metrics(tid, "smoke", {"note": "verification"})
        reg.decide(
            tid,
            status=TrialStatus.REJECTED,
            decided_by="verify_foundation",
            rationale="smoke test only",
        )
        return reg.get(tid).status is TrialStatus.REJECTED, f"trial {tid} recorded and rejected"


def _order_path_works(desk):
    from alpha_desk.core.types import Intent, Side

    now = datetime.now(IST).replace(hour=11, minute=0, second=0, microsecond=0)
    desk.state.heartbeat(now)
    intent = Intent(
        ts=now,
        instrument=desk.instruments["NIFTY"],
        side=Side.BUY,
        ref_price=24_000.0,
        stop_price=23_960.0,
        target_price=24_160.0,
        strategy_id=desk.strategy_spec.strategy_id,
        alpha_score=1.5,
    )
    result = desk.order_path.submit(intent, desk.state, now)
    return result.executed, f"paper order path -> {result.status.value} {result.detail}"


def _audit_chain(desk):
    ok, message = desk.audit.verify_chain()
    return ok, message


def _costs_verified(desk):
    return desk.cost_model.verified, (
        "cost schedule reconciled"
        if desk.cost_model.verified
        else "NOT reconciled against a contract note - blocks any promote decision"
    )


def _config_signed(desk):
    return desk.risk_config.is_signed, (
        f"signed by {desk.risk_config.signed_by}"
        if desk.risk_config.is_signed
        else "risk_config.yaml signed_by/signed_on are empty - HoQR must sign at G0"
    )


def main() -> int:
    print("\nProject Alpha - Gate G0 foundation verification")
    print("=" * 70)

    checks = Check()
    checks.run("configs load and validate", _configs_load)
    checks.run("full test suite green", _tests_green)
    checks.run("risk kernel test count >= 20", _risk_test_count)
    checks.run("secrets pattern safe", _secrets_safe)
    checks.run("live trading is OFF", _live_is_off)

    desk = build_desk()
    checks.run("kill switch engages and clears", lambda: _kill_switch_works(desk))
    checks.run("trial registry accepts and rejects", lambda: _registry_works(desk))
    checks.run("paper order path executes", lambda: _order_path_works(desk))
    checks.run("audit hash chain intact", lambda: _audit_chain(desk))
    checks.run("cost schedule verified", lambda: _costs_verified(desk), mandatory=False)
    checks.run("risk config signed by HoQR", lambda: _config_signed(desk), mandatory=False)

    print(checks.render())
    print("=" * 70)

    warnings = desk.preflight()
    if warnings:
        print("Preflight notes:")
        for w in warnings:
            print(f"  - {w}")
        print("=" * 70)

    if checks.mandatory_failures:
        print(f"RESULT: {checks.mandatory_failures} mandatory check(s) FAILED. G0 not met.\n")
        return 1

    print("RESULT: mandatory checks PASS.")
    print("G0 still requires HoQR to sign risk_config.yaml and confirm the cost schedule.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
