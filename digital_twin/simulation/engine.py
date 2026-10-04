"""
Simulation Engine: a small discrete-event simulator built on a heapq.

How it works: every future happening is an event (time, kind, vehicle) in a
priority queue. The engine pops the earliest event, updates the campus
state and schedules what follows. The clock jumps from event to event, so
1,000 vehicles take well under a second.

One vehicle: ARRIVE at its gate -> FIFO gate queue (one vehicle admitted
every `service_s` seconds) -> ADMIT: the strategy picks a lot -> drive ->
REACH_LOT: park if there is really a space, else ask the strategy again (up
to `max_lots_tried` lots) or leave -> stay for the dwell time -> LEAVE.

The engine, not the strategy, enforces the rules: closed or full lots take
nobody, closed gates admit nobody, closed roads are not driven on.

Randomness: one numpy SeedSequence per run, split into three streams
(demand, dwell, forecast noise). The same seed gives the same vehicles for
every strategy and forecast-error level, so comparisons are fair. Decision
latency is wall-clock time, so it is reported apart from the seeded results.
"""

import heapq
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import networkx as nx
import numpy as np

from digital_twin.graph_service import CampusGraphService
from digital_twin.simulation.scenario import ScenarioConfig
from digital_twin.simulation.strategy import AllocationStrategy, CampusState, Vehicle
from digital_twin.twin_service import DigitalTwinService

# Why a vehicle's visit ended.
PARKED = "parked"
LEFT_ALL_FULL = "left_all_full"  # every lot it tried was full
LEFT_NO_SPACE = "left_no_space"  # no lot showed space (or the strategy gave an invalid lot)
LEFT_NO_ROUTE = "left_no_route"  # no drivable route from its gate to any lot


def _rate_at(profile: dict, minutes: np.ndarray) -> np.ndarray:
    """Relative arrival rate at each minute (constant, or linear between points)."""
    if profile["type"] == "constant":
        return np.full(len(minutes), float(profile["rate"]))
    points = sorted(profile["points"], key=lambda p: p["time"])
    return np.interp(minutes, [p["time"] for p in points], [p["rate"] for p in points])


@dataclass
class SimulationResult:
    run_id: str
    campus_id: str
    scenario_id: str
    random_seed: int
    strategy_name: str
    vehicles: list = field(default_factory=list)  # one row per vehicle
    timesteps: list = field(default_factory=list)  # campus state at the end of each minute
    overflow_events: list = field(default_factory=list)  # every failed parking attempt
    summary: dict = field(default_factory=dict)
    decision_latency_ms: list = field(default_factory=list)


@dataclass
class _Car:
    vehicle: Vehicle
    arrival_s: float
    admit_s: Optional[float] = None
    parked_s: Optional[float] = None
    target: Optional[str] = None
    parked_lot: Optional[str] = None
    tried: set = field(default_factory=set)
    drive_m: float = 0.0
    dwell_s: float = 0.0
    outcome: Optional[str] = None


class SimulationEngine:
    def __init__(self, graph_service=None, twin_service=None):
        self.graph_service = graph_service or CampusGraphService()
        self.twin_service = twin_service or DigitalTwinService()

    def run(
        self,
        scenario: ScenarioConfig,
        strategy: AllocationStrategy,
        conn,
        run_id: Optional[str] = None,
        persist: bool = True,
    ) -> SimulationResult:
        """Simulate one scenario with one strategy. Same seed -> same result.
        persist=False skips the DB writes (used for fast multi-seed experiments)."""
        self.sc, self.strategy = scenario, strategy
        streams = np.random.SeedSequence(scenario.random_seed).spawn(3)
        self.rng_demand, self.rng_dwell, self.rng_noise = map(np.random.default_rng, streams)
        self._load_campus(conn)

        self.queue, self.seq = [], 0
        self.overflow, self.latency_ms, self.timesteps = [], [], []
        self.minute_overflow, self.minute_road_load = set(), dict.fromkeys(self.roads, 0)
        self.violations = 0

        cars = self._generate_arrivals()
        for car in cars:
            self._push(car.arrival_s, "arrive", car)
        minute = 0
        while self.queue:
            t, _, kind, car = heapq.heappop(self.queue)
            while t >= (minute + 1) * 60:  # record every minute that ended before t
                self._record_minute(minute)
                minute += 1
            getattr(self, f"_on_{kind}")(car, t)
        if cars:
            self._record_minute(minute)

        rows = [self._row(c) for c in cars]
        name = f"{strategy.__class__.__module__}.{strategy.__class__.__name__}"
        result = SimulationResult(
            run_id=run_id or f"{scenario.scenario_id}-{uuid.uuid4().hex[:8]}",
            campus_id=scenario.campus_id,
            scenario_id=scenario.scenario_id,
            random_seed=scenario.random_seed,
            strategy_name=name,
            vehicles=rows,
            timesteps=self.timesteps,
            overflow_events=self.overflow,
            summary=self._summary(rows),
            decision_latency_ms=self.latency_ms,
        )
        if persist:
            self._persist(result, cars, conn)
        return result

    # ---------------------------------------------------------------- campus
    def _load_campus(self, conn) -> None:
        """Read gates, lots, roads, destinations and the event from the DB once,
        apply the scenario's closures, and precompute shortest drives/walks."""
        cid = self.sc.campus_id
        closed = {"closed_gates": [], "closed_parking_lots": [], "closed_roads": [],
                  **(self.sc.availability_overrides or {})}

        def rows(table, key):
            return conn.execute(f"SELECT * FROM {table} WHERE campus_id=? ORDER BY {key}", (cid,))

        self.gates = {}
        for r in rows("gates", "gate_id"):
            per_hour = json.loads(r["metadata"] or "{}").get("peak_throughput_per_hour")
            self.gates[r["gate_id"]] = {
                "open": r["status"] == "open" and r["gate_id"] not in closed["closed_gates"],
                "capacity": r["capacity"],
                # seconds to admit one vehicle: configured vehicles/hour, else capacity/minute
                "service_s": 3600 / per_hour if per_hour else 60 / r["capacity"],
                "queue": 0,
                "free_at": 0.0,
            }
        self.lots = {
            r["parking_lot_id"]: {
                "status": "closed" if r["parking_lot_id"] in closed["closed_parking_lots"] else r["status"],
                "usable_capacity": r["usable_capacity"],
                "occupied_spaces": 0,
                "zone": r["zone"],
            }
            for r in rows("parking_lots", "parking_lot_id")
        }
        self.peak = dict.fromkeys(self.lots, 0)
        self.roads = [r["road_id"] for r in rows("roads", "road_id")]
        self.destinations = [
            (r["destination_id"], json.loads(r["nearest_gate_ids"] or "[]"),
             json.loads(r["nearest_parking_lot_ids"] or "[]"))
            for r in rows("destinations", "destination_id")
        ]

        # Event: demand multiplier and affected zones (1.0 and none without an event).
        self.multiplier, self.zones = 1.0, []
        ec = self.sc.event_conditions
        if ec:
            event = None if ec.get("ad_hoc") else conn.execute(
                "SELECT * FROM events WHERE campus_id=? AND event_id=?", (cid, ec.get("event_id"))
            ).fetchone()
            self.multiplier = ec.get("demand_multiplier") or event["expected_demand_multiplier"]
            self.zones = ec.get("affected_zones") or (
                json.loads(event["affected_zones"] or "[]") if event else [])

        # Shortest drives (drivable, open roads only) and walks (walkable edges).
        graph = self.graph_service.build_graph(cid, conn)
        drive, walk = nx.Graph(), nx.Graph()
        drive.add_nodes_from(graph.nodes)  # keep every node, even if its roads are closed
        for u, v, d in graph.edges(data=True):
            attrs = {k: d.get(k) for k in ("weight_time_seconds", "weight_distance_meters", "road_id")}
            if d.get("is_walkable", True):
                walk.add_edge(u, v, **attrs)
            if d.get("is_driveable", True) and d.get("status", "open") == "open" \
                    and d.get("road_id") not in closed["closed_roads"]:
                drive.add_edge(u, v, **attrs)
        self.drive_s, self.drive_path = {}, {}
        for src in [*self.gates, *self.lots]:
            secs, paths = nx.single_source_dijkstra(drive, src, weight="weight_time_seconds") \
                if src in drive else ({}, {})
            reach = [lot for lot in self.lots if lot in secs and lot != src]
            self.drive_s[src] = {lot: secs[lot] for lot in reach}
            self.drive_path[src] = {lot: paths[lot] for lot in reach}
        dest_ids = {d[0] for d in self.destinations}
        self.walk_m = {
            lot: {d: m for d, m in nx.single_source_dijkstra_path_length(walk, lot, weight="weight_distance_meters").items()
                  if d in dest_ids} if lot in walk else {}
            for lot in self.lots
        }
        self.drive = drive

    def _in_event_zone(self, lot_ids) -> bool:
        return bool(self.zones) and ("all_zones" in self.zones or any(
            lot in self.zones or self.lots.get(lot, {}).get("zone") in self.zones for lot in lot_ids))

    # ---------------------------------------------------------------- demand
    def _generate_arrivals(self) -> list:
        """Vehicles for the run. Arrival minutes follow the arrival profile; inside
        the event window the rate (and so the vehicle count) is multiplied by the
        event's multiplier, and destinations near the event's zone are picked that
        many times more often."""
        sc, rng = self.sc, self.rng_demand
        open_gates = [g for g, d in self.gates.items() if d["open"]]
        minutes = np.arange(sc.duration_minutes, dtype=float)
        base = _rate_at(sc.arrival_rate_profile, minutes + 0.5)
        weights = base.copy()
        ec = sc.event_conditions
        if ec:
            start, end = ec.get("start_minute", 0), ec.get("end_minute", sc.duration_minutes)
            weights[(minutes >= start) & (minutes < end)] *= self.multiplier
        if base.sum() <= 0 or not open_gates or not self.destinations:
            return []
        count = round(sc.vehicle_count * weights.sum() / base.sum())

        arrivals = np.sort((rng.choice(minutes, count, p=weights / weights.sum())
                            + rng.random(count)) * 60)
        dest_w = np.array([self.multiplier if self._in_event_zone(lots) else 1.0
                           for _, _, lots in self.destinations])
        picks = rng.choice(len(self.destinations), count, p=dest_w / dest_w.sum())
        cars = []
        for i, (t, di) in enumerate(zip(arrivals, picks)):
            dest_id, dest_gates, _ = self.destinations[di]
            gates = [g for g in open_gates if g in dest_gates] or open_gates
            gate = gates[rng.integers(len(gates))]
            cars.append(_Car(Vehicle(f"{sc.scenario_id}-v{i}", gate, dest_id, int(t // 60)), t))
        return cars

    # ---------------------------------------------------------------- events
    def _push(self, t, kind, car):
        heapq.heappush(self.queue, (t, self.seq, kind, car))
        self.seq += 1  # tie-breaker: same-time events keep their order

    def _on_arrive(self, car, now):
        gate = self.gates[car.vehicle.entry_gate]
        gate["queue"] += 1
        gate["free_at"] = max(now, gate["free_at"]) + gate["service_s"]  # FIFO
        self._push(gate["free_at"], "admit", car)

    def _on_admit(self, car, now):
        self.gates[car.vehicle.entry_gate]["queue"] -= 1
        car.admit_s = now
        car.vehicle.current_node = car.vehicle.entry_gate
        if not self.drive_s.get(car.vehicle.entry_gate):
            car.outcome = LEFT_NO_ROUTE
        else:
            self._go_to_next_lot(car, now, LEFT_NO_SPACE)

    def _on_reach_lot(self, car, now):
        lot_id = car.target
        lot = self.lots[lot_id]
        car.vehicle.current_node = lot_id
        car.tried.add(lot_id)
        if lot["status"] == "open" and lot["occupied_spaces"] < lot["usable_capacity"]:
            lot["occupied_spaces"] += 1
            self.peak[lot_id] = max(self.peak[lot_id], lot["occupied_spaces"])
            car.parked_lot, car.parked_s = lot_id, now
            car.dwell_s = 60 * self.rng_dwell.uniform(self.sc.dwell_minutes["min"],
                                                      self.sc.dwell_minutes["max"])
            self._push(now + car.dwell_s, "leave", car)
            return
        self._log_overflow(lot_id, now)  # it was full
        if len(car.tried) >= self.sc.max_lots_tried:
            car.outcome = LEFT_ALL_FULL
        else:
            self._go_to_next_lot(car, now, LEFT_ALL_FULL)

    def _on_leave(self, car, now):
        self.lots[car.parked_lot]["occupied_spaces"] -= 1
        car.outcome = PARKED

    def _go_to_next_lot(self, car, now, outcome_if_none):
        """Ask the strategy for a lot (timed), then drive there. A missing, repeated
        or unreachable answer is a failed attempt and the vehicle leaves."""
        view = self._strategy_view(car)
        start = time.perf_counter_ns()  # time only the strategy's decision
        answer = self.strategy.assign(car.vehicle, view)
        self.latency_ms.append((time.perf_counter_ns() - start) / 1e6)
        lot = answer.parking_lot_id if answer else None
        here = car.vehicle.current_node
        if lot is None or lot in car.tried or lot not in self.drive_s[here]:
            self._log_overflow(lot, now)
            car.outcome = outcome_if_none
            return
        path = self.drive_path[here][lot]
        for a, b in zip(path, path[1:]):
            edge = self.drive.edges[a, b]
            car.drive_m += edge["weight_distance_meters"]
            if edge["road_id"] in self.minute_road_load:
                self.minute_road_load[edge["road_id"]] += 1
        car.target = lot
        self._push(now + self.drive_s[here][lot], "reach_lot", car)

    def _strategy_view(self, car) -> CampusState:
        """What the strategy sees. With forecast error e > 0, each lot's occupancy
        is shown as occupancy * (1 + Normal(0, e)), kept within [0, capacity].
        The engine itself always uses the true occupancy."""
        error = self.sc.prediction_error_injection_level
        lots = {}
        for lot_id, lot in self.lots.items():
            seen = lot["occupied_spaces"]
            if error > 0:
                noisy = seen * (1 + self.rng_noise.normal(0, error))
                seen = int(np.clip(round(noisy), 0, lot["usable_capacity"]))
            lots[lot_id] = {**lot, "occupied_spaces": seen}
        gates = {g: {"status": "open" if d["open"] else "closed", "capacity": d["capacity"],
                     "queue_length": d["queue"]} for g, d in self.gates.items()}
        return CampusState(self.sc.campus_id, self.drive, lots, gates, {}, set(car.tried))

    def _log_overflow(self, lot_id, now):
        self.overflow.append({"tick": int(now // 60), "parking_lot_id": lot_id})
        if lot_id in self.lots:
            self.minute_overflow.add(lot_id)

    # ------------------------------------------------------------- recording
    def _record_minute(self, minute):
        for lot in self.lots.values():
            self.violations += not 0 <= lot["occupied_spaces"] <= lot["usable_capacity"]
        self.timesteps.append({
            "tick": minute,
            **{f"gate_queue_{g}": d["queue"] for g, d in self.gates.items()},
            **{f"parking_occupancy_{p}": d["occupied_spaces"] for p, d in self.lots.items()},
            **{f"overflow_{p}": p in self.minute_overflow for p in self.lots},
            **{f"road_load_{r}": n for r, n in self.minute_road_load.items()},
        })
        self.minute_overflow, self.minute_road_load = set(), dict.fromkeys(self.roads, 0)

    def _row(self, car) -> dict:
        v = car.vehicle
        walk = self.walk_m.get(car.parked_lot, {}).get(v.destination_id)
        return {
            "vehicle_id": v.vehicle_id,
            "entry_gate": v.entry_gate,
            "destination_id": v.destination_id,
            "arrival_minute": round(car.arrival_s / 60, 3),
            "waiting_time_seconds": round(car.admit_s - car.arrival_s, 2) if car.admit_s else 0,
            "assigned_lot_id": car.parked_lot,
            "lots_tried": len(car.tried),
            "search_time_seconds": round(car.parked_s - car.admit_s, 2) if car.parked_s else 0,
            "travel_distance_meters": round(car.drive_m, 1),
            "walk_distance_meters": None if walk is None else round(walk, 1),
            "dwell_minutes": round(car.dwell_s / 60, 2),
            "outcome": car.outcome,
            "final_state": "completed",
        }

    def _summary(self, rows) -> dict:
        n = len(rows)
        parked = [r for r in rows if r["outcome"] == PARKED]

        def mean(values, digits=2):
            values = [x for x in values if x is not None]
            return round(float(np.mean(values)), digits) if values else None

        return {
            "total_vehicles": n,
            "completed_vehicles": n,
            "parked_vehicles": len(parked),
            "failed_vehicles": n - len(parked),
            "turned_away_pct": round(100 * (n - len(parked)) / n, 2) if n else 0.0,
            "mean_gate_wait_s": mean(r["waiting_time_seconds"] for r in rows),
            "mean_search_time_s": mean(r["search_time_seconds"] for r in parked),
            "mean_lots_tried": mean(r["lots_tried"] for r in parked),
            "mean_walk_m": mean(r["walk_distance_meters"] for r in parked),
            "peak_occupancy_pct": round(max(
                (100 * self.peak[p] / d["usable_capacity"]
                 for p, d in self.lots.items() if d["usable_capacity"]), default=0), 2),
            "peak_occupancy_by_lot": dict(self.peak),
            "capacity_violations": self.violations,
            "decision_latency_mean_ms": mean(self.latency_ms, 4),
        }

    def _persist(self, result, cars, conn):
        """Write the final campus state and the run's metrics to the DB."""
        now = datetime.now(timezone.utc).isoformat()
        cid, twin = result.campus_id, self.twin_service
        conn.execute(
            "INSERT INTO simulation_runs (run_id, campus_id, scenario_id, random_seed, strategy_name,"
            " configuration_version, started_at, completed_at, status, metrics_json,"
            " overflow_events_json) VALUES (?, ?, ?, ?, ?, (SELECT active_configuration_version"
            " FROM campuses WHERE campus_id=?), ?, ?, 'completed', ?, ?)",
            (result.run_id, cid, result.scenario_id, result.random_seed, result.strategy_name,
             cid, now, now, json.dumps(result.summary), json.dumps(result.overflow_events)),
        )
        args = ("simulation", "SYNTHETIC", now, conn)
        for lot_id, lot in self.lots.items():
            twin.update_parking_state(cid, lot_id, lot["occupied_spaces"], *args, commit=False)
        for gate_id, gate in self.gates.items():
            twin.update_gate_state(cid, gate_id, gate["queue"], 0, *args, commit=False)
        for road_id in self.roads:
            twin.update_road_state(cid, road_id, 0, "free", *args, commit=False)
        for car in cars:
            twin.update_vehicle_state(
                cid, car.vehicle.vehicle_id, "completed", "simulation", now, conn,
                destination_id=car.vehicle.destination_id,
                assigned_parking_lot_id=car.parked_lot, commit=False,
            )
        conn.commit()
