"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api";

const MapContainer: any = dynamic(
  async () => {
    const mod = await import("react-leaflet");
    return mod.MapContainer as any;
  },
  { ssr: false }
);

const TileLayer: any = dynamic(
  async () => {
    const mod = await import("react-leaflet");
    return mod.TileLayer as any;
  },
  { ssr: false }
);

const Marker: any = dynamic(
  async () => {
    const mod = await import("react-leaflet");
    return mod.Marker as any;
  },
  { ssr: false }
);

const Popup: any = dynamic(
  async () => {
    const mod = await import("react-leaflet");
    return mod.Popup as any;
  },
  { ssr: false }
);

const Circle: any = dynamic(
  async () => {
    const mod = await import("react-leaflet");
    return mod.Circle as any;
  },
  { ssr: false }
);

const DEFAULT_CENTER = [19.124, 72.83] as [number, number];

type CandidateSite = {
  node_id: string;
  latitude: number;
  longitude: number;
  composite_importance: number;
};

type ExistingStation = {
  station_name: string;
  latitude: number;
  longitude: number;
  source: string;
};

type NetworkStats = {
  summary: {
    nodes: number;
    edges: number;
    candidate_sites: number;
    subregions: number;
    graph_source: string;
  };
};

export default function HomePage() {
  const [stats, setStats] = useState<NetworkStats | null>(null);
  const [candidates, setCandidates] = useState<CandidateSite[]>([]);
  const [existingStations, setExistingStations] = useState<ExistingStation[]>([]);
  const [selectedSites, setSelectedSites] = useState<CandidateSite[]>([]);
  const [coverage, setCoverage] = useState({
    before_pct_covered: 0,
    after_pct_covered: 0,
    before_avg_distance: 0,
    after_avg_distance: 0,
  });
  const [budget, setBudget] = useState(6);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [statsRes, candidatesRes, stationsRes] = await Promise.all([
          fetch(`${API_BASE_URL}/network-stats/`),
          fetch(`${API_BASE_URL}/candidates/`),
          fetch(`${API_BASE_URL}/existing-stations/`),
        ]);

        const statsData = await statsRes.json();
        const candidatesData = await candidatesRes.json();
        const stationsData = await stationsRes.json();

        setStats(statsData);
        setCandidates(candidatesData.candidates || []);
        setExistingStations(stationsData.existing_stations || []);
      } catch (error) {
        console.error("Failed to load API data", error);
      } finally {
        setLoading(false);
      }
    };

    load();
  }, []);

  useEffect(() => {
    const updateOptimization = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/optimize/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ budget }),
        });
        const data = await res.json();
        setSelectedSites(data.selected_sites || []);
        setCoverage(data.coverage_stats || {
          before_pct_covered: 0,
          after_pct_covered: 0,
          before_avg_distance: 0,
          after_avg_distance: 0,
        });
      } catch (error) {
        console.error("Optimization request failed", error);
      }
    };

    updateOptimization();
  }, [budget]);

  const topCandidates = useMemo(
    () => [...candidates].sort((a, b) => b.composite_importance - a.composite_importance).slice(0, 8),
    [candidates]
  );

  return (
    <main style={{ padding: "1.5rem", fontFamily: "sans-serif", background: "#f3f6fa", minHeight: "100vh" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        <header style={{ marginBottom: "1rem" }}>
          <h1 style={{ marginBottom: 6 }}>ChargeOpt</h1>
          <p style={{ margin: 0, color: "#374151" }}>
            EV charging station placement optimizer for a bounded Indian-city study area.
          </p>
        </header>

        <div style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr", gap: "1rem" }}>
          <section style={{ background: "white", borderRadius: 12, padding: "1rem", boxShadow: "0 4px 16px rgba(0,0,0,0.05)" }}>
            <div style={{ height: 460 }}>
              {!loading && (
                <MapContainer center={DEFAULT_CENTER} zoom={13} style={{ width: "100%", height: "100%", borderRadius: 8 }}>
                  <TileLayer
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  />

                  {existingStations.map((station, index) => (
                    <Marker key={`${station.station_name}-${index}`} position={[station.latitude, station.longitude]}>
                      <Popup>Existing station: {station.station_name}</Popup>
                    </Marker>
                  ))}

                  {selectedSites.map((site, index) => (
                    <Circle
                      key={`${site.node_id}-${index}`}
                      center={[site.latitude, site.longitude]}
                      radius={250}
                      pathOptions={{ color: "green", fillColor: "green", fillOpacity: 0.4 }}
                    >
                      <Popup>Recommended station: {site.node_id}</Popup>
                    </Circle>
                  ))}
                </MapContainer>
              )}
            </div>
          </section>

          <aside style={{ background: "white", borderRadius: 12, padding: "1rem", boxShadow: "0 4px 16px rgba(0,0,0,0.05)" }}>
            <h2 style={{ marginTop: 0 }}>Controls</h2>
            <label htmlFor="budget" style={{ display: "block", marginBottom: 8, fontWeight: 600 }}>
              Budget: {budget} stations
            </label>
            <input
              id="budget"
              type="range"
              min={1}
              max={12}
              step={1}
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
              style={{ width: "100%" }}
            />

            <div style={{ marginTop: "1rem", display: "grid", gap: 8 }}>
              <div><strong>Nodes:</strong> {stats?.summary.nodes ?? 0}</div>
              <div><strong>Edges:</strong> {stats?.summary.edges ?? 0}</div>
              <div><strong>Candidate sites:</strong> {stats?.summary.candidate_sites ?? 0}</div>
              <div><strong>Subregions:</strong> {stats?.summary.subregions ?? 0}</div>
              <div><strong>Graph source:</strong> {stats?.summary.graph_source ?? "unknown"}</div>
            </div>

            <div style={{ marginTop: "1.25rem" }}>
              <h3 style={{ marginBottom: 8 }}>Coverage stats</h3>
              <div>Before coverage: {coverage.before_pct_covered.toFixed(1)}%</div>
              <div>After coverage: {coverage.after_pct_covered.toFixed(1)}%</div>
              <div>Before avg distance: {coverage.before_avg_distance.toFixed(1)} m</div>
              <div>After avg distance: {coverage.after_avg_distance.toFixed(1)} m</div>
            </div>
          </aside>
        </div>

        <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginTop: "1rem" }}>
          <div style={{ background: "white", borderRadius: 12, padding: "1rem", boxShadow: "0 4px 16px rgba(0,0,0,0.05)" }}>
            <h2 style={{ marginTop: 0 }}>Top candidate sites</h2>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "0.5rem 0" }}>Node</th>
                  <th style={{ textAlign: "left", padding: "0.5rem 0" }}>Importance</th>
                </tr>
              </thead>
              <tbody>
                {topCandidates.map((candidate) => (
                  <tr key={candidate.node_id}>
                    <td style={{ padding: "0.45rem 0" }}>{candidate.node_id}</td>
                    <td>{candidate.composite_importance.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ background: "white", borderRadius: 12, padding: "1rem", boxShadow: "0 4px 16px rgba(0,0,0,0.05)" }}>
            <h2 style={{ marginTop: 0 }}>Data &amp; Methodology</h2>
            <ul style={{ margin: 0, paddingLeft: "1.2rem", lineHeight: 1.7 }}>
              <li>Real network data: OSM road network used where available.</li>
              <li>Traffic proxy: derived from road hierarchy and graph centrality, not live traffic API data.</li>
              <li>Existing stations: manually curated CSV input, not scraped automatically.</li>
              <li>Population density: coarse proxy with explicit staleness limitation; not assumed to be live census data.</li>
              <li>Optimization: MILP solved with PuLP/CBC in the offline pipeline, then surfaced via the API.</li>
            </ul>
          </div>
        </section>
      </div>
    </main>
  );
}
