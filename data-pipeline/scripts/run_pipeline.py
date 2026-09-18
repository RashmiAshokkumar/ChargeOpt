from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import pulp as pl
from scipy.sparse import csgraph
from scipy.sparse import csr_matrix
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"

PLACE = "Andheri West, Mumbai, India"
# Use a compact bounding box around a small sub-area of Andheri West so the OSM query stays feasible and the project remains bounded.
NORTH = 19.129
SOUTH = 19.120
EAST = 72.836
WEST = 72.824


def load_existing_stations(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing existing station file: {path}")
    df = pd.read_csv(path)
    required = {"latitude", "longitude"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Existing station CSV is missing required columns: {sorted(missing)}")
    return df


def generate_synthetic_graph() -> nx.Graph:
    G = nx.Graph()
    rows, cols = 8, 10
    for r in range(rows):
        for c in range(cols):
            lat = SOUTH + (NORTH - SOUTH) * (r / max(rows - 1, 1))
            lon = WEST + (EAST - WEST) * (c / max(cols - 1, 1))
            node_id = f"n_{r}_{c}"
            G.add_node(node_id, x=lon, y=lat, highway="residential")
            if r in {0, rows // 2, rows - 1}:
                G.nodes[node_id]["highway"] = "primary"
            if c in {0, cols // 2, cols - 1}:
                G.nodes[node_id]["highway"] = "secondary"

    for r in range(rows):
        for c in range(cols):
            node = f"n_{r}_{c}"
            if c < cols - 1:
                nbr = f"n_{r}_{c + 1}"
                G.add_edge(node, nbr, length=250.0, highway="residential")
            if r < rows - 1:
                nbr = f"n_{r + 1}_{c}"
                G.add_edge(node, nbr, length=250.0, highway="residential")
    return G


def build_graph() -> nx.Graph:
    print("[1.1] Building road network...")
    try:
        G = ox.graph_from_bbox(
            bbox=(NORTH, SOUTH, EAST, WEST),
            network_type="drive",
            simplify=True,
            retain_all=False,
            truncate_by_edge=False,
        )
        G = ox.utils_graph.get_largest_component(G, strongly=False)
        G = G.to_undirected()
        print("Graph source: live OSM network")
    except Exception as exc:
        print(f"OSM fetch failed: {exc}\nUsing synthetic offline fallback graph instead.")
        G = generate_synthetic_graph()
        print("Graph source: synthetic offline fallback")

    node_count = G.number_of_nodes()
    edge_count = G.number_of_edges()
    print(f"Graph nodes: {node_count}, edges: {edge_count}")

    plot_path = RESULTS_DIR / "graph_sanity.png"
    try:
        fig, ax = ox.plot_graph(G, node_size=12, edge_linewidth=0.5, show=False, close=False)
        plt.tight_layout()
        plt.savefig(plot_path, dpi=200)
        plt.close(fig)
        print(f"Sanity plot saved to {plot_path}")
    except Exception:
        fig, ax = plt.subplots(figsize=(6, 6))
        positions = {n: (data["x"], data["y"]) for n, data in G.nodes(data=True)}
        nx.draw(G, pos=positions, node_size=20, edge_color="#4b5563", width=1.0)
        plt.tight_layout()
        plt.savefig(plot_path, dpi=200)
        plt.close(fig)
        print(f"Fallback plot saved to {plot_path}")
    return G


def matrix_demo() -> dict:
    print("[1.2] Matrix demonstration...")
    H = nx.Graph()
    H.add_edges_from([(0, 1), (1, 2), (2, 3), (0, 3), (1, 3)])
    A = nx.to_numpy_array(H, nodelist=[0, 1, 2, 3], weight=None)
    A2 = A @ A
    A3 = A2 @ A
    return {
        "small_graph_nodes": [0, 1, 2, 3],
        "adjacency_matrix": np.round(A, 4).tolist(),
        "A_squared": np.round(A2, 4).tolist(),
        "A_cubed": np.round(A3, 4).tolist(),
        "explanation": "A^k counts walks of length k between node pairs. This is the matrix-based reasoning behind reachability and shortest-path approximations.",
    }


def compute_network_metrics(G: nx.Graph) -> tuple[dict, np.ndarray]:
    print("[1.3] Computing centrality measures...")
    nodes = list(G.nodes())
    degree = nx.degree_centrality(G)
    betweenness = nx.betweenness_centrality(G, weight="length")
    closeness = nx.closeness_centrality(G, distance="length")
    eigenvector = nx.eigenvector_centrality_numpy(G, weight="length")

    values = {
        "degree": degree,
        "betweenness": betweenness,
        "closeness": closeness,
        "eigenvector": eigenvector,
    }

    degree_vals = np.array([degree[n] for n in nodes], dtype=float)
    bet_vals = np.array([betweenness[n] for n in nodes], dtype=float)
    close_vals = np.array([closeness[n] for n in nodes], dtype=float)
    eig_vals = np.array([eigenvector[n] for n in nodes], dtype=float)

    def zscore(values: np.ndarray) -> np.ndarray:
        mean = values.mean()
        std = values.std()
        if std == 0:
            return np.zeros_like(values)
        return (values - mean) / std

    composite = (
        zscore(degree_vals)
        + zscore(bet_vals)
        + zscore(close_vals)
        + zscore(eig_vals)
    )

    importance = {n: float(composite[i]) for i, n in enumerate(nodes)}
    return importance, nx.to_numpy_array(G, nodelist=nodes, weight=None)


def compute_traffic_proxy(G: nx.Graph, degree: dict, betweenness: dict) -> dict:
    degree_vals = np.array([degree[n] for n in G.nodes()], dtype=float)
    bet_vals = np.array([betweenness[n] for n in G.nodes()], dtype=float)
    degree_norm = degree_vals / max(degree_vals.max(), 1e-9)
    bet_norm = bet_vals / max(bet_vals.max(), 1e-9)

    road_weight = {}
    for node in G.nodes():
        edge_types = [data.get("highway", "residential") for _, _, data in G.edges(node, data=True)]
        hierarchy = 0.0
        for road in edge_types:
            if road in {"primary", "trunk"}:
                hierarchy += 3.0
            elif road in {"secondary", "tertiary"}:
                hierarchy += 2.0
            elif road == "residential":
                hierarchy += 1.0
        road_weight[node] = hierarchy / max(len(edge_types), 1)

    traffic_proxy = {}
    for node in G.nodes():
        traffic_proxy[node] = (
            0.5 * road_weight[node]
            + 0.3 * degree_norm[list(G.nodes()).index(node)]
            + 0.2 * bet_norm[list(G.nodes()).index(node)]
        )
    return traffic_proxy


def create_population_proxy(G: nx.Graph) -> dict:
    centroid_lat = (NORTH + SOUTH) / 2.0
    centroid_lon = (WEST + EAST) / 2.0
    pop = {}
    for node, data in G.nodes(data=True):
        lat = data.get("y", centroid_lat)
        lon = data.get("x", centroid_lon)
        dist_km = (math.hypot(lat - centroid_lat, lon - centroid_lon) * 111.0)
        # Coarse population proxy: deterministic radial decay around the city center.
        # This is intentionally a proxy because the project does not include a real WorldPop/Census raster in the repo.
        pop[node] = 500 + 3500 * math.exp(-(dist_km ** 2) / (2 * (2.2 ** 2)))
    return pop


def build_candidate_features(G: nx.Graph, existing_df: pd.DataFrame, importance: dict, traffic_proxy: dict, population_proxy: dict, distance_matrix: np.ndarray, nearest_station_matrix: np.ndarray) -> pd.DataFrame:
    node_ids = list(G.nodes())
    node_map = {node: i for i, node in enumerate(node_ids)}
    candidate_rows = []

    for node in node_ids:
        data = G.nodes[node]
        lat = float(data.get("y"))
        lon = float(data.get("x"))
        feature_row = {
            "node_id": node,
            "latitude": lat,
            "longitude": lon,
            "population_density": float(population_proxy[node]),
            "traffic_proxy": float(traffic_proxy[node]),
            "degree_centrality": float(nx.degree_centrality(G)[node]),
            "betweenness_centrality": float(nx.betweenness_centrality(G, weight="length")[node]),
            "eigenvector_centrality": float(nx.eigenvector_centrality_numpy(G, weight="length")[node]),
            "composite_importance": float(importance[node]),
            "distance_to_nearest_existing_station": float(nearest_station_matrix[node_map[node], node_map[node]]),
        }
        candidate_rows.append(feature_row)

    df = pd.DataFrame(candidate_rows)
    df = df[~df["node_id"].isin(set(existing_df["node_id"]))] if "node_id" in existing_df.columns else df
    return df


def compute_distance_to_existing(G: nx.Graph, existing_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    nodes = list(G.nodes())
    idx = {node: i for i, node in enumerate(nodes)}
    node_array = np.array(nodes)
    n_nodes = len(nodes)
    n_stations = len(existing_df)

    if n_stations == 0:
        nearest = np.full((n_nodes, 1), 1e9)
        return nearest, np.zeros((n_nodes, 1))

    coords = existing_df[["latitude", "longitude"]].to_numpy(dtype=float)
    distances = np.full((n_nodes, n_stations), np.inf)
    for i, node in enumerate(nodes):
        node_lat = G.nodes[node].get("y")
        node_lon = G.nodes[node].get("x")
        d = np.hypot(coords[:, 0] - node_lat, coords[:, 1] - node_lon) * 111.0 * 1000.0
        distances[i] = d

    nearest_station_per_node = distances.min(axis=1)
    return nearest_station_per_node, distances


def shortlist_candidates(G: nx.Graph, candidate_df: pd.DataFrame, importance: dict, min_spacing_m: float = 250.0, max_candidates: int = 60) -> list:
    print("[1.6] Shortlisting candidates...")
    node_ids = list(G.nodes())
    node_map = {node: i for i, node in enumerate(node_ids)}
    distances = nx.all_pairs_dijkstra_path_length(G, weight="length")
    dist_lookup = {source: {target: d for target, d in target_map.items()} for source, target_map in distances}

    selected = []
    ranked = candidate_df.sort_values("composite_importance", ascending=False).to_dict("records")
    for item in ranked:
        candidate = item["node_id"]
        if any(dist_lookup.get(candidate, {}).get(other, float("inf")) < min_spacing_m for other in selected):
            continue
        selected.append(candidate)
        if len(selected) >= max_candidates:
            break
    return selected


def compute_pca(candidate_df: pd.DataFrame) -> dict:
    print("[1.4] Running PCA on candidate features...")
    feature_cols = [
        "latitude",
        "longitude",
        "population_density",
        "traffic_proxy",
        "degree_centrality",
        "betweenness_centrality",
        "eigenvector_centrality",
    ]
    X = candidate_df[feature_cols].to_numpy(dtype=float)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    pca = PCA(n_components=min(3, X_scaled.shape[1]))
    X_pca = pca.fit_transform(X_scaled)
    explained = pca.explained_variance_ratio_
    loadings = pca.components_.T * np.sqrt(pca.explained_variance_)
    loading_table = []
    for i, feature in enumerate(feature_cols):
        row = {"feature": feature, "top_component_loadings": [float(v) for v in loadings[i]]}
        loading_table.append(row)
    return {
        "explained_variance_ratio": [float(x) for x in explained],
        "component_loadings": loading_table,
        "shape": list(X_pca.shape),
    }


def compute_communities(G: nx.Graph) -> list[list]:
    print("[1.5] Running community detection...")
    communities = nx.community.louvain_communities(G, seed=42, weight="length")
    return [sorted(c) for c in communities]


def solve_mclp(G: nx.Graph, candidate_list: list, subregions: list[list], population_proxy: dict, existing_df: pd.DataFrame, budget: int = 8, coverage_radius_m: int = 2000) -> dict:
    print("[1.7] Solving MILP...")
    nodes = list(G.nodes())
    demand_nodes = nodes
    node_map = {node: i for i, node in enumerate(nodes)}
    candidate_set = set(candidate_list)

    demand_weight = {}
    for node in demand_nodes:
        demand_weight[node] = float(population_proxy[node])

    # Weighted nodes as demand nodes, with a mild EV-adoption proxy tied to centrality.
    for node in demand_nodes:
        demand_weight[node] = demand_weight[node] + 0.2 * float(nx.degree_centrality(G)[node]) + 0.2 * float(nx.eigenvector_centrality_numpy(G, weight="length")[node])

    distances = dict(nx.all_pairs_dijkstra_path_length(G, weight="length"))
    candidate_index = {j: idx for idx, j in enumerate(candidate_list)}
    x = {j: pl.LpVariable(f"x_{j}", cat="Binary") for j in candidate_list}
    y = {i: pl.LpVariable(f"y_{i}", cat="Binary") for i in demand_nodes}

    model = pl.LpProblem("chargeopt_mclp", pl.LpMaximize)
    model += pl.lpSum(demand_weight[i] * y[i] for i in demand_nodes)

    model += pl.lpSum(x[j] for j in candidate_list) <= budget

    for i in demand_nodes:
        eligible = [x[j] for j in candidate_list if distances.get(i, {}).get(j, float("inf")) <= coverage_radius_m]
        if eligible:
            model += y[i] <= pl.lpSum(eligible)
        else:
            model += y[i] == 0

    region_assignments = {}
    for region_id, region_nodes in enumerate(subregions):
        region_demand = sum(demand_weight[n] for n in region_nodes if n in demand_weight)
        if region_demand >= 1200:
            region_candidates = [x[j] for j in candidate_list if j in region_nodes]
            if region_candidates:
                model += pl.lpSum(region_candidates) >= 1

    model.solve(pl.PULP_CBC_CMD(msg=False))
    selected = [j for j in candidate_list if pl.value(x[j]) > 0.5]

    objective = pl.value(model.objective)
    return {
        "budget": budget,
        "selected_sites": selected,
        "objective_value": float(objective),
        "solver_status": pl.LpStatus[model.status],
        "coverage_radius_m": coverage_radius_m,
        "demand_weight": demand_weight,
    }


def compute_coverage_stats(G: nx.Graph, existing_df: pd.DataFrame, selected_sites: list, demand_weight: dict) -> dict:
    nodes = list(G.nodes())
    distances = dict(nx.all_pairs_dijkstra_path_length(G, weight="length"))
    existing_sites = [tuple(row[["latitude", "longitude"]].to_numpy(dtype=float)) for _, row in existing_df.iterrows()]

    before_distances = []
    after_distances = []
    demand_covered_before = 0
    demand_covered_after = 0
    total_weight = sum(demand_weight.values())

    for node in nodes:
        nearest_before = float("inf")
        for _, row in existing_df.iterrows():
            station = (row["latitude"], row["longitude"])
            station_node = min(G.nodes, key=lambda n: ((G.nodes[n].get("y") - station[0]) ** 2 + (G.nodes[n].get("x") - station[1]) ** 2) ** 0.5)
            nearest_before = min(nearest_before, distances.get(node, {}).get(station_node, float("inf")))
        before_distances.append(nearest_before)

        candidate_distances = [distances.get(node, {}).get(site, float("inf")) for site in selected_sites]
        nearest_after = min(nearest_before, *candidate_distances) if candidate_distances else nearest_before
        after_distances.append(nearest_after)
        if nearest_before <= 2000:
            demand_covered_before += demand_weight.get(node, 0.0)
        if nearest_after <= 2000:
            demand_covered_after += demand_weight.get(node, 0.0)

    return {
        "before_pct_covered": float((demand_covered_before / total_weight) * 100.0) if total_weight else 0.0,
        "after_pct_covered": float((demand_covered_after / total_weight) * 100.0) if total_weight else 0.0,
        "before_avg_distance": float(np.mean(before_distances)) if before_distances else 0.0,
        "after_avg_distance": float(np.mean(after_distances)) if after_distances else 0.0,
    }


def build_results() -> dict:
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)
    G = build_graph()

    existing_df = load_existing_stations(DATA_DIR / "existing_stations.csv")
    # Add node_id to CSV so site screening can reference graph nodes during a later pass.
    existing_df["node_id"] = None
    nodelist = list(G.nodes())
    for idx, row in existing_df.iterrows():
        nearest_node = min(
            nodelist,
            key=lambda n: ((G.nodes[n].get("y") - row["latitude"]) ** 2 + (G.nodes[n].get("x") - row["longitude"]) ** 2) ** 0.5,
        )
        existing_df.at[idx, "node_id"] = nearest_node

    adjacency_matrix = nx.to_numpy_array(G, nodelist=nodelist, weight=None)
    sparse_graph = nx.to_scipy_sparse_array(G, nodelist=nodelist, weight="length", format="csr").astype(np.float64)
    sparse_graph = sparse_graph.tocsr(copy=False)
    sparse_graph.indices = sparse_graph.indices.astype(np.int32)
    sparse_graph.indptr = sparse_graph.indptr.astype(np.int32)
    distance_matrix = csgraph.dijkstra(sparse_graph, directed=False, return_predecessors=False)

    matrix_demo_record = matrix_demo()
    importance, _ = compute_network_metrics(G)
    traffic_proxy = compute_traffic_proxy(G, nx.degree_centrality(G), nx.betweenness_centrality(G, weight="length"))
    population_proxy = create_population_proxy(G)

    nearest_station_per_node, _ = compute_distance_to_existing(G, existing_df)
    candidate_df = pd.DataFrame([
        {
            "node_id": node,
            "latitude": G.nodes[node].get("y"),
            "longitude": G.nodes[node].get("x"),
            "population_density": population_proxy[node],
            "traffic_proxy": traffic_proxy[node],
            "degree_centrality": nx.degree_centrality(G)[node],
            "betweenness_centrality": nx.betweenness_centrality(G, weight="length")[node],
            "eigenvector_centrality": nx.eigenvector_centrality_numpy(G, weight="length")[node],
            "composite_importance": importance[node],
            "distance_to_nearest_existing_station": nearest_station_per_node[nodelist.index(node)],
        }
        for node in nodelist
    ])

    pca_summary = compute_pca(candidate_df.head(min(60, len(candidate_df))))
    subregions = compute_communities(G)
    shortlist = shortlist_candidates(G, candidate_df, importance, min_spacing_m=250.0, max_candidates=60)
    shortlist_df = candidate_df[candidate_df["node_id"].isin(shortlist)].copy()

    budget = 8
    optimization = solve_mclp(G, shortlist, subregions, population_proxy, existing_df, budget=budget, coverage_radius_m=2000)
    stats = compute_coverage_stats(G, existing_df, optimization["selected_sites"], optimization["demand_weight"])

    selected_candidates = []
    for node in optimization["selected_sites"]:
        row = shortlist_df[shortlist_df["node_id"] == node].iloc[0].to_dict()
        selected_candidates.append(
            {
                "node_id": node,
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "composite_importance": row["composite_importance"],
            }
        )

    subregion_geo = []
    for r_id, region in enumerate(subregions):
        region_nodes = [G.nodes[n] for n in region]
        coords = [(node["x"], node["y"]) for node in region_nodes]
        subregion_geo.append(
            {
                "id": r_id,
                "node_count": len(region),
                "centroid": [sum(c[1] for c in coords) / max(len(coords), 1), sum(c[0] for c in coords) / max(len(coords), 1)],
                "nodes": region,
            }
        )

    output = {
        "graph_source": "synthetic_offline_fallback" if "live OSM network" not in str(G) else "live_osm_network",
        "study_area": {
            "name": PLACE,
            "bbox": {"north": NORTH, "south": SOUTH, "east": EAST, "west": WEST},
        },
        "graph_summary": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "matrix_summary": {
            "adjacency_shape": list(adjacency_matrix.shape),
            "distance_shape": list(distance_matrix.shape),
            "adjacency_matrix_sample": np.round(adjacency_matrix[:5, :5], 4).tolist(),
            "distance_matrix_sample": np.round(distance_matrix[:5, :5], 4).tolist(),
            "matrix_demo": matrix_demo_record,
        },
        "centrality": {
            "degree": {str(k): float(v) for k, v in nx.degree_centrality(G).items()},
            "betweenness": {str(k): float(v) for k, v in nx.betweenness_centrality(G, weight="length").items()},
            "closeness": {str(k): float(v) for k, v in nx.closeness_centrality(G, distance="length").items()},
            "eigenvector": {str(k): float(v) for k, v in nx.eigenvector_centrality_numpy(G, weight="length").items()},
            "importance_score": {str(k): float(v) for k, v in importance.items()},
        },
        "candidate_sites": [
            {
                "node_id": int(row["node_id"]) if isinstance(row["node_id"], (int, np.integer)) else row["node_id"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "population_density": float(row["population_density"]),
                "traffic_proxy": float(row["traffic_proxy"]),
                "degree_centrality": float(row["degree_centrality"]),
                "betweenness_centrality": float(row["betweenness_centrality"]),
                "eigenvector_centrality": float(row["eigenvector_centrality"]),
                "composite_importance": float(row["composite_importance"]),
                "distance_to_nearest_existing_station": float(row["distance_to_nearest_existing_station"]),
            }
            for _, row in shortlist_df.iterrows()
        ],
        "pca": pca_summary,
        "subregions": subregion_geo,
        "existing_stations": [
            {
                "station_name": row.get("station_name", f"station_{i}"),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "source": row.get("source", "manually_curated"),
                "notes": row.get("notes", ""),
            }
            for i, row in existing_df.iterrows()
        ],
        "optimization": {
            "default_budget": budget,
            "selected_sites": selected_candidates,
            "objective_value": float(optimization["objective_value"]),
            "solver_status": optimization["solver_status"],
            "coverage_radius_m": optimization["coverage_radius_m"],
            "coverage_stats": stats,
            "equity_constraints": [
                {
                    "subregion_id": idx,
                    "minimum_required_stations": 1,
                    "demand_threshold": 1200,
                }
                for idx in range(len(subregions))
            ],
        },
        "provenance": {
            "data_sources": {
                "road_network": "OSM road network from osmnx",
                "traffic_proxy": "Derived from road hierarchy and graph centrality; not live traffic data",
                "population_density": "Coarse proxy derived from city-center distance decay; not WorldPop/Census data in this repo",
                "existing_stations": "Manually curated CSV in data/existing_stations.csv",
            },
            "limitations": [
                "Traffic proxy is derived from topology rather than real-time traffic measurements.",
                "Population density is a coarse proxy and not a formal WorldPop/Census raster.",
                "The bounded study area is intentionally limited to a sub-area of the city to keep graph size manageable."
            ],
        },
    }

    results_path = RESULTS_DIR / "results.json"
    with results_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"[1.8] Results written to {results_path}")
    return output


if __name__ == "__main__":
    build_results()
