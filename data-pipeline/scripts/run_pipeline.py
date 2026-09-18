"""ChargeOpt data pipeline: builds the Andheri West road network, computes matrix
representations, network centrality, PCA features, community detection, and solves
a Maximum Covering Location Problem (MCLP) via PuLP/CBC.

Run: python scripts/run_pipeline.py

Data honesty notes (see also README.md / provenance block in results.json):
  - Road network: real OSM data pulled live via osmnx/Overpass. If that fetch fails
    (e.g. no network access), the pipeline falls back to a synthetic grid graph and
    labels every downstream artifact accordingly — it never silently presents the
    synthetic graph as real OSM data.
  - Traffic proxy: derived from OSM road hierarchy + graph centrality. Not live
    traffic data.
  - Population density: proxy derived from real OSM building-footprint density
    around each node, smoothed with a distance-to-center decay term. Not WorldPop
    or Census 2011 data (no such raster/shapefile is bundled in this repo).
  - Existing charging stations: SYNTHETIC, deterministically generated (see
    scripts/generate_synthetic_stations.py). Not sourced from PlugShare/Google
    Maps. This is explicitly not "manually curated" and must never be presented
    as such in the UI or report.
"""
from __future__ import annotations

import json
import math
import time
import warnings
from pathlib import Path

import matplotlib
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import pulp as pl
from scipy.sparse import csgraph
from scipy.spatial import cKDTree
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# On macOS, numpy/scipy linked against Apple's Accelerate BLAS backend can leave a
# stale hardware FP-exception flag set that gets reported as a spurious
# "divide by zero/overflow encountered in matmul" RuntimeWarning on the *next*
# matmul call (here, PCA's internal transform), even though the actual inputs and
# outputs are all finite (verified: explained_variance_ratio and loadings are sane).
# This filter silences that specific false alarm without hiding real numeric issues.
warnings.filterwarnings("ignore", message=".*encountered in matmul.*", category=RuntimeWarning)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"

PLACE = "Andheri West, Mumbai, India"
# ~4km x 4km box centered on Andheri West (per project brief: a full city graph is
# too slow for live centrality/MILP demos, so we bound the study area explicitly).
CENTER_LAT, CENTER_LON = 19.1197, 72.8296
HALF_LAT_DEG = 0.018   # ~2km north/south
HALF_LON_DEG = 0.019   # ~2km east/west at this latitude
NORTH = CENTER_LAT + HALF_LAT_DEG
SOUTH = CENTER_LAT - HALF_LAT_DEG
EAST = CENTER_LON + HALF_LON_DEG
WEST = CENTER_LON - HALF_LON_DEG

BUILDING_RADIUS_M = 200.0  # radius used to compute building-density proxy per node
COVERAGE_RADIUS_M = 800.0  # MCLP coverage radius (road-network distance). Chosen as a
# realistic dense-urban EV-charging accessibility catchment (walk/short-drive), not the
# highway-context 2km figure the brief cites only as an example — on this compact ~4km
# study area, a 2km radius makes nearly any station blanket-cover the whole box.
MIN_CANDIDATE_SPACING_M = 250.0
MAX_CANDIDATES = 60
DEFAULT_BUDGET = 10
EQUITY_DEMAND_PERCENTILE = 65  # sub-regions above this percentile of demand must get >=1 station
COMMUNITY_RESOLUTION = 0.05  # lower = fewer, larger Louvain communities (neighborhood-scale zones)

ox.settings.http_user_agent = (
    "ChargeOptAcademicProject/1.0 (MDS coursework; contact ashokrashmi36@gmail.com)"
)
ox.settings.cache_folder = str(ROOT / "cache")
ox.settings.use_cache = True


def log(msg: str) -> None:
    print(f"[pipeline] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 1.1 Graph construction
# ---------------------------------------------------------------------------

def generate_synthetic_graph() -> nx.Graph:
    G = nx.Graph()
    rows, cols = 8, 10
    for r in range(rows):
        for c in range(cols):
            lat = SOUTH + (NORTH - SOUTH) * (r / max(rows - 1, 1))
            lon = WEST + (EAST - WEST) * (c / max(cols - 1, 1))
            node_id = f"n_{r}_{c}"
            highway = "residential"
            if r in {0, rows // 2, rows - 1}:
                highway = "primary"
            if c in {0, cols // 2, cols - 1}:
                highway = "secondary"
            G.add_node(node_id, x=lon, y=lat, highway=highway)
    for r in range(rows):
        for c in range(cols):
            node = f"n_{r}_{c}"
            if c < cols - 1:
                G.add_edge(node, f"n_{r}_{c + 1}", length=250.0, highway="residential")
            if r < rows - 1:
                G.add_edge(node, f"n_{r + 1}_{c}", length=250.0, highway="residential")
    return G


RAW_GRAPH_CACHE = DATA_DIR / "andheri_west_raw.graphml"


def build_graph() -> tuple[nx.Graph, str]:
    log("[1.1] Building road network for study area: " + PLACE)
    try:
        if RAW_GRAPH_CACHE.exists():
            log(f"Loading previously-fetched live OSM graph from local cache: {RAW_GRAPH_CACHE}")
            G = ox.io.load_graphml(RAW_GRAPH_CACHE)
        else:
            G = ox.graph_from_bbox(
                bbox=(WEST, SOUTH, EAST, NORTH),
                network_type="drive",
                simplify=True,
                retain_all=False,
                truncate_by_edge=False,
            )
            RAW_GRAPH_CACHE.parent.mkdir(parents=True, exist_ok=True)
            ox.io.save_graphml(G, RAW_GRAPH_CACHE)
            log(f"Saved freshly-fetched live OSM graph to local cache: {RAW_GRAPH_CACHE}")
        G = ox.truncate.largest_component(G, strongly=False)
        G = G.to_undirected()
        # Collapse parallel edges (MultiGraph -> Graph), keeping the shortest one.
        if G.is_multigraph():
            simple = nx.Graph()
            simple.add_nodes_from(G.nodes(data=True))
            for u, v, data in G.edges(data=True):
                length = data.get("length", 1.0)
                if simple.has_edge(u, v):
                    if length < simple[u][v].get("length", float("inf")):
                        simple[u][v].update(data)
                else:
                    simple.add_edge(u, v, **data)
            G = simple
        source = "live_osm_network"
        log(f"Graph source: {source}")
    except Exception as exc:  # noqa: BLE001 - intentional broad catch with explicit labeling
        log(f"OSM fetch failed ({type(exc).__name__}: {exc}). Falling back to synthetic offline grid.")
        G = generate_synthetic_graph()
        source = "synthetic_offline_fallback"
        log(f"Graph source: {source}")

    log(f"Graph nodes: {G.number_of_nodes()}, edges: {G.number_of_edges()}, "
        f"connected components: {nx.number_connected_components(G)}")

    plot_path = RESULTS_DIR / "graph_sanity.png"
    try:
        positions = {n: (d["x"], d["y"]) for n, d in G.nodes(data=True)}
        fig, ax = plt.subplots(figsize=(7, 7))
        nx.draw(G, pos=positions, ax=ax, node_size=8, edge_color="#4b5563", width=0.6, node_color="#1d4ed8")
        ax.set_title(f"{PLACE} — road network sanity plot ({source})")
        plt.tight_layout()
        plt.savefig(plot_path, dpi=200)
        plt.close(fig)
        log(f"Sanity plot saved to {plot_path}")
    except Exception as exc:  # noqa: BLE001
        log(f"Could not render sanity plot: {exc}")

    return G, source


# ---------------------------------------------------------------------------
# 1.2 Matrix representations
# ---------------------------------------------------------------------------

def matrix_demo(G: nx.Graph, nodelist: list) -> dict:
    log("[1.2] Matrix multiplication / reachability demonstration on a real subgraph...")
    start = nodelist[0]
    visited = [start]
    frontier = [start]
    while frontier and len(visited) < 5:
        nxt = []
        for n in frontier:
            for nbr in G.neighbors(n):
                if nbr not in visited:
                    visited.append(nbr)
                    nxt.append(nbr)
                if len(visited) >= 5:
                    break
            if len(visited) >= 5:
                break
        frontier = nxt
    sub_nodes = visited[:5]
    H = G.subgraph(sub_nodes)
    A = nx.to_numpy_array(H, nodelist=sub_nodes, weight=None)
    A2 = A @ A
    A3 = A2 @ A
    return {
        "subgraph_node_ids": [str(n) for n in sub_nodes],
        "adjacency_matrix": np.round(A, 4).tolist(),
        "A_squared": np.round(A2, 4).tolist(),
        "A_cubed": np.round(A3, 4).tolist(),
        "explanation": (
            "A is the 0/1 adjacency matrix of a 5-node subgraph taken from the real road "
            "network via BFS from an arbitrary start node. (A^k)_{ij} counts the number of "
            "length-k walks between node i and node j — e.g. (A^2)_{ii} counts 2-step closed "
            "walks (degree-related), and nonzero (A^3)_{ij} entries reveal pairs reachable in "
            "exactly 3 hops. This is the matrix-multiplication reasoning underlying reachability "
            "and (for weighted/shortest-path variants) the Dijkstra-based distance matrix used "
            "elsewhere in this pipeline."
        ),
    }


def build_matrices(G: nx.Graph, nodelist: list) -> tuple[np.ndarray, np.ndarray]:
    log("[1.2] Building adjacency and all-pairs shortest-path distance matrices...")
    adjacency_matrix = nx.to_numpy_array(G, nodelist=nodelist, weight=None)
    sparse_graph = nx.to_scipy_sparse_array(G, nodelist=nodelist, weight="length", format="csr").astype(np.float64)
    sparse_graph = sparse_graph.tocsr(copy=False)
    sparse_graph.indices = sparse_graph.indices.astype(np.int32)
    sparse_graph.indptr = sparse_graph.indptr.astype(np.int32)
    t0 = time.time()
    distance_matrix = csgraph.dijkstra(sparse_graph, directed=False, return_predecessors=False)
    log(f"Dijkstra all-pairs distance matrix computed in {time.time() - t0:.2f}s, shape {distance_matrix.shape}")
    return adjacency_matrix, distance_matrix


# ---------------------------------------------------------------------------
# 1.3 Network centrality (computed once, reused everywhere)
# ---------------------------------------------------------------------------

def compute_centrality(G: nx.Graph, nodelist: list) -> dict:
    log("[1.3] Computing centrality measures (degree, betweenness, closeness, eigenvector)...")
    degree = nx.degree_centrality(G)
    betweenness = nx.betweenness_centrality(G, weight="length")
    closeness = nx.closeness_centrality(G, distance="length")
    try:
        eigenvector = nx.eigenvector_centrality_numpy(G, weight="length")
    except Exception as exc:  # noqa: BLE001
        log(f"eigenvector_centrality_numpy failed ({exc}); falling back to power-iteration variant.")
        eigenvector = nx.eigenvector_centrality(G, max_iter=2000, weight="length")

    degree_vals = np.array([degree[n] for n in nodelist])
    bet_vals = np.array([betweenness[n] for n in nodelist])
    close_vals = np.array([closeness[n] for n in nodelist])
    eig_vals = np.array([eigenvector[n] for n in nodelist])

    def zscore(v: np.ndarray) -> np.ndarray:
        std = v.std()
        return np.zeros_like(v) if std == 0 else (v - v.mean()) / std

    composite = zscore(degree_vals) + zscore(bet_vals) + zscore(close_vals) + zscore(eig_vals)
    importance = {n: float(composite[i]) for i, n in enumerate(nodelist)}

    return {
        "degree": degree,
        "betweenness": betweenness,
        "closeness": closeness,
        "eigenvector": eigenvector,
        "importance": importance,
    }


# ---------------------------------------------------------------------------
# Traffic proxy (road hierarchy + centrality) — explicitly NOT live traffic data
# ---------------------------------------------------------------------------

def compute_traffic_proxy(G: nx.Graph, nodelist: list, node_index: dict, centrality: dict) -> dict:
    degree_vals = np.array([centrality["degree"][n] for n in nodelist])
    bet_vals = np.array([centrality["betweenness"][n] for n in nodelist])
    degree_norm = degree_vals / max(degree_vals.max(), 1e-9)
    bet_norm = bet_vals / max(bet_vals.max(), 1e-9)

    hierarchy_weight = {"motorway": 4.0, "trunk": 3.5, "primary": 3.0, "secondary": 2.5,
                         "tertiary": 2.0, "residential": 1.0, "living_street": 0.7,
                         "unclassified": 1.2, "service": 0.5}

    def road_class(v):
        if isinstance(v, list):
            v = v[0] if v else "residential"
        return v or "residential"

    road_weight = {}
    for node in nodelist:
        classes = [road_class(data.get("highway", "residential")) for _, _, data in G.edges(node, data=True)]
        if not classes:
            road_weight[node] = hierarchy_weight["residential"]
            continue
        road_weight[node] = float(np.mean([hierarchy_weight.get(c, 1.0) for c in classes]))

    max_hierarchy = max(road_weight.values()) if road_weight else 1.0
    traffic_proxy = {}
    for node in nodelist:
        idx = node_index[node]
        traffic_proxy[node] = float(
            0.5 * (road_weight[node] / max(max_hierarchy, 1e-9))
            + 0.3 * degree_norm[idx]
            + 0.2 * bet_norm[idx]
        )
    return traffic_proxy


# ---------------------------------------------------------------------------
# Population density proxy: real OSM building-footprint density + distance decay
# ---------------------------------------------------------------------------

def compute_population_proxy(G: nx.Graph, nodelist: list) -> tuple[dict, str]:
    log("[population proxy] Attempting to fetch OSM building footprints for a density proxy...")
    try:
        t0 = time.time()
        buildings = ox.features_from_bbox((WEST, SOUTH, EAST, NORTH), tags={"building": True})
        log(f"Fetched {len(buildings)} building footprints in {time.time() - t0:.1f}s")
        buildings_proj = ox.projection.project_gdf(buildings)
        centroids = buildings_proj.geometry.centroid
        building_xy = np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()])
        tree = cKDTree(building_xy)

        crs_ref = buildings_proj.crs
        import geopandas as gpd
        from shapely.geometry import Point

        node_points = [Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodelist]
        node_gdf = gpd.GeoDataFrame(geometry=node_points, crs="EPSG:4326").to_crs(crs_ref)
        node_xy = np.column_stack([node_gdf.geometry.x.to_numpy(), node_gdf.geometry.y.to_numpy()])

        counts = tree.query_ball_point(node_xy, r=BUILDING_RADIUS_M, return_length=True)
        area_m2 = math.pi * (BUILDING_RADIUS_M ** 2)
        density_per_hectare = counts / (area_m2 / 10_000.0)

        pop = {n: float(500.0 + 150.0 * density_per_hectare[i]) for i, n in enumerate(nodelist)}
        source = (
            f"OSM building-footprint density proxy: buildings within {BUILDING_RADIUS_M:.0f}m of each "
            "node, scaled to a documented 'proxy population units' scale (500 base + 150 per "
            "building/hectare). This is a real, OSM-derived density signal but is NOT a WorldPop or "
            "Census 2011 population count."
        )
        log("Population proxy source: OSM building-footprint density")
        return pop, source
    except Exception as exc:  # noqa: BLE001
        log(f"Building-footprint fetch failed ({exc}); falling back to distance-decay-from-center proxy.")
        centroid_lat, centroid_lon = (NORTH + SOUTH) / 2.0, (WEST + EAST) / 2.0
        pop = {}
        for node in nodelist:
            lat, lon = G.nodes[node]["y"], G.nodes[node]["x"]
            dist_km = math.hypot(lat - centroid_lat, lon - centroid_lon) * 111.0
            pop[node] = 500 + 3500 * math.exp(-(dist_km ** 2) / (2 * (2.2 ** 2)))
        source = (
            "Distance-decay-from-center proxy (building-footprint fetch unavailable this run): "
            "radial exponential decay around the study-area centroid. Coarse fallback proxy, "
            "NOT WorldPop or Census 2011 data."
        )
        return pop, source


# ---------------------------------------------------------------------------
# Existing stations + distance-to-nearest-station (road-network distance)
# ---------------------------------------------------------------------------

def load_existing_stations(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing existing station file: {path}")
    df = pd.read_csv(path)
    missing = {"latitude", "longitude"} - set(df.columns)
    if missing:
        raise ValueError(f"Existing station CSV is missing required columns: {sorted(missing)}")
    return df


def snap_stations_to_nodes(G: nx.Graph, nodelist: list, existing_df: pd.DataFrame) -> pd.DataFrame:
    df = existing_df.copy()
    node_lat = np.array([G.nodes[n]["y"] for n in nodelist])
    node_lon = np.array([G.nodes[n]["x"] for n in nodelist])
    node_ids = []
    for _, row in df.iterrows():
        d2 = (node_lat - row["latitude"]) ** 2 + (node_lon - row["longitude"]) ** 2
        node_ids.append(nodelist[int(np.argmin(d2))])
    df["node_id"] = node_ids
    return df


def nearest_station_distance(nodelist: list, node_index: dict, distance_matrix: np.ndarray, station_node_ids: list) -> np.ndarray:
    if not station_node_ids:
        return np.full(len(nodelist), np.inf)
    station_idx = [node_index[n] for n in station_node_ids]
    sub = distance_matrix[:, station_idx]
    return sub.min(axis=1)


# ---------------------------------------------------------------------------
# 1.4 Candidate feature vectors + PCA
# ---------------------------------------------------------------------------

def build_candidate_features(nodelist: list, G: nx.Graph, centrality: dict, traffic_proxy: dict,
                              population_proxy: dict, nearest_station_dist: np.ndarray) -> pd.DataFrame:
    rows = []
    for i, node in enumerate(nodelist):
        rows.append({
            "node_id": node,
            "latitude": float(G.nodes[node]["y"]),
            "longitude": float(G.nodes[node]["x"]),
            "population_density": float(population_proxy[node]),
            "traffic_proxy": float(traffic_proxy[node]),
            "degree_centrality": float(centrality["degree"][node]),
            "betweenness_centrality": float(centrality["betweenness"][node]),
            "closeness_centrality": float(centrality["closeness"][node]),
            "eigenvector_centrality": float(centrality["eigenvector"][node]),
            "composite_importance": float(centrality["importance"][node]),
            "distance_to_nearest_existing_station": float(nearest_station_dist[i]),
        })
    return pd.DataFrame(rows)


def compute_pca(candidate_df: pd.DataFrame) -> dict:
    log("[1.4] Running PCA on candidate feature matrix...")
    feature_cols = [
        "latitude", "longitude", "population_density", "traffic_proxy",
        "degree_centrality", "betweenness_centrality", "eigenvector_centrality",
        "distance_to_nearest_existing_station",
    ]
    X = candidate_df[feature_cols].to_numpy(dtype=float)
    X_scaled = StandardScaler().fit_transform(X)
    n_components = min(4, X_scaled.shape[1])
    pca = PCA(n_components=n_components)
    X_pca = pca.fit_transform(X_scaled)
    explained = pca.explained_variance_ratio_
    loadings = pca.components_.T * np.sqrt(pca.explained_variance_)
    loading_table = [
        {"feature": feat, "component_loadings": [float(v) for v in loadings[i]]}
        for i, feat in enumerate(feature_cols)
    ]
    return {
        "feature_columns": feature_cols,
        "explained_variance_ratio": [float(x) for x in explained],
        "cumulative_explained_variance": [float(x) for x in np.cumsum(explained)],
        "component_loadings": loading_table,
        "shape": list(X_pca.shape),
    }


# ---------------------------------------------------------------------------
# 1.5 Community detection (sub-regions for equity constraints)
# ---------------------------------------------------------------------------

def compute_communities(G: nx.Graph) -> list[list]:
    log(f"[1.5] Running Louvain community detection (resolution={COMMUNITY_RESOLUTION})...")
    communities = nx.community.louvain_communities(
        G, seed=42, weight="length", resolution=COMMUNITY_RESOLUTION
    )
    return [sorted(c, key=str) for c in communities]


# ---------------------------------------------------------------------------
# 1.6 Candidate shortlist
# ---------------------------------------------------------------------------

def shortlist_candidates(nodelist: list, node_index: dict, distance_matrix: np.ndarray,
                          candidate_df: pd.DataFrame, existing_df: pd.DataFrame,
                          min_spacing_m: float, max_candidates: int) -> list:
    log("[1.6] Shortlisting candidate sites...")
    excluded_near_existing = set()
    for node_id in existing_df["node_id"]:
        idx = node_index[node_id]
        near = np.where(distance_matrix[idx] < 300.0)[0]
        excluded_near_existing.update(nodelist[i] for i in near)

    ranked = candidate_df[~candidate_df["node_id"].isin(excluded_near_existing)]
    ranked = ranked.sort_values("composite_importance", ascending=False).to_dict("records")

    selected = []
    for item in ranked:
        cand = item["node_id"]
        cand_idx = node_index[cand]
        if any(distance_matrix[cand_idx, node_index[s]] < min_spacing_m for s in selected):
            continue
        selected.append(cand)
        if len(selected) >= max_candidates:
            break
    log(f"Shortlisted {len(selected)} candidate sites "
        f"(excluded {len(excluded_near_existing)} nodes within 300m of an existing station).")
    return selected


# ---------------------------------------------------------------------------
# 1.7 MCLP optimization (PuLP / CBC)
# ---------------------------------------------------------------------------

def solve_mclp(nodelist: list, node_index: dict, distance_matrix: np.ndarray, candidate_list: list,
                subregions: list[list], demand_weight: dict, budget: int,
                coverage_radius_m: float) -> dict:
    log(f"[1.7] Solving MCLP (MILP) for budget={budget}...")
    x = {j: pl.LpVariable(f"x_{node_index[j]}", cat="Binary") for j in candidate_list}
    y = {i: pl.LpVariable(f"y_{node_index[i]}", cat="Binary") for i in nodelist}

    model = pl.LpProblem("chargeopt_mclp", pl.LpMaximize)
    model += pl.lpSum(demand_weight[i] * y[i] for i in nodelist)
    model += pl.lpSum(x[j] for j in candidate_list) <= budget

    candidate_idx = np.array([node_index[j] for j in candidate_list])
    for i in nodelist:
        i_idx = node_index[i]
        within = distance_matrix[i_idx, candidate_idx] <= coverage_radius_m
        eligible = [x[candidate_list[k]] for k, ok in enumerate(within) if ok]
        if eligible:
            model += y[i] <= pl.lpSum(eligible)
        else:
            model += y[i] == 0

    region_demands = [sum(demand_weight[n] for n in region if n in demand_weight) for region in subregions]
    threshold = float(np.percentile(region_demands, EQUITY_DEMAND_PERCENTILE)) if region_demands else 0.0
    equity_regions_bound = []
    for region_nodes, region_demand in zip(subregions, region_demands):
        if region_demand >= threshold and region_demand > 0:
            region_candidates = [x[j] for j in candidate_list if j in set(region_nodes)]
            if region_candidates:
                model += pl.lpSum(region_candidates) >= 1
                equity_regions_bound.append(True)
            else:
                equity_regions_bound.append(False)
        else:
            equity_regions_bound.append(False)

    t0 = time.time()
    model.solve(pl.PULP_CBC_CMD(msg=False))
    solve_time = time.time() - t0
    status = pl.LpStatus[model.status]

    # CBC leaves stale/meaningless variable values on the model when a problem is
    # infeasible (e.g. leftover branch-and-bound relaxation values) — treating those
    # as a valid selection would silently present nonsense as an optimal plan.
    if status == "Optimal":
        selected = [j for j in candidate_list if x[j].value() and x[j].value() > 0.5]
        objective = float(pl.value(model.objective) or 0.0)
    else:
        selected = []
        objective = 0.0

    log(f"MCLP solved in {solve_time:.2f}s, status={status}, "
        f"selected={len(selected)}/{budget}, objective={objective:.2f}, "
        f"equity threshold(p{EQUITY_DEMAND_PERCENTILE})={threshold:.1f}, "
        f"regions bound={sum(equity_regions_bound)}/{len(subregions)}")

    return {
        "budget": budget,
        "selected_sites": selected,
        "objective_value": objective,
        "solver_status": status,
        "coverage_radius_m": coverage_radius_m,
        "equity_threshold": threshold,
        "solve_time_seconds": solve_time,
    }


def build_road_network_geojson(G: nx.Graph, nodelist: list) -> dict:
    """Simplified node/edge geometry for the frontend's road-network map layer."""
    log("[1.8] Packaging road network geometry for map rendering...")
    nodes_out = [
        {"node_id": str(n), "latitude": float(G.nodes[n]["y"]), "longitude": float(G.nodes[n]["x"])}
        for n in nodelist
    ]
    edges_out = []
    seen = set()
    for u, v, data in G.edges(data=True):
        key = frozenset((u, v))
        if key in seen:
            continue
        seen.add(key)
        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0] if highway else "residential"
        geom = data.get("geometry")
        if geom is not None and hasattr(geom, "coords"):
            coords = [[float(lat), float(lon)] for lon, lat in geom.coords]
        else:
            coords = [
                [float(G.nodes[u]["y"]), float(G.nodes[u]["x"])],
                [float(G.nodes[v]["y"]), float(G.nodes[v]["x"])],
            ]
        edges_out.append({"highway": highway, "coords": coords})
    return {"nodes": nodes_out, "edges": edges_out}


def build_optimization_input(G: nx.Graph, nodelist: list, node_index: dict, distance_matrix: np.ndarray,
                              shortlist: list, subregions: list[list], demand_weight: dict,
                              candidate_df: pd.DataFrame, coverage_radius_m: float,
                              equity_percentile: int, existing_station_nodes: list) -> dict:
    """Self-contained MCLP problem data (candidates, demand nodes, coverage sets,
    sub-region membership) so the backend can re-solve the exact same MILP live for
    any budget, without needing the full graph or osmnx at request time."""
    log("[opt-input] Packaging self-contained MCLP problem data for live backend re-solve...")
    coverage = {}
    for j in shortlist:
        j_idx = node_index[j]
        within = np.where(distance_matrix[:, j_idx] <= coverage_radius_m)[0]
        coverage[str(j)] = [str(nodelist[i]) for i in within]

    region_demands = [sum(demand_weight[n] for n in region if n in demand_weight) for region in subregions]
    threshold = float(np.percentile(region_demands, equity_percentile)) if region_demands else 0.0

    candidate_lookup = candidate_df.set_index("node_id")
    candidates_out = []
    for j in shortlist:
        row = candidate_lookup.loc[j]
        candidates_out.append({
            "node_id": str(j),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
        })

    demand_nodes_out = [
        {"node_id": str(n), "latitude": float(G.nodes[n]["y"]), "longitude": float(G.nodes[n]["x"]),
         "demand_weight": float(demand_weight[n])}
        for n in nodelist
    ]

    subregions_out = [
        {"id": r_id, "node_ids": [str(n) for n in region], "total_demand": float(region_demands[r_id])}
        for r_id, region in enumerate(subregions)
    ]

    existing_dist = nearest_station_distance(nodelist, node_index, distance_matrix, existing_station_nodes)
    existing_coverage = [str(nodelist[i]) for i, d in enumerate(existing_dist) if d <= coverage_radius_m]

    return {
        "candidates": candidates_out,
        "demand_nodes": demand_nodes_out,
        "coverage": coverage,
        "subregions": subregions_out,
        "coverage_radius_m": coverage_radius_m,
        "equity_demand_threshold": threshold,
        "equity_demand_percentile": equity_percentile,
        "existing_coverage_demand_node_ids": existing_coverage,
    }


def compute_coverage_stats(nodelist: list, node_index: dict, distance_matrix: np.ndarray,
                            existing_station_nodes: list, selected_sites: list,
                            demand_weight: dict, coverage_radius_m: float) -> dict:
    before_dist = nearest_station_distance(nodelist, node_index, distance_matrix, existing_station_nodes)
    after_station_nodes = list(dict.fromkeys(existing_station_nodes + selected_sites))
    after_dist = nearest_station_distance(nodelist, node_index, distance_matrix, after_station_nodes)

    total_weight = sum(demand_weight.values())
    weights = np.array([demand_weight[n] for n in nodelist])
    covered_before = weights[before_dist <= coverage_radius_m].sum()
    covered_after = weights[after_dist <= coverage_radius_m].sum()

    finite_before = before_dist[np.isfinite(before_dist)]
    finite_after = after_dist[np.isfinite(after_dist)]

    return {
        "before_pct_covered": float(covered_before / total_weight * 100.0) if total_weight else 0.0,
        "after_pct_covered": float(covered_after / total_weight * 100.0) if total_weight else 0.0,
        "before_avg_distance_m": float(finite_before.mean()) if len(finite_before) else None,
        "after_avg_distance_m": float(finite_after.mean()) if len(finite_after) else None,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_results() -> dict:
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)
    G, graph_source = build_graph()
    nodelist = list(G.nodes())
    node_index = {n: i for i, n in enumerate(nodelist)}

    matrix_demo_record = matrix_demo(G, nodelist)
    adjacency_matrix, distance_matrix = build_matrices(G, nodelist)

    centrality = compute_centrality(G, nodelist)
    traffic_proxy = compute_traffic_proxy(G, nodelist, node_index, centrality)
    population_proxy, population_proxy_source = compute_population_proxy(G, nodelist)

    existing_df = load_existing_stations(DATA_DIR / "existing_stations.csv")
    existing_df = snap_stations_to_nodes(G, nodelist, existing_df)
    existing_station_nodes = existing_df["node_id"].tolist()
    nearest_station_dist = nearest_station_distance(nodelist, node_index, distance_matrix, existing_station_nodes)

    candidate_df = build_candidate_features(nodelist, G, centrality, traffic_proxy, population_proxy, nearest_station_dist)

    pca_summary = compute_pca(candidate_df)
    subregions = compute_communities(G)
    log(f"Found {len(subregions)} sub-regions (community detection), "
        f"sizes={[len(r) for r in subregions]}")

    shortlist = shortlist_candidates(nodelist, node_index, distance_matrix, candidate_df, existing_df,
                                      MIN_CANDIDATE_SPACING_M, MAX_CANDIDATES)
    shortlist_df = candidate_df[candidate_df["node_id"].isin(shortlist)].copy()

    demand_weight = {
        n: float(population_proxy[n]) + 0.2 * float(centrality["degree"][n]) * 1000.0
        + 0.2 * float(centrality["eigenvector"][n]) * 1000.0
        for n in nodelist
    }

    log("[1.7 sanity check] Comparing MCLP at B=5 vs B=15 before proceeding...")
    sanity_runs = {}
    for b in (5, 15):
        res = solve_mclp(nodelist, node_index, distance_matrix, shortlist, subregions, demand_weight, b, COVERAGE_RADIUS_M)
        if res["solver_status"] != "Optimal":
            raise RuntimeError(
                f"MCLP sanity check failed: budget={b} solved as '{res['solver_status']}', not Optimal. "
                "The equity constraint likely mandates more sub-regions than this budget allows — "
                "reduce EQUITY_DEMAND_PERCENTILE, coarsen COMMUNITY_RESOLUTION, or raise the budget."
            )
        stats = compute_coverage_stats(nodelist, node_index, distance_matrix, existing_station_nodes,
                                        res["selected_sites"], demand_weight, COVERAGE_RADIUS_M)
        sanity_runs[b] = {"selected": len(res["selected_sites"]), "objective": res["objective_value"],
                           "after_pct_covered": stats["after_pct_covered"]}
    log(f"Sanity check results: {sanity_runs}")
    if sanity_runs[15]["objective"] < sanity_runs[5]["objective"]:
        raise RuntimeError("MCLP sanity check failed: objective did not increase with budget. Model is wrong.")
    if sanity_runs[15]["selected"] <= sanity_runs[5]["selected"]:
        raise RuntimeError("MCLP sanity check failed: higher budget did not select more sites.")
    log("Sanity check passed: larger budget -> more sites selected -> higher objective/coverage.")

    optimization = solve_mclp(nodelist, node_index, distance_matrix, shortlist, subregions, demand_weight,
                               DEFAULT_BUDGET, COVERAGE_RADIUS_M)
    if optimization["solver_status"] != "Optimal":
        raise RuntimeError(f"Default budget={DEFAULT_BUDGET} solved as '{optimization['solver_status']}', not Optimal.")
    stats = compute_coverage_stats(nodelist, node_index, distance_matrix, existing_station_nodes,
                                    optimization["selected_sites"], demand_weight, COVERAGE_RADIUS_M)

    optimization_input = build_optimization_input(G, nodelist, node_index, distance_matrix, shortlist,
                                                    subregions, demand_weight, candidate_df,
                                                    COVERAGE_RADIUS_M, EQUITY_DEMAND_PERCENTILE,
                                                    existing_station_nodes)
    optimization_input["existing_station_node_ids"] = [str(n) for n in existing_station_nodes]

    road_network = build_road_network_geojson(G, nodelist)

    selected_candidates = []
    for node in optimization["selected_sites"]:
        row = shortlist_df[shortlist_df["node_id"] == node].iloc[0]
        selected_candidates.append({
            "node_id": str(node),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "composite_importance": float(row["composite_importance"]),
        })

    subregion_geo = []
    for r_id, region in enumerate(subregions):
        coords = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in region]  # (lon, lat)
        hull_coords = []
        if len(coords) >= 3:
            try:
                from scipy.spatial import ConvexHull
                pts = np.array(coords)
                hull = ConvexHull(pts)
                hull_coords = [[float(pts[i][1]), float(pts[i][0])] for i in hull.vertices]  # [lat, lon]
            except Exception as exc:  # noqa: BLE001
                log(f"Convex hull failed for subregion {r_id}: {exc}")
        subregion_geo.append({
            "id": r_id,
            "node_count": len(region),
            "centroid": [
                sum(c[1] for c in coords) / max(len(coords), 1),
                sum(c[0] for c in coords) / max(len(coords), 1),
            ],
            "boundary": hull_coords,  # [[lat, lon], ...] convex hull of member nodes
            "node_ids": [str(n) for n in region],
            "total_demand": float(sum(demand_weight.get(n, 0.0) for n in region)),
        })

    output = {
        "graph_source": graph_source,
        "study_area": {
            "name": PLACE,
            "bbox": {"north": NORTH, "south": SOUTH, "east": EAST, "west": WEST},
        },
        "graph_summary": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "connected_components": nx.number_connected_components(G),
        },
        "matrix_summary": {
            "adjacency_shape": list(adjacency_matrix.shape),
            "distance_shape": list(distance_matrix.shape),
            "adjacency_matrix_sample": np.round(adjacency_matrix[:5, :5], 4).tolist(),
            "distance_matrix_sample_m": np.round(distance_matrix[:5, :5], 2).tolist(),
            "matrix_demo": matrix_demo_record,
        },
        "centrality": {
            "degree": {str(k): float(v) for k, v in centrality["degree"].items()},
            "betweenness": {str(k): float(v) for k, v in centrality["betweenness"].items()},
            "closeness": {str(k): float(v) for k, v in centrality["closeness"].items()},
            "eigenvector": {str(k): float(v) for k, v in centrality["eigenvector"].items()},
            "importance_score": {str(k): float(v) for k, v in centrality["importance"].items()},
        },
        "candidate_sites": [
            {
                # NOTE: use to_dict("records"), not iterrows() — shortlist_df is all-numeric,
                # so iterrows() would coerce each row's int64 node_id to float64 (pandas'
                # per-row dtype-unification gotcha), corrupting IDs to "12345.0" strings.
                "node_id": str(row["node_id"]),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "population_density": float(row["population_density"]),
                "traffic_proxy": float(row["traffic_proxy"]),
                "degree_centrality": float(row["degree_centrality"]),
                "betweenness_centrality": float(row["betweenness_centrality"]),
                "closeness_centrality": float(row["closeness_centrality"]),
                "eigenvector_centrality": float(row["eigenvector_centrality"]),
                "composite_importance": float(row["composite_importance"]),
                "distance_to_nearest_existing_station": float(row["distance_to_nearest_existing_station"]),
            }
            for row in shortlist_df.to_dict("records")
        ],
        "pca": pca_summary,
        "subregions": subregion_geo,
        "optimization_input": optimization_input,
        "road_network": road_network,
        "existing_stations": [
            {
                "station_name": row.get("station_name", f"station_{i}"),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "operator": row.get("operator", ""),
                "source": row.get("source", "synthetic_generated"),
                "notes": row.get("notes", ""),
            }
            for i, row in existing_df.iterrows()
        ],
        "optimization": {
            "default_budget": DEFAULT_BUDGET,
            "selected_sites": selected_candidates,
            "objective_value": optimization["objective_value"],
            "solver_status": optimization["solver_status"],
            "coverage_radius_m": optimization["coverage_radius_m"],
            "equity_demand_threshold": optimization["equity_threshold"],
            "coverage_stats": stats,
            "budget_sensitivity_check": sanity_runs,
        },
        "provenance": {
            "data_sources": {
                "road_network": (
                    "Real OSM road network fetched live via osmnx/Overpass."
                    if graph_source == "live_osm_network" else
                    "SYNTHETIC offline fallback grid graph (live OSM fetch was unavailable this run). "
                    "Not real road data."
                ),
                "traffic_proxy": "Derived from OSM road hierarchy (highway=*) + graph degree/betweenness centrality. Not live traffic data.",
                "population_density": population_proxy_source,
                "existing_stations": (
                    "SYNTHETIC, deterministically generated (see scripts/generate_synthetic_stations.py). "
                    "NOT sourced from PlugShare/Google Maps and NOT manually curated. Replace "
                    "data/existing_stations.csv with a real curated CSV before drawing real-world conclusions."
                ),
            },
            "limitations": [
                "Traffic proxy is derived from topology and road class, not real-time traffic measurements.",
                "Population density is an OSM-derived proxy (or, on fallback, a distance-decay proxy), not a formal WorldPop/Census raster — real population counts are not available offline in this environment.",
                "Existing charging station locations are synthetic placeholders, not real curated data.",
                "The bounded study area is intentionally limited to a ~4km x 4km sub-area of Andheri West to keep graph size tractable for live centrality/MILP computation.",
            ],
        },
    }

    results_path = RESULTS_DIR / "results.json"
    with results_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    log(f"[1.8] Results written to {results_path}")
    return output


if __name__ == "__main__":
    build_results()
