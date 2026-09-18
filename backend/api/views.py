import json
from pathlib import Path

from django.http import JsonResponse

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = ROOT / "data-pipeline" / "results" / "results.json"


def _load_results():
    if not RESULTS_PATH.exists():
        return {}
    with RESULTS_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def network_stats(request):
    data = _load_results()
    summary = data.get("graph_summary", {})
    return JsonResponse({
        "status": "ready",
        "note": "This endpoint serves the generated pipeline output. If OSM is blocked, the app uses a synthetic demo network with clear provenance labeling.",
        "summary": {
            "nodes": summary.get("nodes", 0),
            "edges": summary.get("edges", 0),
            "candidate_sites": len(data.get("candidate_sites", [])),
            "subregions": len(data.get("subregions", [])),
            "graph_source": data.get("graph_source", "unknown"),
        },
    })


def candidates(request):
    data = _load_results()
    return JsonResponse({"candidates": data.get("candidate_sites", [])})


def existing_stations(request):
    data = _load_results()
    return JsonResponse({"existing_stations": data.get("existing_stations", [])})


def subregions(request):
    data = _load_results()
    features = []
    for region in data.get("subregions", []):
        centroid = region.get("centroid", [0, 0])
        features.append({
            "type": "Feature",
            "properties": {"id": region.get("id"), "node_count": region.get("node_count")},
            "geometry": {"type": "Point", "coordinates": [centroid[1], centroid[0]]},
        })
    return JsonResponse({"type": "FeatureCollection", "features": features})


def optimize(request):
    data = _load_results()
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        budget = int(body.get("budget", data.get("optimization", {}).get("default_budget", 0)))
    else:
        budget = int(data.get("optimization", {}).get("default_budget", 0))

    payload = data.get("optimization", {})
    selected = payload.get("selected_sites", [])
    stats = payload.get("coverage_stats", {
        "before_pct_covered": 0,
        "after_pct_covered": 0,
        "before_avg_distance": 0,
        "after_avg_distance": 0,
    })

    if budget and selected:
        max_index = min(len(selected), max(1, budget))
        selected = selected[:max_index]

    return JsonResponse({
        "selected_sites": selected,
        "budget": budget,
        "objective_value": payload.get("objective_value", 0),
        "coverage_stats": stats,
    })
