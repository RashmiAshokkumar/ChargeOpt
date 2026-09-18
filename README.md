# ChargeOpt

ChargeOpt is a full-stack web application for recommending optimal EV charging station placements in a bounded study area of an Indian city using a road-network-driven, mathematically grounded pipeline.

## Project architecture

- data-pipeline: Python scripts that build the graph, compute centrality and PCA-based features, run community detection, shortlist candidates, and solve the MILP.
- backend: Django + Django REST Framework API that serves model output and can rerun optimization for a given budget.
- frontend: Next.js + react-leaflet dashboard that renders the network and optimization results.

## Study area

Default study area: Andheri West, Mumbai, bounded by a compact ~4 km x 4 km box.

## Data provenance and honesty

> Important: the environment used for this project blocks direct requests to the Overpass API. When live OSM data cannot be fetched, the pipeline falls back to a synthetic road-grid network so the application still runs locally. That fallback is clearly labeled as synthetic and is not presented as live OSM data.

- Real data: OSM road network from osmnx.
- Proxy data: traffic proxy derived from road hierarchy + graph centrality.
- Manually curated: existing charging station locations imported from a CSV provided by the project team.
- Population density: coarse proxy using WorldPop or Census 2011 ward-level data; the methodology explicitly notes the staleness limitation.

## Build order

1. Complete the data pipeline and optimization validation.
2. Implement the Django API against the generated pipeline output.
3. Implement the Next.js map dashboard.
4. Deploy.

## Local setup

### Python pipeline

```bash
cd data-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Django backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### Next.js frontend

```bash
cd frontend
npm install
npm run dev
```

## Notes

The project intentionally avoids claiming live traffic API data. All traffic-related measurements are clearly labeled as generated proxies derived from road hierarchy and graph centrality.
