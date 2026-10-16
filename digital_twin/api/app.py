"""
ParkingNav-X REST API
VIT-AP University Smart Parking Digital Twin
"""
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from digital_twin.config_loader import load_campus_config
from digital_twin.db import apply_migrations, get_connection
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
    "P":  ParkingNavXFullStrategy(),   # alias
}


def get_conn():
    conn = get_connection(DB_PATH)
    apply_migrations(conn)
    load_campus_config(CAMPUS_YAML, conn)
    return conn


# ── Models ────────────────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    scenario: str = "peak_hour"
    strategy: str = "B4"


class PredictRequest(BaseModel):
    lot_id: str
    horizon_minutes: int = 15


class OptimizeRequest(BaseModel):
    scenario: str = "peak_hour"
    weight_profile: str = "P1"


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
    """Return VIT-AP campus summary: gates, parking lots, roads."""
    conn = get_conn()
    gates = conn.execute("SELECT gate_id, name, capacity, status FROM gates WHERE campus_id='vitap'").fetchall()
    lots  = conn.execute("SELECT parking_lot_id, name, total_capacity, usable_capacity, status FROM parking_lots WHERE campus_id='vitap'").fetchall()
    roads = conn.execute("SELECT road_id, name, length_meters, status FROM roads WHERE campus_id='vitap'").fetchall()
    dests = conn.execute("SELECT destination_id, name, category FROM destinations WHERE campus_id='vitap'").fetchall()
    conn.close()
    return {
        "campus_id": "vitap",
        "name": "VIT-AP University",
        "location": "Amaravati, Andhra Pradesh, India",
        "gates": [{"id": r[0], "name": r[1], "capacity": r[2], "status": r[3]} for r in gates],
        "parking_lots": [{"id": r[0], "name": r[1], "total_capacity": r[2], "usable_capacity": r[3], "status": r[4]} for r in lots],
        "roads": [{"id": r[0], "name": r[1], "length_m": r[2], "status": r[3]} for r in roads],
        "destinations": [{"id": r[0], "name": r[1], "category": r[2]} for r in dests],
    }


@app.get("/scenarios", tags=["Simulation"])
def list_scenarios():
    """List all available simulation scenarios."""
    files = sorted(SCENARIO_DIR.glob("*.yaml"))
    return {"scenarios": [f.stem for f in files]}


@app.get("/strategies", tags=["Simulation"])
def list_strategies():
    """List all available parking allocation strategies."""
    return {
        "strategies": [
            {"code": "B1", "name": "First Available",     "description": "Naive — assigns first open lot found"},
            {"code": "B2", "name": "Nearest Available",   "description": "Picks the closest open lot by distance"},
            {"code": "B3", "name": "Prediction Only",     "description": "Avoids nearly-full lots using fill-rate forecast"},
            {"code": "B4", "name": "ParkingNav-X (full)", "description": "Prediction + gate load balancing + zone routing"},
        ]
    }


@app.post("/simulate", tags=["Simulation"])
def simulate(req: SimulateRequest):
    """
    Run a parking simulation scenario with a chosen strategy.

    - **scenario**: e.g. `peak_hour`, `E1_normal_day`, `E4_gate_closure`
    - **strategy**: `B1`, `B2`, `B3`, or `B4`
    """
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
    """
    Run all 4 strategies on a scenario and return side-by-side comparison.
    """
    scenario_file = SCENARIO_DIR / f"{scenario}.yaml"
    if not scenario_file.exists():
        available = [f.stem for f in SCENARIO_DIR.glob("*.yaml")]
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario}' not found. Available: {available}")

    conn = get_conn()
    results = {}
    for code in ["B1", "B2", "B3", "B4"]:
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
    """List all campus events loaded from vitap_events.csv."""
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


# ── Prediction endpoints ───────────────────────────────────────────────────────

@app.get("/predict/occupancy", tags=["Prediction"])
def predict_occupancy(lot_id: str = "vitap-lot-academic-main", horizon_minutes: int = 15):
    """
    Predict occupancy for a parking lot at horizon_minutes from now.
    Returns predicted fill rate with 90% conformal prediction interval.
    """
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
            })
        return {"horizon_minutes": horizon_minutes, "predictions": predictions}
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
    """
    Run multi-objective optimization to generate parking recommendations.

    Weight profiles:
    - P1: minimize wait time (w1=0.4, w2=0.3, w3=0.2, w4=0.05, w5=0.05)
    - P2: balanced (equal weights)
    - P3: minimize overflow penalty (w5=0.5)
    """
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
