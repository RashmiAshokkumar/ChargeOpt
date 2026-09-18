# Data pipeline

This folder contains the offline analysis pipeline for ChargeOpt. The intended workflow is: build the road network, validate graph properties, compute graph matrices and centrality, generate candidate features, solve the MILP, and write a results artifact.

Key constraints from the project brief:
- Real traffic data is not used; the pipeline derives a traffic proxy from road hierarchy and graph centrality.
- Existing stations are loaded from a manually curated CSV.
- The optimization step uses a genuine MILP with PuLP/CBC.

## Expected input files

- `data/existing_stations.csv` — manually curated charging station locations
- optional: `data/population_density.geojson` or similar coarse population proxy

## Typical run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_pipeline.py
```

The script writes a JSON summary to `results/results.json`.
