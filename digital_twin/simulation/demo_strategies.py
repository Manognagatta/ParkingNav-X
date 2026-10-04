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


BASELINES = {
    "first_available": FirstAvailableStrategy,
    "nearest_available": DemoNearestAvailableStrategy,
}
