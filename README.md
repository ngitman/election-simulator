# Gitman's Political Simulator

Runs state-level election simulations (Florida, New York) and shows:

- **Choropleth** — counties colored by winner and margin (blue = Democrat, red = Republican, orange = Other).
- **Bar chart** — statewide vote totals (Democrat vs Republican, plus Other).

Two UIs: **FastAPI + Svelte** (web) and **tkinter** (desktop).

## FastAPI + Svelte (recommended)

### Requirements

- Python 3.10+ and Node 20+
- Backend: `pip install -r requirements.txt`
- Frontend: `cd frontend && npm install`

### Run

**Terminal 1 — backend:**

```bash
cd florida_election_app
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

**Terminal 2 — frontend:**

```bash
cd florida_election_app/frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Use the **State** dropdown to switch between Florida and New York, then click **Run simulation** to re-run. Enable **Presidential** mode to run both states at once and see an Electoral College result (FL 30 + NY 28 EVs).

The backend loads state counties from `shapefiles/<state>/` first, then Census TIGER if needed. If load fails (404/SSL), put a county shapefile in `shapefiles/florida/` (see Shapefiles folder below).

### Render (frontend + backend managed together)

This repo includes `render.yaml` to provision both services:

- `election-simulator-api` (Python web service, root `backend/`)
- `election-simulator-frontend` (static site, root `frontend/`)

Deploy via Render Blueprint from the repo root. After first deploy, update these values to your actual Render domains:

- `FRONTEND_ORIGINS` on backend
- `VITE_API_BASE_URL` on frontend

If you deploy backend manually, use:

- **Root Directory:** `backend`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `./render_start.sh`
- **Recommended Env Vars for low memory:**
  - `GEOMETRY_SIMPLIFY_TOLERANCE=0.01`
  - `GEOMETRY_COORD_PRECISION=0.0001`

### Docker Compose (frontend + backend together)

Start both services with one command:

```bash
docker compose up --build
```

Then open:

- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:8000`

Stop:

```bash
docker compose down
```

### Render (Docker-compatible backend, optional)

This repo also includes `Dockerfile.backend` if you prefer a Docker web service for backend only.

### Backend Exposure Note

For browser-based apps, an API used directly by the frontend is internet-reachable by design. To minimize exposure:

- Restrict CORS using `FRONTEND_ORIGINS` (comma-separated origins).
- Keep secrets server-side only (never in frontend code).
- Add auth/rate limits if you need stronger protection.

If you want a truly non-public backend, use a server-side frontend/proxy on the same private network rather than direct browser calls.

### Memory optimization notes

The backend now trims loaded county data to only required columns and simplifies cached map geometry.
If you hit memory limits, increase simplification:

- `GEOMETRY_SIMPLIFY_TOLERANCE=0.02` (or `0.03` for stronger reduction)
- `GEOMETRY_COORD_PRECISION=0.0005`

### Backend diagnostics (Render logs)

Set `LOG_LEVEL=INFO` (default) or `LOG_LEVEL=DEBUG` on the API service. Logs include **stage markers** plus **RSS snapshots** on Linux (`rss_mb` from `/proc/self/status`) and peak RSS where available, for example: `ensure_loaded_begin`, `geo_cache_to_crs_end`, `simulation_run_complete`, `geojson_build_end`.

- Logger names: `election_sim.api`, `election_sim.data_loader`
- `MemoryError` responses log at `CRITICAL` with a stack trace in server logs

## Disclaimer

This simulator is for exploration only. Results are randomized and not intended to represent real election outcomes or official forecasts.

---

## Tkinter desktop app

```bash
pip install -r requirements.txt
python app.py
```

Optional: `python app.py /path/to/florida_counties.shp`. The app also looks in `shapefiles/florida/` before using the network.

## Shapefiles folder (multi-state ready)

- **`shapefiles/florida/`** and **`shapefiles/new_york/`** — Put state county shapefiles (`.shp` or `.zip`) here. See `shapefiles/README.md` for TIGER links.
- Add more states by creating `shapefiles/<state>/` and a matching population CSV.

## Shapefile

- The notebook used a file like `fl_cit_2022_cnty.shp` with a `TOT_POP22` column; you can point the app at that or any Florida county shapefile.
- If your shapefile has no population column, the app will merge in population from `fl_county_population.csv` (by county name).
- Without any shapefile, the app can download geometry from Census TIGER and use the same CSV for population.

## Controls

- **Web:** Run simulation — re-run with new random margins; map and bar chart update.
- **Tkinter:** Run simulation, Open shapefile…, Open shapefiles folder.

## Later: Redistricting Hub API

The simulation currently uses the same hardcoded logic as the notebook. To drive it from real electoral data (e.g. your ARS Data Sourcing API), you’d add a client that calls your API and maps results into the same GeoDataFrame/columns the simulation and choropleth expect.
