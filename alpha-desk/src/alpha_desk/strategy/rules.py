"""Strategy rules: alpha score → Intent or flat.

No sizing here. No broker calls. Threshold edges are unit-tested so a zero
score cannot accidentally become an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from alpha_desk.core.types import Intent, Instrument, Side


class SignalAction(str, Enum):
    FLAT = "FLAT"
    ENTER = "ENTER"


@dataclass(frozen=True)
class RulesConfig:
    strategy_id: str
    alpha_threshold: float = 0.0
    require_regime_ok: bool = True
    allowed_regime_buckets: tuple[float, ...] = (0.0, 1.0, 2.0)
    require_liquidity_ok: bool = True
    min_volume_zscore: float = -5.0  # permissive default for thin Yahoo volume
    stop_atr_multiple: float = 1.5
    target_rr: float = 2.0
    feature_set_hash: str = ""

    def __post_init__(self) -> None:
        if self.alpha_threshold < 0:
            raise ValueError("alpha_threshold cannot be negative")
        if self.stop_atr_multiple <= 0 or self.target_rr <= 0:
            raise ValueError("stop/target multiples must be positive")


@dataclass(frozen=True)
class SignalDecision:
    action: SignalAction
    reason: str
    intent: Intent | None = None

    @property
    def is_flat(self) -> bool:
        return self.action is SignalAction.FLAT


class StrategyRules:
    """Deterministic mapping from model score + features to an Intent."""

    def __init__(self, config: RulesConfig) -> None:
        self._cfg = config

    @property
    def config(self) -> RulesConfig:
        return self._cfg

    def decide(
        self,
        *,
        ts: datetime,
        instrument: Instrument,
        ref_price: float,
        atr_points: float,
        alpha_score: float,
        features: dict[str, float] | None = None,
    ) -> SignalDecision:
        feats = features or {}
        if ref_price <= 0 or atr_points <= 0:
            return SignalDecision(SignalAction.FLAT, "invalid price or ATR")

        regime_ok = self._regime_ok(feats)
        if self._cfg.require_regime_ok and not regime_ok:
            return SignalDecision(
                SignalAction.FLAT,
                f"regime blocked (bucket={feats.get('vol_regime_bucket')})",
            )

        liquidity_ok = self._liquidity_ok(feats)
        if self._cfg.require_liquidity_ok and not liquidity_ok:
            return SignalDecision(
                SignalAction.FLAT,
                f"liquidity blocked (volume_zscore={feats.get('volume_zscore_20')})",
            )

        # Zero score is always flat (even when threshold is 0.0).
        if alpha_score == 0.0 or abs(alpha_score) < self._cfg.alpha_threshold:
            return SignalDecision(
                SignalAction.FLAT,
                f"|alpha| {abs(alpha_score):.6f} below threshold {self._cfg.alpha_threshold}",
            )

        side = Side.BUY if alpha_score > 0 else Side.SELL
        stop_dist = self._cfg.stop_atr_multiple * atr_points
        target_dist = stop_dist * self._cfg.target_rr
        if side is Side.BUY:
            stop = ref_price - stop_dist
            target = ref_price + target_dist
        else:
            stop = ref_price + stop_dist
            target = ref_price - target_dist

        intent = Intent(
            ts=ts,
            instrument=instrument,
            side=side,
            ref_price=ref_price,
            stop_price=round(stop, 2),
            target_price=round(target, 2),
            strategy_id=self._cfg.strategy_id,
            alpha_score=float(alpha_score),
            regime_ok=regime_ok,
            liquidity_ok=liquidity_ok,
            feature_set_hash=self._cfg.feature_set_hash,
        )
        return SignalDecision(
            SignalAction.ENTER,
            f"{side.value} |alpha|={abs(alpha_score):.6f} >= {self._cfg.alpha_threshold}",
            intent=intent,
        )

    def _regime_ok(self, feats: dict[str, float]) -> bool:
        if "vol_regime_bucket" not in feats:
            # Missing regime feature fails closed when required.
            return not self._cfg.require_regime_ok
        bucket = float(feats["vol_regime_bucket"])
        return bucket in self._cfg.allowed_regime_buckets

    def _liquidity_ok(self, feats: dict[str, float]) -> bool:
        if "volume_zscore_20" not in feats:
            return not self._cfg.require_liquidity_ok
        return float(feats["volume_zscore_20"]) >= self._cfg.min_volume_zscore


def rules_from_strategy_spec(strategy_spec, *, feature_set_hash: str = "") -> RulesConfig:
    """Pull numeric thresholds from strategy_spec extras (entry_rules / exit_rules)."""
    entry = getattr(strategy_spec, "entry_rules", None) or {}
    exit_rules = getattr(strategy_spec, "exit_rules", None) or {}
    if not isinstance(entry, dict):
        entry = {}
    if not isinstance(exit_rules, dict):
        exit_rules = {}

    allowed = entry.get("allowed_regime_buckets", [0, 1, 2])
    stop_mult = float(exit_rules.get("stop_atr_multiple", 1.5))
    # exit_rules may encode stop as string; prefer numeric key we add to YAML.
    if "stop_atr_multiple" in entry:
        stop_mult = float(entry["stop_atr_multiple"])

    return RulesConfig(
        strategy_id=strategy_spec.strategy_id,
        alpha_threshold=float(entry.get("alpha_score_threshold", 0.0)),
        require_regime_ok=bool(entry.get("require_regime_ok", True)),
        allowed_regime_buckets=tuple(float(x) for x in allowed),
        require_liquidity_ok=bool(entry.get("require_liquidity_ok", True)),
        min_volume_zscore=float(entry.get("min_volume_zscore", -5.0)),
        stop_atr_multiple=stop_mult,
        target_rr=float(entry.get("min_rr_after_costs", 2.0)),
        feature_set_hash=feature_set_hash,
    )
