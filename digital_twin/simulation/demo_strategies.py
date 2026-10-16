"""
Demo allocation strategies: B3 (Prediction-Only) and B4/P (ParkingNav-X Full).
These are the advanced strategies that go beyond the B1/B2 baselines in strategy.py.
"""

import networkx as nx

from digital_twin.simulation.strategy import AllocationStrategy, AssignmentResult


def _has_space(lot_id, lot, campus_state) -> bool:
    return (
        lot["status"] == "open"
        and lot["occupied_spaces"] < lot["usable_capacity"]
        and lot_id not in getattr(campus_state, "excluded_lots", set())
    )


class PredictionOnlyStrategy(AllocationStrategy):
    """B3 — Prediction-aware strategy.

    Uses predicted occupancy (fill rate) to avoid lots that are likely to
    fill up soon, steering vehicles to lots with headroom.
    """

    label = "B3-PredictionOnly"
    FILL_THRESHOLD = 0.80

    def assign(self, vehicle, campus_state):
        best_lot, best_score = None, float("inf")
        for lot_id, lot in campus_state.parking_lots.items():
            if lot["status"] != "open":
                continue
            cap = lot["usable_capacity"]
            occ = lot["occupied_spaces"]
            if cap <= 0 or occ >= cap:
                continue
            fill_rate = occ / cap
            if fill_rate > self.FILL_THRESHOLD:
                continue
            if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                continue
            distance = nx.shortest_path_length(
                campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
            )
            score = distance * (1 + fill_rate)
            if score < best_score:
                best_lot, best_score = lot_id, score
        # Fallback: threshold filtered everything → pick nearest available
        if best_lot is None:
            for lot_id, lot in campus_state.parking_lots.items():
                if lot["status"] != "open" or lot["occupied_spaces"] >= lot["usable_capacity"]:
                    continue
                if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                    continue
                distance = nx.shortest_path_length(
                    campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
                )
                if best_lot is None or distance < best_score:
                    best_lot, best_score = lot_id, distance
        return AssignmentResult(parking_lot_id=best_lot, gate_id=vehicle.entry_gate)


class ParkingNavXFullStrategy(AllocationStrategy):
    """B4/P — ParkingNav-X full system strategy.

    Combines: prediction-aware lot selection + gate load balancing +
    zone-aware routing + forecast-driven policy updates.
    """

    label = "P-ParkingNavX"
    FILL_THRESHOLD = 0.85
    GATE_WEIGHT = 0.3

    def __init__(self):
        self._forecast = None  # injected by update_policy from prediction engine

    def update_policy(self, state_snapshot: dict, forecast: dict = None) -> None:
        """Called every 5 min by simulation engine. Store forecast for next assign calls."""
        self._forecast = forecast

    def assign(self, vehicle, campus_state):
        best_lot, best_score = None, float("inf")

        # Prefer the least-loaded gate
        gate_loads = {
            g_id: g.get("queue_length", 0)
            for g_id, g in campus_state.gates.items()
            if g.get("status", "open") == "open"
        }
        preferred_gate = min(gate_loads, key=gate_loads.get) if gate_loads else vehicle.entry_gate

        # Use forecasted fill rates if available, else current
        predicted_fills = {}
        if self._forecast and "lot_fill_rates" in self._forecast:
            predicted_fills = self._forecast["lot_fill_rates"]

        for lot_id, lot in campus_state.parking_lots.items():
            if lot["status"] != "open":
                continue
            cap = lot["usable_capacity"]
            occ = lot["occupied_spaces"]
            if cap <= 0 or occ >= cap:
                continue
            # Use predicted fill rate if available, else current
            fill_rate = predicted_fills.get(lot_id, occ / cap if cap > 0 else 1.0)
            if fill_rate > self.FILL_THRESHOLD:
                continue

            entry = preferred_gate if nx.has_path(campus_state.graph, preferred_gate, lot_id) else vehicle.entry_gate
            if not nx.has_path(campus_state.graph, entry, lot_id):
                continue

            distance = nx.shortest_path_length(
                campus_state.graph, entry, lot_id, weight="weight_distance_meters"
            )
            gate_penalty = gate_loads.get(entry, 0) * self.GATE_WEIGHT
            score = distance * (1 + fill_rate) + gate_penalty

            if score < best_score:
                best_lot, best_score = lot_id, score

        # Fallback to nearest if all filtered out
        if best_lot is None:
            for lot_id, lot in campus_state.parking_lots.items():
                if lot["status"] != "open" or lot["occupied_spaces"] >= lot["usable_capacity"]:
                    continue
                if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                    continue
                distance = nx.shortest_path_length(
                    campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
                )
                if best_lot is None or distance < best_score:
                    best_lot, best_score = lot_id, distance

        return AssignmentResult(parking_lot_id=best_lot, gate_id=vehicle.entry_gate)
