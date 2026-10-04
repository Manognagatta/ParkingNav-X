"""
CLI: run a scenario for each strategy, forecast-error level and seed.

    python -m digital_twin.cli.run --scenario configs/scenarios/vitap/peak_hour.yaml
    python -m digital_twin.cli.run --all --campus vitap
    python -m digital_twin.cli.run --scenario configs/scenarios/vitap/peak_hour.yaml --forecast-error 0 0.1 0.2

--strategy takes first_available, nearest_available, or any module.ClassName.

Writes:
    experiments/runs/<scenario>/<strategy>/err<NN>/seed_<n>/vehicles.csv, timesteps.csv
    experiments/results/<scenario>.csv   one row of metrics per run
    experiments/results/<scenario>.md    mean ± 95% CI over seeds

Without --db the campus YAML is loaded into a fresh in-memory database.
"""

import argparse
import importlib
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from digital_twin.config_loader import load_campus_config
from digital_twin.db import init_db
from digital_twin.simulation.demo_strategies import BASELINES
from digital_twin.simulation.engine import SimulationEngine
from digital_twin.simulation.scenario import ScenarioLoader, ScenarioValidator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"
RESULTS_DIR = PROJECT_ROOT / "experiments" / "results"

TABLE = {  # metric -> column name in the results table
    "total_vehicles": "Vehicles",
    "turned_away_pct": "Turned away (%)",
    "mean_gate_wait_s": "Gate wait (s)",
    "mean_search_time_s": "Time to park (s)",
    "mean_lots_tried": "Lots tried",
    "mean_walk_m": "Walk to building (m)",
    "peak_occupancy_pct": "Peak lot occupancy (%)",
    "capacity_violations": "Capacity violations",
    "decision_latency_mean_ms": "Decision time (ms)",
}


def make_strategy(name: str):
    if name in BASELINES:
        return BASELINES[name]()
    module, cls = name.rsplit(".", 1)
    return getattr(importlib.import_module(module), cls)()


def mean_ci(values) -> str:
    """'mean ± half-width of the 95% confidence interval' over seeds."""
    a = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy()
    if a.size == 0:
        return "-"
    half = 1.96 * a.std(ddof=1) / np.sqrt(a.size) if a.size > 1 else 0.0
    digits = 4 if 0 < a.mean() < 0.1 else 1
    return f"{a.mean():.{digits}f} ± {half:.{digits}f}"


def run_scenario(path, strategies, seeds=30, errors=(0.0,), db=None) -> pd.DataFrame:
    """Run every strategy x error level x seed; write CSVs and the results table."""
    path = Path(path).resolve()
    base = ScenarioLoader.load(path)
    conn = init_db(db or ":memory:")
    if not db:
        load_campus_config(PROJECT_ROOT / "configs" / "campuses" / f"{base.campus_id}.yaml", conn)
    problems = ScenarioValidator.validate(base, conn)
    if problems:
        raise SystemExit(f"{path.name} is invalid:\n- " + "\n- ".join(problems))

    engine, rows = SimulationEngine(), []
    for name in strategies:
        label = name.rsplit(".", 1)[-1]
        for err in errors:
            for seed in range(base.random_seed, base.random_seed + seeds):
                sc = replace(base, random_seed=seed, prediction_error_injection_level=err)
                result = engine.run(sc, make_strategy(name), conn, persist=bool(db))
                out = RUNS_DIR / base.scenario_id / label / f"err{round(err * 100):02d}" / f"seed_{seed}"
                out.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(result.vehicles).to_csv(out / "vehicles.csv", index=False)
                pd.DataFrame(result.timesteps).to_csv(out / "timesteps.csv", index=False)
                metrics = {k: v for k, v in result.summary.items() if not isinstance(v, dict)}
                rows.append({"strategy": label, "forecast_error": err, "seed": seed, **metrics})
    runs = pd.DataFrame(rows)

    table = [
        {"Strategy": s, "Forecast error": f"{e:.0%}", "Seeds": len(g),
         **{col: mean_ci(g[m]) for m, col in TABLE.items()}}
        for (s, e), g in runs.groupby(["strategy", "forecast_error"], sort=False)
    ]
    header = list(table[0])
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    md = [
        f"# {base.name}: {base.description}",
        "",
        f"`{rel}`, seeds {base.random_seed}-{base.random_seed + seeds - 1}, mean ± 95% CI. "
        "Demand is SYNTHETIC. Decision time is wall-clock; everything else is reproducible.",
        f"Regenerate: `python -m digital_twin.cli.run --scenario {rel} --seeds {seeds} "
        f"--strategy {' '.join(strategies)} --forecast-error {' '.join(map(str, errors))}`",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(str(r[h]) for h in header) + " |" for r in table),
        "",
    ]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    runs.to_csv(RESULTS_DIR / f"{base.scenario_id}.csv", index=False)
    (RESULTS_DIR / f"{base.scenario_id}.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return runs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run ParkingNav-X simulations.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--scenario", type=Path)
    target.add_argument("--all", action="store_true", help="every scenario of --campus")
    parser.add_argument("--campus", default="vitap")
    parser.add_argument("--strategy", nargs="+", default=list(BASELINES))
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--forecast-error", type=float, nargs="+", default=[0.0])
    parser.add_argument("--db", default=None, help="also save runs into this SQLite DB")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # so "±" prints on Windows

    paths = (sorted((PROJECT_ROOT / "configs" / "scenarios" / args.campus).glob("*.yaml"))
             if args.all else [args.scenario])
    for path in paths:
        run_scenario(path, args.strategy, args.seeds, args.forecast_error, args.db)


if __name__ == "__main__":
    main()
