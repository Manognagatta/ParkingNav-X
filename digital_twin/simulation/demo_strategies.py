"""
Baseline allocation strategies that run inside the simulator. They are the
yardstick Member 3's optimizer is compared against:

* FirstAvailableStrategy: the first lot, in the campus config's order, that
  shows a free space (a driver checking lots in a fixed order).
* DemoNearestAvailableStrategy: the open lot with a free space that is the
  shortest drive from where the vehicle is now.

Both skip lots the vehicle already found full (campus_state.excluded_lots).
"""

import networkx as nx

from digital_twin.simulation.strategy import AllocationStrategy, AssignmentResult


def _has_space(lot_id, lot, campus_state) -> bool:
    return (
        lot["status"] == "open"
        and lot["occupied_spaces"] < lot["usable_capacity"]
        and lot_id not in campus_state.excluded_lots
    )


class FirstAvailableStrategy(AllocationStrategy):
    """First lot in config order that shows a free space and can be reached."""

    def assign(self, vehicle, campus_state):
        here = vehicle.current_node or vehicle.entry_gate
        for lot_id, lot in campus_state.parking_lots.items():
            if _has_space(lot_id, lot, campus_state) and nx.has_path(campus_state.graph, here, lot_id):
                return AssignmentResult(parking_lot_id=lot_id, gate_id=vehicle.entry_gate)
        return AssignmentResult(parking_lot_id=None, gate_id=vehicle.entry_gate)


class DemoNearestAvailableStrategy(AllocationStrategy):
    """Picks the open, available lot with the shortest drive distance from
    the vehicle's current position. A minimal real implementation, not the
    project's final strategy."""

    label = "B2-NearestAvailable"

    def assign(self, vehicle, campus_state):
        here = vehicle.current_node or vehicle.entry_gate
        best_lot, best_distance = None, float("inf")
        for lot_id, lot in campus_state.parking_lots.items():
            if not _has_space(lot_id, lot, campus_state):
                continue
            if not nx.has_path(campus_state.graph, here, lot_id):
                continue
            distance = nx.shortest_path_length(
                campus_state.graph, here, lot_id, weight="weight_distance_meters"
            )
            if distance < best_distance:
                best_lot, best_distance = lot_id, distance
        return AssignmentResult(parking_lot_id=best_lot, gate_id=vehicle.entry_gate)


class PredictionOnlyStrategy(AllocationStrategy):
    """B3 — Prediction-aware strategy.

    Uses predicted occupancy (fill rate) to avoid lots that are likely to
    fill up soon, steering vehicles to lots with headroom. Without a real
    Member 2 forecast, the fill-rate proxy is computed from current state.
    This is a placeholder that will be replaced once the forecast module
    is integrated.
    """

    FILL_THRESHOLD = 0.80  # avoid lots more than 80% full

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
                continue  # skip nearly-full lots to avoid future overflow
            if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                continue
            distance = nx.shortest_path_length(
                campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
            )
            # Score = distance penalised by fill rate (prefer near + emptier lots)
            score = distance * (1 + fill_rate)
            if score < best_score:
                best_lot, best_score = lot_id, score
        # Fallback: if threshold filtered everything, pick nearest available
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


<<<<<<< HEAD
=======
class PredictionOnlyStrategy(AllocationStrategy):
    """B3 — Prediction-aware strategy.

    Uses predicted occupancy (fill rate) to avoid lots that are likely to
    fill up soon, steering vehicles to lots with headroom. Without a real
    Member 2 forecast, the fill-rate proxy is computed from current state.
    This is a placeholder that will be replaced once the forecast module
    is integrated.
    """

    label = "B3-PredictionOnly"
    FILL_THRESHOLD = 0.80  # avoid lots more than 80% full

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
                continue  # skip nearly-full lots to avoid future overflow
            if not nx.has_path(campus_state.graph, vehicle.entry_gate, lot_id):
                continue
            distance = nx.shortest_path_length(
                campus_state.graph, vehicle.entry_gate, lot_id, weight="weight_distance_meters"
            )
            # Score = distance penalised by fill rate (prefer near + emptier lots)
            score = distance * (1 + fill_rate)
            if score < best_score:
                best_lot, best_score = lot_id, score
        # Fallback: if threshold filtered everything, pick nearest available
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


>>>>>>> 7e7480a3c640692a24ace314c17353395f1eb2fc
class ParkingNavXFullStrategy(AllocationStrategy):
    """P — ParkingNav-X full system strategy.

    Combines: prediction-aware lot selection + gate load balancing +
    zone-aware routing. Vehicles are steered to the lot with the best
    combined score across distance, predicted fill headroom, and gate
    queue length. This is the target end-state of the ParkingNav-X system.
    """

<<<<<<< HEAD
=======
    label = "P-ParkingNavX"
>>>>>>> 7e7480a3c640692a24ace314c17353395f1eb2fc
    FILL_THRESHOLD = 0.85
    GATE_WEIGHT = 0.3      # how much gate queue penalises a lot

    def assign(self, vehicle, campus_state):
        best_lot, best_score = None, float("inf")

        # Prefer the least-loaded gate
        gate_loads = {
            g_id: g.get("queue_length", 0)
            for g_id, g in campus_state.gates.items()
            if g.get("status", "open") == "open"
        }
        preferred_gate = min(gate_loads, key=gate_loads.get) if gate_loads else vehicle.entry_gate

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
<<<<<<< HEAD


BASELINES = {
    "first_available": FirstAvailableStrategy,
    "nearest_available": DemoNearestAvailableStrategy,
}
=======
>>>>>>> 7e7480a3c640692a24ace314c17353395f1eb2fc
