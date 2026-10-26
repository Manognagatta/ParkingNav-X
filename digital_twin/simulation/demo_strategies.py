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

    Ablation flags (set via update_policy's ablation_flags kwarg):
    - use_prediction: if False, use current occupancy only (no forecast)
    - use_optimization: if False, skip gate load balancing; pure distance score
    - use_uncertainty: if False, ignore uncertainty width in scoring (point estimate only)
    - proactive: if False, only avoid lots that are currently full (not forecast-full)
    """

    label = "P-ParkingNavX"
    FILL_THRESHOLD = 0.85
    GATE_WEIGHT = 0.3

    def __init__(self):
        self._forecast = None  # injected by update_policy from prediction engine
        # Ablation flags — all True = full system
        self._use_prediction = True
        self._use_optimization = True
        self._use_uncertainty = True
        self._proactive = True

    def update_policy(self, state_snapshot: dict, forecast: dict = None,
                      ablation_flags: dict = None) -> None:
        """Called every 5 min by simulation engine. Store forecast and ablation flags."""
        self._forecast = forecast
        if ablation_flags is not None:
            self._use_prediction = ablation_flags.get("use_prediction", True)
            self._use_optimization = ablation_flags.get("use_optimization", True)
            self._use_uncertainty = ablation_flags.get("use_uncertainty", True)
            self._proactive = ablation_flags.get("proactive", True)

    def assign(self, vehicle, campus_state):
        best_lot, best_score = None, float("inf")

        # Gate load balancing — only when use_optimization=True
        if self._use_optimization:
            gate_loads = {
                g_id: g.get("queue_length", 0)
                for g_id, g in campus_state.gates.items()
                if g.get("status", "open") == "open"
            }
            preferred_gate = min(gate_loads, key=gate_loads.get) if gate_loads else vehicle.entry_gate
        else:
            gate_loads = {}
            preferred_gate = vehicle.entry_gate

        # Determine effective fill rates:
        # - use_prediction=True and proactive=True: use forecast fill (if available)
        # - use_prediction=True and proactive=False: use forecast but only filter on current occupancy
        # - use_prediction=False: use current occupancy only
        if self._use_prediction and self._forecast and "lot_fill_rates" in self._forecast:
            predicted_fills = self._forecast["lot_fill_rates"]
        else:
            predicted_fills = {}

        for lot_id, lot in campus_state.parking_lots.items():
            if lot["status"] != "open":
                continue
            cap = lot["usable_capacity"]
            occ = lot["occupied_spaces"]
            if cap <= 0 or occ >= cap:
                continue

            current_fill = occ / cap if cap > 0 else 1.0

            if self._proactive and self._use_prediction:
                # Proactive: avoid lots forecast to exceed threshold
                effective_fill = predicted_fills.get(lot_id, current_fill)
            else:
                # Reactive: only avoid lots currently at/above threshold
                effective_fill = current_fill

            if effective_fill > self.FILL_THRESHOLD:
                continue

            entry = (
                preferred_gate
                if self._use_optimization and nx.has_path(campus_state.graph, preferred_gate, lot_id)
                else vehicle.entry_gate
            )
            if not nx.has_path(campus_state.graph, entry, lot_id):
                continue

            distance = nx.shortest_path_length(
                campus_state.graph, entry, lot_id, weight="weight_distance_meters"
            )

            if self._use_optimization:
                gate_penalty = gate_loads.get(entry, 0) * self.GATE_WEIGHT
            else:
                gate_penalty = 0.0

            # use_uncertainty: when True, penalise uncertain lots slightly more
            # (placeholder: if forecast has upper_90 keys, widen effective fill)
            if self._use_uncertainty and self._use_prediction and self._forecast:
                upper_fills = self._forecast.get("lot_fill_rates_upper", {})
                effective_fill = max(effective_fill, upper_fills.get(lot_id, effective_fill))

            score = distance * (1 + effective_fill) + gate_penalty

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
