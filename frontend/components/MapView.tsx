"use client";

import { MapContainer, TileLayer, Marker, Popup, Polyline, Polygon, CircleMarker } from "react-leaflet";
import HeatmapLayer from "./HeatmapLayer";
import type {
  CandidateSite,
  ExistingStation,
  HeatmapPoint,
  RoadNetwork,
  SelectedSite,
  SubregionFeature,
} from "../lib/api";

const DEFAULT_CENTER: [number, number] = [19.1197, 72.8296];

export type LayerToggles = {
  roadNetwork: boolean;
  heatmap: boolean;
  existingStations: boolean;
  recommendedStations: boolean;
  subregions: boolean;
};

type Props = {
  roadNetwork: RoadNetwork | null;
  heatmapPoints: HeatmapPoint[];
  existingStations: ExistingStation[];
  selectedSites: SelectedSite[];
  candidates: CandidateSite[];
  subregionFeatures: SubregionFeature[];
  layers: LayerToggles;
};

const HIGHWAY_COLOR: Record<string, string> = {
  motorway: "#7c2d12",
  trunk: "#9a3412",
  primary: "#c2410c",
  secondary: "#ea580c",
  tertiary: "#f97316",
  residential: "#94a3b8",
  living_street: "#cbd5e1",
  unclassified: "#cbd5e1",
  service: "#e2e8f0",
};

export default function MapView({
  roadNetwork,
  heatmapPoints,
  existingStations,
  selectedSites,
  candidates,
  subregionFeatures,
  layers,
}: Props) {
  return (
    <MapContainer center={DEFAULT_CENTER} zoom={14} style={{ width: "100%", height: "100%", borderRadius: 8 }}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {layers.heatmap && <HeatmapLayer points={heatmapPoints} visible={layers.heatmap} />}

      {layers.roadNetwork &&
        roadNetwork?.edges.map((edge, i) => (
          <Polyline
            key={i}
            positions={edge.coords}
            pathOptions={{
              color: HIGHWAY_COLOR[edge.highway] || "#94a3b8",
              weight: ["motorway", "trunk", "primary"].includes(edge.highway) ? 2.5 : 1,
              opacity: 0.7,
            }}
          />
        ))}

      {layers.subregions &&
        subregionFeatures.map((f) => {
          if (f.geometry.type !== "Polygon") return null;
          const ring = (f.geometry.coordinates as number[][][])[0];
          const positions = ring.map(([lon, lat]) => [lat, lon] as [number, number]);
          return (
            <Polygon
              key={f.properties.id}
              positions={positions}
              pathOptions={{ color: "#6366f1", weight: 1.5, fillOpacity: 0.05 }}
            >
              <Popup>
                Sub-region {f.properties.id} — {f.properties.node_count} nodes, total demand{" "}
                {f.properties.total_demand.toFixed(0)}
              </Popup>
            </Polygon>
          );
        })}

      {layers.existingStations &&
        existingStations.map((station, index) => (
          <CircleMarker
            key={`${station.station_name}-${index}`}
            center={[station.latitude, station.longitude]}
            radius={6}
            pathOptions={{ color: "#dc2626", fillColor: "#dc2626", fillOpacity: 0.8 }}
          >
            <Popup>
              <strong>{station.station_name}</strong>
              <br />
              Operator: {station.operator}
              <br />
              <em>Source: {station.source} — synthetic, not real PlugShare/Google Maps data</em>
            </Popup>
          </CircleMarker>
        ))}

      {layers.recommendedStations &&
        selectedSites.map((site, index) => {
          const candidate = candidates.find((c) => c.node_id === site.node_id);
          return (
            <CircleMarker
              key={`${site.node_id}-${index}`}
              center={[site.latitude, site.longitude]}
              radius={8}
              pathOptions={{ color: "#16a34a", fillColor: "#16a34a", fillOpacity: 0.85 }}
            >
              <Popup>
                <strong>Recommended new station</strong>
                <br />
                Node: {site.node_id}
                {candidate && (
                  <>
                    <br />
                    Composite importance: {candidate.composite_importance.toFixed(3)}
                  </>
                )}
              </Popup>
            </CircleMarker>
          );
        })}
    </MapContainer>
  );
}
