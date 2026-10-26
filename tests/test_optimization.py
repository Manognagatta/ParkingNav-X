"""Tests for Stage 8: Optimization Engine."""
import pytest


def test_weight_profiles_sum_to_one():
    """All weight profiles must have weights summing to 1."""
    from digital_twin.optimization.engine import WEIGHT_PROFILES
    for name, weights in WEIGHT_PROFILES.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, f"Profile {name} weights sum to {total}"


def test_cost_components_in_unit_interval():
    """All cost components return values in [0, 1]."""
    from digital_twin.optimization.engine import CostComponents, MultiObjectiveOptimizer
    cc = CostComponents()
    lot_fills = {
        "vitap-lot-academic-main": 0.85,
        "vitap-lot-hostel": 0.60,
        "vitap-lot-admin-visitor": 0.95,
        "vitap-lot-sports": 0.40,
        "vitap-lot-overflow": 0.20,
    }
    gate_states = {
        "vitap-gate-main": {"capacity": 6, "queue_length": 8},
    }
    c1 = cc.waiting_time_cost(gate_states)
    c2 = cc.queue_cost(gate_states)
    c3 = cc.search_travel_cost(lot_fills)
    c4 = cc.utilization_imbalance(lot_fills)
    c5 = cc.overflow_penalty(lot_fills)

    for label, val in [("c1", c1), ("c2", c2), ("c3", c3), ("c4", c4), ("c5", c5)]:
        assert 0.0 <= val <= 1.0, f"{label} = {val} out of [0, 1]"


def test_objective_J_in_unit_interval():
    """Objective J must be in [0, 1]."""
    from digital_twin.optimization.engine import MultiObjectiveOptimizer
    opt = MultiObjectiveOptimizer(weight_profile="P1")
    lot_fills = {
        "vitap-lot-academic-main": 0.85,
        "vitap-lot-hostel": 0.60,
        "vitap-lot-admin-visitor": 0.95,
        "vitap-lot-sports": 0.40,
        "vitap-lot-overflow": 0.20,
    }
    gate_states = {"vitap-gate-main": {"capacity": 6, "queue_length": 5}}
    cost = opt.compute_cost(lot_fills, gate_states)
    assert 0.0 <= cost["J"] <= 1.0, f"J = {cost['J']} out of [0, 1]"
    assert all(k in cost for k in ["waiting_time", "queue_cost", "search_travel", "utilization_imbalance", "overflow_penalty"])


def test_proactive_trigger_fires():
    """Proactive trigger fires when forecast > 90% and current < 90%."""
    from digital_twin.optimization.engine import MultiObjectiveOptimizer
    opt = MultiObjectiveOptimizer()
    current_fills = {"vitap-lot-academic-main": 0.85}
    forecast_fills = {"vitap-lot-academic-main": 0.95}  # forecast > 90%
    alerts = opt._check_proactive_trigger(current_fills, forecast_fills)
    assert len(alerts) >= 1
    assert alerts[0]["lot_id"] == "vitap-lot-academic-main"
    assert alerts[0]["type"] == "PROACTIVE_REROUTE"


def test_proactive_trigger_does_not_fire_when_reactive():
    """Proactive trigger should NOT fire when current already >= 90% (reactive, not proactive)."""
    from digital_twin.optimization.engine import MultiObjectiveOptimizer
    opt = MultiObjectiveOptimizer()
    current_fills = {"vitap-lot-academic-main": 0.95}   # already full
    forecast_fills = {"vitap-lot-academic-main": 0.98}
    alerts = opt._check_proactive_trigger(current_fills, forecast_fills)
    assert len(alerts) == 0, "Should not fire when already at capacity"


def test_lot_ranking_order():
    """Lots with lower fill rates should be ranked earlier (more available)."""
    from digital_twin.optimization.engine import MultiObjectiveOptimizer
    opt = MultiObjectiveOptimizer()
    lot_fills = {
        "vitap-lot-academic-main": 0.90,
        "vitap-lot-hostel": 0.20,
        "vitap-lot-admin-visitor": 0.70,
        "vitap-lot-sports": 0.50,
        "vitap-lot-overflow": 0.30,
    }
    ranked = opt._rank_lots(lot_fills, None)
    # First ranked should be the least full
    assert ranked[0]["current_fill_rate"] < ranked[-1]["current_fill_rate"]


def test_utilization_imbalance_zero_for_uniform():
    """Imbalance should be near zero when all lots have equal fill rates."""
    from digital_twin.optimization.engine import CostComponents
    cc = CostComponents()
    uniform = {lid: 0.5 for lid in ["a", "b", "c", "d", "e"]}
    imb = cc.utilization_imbalance(uniform)
    assert imb < 0.01, f"Uniform fill should have ~0 imbalance, got {imb}"


def test_overflow_penalty_zero_for_low_fill():
    """No overflow penalty when all lots below 95% fill."""
    from digital_twin.optimization.engine import CostComponents
    cc = CostComponents()
    fills = {"a": 0.5, "b": 0.7, "c": 0.8, "d": 0.89, "e": 0.3}
    penalty = cc.overflow_penalty(fills)
    assert penalty == 0.0, "No lots over 95% should mean zero overflow penalty"


def test_recommendation_has_objective_score_and_why_chosen():
    """LOT_RANKING recommendation must include objective_score and why_chosen fields."""
    from digital_twin.optimization.engine import MultiObjectiveOptimizer
    from digital_twin.db import get_connection, apply_migrations
    from digital_twin.config_loader import load_campus_config
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent
    conn = get_connection(":memory:")
    apply_migrations(conn)
    load_campus_config(ROOT / "configs" / "campus" / "vitap.yaml", conn)

    opt = MultiObjectiveOptimizer(weight_profile="P2")
    recs = opt.generate_recommendations(conn)
    conn.close()

    # Find the LOT_RANKING recommendation
    lot_ranking = next((r for r in recs if r.get("type") == "LOT_RANKING"), None)
    assert lot_ranking is not None, "Expected a LOT_RANKING recommendation"
    assert "objective_score" in lot_ranking, "LOT_RANKING must include objective_score"
    assert isinstance(lot_ranking["objective_score"], float), "objective_score must be float"
    assert 0.0 <= lot_ranking["objective_score"] <= 1.0, "objective_score must be in [0, 1]"
    assert "why_chosen" in lot_ranking, "LOT_RANKING must include why_chosen"
    assert isinstance(lot_ranking["why_chosen"], str), "why_chosen must be a string"
    assert len(lot_ranking["why_chosen"]) > 10, "why_chosen must be a non-trivial sentence"
    assert "alternatives" in lot_ranking, "LOT_RANKING must include alternatives list"

    # Each ranked lot should also have objective_score and rank
    ranked = lot_ranking.get("ranked_lots", [])
    assert len(ranked) > 0, "ranked_lots must not be empty"
    assert "objective_score" in ranked[0], "Each ranked lot must have objective_score"
    assert "rank" in ranked[0], "Each ranked lot must have rank field"
    assert ranked[0]["rank"] == 1, "Best lot should have rank=1"


def test_all_weight_profiles_produce_valid_J():
    """All three weight profiles produce valid J values."""
    from digital_twin.optimization.engine import MultiObjectiveOptimizer, WEIGHT_PROFILES
    lot_fills = {"vitap-lot-academic-main": 0.75, "vitap-lot-hostel": 0.50}
    gate_states = {"vitap-gate-main": {"capacity": 6, "queue_length": 3}}
    for profile in WEIGHT_PROFILES:
        opt = MultiObjectiveOptimizer(weight_profile=profile)
        cost = opt.compute_cost(lot_fills, gate_states)
        assert 0.0 <= cost["J"] <= 1.0, f"Profile {profile}: J out of range"
