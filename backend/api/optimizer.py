"""Live MCLP (Maximum Covering Location Problem) solver.

Re-solves the exact same MILP the offline data pipeline validated, using the
self-contained problem data (candidates, demand nodes, coverage sets, sub-region
membership) that the pipeline writes into results.json under "optimization_input".
This is a real re-optimization for each requested budget, not a re-ranking or
truncation of a precomputed list.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

import pulp as pl

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = ROOT / "data-pipeline" / "results" / "results.json"


class OptimizationDataUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def _load_optimization_input() -> dict:
    if not RESULTS_PATH.exists():
        raise OptimizationDataUnavailable(
            f"Pipeline results not found at {RESULTS_PATH}. Run the data pipeline first."
        )
    with RESULTS_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    opt_input = data.get("optimization_input")
    if not opt_input:
        raise OptimizationDataUnavailable(
            "results.json has no 'optimization_input' block. Re-run the data pipeline "
            "(scripts/run_pipeline.py) with the current schema."
        )
    return opt_input


def clear_cache() -> None:
    _load_optimization_input.cache_clear()


def solve_mclp_live(budget: int) -> dict:
    """Build and solve the MCLP for the given budget. Returns selected sites,
    objective value, coverage stats, and solver diagnostics."""
    opt_input = _load_optimization_input()

    candidates = opt_input["candidates"]
    demand_nodes = opt_input["demand_nodes"]
    coverage = opt_input["coverage"]  # candidate_node_id -> [demand_node_id, ...]
    subregions = opt_input["subregions"]
    coverage_radius_m = opt_input["coverage_radius_m"]
    equity_threshold = opt_input["equity_demand_threshold"]

    candidate_ids = [c["node_id"] for c in candidates]
    candidate_by_id = {c["node_id"]: c for c in candidates}
    demand_weight = {d["node_id"]: d["demand_weight"] for d in demand_nodes}
    demand_ids = list(demand_weight.keys())

    x = {j: pl.LpVariable(f"x_{i}", cat="Binary") for i, j in enumerate(candidate_ids)}
    y = {i: pl.LpVariable(f"y_{i}", cat="Binary") for i in range(len(demand_ids))}
    demand_index = {node_id: i for i, node_id in enumerate(demand_ids)}

    model = pl.LpProblem("chargeopt_mclp_live", pl.LpMaximize)
    model += pl.lpSum(demand_weight[node_id] * y[demand_index[node_id]] for node_id in demand_ids)
    model += pl.lpSum(x[j] for j in candidate_ids) <= budget

    # Build demand_node -> covering candidates map from the candidate -> demand_nodes coverage sets.
    covers = {node_id: [] for node_id in demand_ids}
    for cand_id, covered_demand_ids in coverage.items():
        if cand_id not in x:
            continue
        for d_id in covered_demand_ids:
            if d_id in demand_index:
                covers[d_id].append(cand_id)

    for node_id in demand_ids:
        eligible = [x[cand_id] for cand_id in covers[node_id]]
        yi = y[demand_index[node_id]]
        if eligible:
            model += yi <= pl.lpSum(eligible)
        else:
            model += yi == 0

    for region in subregions:
        if region["total_demand"] >= equity_threshold and region["total_demand"] > 0:
            region_node_ids = set(region["node_ids"])
            region_candidates = [x[j] for j in candidate_ids if j in region_node_ids]
            if region_candidates:
                model += pl.lpSum(region_candidates) >= 1

    t0 = time.time()
    model.solve(pl.PULP_CBC_CMD(msg=False))
    solve_time = time.time() - t0
    status = pl.LpStatus[model.status]

    # A non-Optimal status (typically "Infeasible" when a low budget can't satisfy
    # every equity-mandated sub-region) leaves stale/meaningless values on the
    # variables — never present those as a real site selection.
    if status != "Optimal":
        return {
            "budget": budget,
            "selected_sites": [],
            "objective_value": 0.0,
            "solver_status": status,
            "coverage_radius_m": coverage_radius_m,
            "solve_time_seconds": solve_time,
            "coverage_stats": {"before_pct_covered": 0.0, "after_pct_covered": 0.0},
            "error": (
                f"No feasible solution at budget={budget}: the equity constraint requires at "
                "least one station in each sufficiently-high-demand sub-region, which this "
                "budget cannot satisfy. Try a higher budget."
            ),
        }

    selected_ids = [j for j in candidate_ids if x[j].value() and x[j].value() > 0.5]
    objective = pl.value(model.objective) or 0.0

    selected_sites = [
        {
            "node_id": j,
            "latitude": candidate_by_id[j]["latitude"],
            "longitude": candidate_by_id[j]["longitude"],
        }
        for j in selected_ids
    ]

    # Coverage before/after, using the precomputed candidate->demand coverage sets
    # (for newly selected sites) plus the pipeline-computed set of demand nodes
    # already within coverage_radius_m of an existing (synthetic) station.
    total_weight = sum(demand_weight.values()) or 1.0
    candidate_covers_demand = {cand_id: set(dset) for cand_id, dset in coverage.items()}

    covered_before = set(opt_input.get("existing_coverage_demand_node_ids", []))
    covered_after = set(covered_before)
    for j in selected_ids:
        covered_after |= candidate_covers_demand.get(j, set())

    weight_before = sum(demand_weight[n] for n in covered_before if n in demand_weight)
    weight_after = sum(demand_weight[n] for n in covered_after if n in demand_weight)

    return {
        "budget": budget,
        "selected_sites": selected_sites,
        "objective_value": float(objective),
        "solver_status": status,
        "coverage_radius_m": coverage_radius_m,
        "solve_time_seconds": solve_time,
        "coverage_stats": {
            "before_pct_covered": float(weight_before / total_weight * 100.0),
            "after_pct_covered": float(weight_after / total_weight * 100.0),
        },
    }
