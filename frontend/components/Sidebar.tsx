"use client";

import type { CandidateSite, CoverageStats, NetworkStats } from "../lib/api";
import type { LayerToggles } from "./MapView";

type Props = {
  budget: number;
  onBudgetChange: (n: number) => void;
  stats: NetworkStats | null;
  coverage: CoverageStats;
  topCandidates: CandidateSite[];
  layers: LayerToggles;
  onToggleLayer: (key: keyof LayerToggles) => void;
  optimizeError: string | null;
  solveTimeSeconds: number | null;
  selectedCount: number;
};

const LAYER_LABELS: Record<keyof LayerToggles, string> = {
  roadNetwork: "Road network",
  heatmap: "Demand / population heatmap",
  existingStations: "Existing stations (synthetic)",
  recommendedStations: "Recommended new stations",
  subregions: "Sub-region boundaries",
};

export default function Sidebar({
  budget,
  onBudgetChange,
  stats,
  coverage,
  topCandidates,
  layers,
  onToggleLayer,
  optimizeError,
  solveTimeSeconds,
  selectedCount,
}: Props) {
  return (
    <aside className="panel">
      <h2>Controls</h2>
      <label htmlFor="budget" className="budget-label">
        Budget: {budget} new stations {selectedCount > 0 && `(${selectedCount} selected)`}
      </label>
      <input
        id="budget"
        type="range"
        min={1}
        max={30}
        step={1}
        value={budget}
        onChange={(event) => onBudgetChange(Number(event.target.value))}
        style={{ width: "100%" }}
      />
      {optimizeError && <p className="error-text">{optimizeError}</p>}
      {solveTimeSeconds !== null && (
        <p className="muted-text">MILP re-solved live in {solveTimeSeconds.toFixed(2)}s (PuLP/CBC)</p>
      )}

      <div className="stat-grid">
        <div><strong>Nodes:</strong> {stats?.summary.nodes ?? "—"}</div>
        <div><strong>Edges:</strong> {stats?.summary.edges ?? "—"}</div>
        <div><strong>Candidate sites:</strong> {stats?.summary.candidate_sites ?? "—"}</div>
        <div><strong>Sub-regions:</strong> {stats?.summary.subregions ?? "—"}</div>
        <div><strong>Graph source:</strong> {stats?.graph_source ?? "—"}</div>
      </div>

      <h3>Coverage stats</h3>
      <div className="stat-grid">
        <div>Before: {coverage.before_pct_covered.toFixed(1)}% demand covered</div>
        <div>After: {coverage.after_pct_covered.toFixed(1)}% demand covered</div>
      </div>

      <h3>Map layers</h3>
      <div className="layer-toggles">
        {(Object.keys(LAYER_LABELS) as (keyof LayerToggles)[]).map((key) => (
          <label key={key} className="layer-toggle">
            <input type="checkbox" checked={layers[key]} onChange={() => onToggleLayer(key)} />
            {LAYER_LABELS[key]}
          </label>
        ))}
      </div>

      <h3>Top candidate sites (by composite importance)</h3>
      <table className="candidate-table">
        <thead>
          <tr>
            <th>Node</th>
            <th>Importance</th>
          </tr>
        </thead>
        <tbody>
          {topCandidates.slice(0, 8).map((c) => (
            <tr key={c.node_id}>
              <td>{c.node_id}</td>
              <td>{c.composite_importance.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </aside>
  );
}
