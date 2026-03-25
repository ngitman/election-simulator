"""
Load state county geometry and ensure we have population for simulation.
Supports: (1) local shapefile with TOT_POP22, (2) Census TIGER national file + state pop CSV.
"""
import logging
import os
import ssl
import tempfile
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

from observability import log_stage
from state_metadata import STATE_FIPS, SUPPORTED_STATES, normalize_state_key

_logger = logging.getLogger("election_sim.data_loader")

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

# Written after first successful TIGER fetch so we do not re-download the national zip every time.
TIGER_CACHE_SHP = "tiger_counties_cache.shp"

# Approximate max county count per state (FL=67, NY=62); used to catch tracts/subdivisions.
_COUNTY_ROW_CAP = {"florida": 80, "new_york": 75}


def _tiger_cache_path(state_key: str) -> Path:
    return SHAPEFILES_DIR / normalize_state_key(state_key) / TIGER_CACHE_SHP


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


def _shapefile_pick_priority(path: Path) -> tuple[int, str]:
    """Prefer filenames that look like county layers; deprioritize tracts, subdivisions, etc."""
    n = path.name.lower()
    bad = ("tract", "cousub", "place", "tabblock", "bg20", "block", "zcta", "subdivision")
    if any(b in n for b in bad):
        return (2, path.name)
    if "county" in n:
        return (0, path.name)
    return (1, path.name)


def find_state_shapefile(state_key: str) -> Path | None:
    """Look in shapefiles/<state_key>/ for any .shp or .zip (excluding auto TIGER cache)."""
    folder = SHAPEFILES_DIR / normalize_state_key(state_key)
    if not folder.is_dir():
        return None
    candidates: list[Path] = []
    for ext in ("*.shp", "*.zip"):
        for p in folder.glob(ext):
            if p.is_file() and not p.name.startswith("tiger_counties_cache"):
                candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=_shapefile_pick_priority)
    return candidates[0]


def _validate_county_layer(gdf: gpd.GeoDataFrame, state_key: str) -> None:
    """
    Fail fast if the user dropped tracts, block groups, or other subcounty layers.
    Those have thousands of polygons and break the map + county simulation.
    """
    if gdf.empty:
        raise ValueError(
            "No features left for this state. Check STATEFP or use a counties-only shapefile."
        )
    cols = set(gdf.columns)
    if "TRACTCE" in cols:
        raise ValueError(
            "This file looks like census tracts, not counties. Remove it and download Census TIGER "
            "**Counties** (COUNTY layer, e.g. tl_2024_12_county.zip for Florida), not tracts."
        )
    if "BLKGRPCE" in cols:
        raise ValueError(
            "This file looks like census block groups, not counties. Use a TIGER **Counties** shapefile."
        )
    if "NAMELSAD" in gdf.columns:
        sample = gdf["NAMELSAD"].astype(str).head(50)
        if sample.str.contains("Census Tract", case=False, na=False).any():
            raise ValueError(
                "This layer contains census tracts (NAMELSAD). Replace with a **Counties** shapefile."
            )
        if sample.str.contains("subdivision", case=False, na=False).any():
            raise ValueError(
                "This layer looks like county subdivisions, not counties. Use TIGER **Counties**."
            )

    cap = _COUNTY_ROW_CAP.get(state_key, 100)
    n = len(gdf)
    if n > cap:
        raise ValueError(
            f"This layer has {n} features for this state; a county map should have about "
            f"{cap - 15}–{cap} areas, not thousands. You likely have tracts, block groups, or "
            "county subdivisions. Delete the wrong file from "
            f"{SHAPEFILES_DIR / normalize_state_key(state_key)} and add tl_YYYY_SS_county.zip "
            "from Census TIGER COUNTY, or clear the folder to use the built-in web fallback."
        )


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
    log_stage(_logger, "load_counties_begin", state=state_key, shapefile=shapefile_path or "auto")
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
    if not shapefile_path:
        cache_p = _tiger_cache_path(state_key)
        if cache_p.is_file():
            shapefile_path = str(cache_p)

    if shapefile_path and os.path.isfile(shapefile_path):
        if Path(shapefile_path).name == TIGER_CACHE_SHP:
            log_stage(_logger, "load_counties_disk_cache_hit", state=state_key, path=shapefile_path)
        gdf = gpd.read_file(shapefile_path)
        log_stage(_logger, "load_counties_read_local", state=state_key, rows=len(gdf), columns=list(gdf.columns)[:12])
        if "STATEFP" in gdf.columns:
            gdf = gdf[gdf["STATEFP"] == state_fips].copy()
        _validate_county_layer(gdf, state_key)
        if "NAME" not in gdf.columns and "COUNTY" in gdf.columns:
            gdf["NAME"] = gdf["COUNTY"]
        gdf["NAME_normalized"] = gdf["NAME"].apply(lambda n: _normalize_county_name(n, state_key))

        if "TOT_POP22" in gdf.columns or "TOT_POP" in gdf.columns:
            pop_col = "TOT_POP22" if "TOT_POP22" in gdf.columns else "TOT_POP"
            if pop_col != "TOT_POP22":
                gdf["TOT_POP22"] = gdf[pop_col]
            gdf = gdf.to_crs(epsg=3857)
            out = _trim_state_gdf(gdf)
            log_stage(_logger, "load_counties_local_done", state=state_key, rows=len(out))
            return out

        gdf = gdf.merge(
            pop_df[["NAME_normalized", "population"]],
            on="NAME_normalized",
            how="left",
        )
        gdf["TOT_POP22"] = gdf["population"].fillna(50000).astype(int)
        gdf = gdf.drop(columns=["population"], errors="ignore")
        gdf = gdf.to_crs(epsg=3857)
        out = _trim_state_gdf(gdf)
        log_stage(_logger, "load_counties_local_merged_done", state=state_key, rows=len(out))
        return out

    if not use_web_fallback:
        raise FileNotFoundError(
            f"No shapefile for {state_key}. Put a county .shp/.zip in {SHAPEFILES_DIR / state_key}."
        )

    ssl_ctx = _make_ssl_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
    last_error = None
    for url in TIGER_URLS:
        try:
            log_stage(_logger, "load_counties_tiger_download_begin", state=state_key, url=url)
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                with opener.open(url) as resp:
                    tmp.write(resp.read())
                tmp_path = tmp.name
            gdf = gpd.read_file(tmp_path)
            log_stage(_logger, "load_counties_tiger_read", state=state_key, rows=len(gdf))
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            last_error = None
            break
        except Exception as e:
            _logger.warning("TIGER fetch failed state=%s url=%s err=%s", state_key, url, e)
            last_error = e
    if last_error is not None:
        raise FileNotFoundError(
            f"Could not load {state_key} from Census TIGER. "
            f"Put a county shapefile in {SHAPEFILES_DIR / state_key}. Detail: {last_error}"
        ) from last_error

    if "STATEFP" in gdf.columns:
        gdf = gdf[gdf["STATEFP"] == state_fips].copy()
        log_stage(_logger, "load_counties_tiger_filtered", state=state_key, rows=len(gdf))
    _validate_county_layer(gdf, state_key)
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
    out = _trim_state_gdf(gdf)
    log_stage(_logger, "load_counties_tiger_done", state=state_key, rows=len(out))
    try:
        cache_path = _tiger_cache_path(state_key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_file(cache_path)
        log_stage(_logger, "load_counties_tiger_saved_cache", state=state_key, path=str(cache_path))
    except Exception as e:
        _logger.warning("could not save TIGER disk cache state=%s err=%s", state_key, e)
    return out


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
