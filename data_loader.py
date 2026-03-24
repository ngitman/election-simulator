"""
Load state county geometry and ensure we have population for simulation.
Supports: (1) local shapefile with TOT_POP22, (2) Census TIGER national file + state pop CSV.
"""
import os
import ssl
import tempfile
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

from state_metadata import STATE_FIPS, SUPPORTED_STATES, normalize_state_key

def _make_ssl_context():
    """Build an SSL context that verifies certificates (fixes macOS CERTIFICATE_VERIFY_FAILED)."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


# State config: state_key -> (Census FIPS code, population CSV filename)
_APP_DIR = Path(__file__).resolve().parent
SHAPEFILES_DIR = _APP_DIR / "shapefiles"
STATE_CONFIG = {
    "florida": (_APP_DIR / "fl_county_population.csv"),
    "new_york": (_APP_DIR / "ny_county_population.csv"),
}

TIGER_URLS = [
    "https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip",
    "https://www2.census.gov/geo/tiger/TIGER2023/COUNTY/tl_2023_us_county.zip",
]


def _trim_state_gdf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Keep only columns required by simulation/runtime.
    This significantly reduces memory versus retaining full TIGER attribute tables.
    """
    keep_cols = [c for c in ("NAME", "COUNTY", "TOT_POP22", "geometry") if c in gdf.columns]
    gdf = gdf[keep_cols].copy()
    if "COUNTY" not in gdf.columns and "NAME" in gdf.columns:
        gdf["COUNTY"] = gdf["NAME"] + " County"
    if "TOT_POP22" in gdf.columns:
        gdf["TOT_POP22"] = pd.to_numeric(gdf["TOT_POP22"], errors="coerce").fillna(50000).astype("int32")
    return gdf


def find_state_shapefile(state_key: str) -> Path | None:
    """Look in shapefiles/<state_key>/ for any .shp or .zip. Returns path or None."""
    folder = SHAPEFILES_DIR / normalize_state_key(state_key)
    if not folder.is_dir():
        return None
    for ext in ("*.shp", "*.zip"):
        for p in folder.glob(ext):
            if p.is_file():
                return p
    return None


def _normalize_county_name(name: str, state_key: str = "") -> str:
    """Strip ' County' and standardize for matching."""
    if pd.isna(name):
        return ""
    s = str(name).replace(" County", "").strip()
    if state_key == "florida" and s == "Dade":
        s = "Miami-Dade"
    return s


def load_state_counties(
    state_key: str,
    shapefile_path: str | Path | None = None,
    pop_csv_path: str | Path | None = None,
    use_web_fallback: bool = True,
) -> gpd.GeoDataFrame:
    """
    Load state counties as a GeoDataFrame with TOT_POP22.
    state_key: "florida" or "new_york".
    """
    state_key = normalize_state_key(state_key)
    if state_key not in SUPPORTED_STATES:
        raise ValueError(f"Unknown state: {state_key}. Supported: {list(SUPPORTED_STATES)}")
    state_fips = STATE_FIPS[state_key]
    default_pop_csv = STATE_CONFIG[state_key]
    pop_csv_path = pop_csv_path or default_pop_csv
    if not Path(pop_csv_path).is_file():
        raise FileNotFoundError(f"Population CSV not found: {pop_csv_path}")

    pop_df = pd.read_csv(pop_csv_path)
    pop_df["NAME_normalized"] = pop_df["NAME"].apply(lambda n: _normalize_county_name(n, state_key))

    if not shapefile_path:
        shapefile_path = find_state_shapefile(state_key)
        if shapefile_path:
            shapefile_path = str(shapefile_path)

    if shapefile_path and os.path.isfile(shapefile_path):
        gdf = gpd.read_file(shapefile_path)
        if "STATEFP" in gdf.columns:
            gdf = gdf[gdf["STATEFP"] == state_fips].copy()
        if "NAME" not in gdf.columns and "COUNTY" in gdf.columns:
            gdf["NAME"] = gdf["COUNTY"]
        gdf["NAME_normalized"] = gdf["NAME"].apply(lambda n: _normalize_county_name(n, state_key))

        if "TOT_POP22" in gdf.columns or "TOT_POP" in gdf.columns:
            pop_col = "TOT_POP22" if "TOT_POP22" in gdf.columns else "TOT_POP"
            if pop_col != "TOT_POP22":
                gdf["TOT_POP22"] = gdf[pop_col]
            gdf = gdf.to_crs(epsg=3857)
            return _trim_state_gdf(gdf)

        gdf = gdf.merge(
            pop_df[["NAME_normalized", "population"]],
            on="NAME_normalized",
            how="left",
        )
        gdf["TOT_POP22"] = gdf["population"].fillna(50000).astype(int)
        gdf = gdf.drop(columns=["population"], errors="ignore")
        gdf = gdf.to_crs(epsg=3857)
        return _trim_state_gdf(gdf)

    if not use_web_fallback:
        raise FileNotFoundError(
            f"No shapefile for {state_key}. Put a county .shp/.zip in {SHAPEFILES_DIR / state_key}."
        )

    ssl_ctx = _make_ssl_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
    last_error = None
    for url in TIGER_URLS:
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                with opener.open(url) as resp:
                    tmp.write(resp.read())
                tmp_path = tmp.name
            gdf = gpd.read_file(tmp_path)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            last_error = None
            break
        except Exception as e:
            last_error = e
    if last_error is not None:
        raise FileNotFoundError(
            f"Could not load {state_key} from Census TIGER. "
            f"Put a county shapefile in {SHAPEFILES_DIR / state_key}. Detail: {last_error}"
        ) from last_error

    if "STATEFP" in gdf.columns:
        gdf = gdf[gdf["STATEFP"] == state_fips].copy()
    gdf["NAME_normalized"] = gdf["NAME"].apply(lambda n: _normalize_county_name(n, state_key))
    gdf = gdf.merge(
        pop_df[["NAME_normalized", "population"]],
        on="NAME_normalized",
        how="left",
    )
    gdf["TOT_POP22"] = gdf["population"].fillna(50000).astype(int)
    gdf = gdf.drop(columns=["population"], errors="ignore")
    if "COUNTY" not in gdf.columns:
        gdf["COUNTY"] = gdf["NAME"] + " County"
    gdf = gdf.to_crs(epsg=3857)
    return _trim_state_gdf(gdf)


def load_florida_counties(
    shapefile_path: str | Path | None = None,
    pop_csv_path: str | Path | None = None,
    use_web_fallback: bool = True,
) -> gpd.GeoDataFrame:
    """Load Florida counties (convenience wrapper)."""
    return load_state_counties(
        "florida",
        shapefile_path=shapefile_path,
        pop_csv_path=pop_csv_path,
        use_web_fallback=use_web_fallback,
    )
