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
