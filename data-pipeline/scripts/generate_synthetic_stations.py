"""Generate a synthetic existing-EV-charging-station dataset for the study area.

IMPORTANT (data honesty): this dataset is NOT sourced from PlugShare, Google Maps,
or any real observation. It is deterministically generated (seeded RNG) so the
optimization pipeline has a plausible "existing stations" layer to work against.
It must always be labeled "synthetic" in the app/report, never "manually curated"
or "scraped". See README / data provenance section.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "existing_stations.csv"

# Same bounding box as the pipeline's study area (Andheri West, Mumbai, ~4km x 4km).
NORTH, SOUTH, EAST, WEST = 19.1377, 19.1017, 72.8486, 72.8106

OPERATORS = [
    "Tata Power EZ Charge",
    "Statiq",
    "ChargeZone",
    "Ather Grid",
    "Jio-bp Pulse",
    "BPCL Energy Station",
    "Kazam",
    "Zeon Charging",
]

SEED = 20240601  # fixed seed for reproducibility
# Chosen to roughly match plausible current EV charging infrastructure density for a
# ~4km x 4km Indian suburban study area (sparse), not tuned to any particular
# optimization outcome.
N_STATIONS = 16


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for i in range(1, N_STATIONS + 1):
        lat = rng.uniform(SOUTH + 0.002, NORTH - 0.002)
        lon = rng.uniform(WEST + 0.002, EAST - 0.002)
        operator = OPERATORS[i % len(OPERATORS)]
        rows.append(
            {
                "station_name": f"Synthetic Site {i:02d} ({operator})",
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "operator": operator,
                "source": "synthetic_generated",
                "notes": (
                    "Synthetic placeholder, not sourced from PlugShare/Google Maps or any "
                    "real observation. Deterministically generated (seed=%d) to populate the "
                    "'existing stations' layer for pipeline development and demo purposes. "
                    "Replace with a real manually curated CSV before drawing real-world "
                    "conclusions." % SEED
                ),
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["station_name", "latitude", "longitude", "operator", "source", "notes"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} synthetic stations to {OUT_PATH}")


if __name__ == "__main__":
    main()
