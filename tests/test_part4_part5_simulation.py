from pathlib import Path

import pytest

from digital_twin.config_loader import load_campus_config
from digital_twin.db import apply_migrations, get_connection
from digital_twin.simulation.engine import SimulationEngine
from digital_twin.simulation.scenario import ScenarioLoader
from digital_twin.simulation.strategy import (
    AllocationStrategy,
    AssignmentResult,
    FixedLotStrategy,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = PROJECT_ROOT / "configs" / "campuses"
SCENARIOS = PROJECT_ROOT / "configs" / "scenarios" / "sample"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    apply_migrations(c)
    load_campus_config(CONFIGS / "sample.yaml", c)
    yield c
    c.close()


class AlwaysNearestOpenStrategy(AllocationStrategy):
    """Minimal real-ish strategy for engine tests -- picks the first open,
    available lot by sorted id. Not owned by Member 1 in spirit; used here
    only to exercise the engine end-to-end."""

    def assign(self, vehicle, campus_state):
        for lot_id in sorted(campus_state.parking_lots):
            lot = campus_state.parking_lots[lot_id]
            if lot["status"] == "open" and lot["occupied_spaces"] < lot["usable_capacity"]:
                return AssignmentResult(parking_lot_id=lot_id, gate_id=vehicle.entry_gate)
        return AssignmentResult(parking_lot_id=None)


def test_same_seed_produces_byte_identical_metrics(conn):
    scenario = ScenarioLoader.load(SCENARIOS / "normal_day.yaml")
    engine = SimulationEngine()

    result_a = engine.run(scenario, AlwaysNearestOpenStrategy(), conn, run_id="run-a")
    result_b = engine.run(scenario, AlwaysNearestOpenStrategy(), conn, run_id="run-b")

    assert result_a.vehicles == result_b.vehicles
    assert result_a.timesteps == result_b.timesteps


def test_closed_parking_lot_receives_zero_assignments(conn):
    raw_scenario = SCENARIOS / "parking_closure.yaml"
    scenario = ScenarioLoader.load(raw_scenario)
    engine = SimulationEngine()
    result = engine.run(scenario, AlwaysNearestOpenStrategy(), conn)

    assigned_lots = {v["assigned_lot_id"] for v in result.vehicles if v["assigned_lot_id"]}
    assert "sample-lot-1" not in assigned_lots


def test_closed_gate_admits_zero_vehicles(conn):
    scenario = ScenarioLoader.load(SCENARIOS / "gate_closure.yaml")
    engine = SimulationEngine()
    result = engine.run(scenario, AlwaysNearestOpenStrategy(), conn)

    assert all(v["assigned_lot_id"] is None for v in result.vehicles)
    assert all(v["waiting_time_seconds"] == 0 for v in result.vehicles)


def test_gate_queue_length_never_negative(conn):
    scenario = ScenarioLoader.load(SCENARIOS / "normal_day.yaml")
    engine = SimulationEngine()
    result = engine.run(scenario, AlwaysNearestOpenStrategy(), conn)

    for ts in result.timesteps:
        for key, value in ts.items():
            if key.startswith("gate_queue_"):
                assert value >= 0


def test_parking_occupancy_never_exceeds_usable_capacity(conn):
    scenario = ScenarioLoader.load(SCENARIOS / "normal_day.yaml")
    engine = SimulationEngine()
    result = engine.run(scenario, AlwaysNearestOpenStrategy(), conn)

    max_occupancy_seen = max(
        (v for ts in result.timesteps for k, v in ts.items() if k == "parking_occupancy_sample-lot-1"),
        default=0,
    )
    assert max_occupancy_seen <= 18  # sample-lot-1 usable_capacity


def test_fixed_lot_strategy_is_actually_called_not_bypassed(conn):
    """Trivial 2-vehicle scenario: every vehicle must end up assigned to
    the strategy's fixed lot -- proving the engine calls the interface."""
    import yaml
    raw = yaml.safe_load(open(SCENARIOS / "normal_day.yaml"))
    raw["vehicle_count"] = 2
    raw["duration_minutes"] = 30
    scenario = ScenarioLoader.from_dict(raw)

    engine = SimulationEngine()
    result = engine.run(scenario, FixedLotStrategy("sample-lot-2"), conn)

    assigned = [v["assigned_lot_id"] for v in result.vehicles]
    assert assigned == ["sample-lot-2", "sample-lot-2"]


def test_overflow_event_recorded_once_per_timestep_not_per_vehicle(conn):
    import yaml
    raw = yaml.safe_load(open(SCENARIOS / "normal_day.yaml"))
    raw["vehicle_count"] = 5
    raw["duration_minutes"] = 30  # long enough that every vehicle finishes its search
    raw["arrival_rate_profile"] = {"type": "constant", "rate": 5.0}
    scenario = ScenarioLoader.from_dict(raw)

    engine = SimulationEngine()
    # A strategy that always points at a nonexistent lot forces every search to fail.
    result = engine.run(scenario, FixedLotStrategy("nonexistent-lot"), conn)

    assert all(v["assigned_lot_id"] is None for v in result.vehicles)
    assert all(v["final_state"] == "completed" for v in result.vehicles)
    # One failed attempt logged per vehicle...
    assert len(result.overflow_events) == 5
    # ...but the per-timestep flag must collapse same-tick duplicates into one boolean, not a count.
    for ts in result.timesteps:
        for key, value in ts.items():
            if key.startswith("overflow_"):
                assert value in (True, False)


# --------------------------------------------------------------------------
# VIT-AP: baselines, event-aware demand, forecast error, closures, scale
# --------------------------------------------------------------------------
import time  # noqa: E402
from dataclasses import replace  # noqa: E402

import networkx as nx  # noqa: E402

from digital_twin.cli import run as cli_run  # noqa: E402
from digital_twin.simulation.demo_strategies import (  # noqa: E402
    BASELINES,
    DemoNearestAvailableStrategy,
    FirstAvailableStrategy,
)
from digital_twin.simulation.engine import LEFT_NO_ROUTE  # noqa: E402
from digital_twin.simulation.strategy import CampusState, Vehicle  # noqa: E402

VITAP_SCENARIOS = PROJECT_ROOT / "configs" / "scenarios" / "vitap"


@pytest.fixture(scope="module")
def vitap(tmp_path_factory):
    c = get_connection(tmp_path_factory.mktemp("vitap") / "vitap.db")
    apply_migrations(c)
    load_campus_config(CONFIGS / "vitap.yaml", c)
    yield c
    c.close()


def vitap_run(conn, name="peak_hour", strategy="nearest_available", **changes):
    scenario = replace(ScenarioLoader.load(VITAP_SCENARIOS / f"{name}.yaml"), **changes)
    return SimulationEngine().run(scenario, BASELINES[strategy](), conn, persist=False)


def test_same_seed_same_vehicles_for_every_strategy_and_error_level(vitap):
    """Fair comparison: only the parking decisions change, never the demand."""
    def demand(r):
        return [(v["entry_gate"], v["destination_id"], v["arrival_minute"]) for v in r.vehicles]

    a = vitap_run(vitap, strategy="first_available")
    b = vitap_run(vitap, prediction_error_injection_level=0.2)
    assert demand(a) == demand(b)
    assert demand(a) != demand(vitap_run(vitap, random_seed=999))


@pytest.mark.parametrize("name", sorted(p.stem for p in VITAP_SCENARIOS.glob("*.yaml")))
def test_every_vitap_scenario_respects_capacity(vitap, name):
    result = vitap_run(vitap, name, strategy="first_available")
    s = result.summary
    assert s["capacity_violations"] == 0
    assert s["parked_vehicles"] + s["failed_vehicles"] == s["total_vehicles"]
    if result.timesteps:  # campus is empty again at the end
        assert all(v == 0 for k, v in result.timesteps[-1].items()
                   if k.startswith(("parking_occupancy_", "gate_queue_")))


def test_vitap_closures_are_enforced(vitap):
    lot = vitap_run(vitap, "lot_closure")
    assert "vitap-lot-academic-main" not in {v["assigned_lot_id"] for v in lot.vehicles}
    gate = vitap_run(vitap, "gate_closure")
    assert {v["entry_gate"] for v in gate.vehicles} == {"vitap-gate-main"}
    road = vitap_run(vitap, "road_closure")
    assert {v["outcome"] for v in road.vehicles} == {LEFT_NO_ROUTE}


def test_event_adds_vehicles_inside_its_window_and_zone(vitap):
    normal = vitap_run(vitap, "event_day", event_conditions=None, vehicle_count=3000)
    event = vitap_run(vitap, "event_day", vehicle_count=3000)

    def after_start(r):
        return sum(v["arrival_minute"] >= 120 for v in r.vehicles)

    def to_gym(r):  # the gym's nearest lot is in the event's sports_zone
        return sum(v["destination_id"] == "vitap-gym-001" for v in r.vehicles) / len(r.vehicles)

    assert after_start(event) > 1.3 * after_start(normal)
    assert to_gym(event) > 1.2 * to_gym(normal)


def test_baselines_choose_as_defined():
    graph = nx.Graph()
    graph.add_edge("g", "lot-a", weight_distance_meters=300)  # first in config order, farther
    graph.add_edge("g", "lot-b", weight_distance_meters=100)  # nearer
    lots = {lot: {"status": "open", "usable_capacity": 10, "occupied_spaces": 0}
            for lot in ("lot-a", "lot-b")}
    state = CampusState("t", graph, lots, {}, {})
    car = Vehicle("v", "g", None, 0)
    assert FirstAvailableStrategy().assign(car, state).parking_lot_id == "lot-a"
    assert DemoNearestAvailableStrategy().assign(car, state).parking_lot_id == "lot-b"
    state.excluded_lots = {"lot-b"}  # already found full -> skipped
    assert DemoNearestAvailableStrategy().assign(car, state).parking_lot_id == "lot-a"


@pytest.mark.parametrize("n", [500, 1000])
def test_scales_to_500_and_1000_vehicles(vitap, n):
    started = time.perf_counter()
    result = vitap_run(vitap, f"scale_{n}")
    assert result.summary["total_vehicles"] == n
    assert time.perf_counter() - started < 5.0
    assert result.summary["decision_latency_mean_ms"] > 0


def test_cli_results_are_reproducible(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(cli_run, "RESULTS_DIR", tmp_path / "results")
    path = VITAP_SCENARIOS / "normal_day.yaml"
    a = cli_run.run_scenario(path, ["first_available"], seeds=3, errors=[0.0, 0.1])
    b = cli_run.run_scenario(path, ["first_available"], seeds=3, errors=[0.0, 0.1])
    clock = ["decision_latency_mean_ms"]
    assert a.drop(columns=clock).equals(b.drop(columns=clock)) and len(a) == 6
    assert (tmp_path / "results" / "vitap-normal-day.md").exists()
    assert (tmp_path / "runs" / "vitap-normal-day" / "first_available" / "err10" / "seed_7"
            / "vehicles.csv").exists()
