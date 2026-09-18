import json
from pathlib import Path

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CandidateSite, ExistingStation, SubRegion, OptimizationRun
from .optimizer import OptimizationDataUnavailable, solve_mclp_live
from .serializers import (
    CandidateSiteSerializer,
    ExistingStationSerializer,
    OptimizationRunSerializer,
)

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = ROOT / "data-pipeline" / "results" / "results.json"


def _load_results() -> dict:
    if not RESULTS_PATH.exists():
        return {}
    with RESULTS_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@api_view(["GET"])
def network_stats(request):
    data = _load_results()
    summary = data.get("graph_summary", {})
    return Response({
        "graph_source": data.get("graph_source", "unknown"),
        "study_area": data.get("study_area", {}),
        "summary": {
            "nodes": summary.get("nodes", 0),
            "edges": summary.get("edges", 0),
            "connected_components": summary.get("connected_components"),
            "candidate_sites": CandidateSite.objects.count(),
            "subregions": SubRegion.objects.count(),
        },
        "matrix_summary": data.get("matrix_summary", {}),
        "centrality_distributions": {
            "degree": list(data.get("centrality", {}).get("degree", {}).values()),
            "betweenness": list(data.get("centrality", {}).get("betweenness", {}).values()),
            "closeness": list(data.get("centrality", {}).get("closeness", {}).values()),
            "eigenvector": list(data.get("centrality", {}).get("eigenvector", {}).values()),
        },
        "pca": data.get("pca", {}),
        "provenance": data.get("provenance", {}),
    })


@api_view(["GET"])
def road_network(request):
    data = _load_results()
    return Response(data.get("road_network", {"nodes": [], "edges": []}))


@api_view(["GET"])
def demand_heatmap(request):
    """Demand-weight heatmap points: population-density proxy blended with degree/
    eigenvector centrality (see provenance for exact formula). Not a raw population
    count — see /api/network-stats/ provenance block."""
    data = _load_results()
    demand_nodes = data.get("optimization_input", {}).get("demand_nodes", [])
    points = [
        {"lat": n["latitude"], "lon": n["longitude"], "weight": n["demand_weight"]}
        for n in demand_nodes
    ]
    return Response({"points": points})


@api_view(["GET"])
def candidates(request):
    qs = CandidateSite.objects.all().order_by("-composite_importance")
    return Response({"candidates": CandidateSiteSerializer(qs, many=True).data})


@api_view(["GET"])
def existing_stations(request):
    qs = ExistingStation.objects.all()
    return Response({"existing_stations": ExistingStationSerializer(qs, many=True).data})


@api_view(["GET"])
def subregions(request):
    qs = SubRegion.objects.all().order_by("region_id")
    features = []
    for region in qs:
        properties = {
            "id": region.region_id,
            "node_count": region.node_count,
            "total_demand": region.total_demand,
            "centroid": [region.centroid_lat, region.centroid_lon],
        }
        if region.boundary and len(region.boundary) >= 3:
            # boundary is [[lat, lon], ...]; GeoJSON wants [lon, lat] and a closed ring.
            ring = [[lon, lat] for lat, lon in region.boundary]
            ring.append(ring[0])
            geometry = {"type": "Polygon", "coordinates": [ring]}
        else:
            geometry = {"type": "Point", "coordinates": [region.centroid_lon, region.centroid_lat]}
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    return Response({"type": "FeatureCollection", "features": features})


class OptimizeView(APIView):
    """POST {"budget": N} -> live-solves the MCLP MILP for that budget and returns
    the selected sites + coverage stats. This is a real PuLP/CBC re-solve, not a
    truncation of a precomputed ranking."""

    def post(self, request):
        try:
            budget = int(request.data.get("budget", 10))
        except (TypeError, ValueError):
            return Response({"error": "budget must be an integer"}, status=400)
        if budget < 1:
            return Response({"error": "budget must be >= 1"}, status=400)

        try:
            result = solve_mclp_live(budget)
        except OptimizationDataUnavailable as exc:
            return Response({"error": str(exc)}, status=503)

        if result["solver_status"] != "Optimal":
            return Response({
                "selected_sites": [],
                "budget": result["budget"],
                "objective_value": 0.0,
                "solver_status": result["solver_status"],
                "coverage_stats": result["coverage_stats"],
                "solve_time_seconds": result["solve_time_seconds"],
                "error": result.get("error", "No feasible solution at this budget."),
            }, status=200)

        run = OptimizationRun.objects.create(
            budget=result["budget"],
            objective_value=result["objective_value"],
            solver_status=result["solver_status"],
            coverage_radius_m=result["coverage_radius_m"],
            before_pct_covered=result["coverage_stats"]["before_pct_covered"],
            after_pct_covered=result["coverage_stats"]["after_pct_covered"],
            before_avg_distance_m=None,
            after_avg_distance_m=None,
            selected_site_ids=[s["node_id"] for s in result["selected_sites"]],
            solve_time_seconds=result["solve_time_seconds"],
        )

        return Response({
            "selected_sites": result["selected_sites"],
            "budget": result["budget"],
            "objective_value": result["objective_value"],
            "solver_status": result["solver_status"],
            "coverage_stats": result["coverage_stats"],
            "solve_time_seconds": result["solve_time_seconds"],
            "run_id": run.id,
        })


@api_view(["GET"])
def optimization_history(request):
    qs = OptimizationRun.objects.all()[:20]
    return Response({"runs": OptimizationRunSerializer(qs, many=True).data})
