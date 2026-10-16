# ParkingNav-X

**Event-Aware Predictive Digital Twin for Proactive Campus Parking and Mobility Management**

VIT-AP University, Amaravati, Andhra Pradesh — B.Tech CSE Capstone Project

## Overview

ParkingNav-X is a complete smart-parking research prototype that:
1. **Digital Twin** — Models VIT-AP campus (gates, roads, parking lots, destinations) with real OpenStreetMap coordinates
2. **Simulation Engine** — Discrete-time vehicle simulation with BPR travel times, gate queuing, event-aware demand
3. **Prediction Engine (Stage 6)** — Occupancy forecasting with conformal prediction intervals (Historical, Moving Average, XGBoost)
4. **Risk Engine (Stage 7)** — Multi-dimensional risk scoring (overflow, gate queue, congestion, search, network)
5. **Optimization Engine (Stage 8)** — Multi-objective optimizer with proactive recommendations (J = Σ wᵢ·costᵢ)
6. **REST API** — FastAPI backend serving all stages
7. **React Frontend** — Live dashboard with Leaflet map, Recharts metrics, scenario controls

## Folder Layout

```
ParkingNav-X/
├── digital_twin/
│   ├── api/              FastAPI REST endpoints
│   ├── simulation/       Discrete-time simulation engine + strategies
│   ├── prediction/       Stage 6: occupancy forecasting
│   ├── risk/             Stage 7: risk scoring
│   ├── optimization/     Stage 8: multi-objective optimizer
│   ├── config_loader.py  Loads campus YAML into SQLite
│   └── db.py             SQLite connection + migration runner
├── configs/
│   ├── campus/           Campus definitions: vitap.yaml (real VIT-AP campus)
│   └── scenarios/        Simulation scenarios (vitap/)
├── data/                 Synthetic multi-year demand CSV, events data
├── migrations/           SQLite schema migrations (applied automatically)
├── scripts/              run_batch.py, run_all_experiments.py, generate_results_summary.py
├── tests/                Pytest test suite
├── frontend/             React + Vite + Tailwind + Leaflet + Recharts
├── experiments/          results/ (committed), runs/ (per-run CSVs, not committed)
└── docs/                 Reports and roadmap
```

## Quick Start

### Backend

```bash
cd ParkingNav-X
pip install -r requirements.txt
uvicorn digital_twin.api.app:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173

### Docker (full stack)

```bash
docker compose up --build
```

## Strategies

| Code | Name | Description |
|------|------|-------------|
| B1 | First Available | Naïve — assigns first open lot found |
| B2 | Nearest Available | Picks the closest open lot by distance |
| B3 | Prediction Only | Avoids nearly-full lots using fill-rate forecast |
| B4 | ParkingNav-X (full) | Prediction + gate load balancing + zone routing |

## Key Metrics

- `avg_parking_search_time` — average time spent searching for a space
- `avg_waiting_time` — average gate queue wait time
- `avg_gate_queue` / `max_gate_queue` — gate congestion
- `overflow_events` — number of times all lots were full
- `total_travel_time` — sum of all vehicle travel times

## Campus: VIT-AP University

- **Gates**: Main Gate (16.4912°N, 80.5018°E), Visitor Gate (16.4895°N, 80.4960°E)
- **Parking Lots**: Academic Main (120), Hostel (180), Admin/Visitor (80), Sports (100), Overflow (60)
- **Total Capacity**: 540 spaces (usable: 472)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | System info |
| GET | `/campus` | Campus summary |
| GET | `/scenarios` | Available scenarios |
| GET | `/strategies` | Available strategies |
| POST | `/simulate` | Run simulation |
| GET | `/compare/{scenario}` | Compare all strategies |
| GET | `/events` | Campus events |
| GET | `/predict/occupancy` | Predict lot occupancy |
| GET | `/predict/all` | Predict all lots |
| GET | `/risk/current` | Current risk scores |
| GET | `/risk/forecast` | Forecasted risk scores |
| POST | `/optimize` | Generate recommendations |
| GET | `/recommendations` | Latest recommendations |
