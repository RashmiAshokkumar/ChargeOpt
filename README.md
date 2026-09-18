# ChargeOpt

ChargeOpt recommends optimal locations for new EV charging stations in a bounded
sub-area of an Indian city, using road-network analysis, linear algebra, and a real
mathematical optimization formulation — not a heuristic ranking.

**Study area**: Andheri West, Mumbai, bounded by a ~4km x 4km box (real OSM road
network: ~1,400 nodes, ~2,000 edges).

## Architecture

```
data-pipeline/  Python: builds the graph, matrices, centrality, PCA, community
                detection, candidate shortlist, and the MCLP MILP (PuLP/CBC).
                Run once offline -> writes results/results.json.
backend/        Django + DRF. Seeds its DB from results.json and serves it via
                REST endpoints. /api/optimize/ re-solves the MCLP live for any
                budget using a self-contained problem package the pipeline wrote
                into results.json — this is a real re-optimization, not a
                truncation of a precomputed ranking.
frontend/       Next.js (App Router) + react-leaflet. Map with toggleable layers
                (road network, demand heatmap, existing/recommended stations,
                sub-region boundaries), a debounced budget slider, and a
                Data & Methodology panel rendered directly from the API's
                provenance data (not hardcoded).
```

## Data provenance — what's real, proxied, or synthetic

| Source | Status |
|---|---|
| Road network | **Real** — live OSM data via osmnx/Overpass |
| Traffic proxy | **Proxy** — derived from OSM road hierarchy + graph centrality, not live traffic data |
| Population density | **Proxy** — OSM building-footprint density near each node, not WorldPop/Census |
| Existing charging stations | **Synthetic** — deterministically generated, not PlugShare/Google Maps data |

See `data-pipeline/README.md` for the full breakdown and the reasoning behind
specific parameter choices (coverage radius, station count, community-detection
resolution). The frontend's "Data & Methodology" panel shows this same provenance
live, pulled from the API.

## Optimization method

New station sites are chosen by solving a **Maximum Covering Location Problem
(MCLP)** as a Mixed-Integer Linear Program:

- Decision variables: `x_j ∈ {0,1}` (build at candidate site j), `y_i ∈ {0,1}`
  (demand node i is covered)
- Objective: maximize `Σ demand_weight_i · y_i`
- Constraints: budget (`Σ x_j ≤ B`), coverage linkage on **road-network distance**
  (`y_i ≤ Σ x_j` over candidates within 800m of i), and per-sub-region equity
  (≥1 station in each sufficiently-high-demand Louvain community)

Solved with PuLP's CBC solver. Centrality and PCA feed candidate *generation*
only — the MILP performs the actual budget-constrained site selection.

At the pipeline's default budget (10 stations): coverage goes from **50.2% → 89.1%**
of weighted demand, and average distance to the nearest charger drops from
**864m → 541m**. The pipeline asserts B=5 vs B=15 produce meaningfully different
results before writing output, and raises if the solver returns a non-Optimal
status rather than silently presenting garbage relaxation values.

## Local setup

### 1. Data pipeline (run first)

```bash
cd data-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_synthetic_stations.py
python scripts/run_pipeline.py
```

### 2. Django backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_from_pipeline   # loads ../data-pipeline/results/results.json
python manage.py runserver
```

### 3. Next.js frontend

```bash
cd frontend
cp ../.env.example .env.local   # edit NEXT_PUBLIC_API_BASE_URL if needed
npm install
npm run dev
```

Visit `http://localhost:3000`.

## API endpoints

- `GET /api/network-stats/` — graph summary, matrix/PCA summaries, provenance
- `GET /api/road-network/` — simplified node/edge geometry for the map
- `GET /api/demand-heatmap/` — demand-weight points for the heatmap layer
- `GET /api/candidates/` — shortlisted candidate sites with all scores
- `GET /api/existing-stations/`
- `GET /api/subregions/` — GeoJSON polygons (convex hulls) for equity zones
- `POST /api/optimize/` `{"budget": N}` — live MCLP re-solve for that budget
- `GET /api/optimization-history/` — past optimization runs

## Deployment

- **Frontend → Vercel**: set `NEXT_PUBLIC_API_BASE_URL` to the deployed backend's
  `/api` URL in Vercel project env vars. Root directory: `frontend/`.
- **Backend → Render/Railway** with a Postgres addon:
  - Root directory: `backend/`
  - Build: `pip install -r requirements.txt`
  - The included `Procfile` handles migrate + seed + collectstatic (release phase)
    and starts `gunicorn chargeopt.wsgi:application`.
  - Env vars: `DJANGO_SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS=<your-render-domain>`,
    `DATABASE_URL` (provided by the Postgres addon), `CORS_ALLOWED_ORIGINS=<your-vercel-domain>`.
  - **Important**: `data-pipeline/results/results.json` and
    `data-pipeline/data/existing_stations.csv` are committed to the repo on
    purpose — the deployed backend seeds from these frozen pipeline outputs
    rather than re-running the OSM fetch + MILP solve on every deploy. To publish
    new pipeline results, re-run the pipeline locally, commit the updated
    `results.json`, and redeploy (the release phase re-seeds automatically).

See `.env.example` for the full list of environment variables.

## Known limitations (see also the in-app Data & Methodology panel)

- Traffic proxy is topological, not real-time.
- Population density is an OSM-derived proxy, not a formal WorldPop/Census raster.
- Existing station locations are synthetic placeholders.
- The study area is intentionally bounded to ~4km x 4km to keep live
  centrality/MILP computation tractable for a course-project demo.
