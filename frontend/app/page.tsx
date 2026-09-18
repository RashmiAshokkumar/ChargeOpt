"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { api } from "../lib/api";
import type {
  CandidateSite,
  CoverageStats,
  ExistingStation,
  HeatmapPoint,
  NetworkStats,
  RoadNetwork,
  SelectedSite,
  SubregionFeature,
} from "../lib/api";
import { useDebouncedValue } from "../lib/useDebounce";
import Sidebar from "../components/Sidebar";
import MethodologyPanel from "../components/MethodologyPanel";
import type { LayerToggles } from "../components/MapView";

const MapView = dynamic(() => import("../components/MapView"), { ssr: false });

const EMPTY_COVERAGE: CoverageStats = { before_pct_covered: 0, after_pct_covered: 0 };

export default function HomePage() {
  const [stats, setStats] = useState<NetworkStats | null>(null);
  const [roadNetwork, setRoadNetwork] = useState<RoadNetwork | null>(null);
  const [heatmapPoints, setHeatmapPoints] = useState<HeatmapPoint[]>([]);
  const [candidates, setCandidates] = useState<CandidateSite[]>([]);
  const [existingStations, setExistingStations] = useState<ExistingStation[]>([]);
  const [subregionFeatures, setSubregionFeatures] = useState<SubregionFeature[]>([]);

  const [selectedSites, setSelectedSites] = useState<SelectedSite[]>([]);
  const [coverage, setCoverage] = useState<CoverageStats>(EMPTY_COVERAGE);
  const [optimizeError, setOptimizeError] = useState<string | null>(null);
  const [solveTimeSeconds, setSolveTimeSeconds] = useState<number | null>(null);

  const [budgetInput, setBudgetInput] = useState(10);
  const debouncedBudget = useDebouncedValue(budgetInput, 400);

  const [layers, setLayers] = useState<LayerToggles>({
    roadNetwork: true,
    heatmap: false,
    existingStations: true,
    recommendedStations: true,
    subregions: false,
  });

  useEffect(() => {
    (async () => {
      try {
        const [statsData, roadData, heatmapData, candidatesData, stationsData, subregionsData] =
          await Promise.all([
            api.networkStats(),
            api.roadNetwork(),
            api.demandHeatmap(),
            api.candidates(),
            api.existingStations(),
            api.subregions(),
          ]);
        setStats(statsData);
        setRoadNetwork(roadData);
        setHeatmapPoints(heatmapData.points);
        setCandidates(candidatesData.candidates);
        setExistingStations(stationsData.existing_stations);
        setSubregionFeatures(subregionsData.features);
      } catch (error) {
        console.error("Failed to load initial API data", error);
      }
    })();
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const result = await api.optimize(debouncedBudget);
        setSelectedSites(result.selected_sites);
        setCoverage(result.coverage_stats);
        setSolveTimeSeconds(result.solve_time_seconds ?? null);
        setOptimizeError((result as { error?: string }).error ?? null);
      } catch (error) {
        console.error("Optimization request failed", error);
        setOptimizeError("Could not reach the optimization API.");
      }
    })();
  }, [debouncedBudget]);

  const toggleLayer = useCallback((key: keyof LayerToggles) => {
    setLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const topCandidates = useMemo(
    () => [...candidates].sort((a, b) => b.composite_importance - a.composite_importance),
    [candidates]
  );

  return (
    <main>
      <div className="app-shell">
        <header className="app-header">
          <h1>ChargeOpt</h1>
          <p>
            EV charging station placement optimizer for {stats?.study_area.name ?? "a bounded study area"} —
            road-network centrality, PCA, community detection, and a real MCLP MILP (PuLP/CBC).
          </p>
        </header>

        <div className="layout-grid">
          <section className="panel map-panel">
            <div className="map-inner">
              <MapView
                roadNetwork={roadNetwork}
                heatmapPoints={heatmapPoints}
                existingStations={existingStations}
                selectedSites={selectedSites}
                candidates={candidates}
                subregionFeatures={subregionFeatures}
                layers={layers}
              />
            </div>
          </section>

          <Sidebar
            budget={budgetInput}
            onBudgetChange={setBudgetInput}
            stats={stats}
            coverage={coverage}
            topCandidates={topCandidates}
            layers={layers}
            onToggleLayer={toggleLayer}
            optimizeError={optimizeError}
            solveTimeSeconds={solveTimeSeconds}
            selectedCount={selectedSites.length}
          />
        </div>

        <MethodologyPanel stats={stats} />
      </div>
    </main>
  );
}
