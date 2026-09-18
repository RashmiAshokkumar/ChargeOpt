"use client";

import { useEffect } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet.heat";

type Props = {
  points: { lat: number; lon: number; weight: number }[];
  visible: boolean;
};

export default function HeatmapLayer({ points, visible }: Props) {
  const map = useMap();

  useEffect(() => {
    if (!visible || points.length === 0) return;

    const maxWeight = Math.max(...points.map((p) => p.weight), 1);
    const heatPoints: [number, number, number][] = points.map((p) => [
      p.lat,
      p.lon,
      p.weight / maxWeight,
    ]);

    const layer = L.heatLayer(heatPoints, { radius: 22, blur: 18, maxZoom: 17 });
    layer.addTo(map);

    return () => {
      map.removeLayer(layer);
    };
  }, [map, points, visible]);

  return null;
}
