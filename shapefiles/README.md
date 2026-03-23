# State county shapefiles (manual downloads)

Place state county shapefiles here so the app can use them without downloading from Census TIGER.

## Layout

- **`florida/`** — Drop any Florida county shapefile here (`.shp` or `.zip`).
  - The app looks here first. If it finds a file, it uses it instead of fetching from the web.
  - Example: download [TIGER 2025 Florida counties](https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/) — file `tl_2025_12_county.zip` — and put it in `shapefiles/florida/`.
- **Other states (future)** — For multi-state support, add folders like `texas/`, `ohio/`, etc. and put each state’s county `.shp` or `.zip` in the matching folder.

## Where to get shapefiles

- **Census TIGER/Line**: <https://www.census.gov/cgi-bin/geo/shapefiles/index.php> → choose year → Counties → select state.
- Or use the **Open shapefiles folder** button in the app to open `florida` and drag your downloaded file in.
