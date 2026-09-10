"""The label definition must be derived from config, not hand-written twice.

If the risk policy changes the minimum R:R, the loss model's training target has
to move with it. Otherwise the model is answering a question the loss gate no
longer asks, and nothing in the pipeline would notice.
"""

from __future__ import annotations

from alpha_desk.labels.definitions import label_definition_from_config


def test_target_rr_follows_the_risk_config(risk_config, strategy_spec):
    definition = label_definition_from_config(risk_config, strategy_spec)
    assert definition.target_rr == risk_config.quality_gate.min_rr_after_costs
    assert definition.horizon_bars == strategy_spec.horizon.bars


def test_changing_the_rr_policy_changes_the_label_id(risk_config, strategy_spec):
    before = label_definition_from_config(risk_config, strategy_spec)
    stricter = risk_config.model_copy(
        update={
            "quality_gate": risk_config.quality_gate.model_copy(
                update={"min_rr_after_costs": 6.0}
            )
        }
    )
    after = label_definition_from_config(stricter, strategy_spec)
    assert after.label_def_id != before.label_def_id
    assert "rr6" in after.label_def_id
