"use client";

import type { NetworkStats } from "../lib/api";

type Props = {
  stats: NetworkStats | null;
};

const SOURCE_LABEL: Record<string, string> = {
  road_network: "Road network",
  traffic_proxy: "Traffic proxy",
  population_density: "Population density",
  existing_stations: "Existing stations",
};

export default function MethodologyPanel({ stats }: Props) {
  const provenance = stats?.provenance;

  return (
    <footer className="panel methodology-panel">
      <h2>Data &amp; Methodology</h2>
      <p className="muted-text">
        Every data source below is labeled by how it was obtained. This is generated directly
        from the pipeline&apos;s provenance record (see results.json), not hardcoded.
      </p>
      <ul>
        {provenance &&
          Object.entries(provenance.data_sources).map(([key, value]) => (
            <li key={key}>
              <strong>{SOURCE_LABEL[key] ?? key}:</strong> {value}
            </li>
          ))}
        {!provenance && <li>Loading provenance from API…</li>}
      </ul>

      {provenance && provenance.limitations.length > 0 && (
        <>
          <h3>Stated limitations</h3>
          <ul>
            {provenance.limitations.map((limitation, i) => (
              <li key={i}>{limitation}</li>
            ))}
          </ul>
        </>
      )}

      <h3>Optimization method</h3>
      <p>
        New station sites are chosen by solving a Maximum Covering Location Problem (MCLP) as a
        Mixed-Integer Linear Program with PuLP/CBC — not by ranking candidates by a score.
        Centrality and PCA feed into candidate generation only; the MILP performs the actual
        budget-constrained site selection, subject to road-network coverage and per-sub-region
        equity constraints.
      </p>
    </footer>
  );
}
