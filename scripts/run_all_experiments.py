"""
Run all ParkingNav-X experiments: E1–E7 × B1–B4 × N seeds.

Usage:
    python scripts/run_all_experiments.py [--seeds 5] [--workers 4]

Outputs:
    experiments/results/summary_table.md
    experiments/results/all_results.json
    experiments/runs/*.csv  (per-run metrics)
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT))

from digital_twin.db import get_connection, apply_migrations
from digital_twin.config_loader import load_campus_config
from digital_twin.simulation.engine import SimulationEngine
from digital_twin.simulation.scenario import ScenarioLoader
from digital_twin.simulation.strategy import FirstAvailableStrategy, NearestAvailableStrategy
from digital_twin.simulation.demo_strategies import PredictionOnlyStrategy, ParkingNavXFullStrategy

DB_PATH = ROOT / "digital_twin.db"
CAMPUS_YAML = ROOT / "configs" / "campus" / "vitap.yaml"
SCENARIO_DIR = ROOT / "configs" / "scenarios" / "vitap"
RESULTS_DIR = ROOT / "experiments" / "results"
RUNS_DIR    = ROOT / "experiments" / "runs"

STRATEGIES = {
    "B1": FirstAvailableStrategy,
    "B2": NearestAvailableStrategy,
    "B3": PredictionOnlyStrategy,
    "B4": ParkingNavXFullStrategy,
}

# The experiment matrix
EXPERIMENTS = [
    "E1_normal_day",
    "E2_event_placement",
    "E3_lot_closure",
    "E4_gate_closure",
    "E5_noise_0",
    "E6_ablation",
    "peak_hour",
]


def get_conn():
    # Use in-memory DB per run to avoid cross-thread UNIQUE constraint and locking errors.
    # Each run is self-contained; results are captured in the returned dict.
    conn = get_connection(":memory:")
    apply_migrations(conn)
    load_campus_config(CAMPUS_YAML, conn)
    return conn


def run_one(scenario: str, strategy_code: str, seed: int) -> dict:
    """Run a single (scenario, strategy, seed) experiment."""
    scenario_file = SCENARIO_DIR / f"{scenario}.yaml"
    if not scenario_file.exists():
        return {"scenario": scenario, "strategy": strategy_code, "seed": seed, "error": "scenario not found"}

    conn = get_conn()
    strategy_cls = STRATEGIES.get(strategy_code)
    if strategy_cls is None:
        conn.close()
        return {"scenario": scenario, "strategy": strategy_code, "seed": seed, "error": "unknown strategy"}

    strategy = strategy_cls()
    sc = ScenarioLoader.load(scenario_file)
    engine = SimulationEngine()
    run_id = f"{scenario}-{strategy_code}-s{seed}"

    t0 = time.time()
    try:
        result = engine.run(sc, strategy, conn, run_id=run_id)
        m = result.metrics
        elapsed = time.time() - t0
        conn.close()
        return {
            "run_id": run_id,
            "scenario": scenario,
            "strategy": strategy_code,
            "seed": seed,
            "elapsed_s": round(elapsed, 2),
            "total_vehicles": m.get("total_vehicles"),
            "parked_vehicles": m.get("parked_vehicles"),
            "overflow_events": m.get("overflow_events_count"),
            "avg_search_time_min": m.get("avg_search_time_min"),
            "avg_wait_time_min": m.get("avg_wait_time_min"),
            "avg_gate_queue": m.get("avg_gate_queue"),
            "max_gate_queue": m.get("max_gate_queue"),
        }
    except Exception as e:
        conn.close()
        return {"run_id": run_id, "scenario": scenario, "strategy": strategy_code, "seed": seed, "error": str(e)}


def build_summary_table(results: list) -> str:
    """Build a Markdown summary table from all results."""
    from collections import defaultdict
    import statistics

    # Group by (scenario, strategy)
    groups = defaultdict(list)
    for r in results:
        if "error" not in r:
            groups[(r["scenario"], r["strategy"])].append(r)

    lines = ["# ParkingNav-X Experiment Results\n",
             "| Scenario | Strategy | Avg Search (min) | Avg Wait (min) | Avg Queue | Overflow Events |",
             "|----------|----------|------------------|----------------|-----------|-----------------|"]

    scenarios = sorted(set(r["scenario"] for r in results))
    for scenario in scenarios:
        for strategy in ["B1", "B2", "B3", "B4"]:
            runs = groups.get((scenario, strategy), [])
            if not runs:
                continue

            def avg(key):
                vals = [r[key] for r in runs if r.get(key) is not None]
                return round(statistics.mean(vals), 2) if vals else "—"

            highlight = "**" if strategy == "B4" else ""
            lines.append(
                f"| {scenario.replace('_', ' ')} | {highlight}{strategy}{highlight} "
                f"| {avg('avg_search_time_min')} | {avg('avg_wait_time_min')} "
                f"| {avg('avg_gate_queue')} | {avg('overflow_events')} |"
            )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run all ParkingNav-X experiments")
    parser.add_argument("--seeds", type=int, default=3, help="Number of seeds per experiment cell")
    parser.add_argument("--workers", type=int, default=2, help="Parallel workers")
    parser.add_argument("--scenarios", nargs="+", default=None, help="Specific scenarios to run")
    args = parser.parse_args()

    scenarios = args.scenarios or EXPERIMENTS
    scenarios = [s for s in scenarios if (SCENARIO_DIR / f"{s}.yaml").exists()]

    if not scenarios:
        print("No valid scenario files found. Check configs/scenarios/vitap/")
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    tasks = [
        (scenario, strategy, seed)
        for scenario in scenarios
        for strategy in ["B1", "B2", "B3", "B4"]
        for seed in range(1, args.seeds + 1)
    ]

    print(f"Running {len(tasks)} experiments ({len(scenarios)} scenarios × 4 strategies × {args.seeds} seeds)")
    print(f"Workers: {args.workers}\n")

    all_results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_one, s, st, sd): (s, st, sd) for s, st, sd in tasks}
        for future in as_completed(futures):
            result = future.result()
            all_results.append(result)
            completed += 1
            if "error" in result:
                print(f"  ✗ [{completed}/{len(tasks)}] {result['run_id']}: {result['error']}")
            else:
                print(
                    f"  ✓ [{completed}/{len(tasks)}] {result['run_id']} "
                    f"— search={result.get('avg_search_time_min')} min, "
                    f"overflow={result.get('overflow_events')}"
                )

    # Save results
    results_path = RESULTS_DIR / "all_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n✓ Results saved to {results_path}")

    # Build and save summary table
    summary = build_summary_table(all_results)
    summary_path = RESULTS_DIR / "summary_table.md"
    with open(summary_path, "w") as f:
        f.write(summary)
    print(f"✓ Summary table saved to {summary_path}")
    print("\n" + summary)


if __name__ == "__main__":
    main()
