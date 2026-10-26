"""
ParkingNav-X REST API
VIT-AP University Smart Parking Digital Twin
"""
import json
import uuid
from pathlib import Path
from typing import Optional

import networkx as nx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from digital_twin.config_loader import load_campus_config
from digital_twin.db import apply_migrations, get_connection
from digital_twin.graph_service import CampusGraphService
from digital_twin.simulation.demo_strategies import ParkingNavXFullStrategy, PredictionOnlyStrategy
from digital_twin.simulation.engine import SimulationEngine
from digital_twin.simulation.scenario import ScenarioLoader
from digital_twin.simulation.strategy import FirstAvailableStrategy, NearestAvailableStrategy

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "digital_twin.db"
CAMPUS_YAML = ROOT / "configs" / "campus" / "vitap.yaml"
SCENARIO_DIR = ROOT / "configs" / "scenarios" / "vitap"

app = FastAPI(
    title="ParkingNav-X API",
    description="VIT-AP University Smart Parking Digital Twin — REST API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STRATEGIES = {
    "B1": FirstAvailableStrategy(),
    "B2": NearestAvailableStrategy(),
    "B3": PredictionOnlyStrategy(),
    "B4": ParkingNavXFullStrategy(),
    "P":  ParkingNavXFullStrategy(),
}

_graph_service = CampusGraphService()


def get_conn():
    conn = get_connection(DB_PATH)
    apply_migrations(conn)
    load_campus_config(CAMPUS_YAML, conn)
    return conn


def _build_graph(conn):
    return _graph_service.build_graph("vitap", conn)


# ── Models ────────────────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    scenario: str = "E1_normal_day"
    strategy: str = "B4"


class PredictRequest(BaseModel):
    lot_id: str
    horizon_minutes: int = 15


class OptimizeRequest(BaseModel):
    scenario: str = "E1_normal_day"
    weight_profile: str = "P1"


class RouteRequest(BaseModel):
    origin: str
    destination: str
    mode: str = "drive"  # drive | walk


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {
        "project": "ParkingNav-X",
        "campus": "VIT-AP University, Amaravati, Andhra Pradesh",
        "version": "2.0.0",
        "docs": "/docs",
    }


@app.get("/campus", tags=["Campus"])
def get_campus():
    """Return VIT-AP campus summary: gates, parking lots, roads, destinations."""
    conn = get_conn()
    gates = conn.execute(
        "SELECT gate_id, name, latitude, longitude, capacity, status FROM gates WHERE campus_id='vitap'"
    ).fetchall()
    lots = conn.execute(
        "SELECT parking_lot_id, name, latitude, longitude, total_capacity, usable_capacity, status FROM parking_lots WHERE campus_id='vitap'"
    ).fetchall()
    roads = conn.execute(
        "SELECT road_id, name, start_node_id, end_node_id, length_meters, expected_travel_time_seconds, is_walkable, is_driveable, status, geometry FROM roads WHERE campus_id='vitap'"
    ).fetchall()
    dests = conn.execute(
        "SELECT destination_id, name, category, latitude, longitude, nearest_parking_lot_ids FROM destinations WHERE campus_id='vitap'"
    ).fetchall()
    conn.close()

    return {
        "campus_id": "vitap",
        "name": "VIT-AP University",
        "location": "Amaravati, Andhra Pradesh, India",
        "gates": [
            {"id": r[0], "name": r[1], "lat": r[2], "lng": r[3], "capacity": r[4], "status": r[5]}
            for r in gates
        ],
        "parking_lots": [
            {"id": r[0], "name": r[1], "lat": r[2], "lng": r[3],
             "total_capacity": r[4], "usable_capacity": r[5], "status": r[6]}
            for r in lots
        ],
        "roads": [
            {
                "id": r[0], "name": r[1],
                "start_node_id": r[2], "end_node_id": r[3],
                "length_meters": r[4], "travel_time_seconds": r[5],
                "is_walkable": bool(r[6]), "is_driveable": bool(r[7]),
                "status": r[8],
                "geometry": json.loads(r[9]) if r[9] else [],
            }
            for r in roads
        ],
        "destinations": [
            {
                "id": r[0], "name": r[1], "category": r[2],
                "lat": r[3], "lng": r[4],
                "nearest_lots": json.loads(r[5]) if r[5] else [],
            }
            for r in dests
        ],
    }


@app.get("/campus/graph", tags=["Campus"])
def get_campus_graph():
    """Return full campus graph (nodes + edges) for routing visualization."""
    conn = get_conn()
    graph = _build_graph(conn)
    conn.close()
    return _graph_service.to_json(graph)


@app.get("/scenarios", tags=["Simulation"])
def list_scenarios():
    files = sorted(SCENARIO_DIR.glob("*.yaml"))
    return {"scenarios": [f.stem for f in files]}


@app.get("/strategies", tags=["Simulation"])
def list_strategies():
    return {
        "strategies": [
            {"code": "B1", "name": "First Available",     "description": "Naive — assigns first open lot found"},
            {"code": "B2", "name": "Nearest Available",   "description": "Picks the closest open lot by distance"},
            {"code": "B3", "name": "Prediction Only",     "description": "Avoids nearly-full lots using fill-rate forecast"},
            {"code": "P",  "name": "ParkingNav-X (full)", "description": "Prediction + gate load balancing + zone routing"},
        ]
    }


@app.post("/simulate", tags=["Simulation"])
def simulate(req: SimulateRequest):
    scenario_file = SCENARIO_DIR / f"{req.scenario}.yaml"
    if not scenario_file.exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario}' not found. Available: {available}")

    strategy = STRATEGIES.get(req.strategy)
    if not strategy:
        raise HTTPException(status_code=400, detail=f"Strategy must be one of: {list(STRATEGIES.keys())}")

    conn = get_conn()
    scenario = ScenarioLoader.load(scenario_file)
    engine = SimulationEngine()
    run_id = f"api-{uuid.uuid4().hex[:8]}"
    result = engine.run(scenario, strategy, conn, run_id=run_id)

    # Populate lot_occupancy_history so the risk engine sees real data
    if result.timesteps:
        last_ts = result.timesteps[-1]
        lot_ids = [k.replace("parking_occupancy_", "") for k in last_ts if k.startswith("parking_occupancy_")]
        for lot_id in lot_ids:
            occupied = last_ts.get(f"parking_occupancy_{lot_id}", 0)
            cap = last_ts.get(f"parking_capacity_{lot_id}", 1) or 1
            fill_rate = round(occupied / cap, 4)
            try:
                conn.execute(
                    "INSERT INTO lot_occupancy_history (lot_id, occupied_spaces, fill_rate) VALUES (?, ?, ?)",
                    (lot_id, int(occupied), fill_rate),
                )
            except Exception:
                pass
        conn.commit()

    conn.close()

    m = result.metrics
    return {
        "run_id": run_id,
        "scenario": req.scenario,
        "strategy": req.strategy,
        "campus": "vitap",
        "duration_min": scenario.duration_minutes,
        "vehicles": scenario.vehicle_count,
        "metrics": {
            "total_vehicles":       m.get("total_vehicles"),
            "parked_vehicles":      m.get("parked_vehicles"),
            "completed_trips":      m.get("completed_vehicles"),
            "overflow_events":      m.get("overflow_events_count"),
            "avg_search_time_min":  round(m["avg_search_time_min"], 2) if m.get("avg_search_time_min") is not None else None,
            "avg_wait_time_min":    round(m["avg_wait_time_min"], 2)   if m.get("avg_wait_time_min")   is not None else None,
            "avg_gate_queue":       round(m["avg_gate_queue"], 2)      if m.get("avg_gate_queue")      is not None else None,
            "peak_gate_queue":      m.get("max_gate_queue"),
        },
    }


@app.get("/compare/{scenario}", tags=["Simulation"])
def compare_strategies(scenario: str):
    """Run all 4 strategies on a scenario and return side-by-side comparison."""
    scenario_file = SCENARIO_DIR / f"{scenario}.yaml"
    if not scenario_file.exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario}' not found. Available: {available}")

    conn = get_conn()
    results = {}
    for code in ["B1", "B2", "B3", "P"]:
        strategy = STRATEGIES[code]
        sc = ScenarioLoader.load(scenario_file)
        engine = SimulationEngine()
        run_id = f"api-cmp-{uuid.uuid4().hex[:6]}"
        result = engine.run(sc, strategy, conn, run_id=run_id)
        m = result.metrics
        results[code] = {
            "overflow_events":     m.get("overflow_events_count"),
            "avg_search_time_min": round(m["avg_search_time_min"], 2) if m.get("avg_search_time_min") is not None else None,
            "avg_wait_time_min":   round(m["avg_wait_time_min"], 2)   if m.get("avg_wait_time_min")   is not None else None,
            "avg_gate_queue":      round(m["avg_gate_queue"], 2)      if m.get("avg_gate_queue")      is not None else None,
            "peak_gate_queue":     m.get("max_gate_queue"),
        }
    conn.close()
    return {"scenario": scenario, "comparison": results}


@app.get("/events", tags=["Campus"])
def get_events():
    conn = get_conn()
    rows = conn.execute(
        "SELECT event_id, event_type, name, start_time, end_time, status FROM events WHERE campus_id='vitap'"
    ).fetchall()
    conn.close()
    return {
        "campus": "vitap",
        "events": [
            {"id": r[0], "type": r[1], "name": r[2], "start": r[3], "end": r[4], "status": r[5]}
            for r in rows
        ],
    }


# ── Routing endpoints ──────────────────────────────────────────────────────────

@app.get("/route", tags=["Routing"])
def get_route(origin: str, destination: str, mode: str = "drive"):
    """
    Compute shortest path between two campus nodes.

    - **origin**: node_id (gate, parking_lot, or destination)
    - **destination**: node_id
    - **mode**: 'drive' (driveable roads only) or 'walk' (walkable paths)
    """
    conn = get_conn()
    graph = _build_graph(conn)
    conn.close()

    if origin not in graph:
        raise HTTPException(status_code=404, detail=f"Origin '{origin}' not found in campus graph")
    if destination not in graph:
        raise HTTPException(status_code=404, detail=f"Destination '{destination}' not found in campus graph")

    # Filter subgraph by mode and open status
    def edge_filter(u, v, data):
        if data.get("status") != "open":
            return False
        if mode == "drive":
            return bool(data.get("is_driveable"))
        return bool(data.get("is_walkable"))

    filtered = nx.subgraph_view(
        graph,
        filter_edge=lambda u, v, k=None: edge_filter(u, v, graph.edges[u, v])
        if not k else edge_filter(u, v, graph.edges[u, v, k])
    )

    try:
        path = nx.dijkstra_path(
            filtered, origin, destination,
            weight="weight_time_seconds"
        )
        length = nx.dijkstra_path_length(
            filtered, origin, destination,
            weight="weight_time_seconds"
        )
        dist = nx.dijkstra_path_length(
            filtered, origin, destination,
            weight="weight_distance_meters"
        )
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=422,
            detail=f"No {mode} route from '{origin}' to '{destination}'. Check road closures or mode restrictions."
        )
    except nx.NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Build geometry from path nodes
    geometry = []
    for node_id in path:
        nd = graph.nodes[node_id]
        if nd.get("latitude") and nd.get("longitude"):
            geometry.append({"lat": nd["latitude"], "lng": nd["longitude"]})

    # Build segment list
    segments = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        if graph.has_edge(u, v):
            edge = graph.edges[u, v]
            segments.append({
                "from": u,
                "to": v,
                "road_id": edge.get("road_id"),
                "time_seconds": edge.get("weight_time_seconds"),
                "distance_meters": edge.get("weight_distance_meters"),
                "is_driveable": edge.get("is_driveable"),
                "is_walkable": edge.get("is_walkable"),
            })

    return {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "path": path,
        "total_time_seconds": round(length),
        "total_distance_meters": round(dist),
        "total_time_minutes": round(length / 60, 1),
        "segments": segments,
        "geometry": geometry,
    }


@app.get("/find_parking", tags=["Routing"])
def find_parking(destination_id: str, gate_id: str = "vitap-gate-main"):
    """
    Find the best parking lot for a given destination, with vehicle + walking routes.

    Returns ranked parking options:
    - Vehicle route: gate → parking lot (driveable)
    - Walking route: parking lot → destination (walkable)
    - Scores by predicted fill rate + total travel time
    """
    conn = get_conn()
    graph = _build_graph(conn)

    # Get all open parking lots
    lots = conn.execute(
        "SELECT parking_lot_id, name, usable_capacity, status FROM parking_lots WHERE campus_id='vitap' AND status='open'"
    ).fetchall()

    # Get destination info
    dest_row = conn.execute(
        "SELECT destination_id, name, category, nearest_parking_lot_ids FROM destinations WHERE campus_id='vitap' AND destination_id=?",
        (destination_id,)
    ).fetchone()

    conn.close()

    if not dest_row:
        raise HTTPException(status_code=404, detail=f"Destination '{destination_id}' not found")

    # Get predictions for fill rates
    try:
        from digital_twin.prediction.engine import get_predictor
        predictor = get_predictor()
        preds = predictor.predict_all(15)
    except Exception:
        preds = {}

    # Preferred lots from destination config
    preferred_lots = []
    if dest_row[3]:
        try:
            preferred_lots = json.loads(dest_row[3])
        except Exception:
            preferred_lots = []

    def _try_route(origin, dest, mode):
        def edge_filter(u, v, data):
            if data.get("status") != "open":
                return False
            if mode == "drive":
                return bool(data.get("is_driveable"))
            return bool(data.get("is_walkable"))

        filtered = nx.subgraph_view(
            graph,
            filter_edge=lambda u, v, k=None: edge_filter(u, v, graph.edges[u, v])
        )
        try:
            path = nx.dijkstra_path(filtered, origin, dest, weight="weight_time_seconds")
            t = nx.dijkstra_path_length(filtered, origin, dest, weight="weight_time_seconds")
            d = nx.dijkstra_path_length(filtered, origin, dest, weight="weight_distance_meters")
            geo = []
            for n in path:
                nd = graph.nodes[n]
                if nd.get("latitude") and nd.get("longitude"):
                    geo.append({"lat": nd["latitude"], "lng": nd["longitude"]})
            return {"path": path, "time_seconds": round(t), "distance_meters": round(d), "geometry": geo}
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    options = []
    for lot in lots:
        lot_id = lot[0]
        lot_name = lot[1]
        capacity = lot[2]

        # Get fill rate prediction
        pred = preds.get(lot_id, {})
        fill_rate = pred.get("fill_rate", 0.4) if pred else 0.4
        available = int(capacity * (1 - fill_rate))

        # Compute drive route: gate → lot
        drive_route = _try_route(gate_id, lot_id, "drive")
        if not drive_route:
            continue  # skip unreachable lots

        # Compute walk route: lot → destination
        walk_route = _try_route(lot_id, destination_id, "walk")
        if not walk_route:
            continue  # skip if no walking path to destination

        # Scoring: lower is better
        # 40% drive time + 40% walk time + 20% fill penalty
        drive_t = drive_route["time_seconds"]
        walk_t = walk_route["time_seconds"]
        fill_penalty = fill_rate * 300  # up to 5 min penalty at full
        score = 0.4 * drive_t + 0.4 * walk_t + 0.2 * fill_penalty

        is_preferred = lot_id in preferred_lots
        if is_preferred:
            score *= 0.85  # 15% bonus for recommended lots

        options.append({
            "lot_id": lot_id,
            "lot_name": lot_name,
            "capacity": capacity,
            "estimated_available": max(0, available),
            "predicted_fill_rate": round(fill_rate, 3),
            "predicted_fill_pct": round(fill_rate * 100, 1),
            "is_preferred_for_destination": is_preferred,
            "drive_route": drive_route,
            "walk_route": walk_route,
            "total_time_seconds": round(drive_t + walk_t),
            "total_time_minutes": round((drive_t + walk_t) / 60, 1),
            "drive_time_minutes": round(drive_t / 60, 1),
            "walk_time_minutes": round(walk_t / 60, 1),
            "score": round(score, 1),
            "recommendation_reason": _explain(lot_name, fill_rate, drive_t, walk_t, is_preferred),
        })

    options.sort(key=lambda x: x["score"])

    return {
        "destination_id": destination_id,
        "destination_name": dest_row[1],
        "destination_category": dest_row[2],
        "gate_used": gate_id,
        "options_count": len(options),
        "best": options[0] if options else None,
        "options": options[:5],  # top 5
        "note": "[SYNTHETIC] Fill rates from XGBoost model trained on synthetic data. Not real occupancy.",
    }


def _explain(lot_name, fill_rate, drive_t, walk_t, preferred):
    parts = []
    if preferred:
        parts.append("Nearest to your destination")
    if fill_rate < 0.5:
        parts.append(f"{(1-fill_rate)*100:.0f}% capacity available")
    elif fill_rate < 0.8:
        parts.append(f"Moderately occupied ({fill_rate*100:.0f}%)")
    else:
        parts.append(f"Near capacity ({fill_rate*100:.0f}%) — consider alternatives")
    parts.append(f"{round(drive_t/60, 1)} min drive + {round(walk_t/60, 1)} min walk")
    return ". ".join(parts)


# ── Prediction endpoints ───────────────────────────────────────────────────────

@app.get("/predict/occupancy", tags=["Prediction"])
def predict_occupancy(lot_id: str = "vitap-lot-academic-main", horizon_minutes: int = 15):
    """Predict occupancy for a parking lot at horizon_minutes from now."""
    try:
        from digital_twin.prediction.engine import get_predictor
        predictor = get_predictor()
        result = predictor.predict(lot_id=lot_id, horizon_minutes=horizon_minutes)
        return {
            "lot_id": lot_id,
            "horizon_minutes": horizon_minutes,
            "predicted_fill_rate": round(float(result["fill_rate"]), 4),
            "lower_90": round(float(result["lower_90"]), 4),
            "upper_90": round(float(result["upper_90"]), 4),
            "model": result["model"],
            "proactive_alert": bool(result["fill_rate"] >= 0.90),
            "data_source": "[SYNTHETIC] Trained on synthetic demand data",
        }
    except Exception as e:
        return {"lot_id": lot_id, "error": str(e), "predicted_fill_rate": None}


@app.get("/predict/all", tags=["Prediction"])
def predict_all_lots(horizon_minutes: int = 15):
    """Predict occupancy for all VIT-AP parking lots."""
    lot_ids = [
        "vitap-lot-academic-main",
        "vitap-lot-hostel",
        "vitap-lot-admin-visitor",
        "vitap-lot-sports",
        "vitap-lot-overflow",
    ]
    try:
        from digital_twin.prediction.engine import get_predictor
        predictor = get_predictor()
        predictions = []
        for lot_id in lot_ids:
            r = predictor.predict(lot_id=lot_id, horizon_minutes=horizon_minutes)
            predictions.append({
                "lot_id": lot_id,
                "predicted_fill_rate": round(float(r["fill_rate"]), 4),
                "lower_90": round(float(r["lower_90"]), 4),
                "upper_90": round(float(r["upper_90"]), 4),
                "proactive_alert": bool(r["fill_rate"] >= 0.90),
                "model": r.get("model", "unknown"),
            })
        return {
            "horizon_minutes": horizon_minutes,
            "predictions": predictions,
            "data_source": "[SYNTHETIC] XGBoost model on synthetic demand data",
        }
    except Exception as e:
        return {"error": str(e), "predictions": []}


# ── Risk endpoints ─────────────────────────────────────────────────────────────

@app.get("/risk/current", tags=["Risk"])
def get_current_risk():
    """Return current risk scores across all risk dimensions."""
    try:
        from digital_twin.risk.engine import RiskEngine
        conn = get_conn()
        re = RiskEngine(conn)
        scores = re.compute_all()
        conn.close()
        return {"status": "ok", "risks": scores}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/risk/forecast", tags=["Risk"])
def get_risk_forecast(horizon: int = 15):
    """Return forecasted risk scores at horizon minutes ahead."""
    try:
        from digital_twin.risk.engine import RiskEngine
        from digital_twin.prediction.engine import get_predictor
        conn = get_conn()
        predictor = get_predictor()
        re = RiskEngine(conn, predictor=predictor)
        scores = re.compute_forecast(horizon_minutes=horizon)
        conn.close()
        return {"horizon_minutes": horizon, "risks": scores}
    except Exception as e:
        return {"horizon_minutes": horizon, "error": str(e)}


# ── Optimization endpoints ─────────────────────────────────────────────────────

@app.post("/optimize", tags=["Optimization"])
def optimize(req: OptimizeRequest):
    try:
        from digital_twin.optimization.engine import MultiObjectiveOptimizer
        conn = get_conn()
        opt = MultiObjectiveOptimizer(weight_profile=req.weight_profile)
        recommendations = opt.generate_recommendations(conn)
        conn.close()
        return {
            "scenario": req.scenario,
            "weight_profile": req.weight_profile,
            "recommendations": recommendations,
        }
    except Exception as e:
        return {"error": str(e), "recommendations": []}


@app.get("/recommendations", tags=["Optimization"])
def get_recommendations():
    """Get latest proactive parking recommendations."""
    try:
        from digital_twin.optimization.engine import MultiObjectiveOptimizer
        conn = get_conn()
        opt = MultiObjectiveOptimizer()
        recs = opt.generate_recommendations(conn)
        conn.close()
        return {"recommendations": recs, "count": len(recs)}
    except Exception as e:
        return {"recommendations": [], "error": str(e)}
