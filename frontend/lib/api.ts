export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api";

export type CandidateSite = {
  node_id: string;
  latitude: number;
  longitude: number;
  population_density: number;
  traffic_proxy: number;
  degree_centrality: number;
  betweenness_centrality: number;
  closeness_centrality: number;
  eigenvector_centrality: number;
  composite_importance: number;
  distance_to_nearest_existing_station: number;
  subregion_id: number | null;
};

export type ExistingStation = {
  station_name: string;
  latitude: number;
  longitude: number;
  operator: string;
  source: string;
  notes: string;
};

export type SelectedSite = {
  node_id: string;
  latitude: number;
  longitude: number;
};

export type CoverageStats = {
  before_pct_covered: number;
  after_pct_covered: number;
  before_avg_distance_m?: number | null;
  after_avg_distance_m?: number | null;
};

export type OptimizeResponse = {
  selected_sites: SelectedSite[];
  budget: number;
  objective_value: number;
  solver_status: string;
  coverage_stats: CoverageStats;
  solve_time_seconds: number;
  run_id: number;
};

export type NetworkStats = {
  graph_source: string;
  study_area: { name: string; bbox: Record<string, number> };
  summary: {
    nodes: number;
    edges: number;
    connected_components: number;
    candidate_sites: number;
    subregions: number;
  };
  matrix_summary: {
    adjacency_shape: number[];
    distance_shape: number[];
    matrix_demo?: {
      subgraph_node_ids: string[];
      adjacency_matrix: number[][];
      A_squared: number[][];
      A_cubed: number[][];
      explanation: string;
    };
  };
  pca: {
    explained_variance_ratio: number[];
    cumulative_explained_variance: number[];
    feature_columns: string[];
    component_loadings: { feature: string; component_loadings: number[] }[];
  };
  provenance: {
    data_sources: Record<string, string>;
    limitations: string[];
  };
};

export type RoadNetwork = {
  nodes: { node_id: string; latitude: number; longitude: number }[];
  edges: { highway: string; coords: [number, number][] }[];
};

export type HeatmapPoint = { lat: number; lon: number; weight: number };

export type SubregionFeature = {
  type: "Feature";
  properties: { id: number; node_count: number; total_demand: number; centroid: [number, number] };
  geometry: { type: "Polygon" | "Point"; coordinates: number[][][] | number[] };
};

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export const api = {
  networkStats: () => getJSON<NetworkStats>("/network-stats/"),
  roadNetwork: () => getJSON<RoadNetwork>("/road-network/"),
  demandHeatmap: () => getJSON<{ points: HeatmapPoint[] }>("/demand-heatmap/"),
  candidates: () => getJSON<{ candidates: CandidateSite[] }>("/candidates/"),
  existingStations: () => getJSON<{ existing_stations: ExistingStation[] }>("/existing-stations/"),
  subregions: () => getJSON<{ type: string; features: SubregionFeature[] }>("/subregions/"),
  optimize: async (budget: number): Promise<OptimizeResponse> => {
    const res = await fetch(`${API_BASE_URL}/optimize/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ budget }),
    });
    if (!res.ok) throw new Error(`optimize failed: ${res.status}`);
    return res.json();
  },
};
