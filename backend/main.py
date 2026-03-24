"""
FastAPI backend for Election Simulator (Florida + New York + Presidential).
Run from project root: uvicorn backend.main:app --reload
"""
from __future__ import annotations

import logging
import sys
import os
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import geopandas as gpd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from observability import configure_logging, log_stage
from data_loader import load_state_counties, SHAPEFILES_DIR, STATE_CONFIG
from state_metadata import (
    STATE_EC_VOTES,
    SUPPORTED_STATES,
    get_state_label,
    normalize_state_key,
)
from simulation import (
    run_simulation,
    get_state_totals,
    DEMOCRAT_NAME,
    REPUBLICAN_NAME,
    DEFAULT_TURNOUT,
    DEFAULT_UNPOPULARITY_INDEX,
)

configure_logging()
logger = logging.getLogger("election_sim.api")

_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_frontend_origins_env = os.getenv("FRONTEND_ORIGINS", "")
_frontend_origins = [o.strip() for o in _frontend_origins_env.split(",") if o.strip()]
_allowed_origins = {origin.rstrip("/") for origin in (_frontend_origins or _default_origins)}
_simplify_tolerance = float(os.getenv("GEOMETRY_SIMPLIFY_TOLERANCE", "0.01"))
_geometry_precision = float(os.getenv("GEOMETRY_COORD_PRECISION", "0.0001"))
_max_cached_states = max(1, int(os.getenv("MAX_CACHED_STATES", "1")))

_docs_enabled = os.getenv("ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes", "on"}
app = FastAPI(
    title="Election Simulator API",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins or _default_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def restrict_api_to_allowed_origins(request: Request, call_next):
    """
    Browser lock-down layer:
    only allow /api requests from configured frontend origins.
    """
    if request.url.path.startswith("/api/"):
        origin = (request.headers.get("origin") or "").rstrip("/")
        referer = request.headers.get("referer") or ""

        referer_allowed = any(referer.startswith(f"{allowed}/") for allowed in _allowed_origins)
        if origin not in _allowed_origins and not referer_allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden origin"},
            )

    return await call_next(request)


# Electoral votes for supported states (subset of 538)
EC_VOTES = STATE_EC_VOTES

_base_by_state: dict[str, gpd.GeoDataFrame] = {}
_last_by_state: dict[str, dict] = {}
_geo_cache_by_state: dict[str, dict] = {}
_cache_order: list[str] = []


def _touch_state_cache(state_key: str) -> None:
    if state_key in _cache_order:
        _cache_order.remove(state_key)
    _cache_order.append(state_key)


def _enforce_cache_budget() -> None:
    while len(_cache_order) > _max_cached_states:
        evict = _cache_order.pop(0)
        _base_by_state.pop(evict, None)
        _geo_cache_by_state.pop(evict, None)
        _last_by_state.pop(evict, None)
        log_stage(logger, "cache_evicted", state=evict, max_cached_states=_max_cached_states)


def _validate_state_key(state: str) -> str:
    state_key = normalize_state_key(state)
    if state_key not in STATE_CONFIG:
        raise HTTPException(status_code=400, detail=f"Unknown state: {state}")
    return state_key


def _gdf_to_geojson(
    gdf: gpd.GeoDataFrame,
    state_key: str,
    democrat_name: str,
    republican: str,
) -> dict:
    """Convert simulation results to GeoJSON using cached WGS84 geometry.

    Geometry itself does not change during simulation, so we avoid repeated
    `to_crs()` calls and per-row geometry extraction on every request.
    """
    log_stage(logger, "geojson_build_begin", state=state_key, rows=len(gdf))
    cache = _geo_cache_by_state[state_key]
    geoms: list[dict] = cache["geoms"]
    names: list[str] = cache["names"]

    # Fast column extraction (avoids iterrows overhead)
    dem_votes = gdf["Democrat_Votes"].astype(int).to_numpy()
    rep_votes = gdf[f"{republican}_Votes"].astype(int).to_numpy()
    other_votes = gdf["Other_Votes"].astype(int).to_numpy()
    cast_ballots = gdf["Cast_Ballots"].astype(int).to_numpy()

    colors = gdf["Color"].to_list()
    winners = gdf["Winner"].to_list()

    margin = gdf["Margin"].astype(float).to_numpy()
    dem_pct = gdf["Democrat_Percentage"].astype(float).to_numpy()
    rep_pct = gdf[f"{republican}_Percentage"].astype(float).to_numpy()
    other_pct = gdf["Other_Percentage"].astype(float).to_numpy()

    n = len(geoms)
    features = []
    for idx in range(n):
        features.append(
            {
                "type": "Feature",
                "geometry": geoms[idx],
                "properties": {
                    "name": names[idx],
                    "color": colors[idx],
                    "winner": winners[idx],
                    "margin": float(margin[idx]),
                    "democrat_pct": float(dem_pct[idx]),
                    "rep_pct": float(rep_pct[idx]),
                    "other_pct": float(other_pct[idx]),
                    "cast_ballots": int(cast_ballots[idx]),
                    "democrat_votes": int(dem_votes[idx]),
                    "rep_votes": int(rep_votes[idx]),
                    "other_votes": int(other_votes[idx]),
                },
            }
        )
    log_stage(logger, "geojson_build_end", state=state_key, features=n)
    return {"type": "FeatureCollection", "features": features}


def _ensure_loaded(state_key: str) -> gpd.GeoDataFrame:
    if state_key not in _base_by_state or _base_by_state[state_key] is None:
        try:
            log_stage(logger, "ensure_loaded_begin", state=state_key)
            base = load_state_counties(state_key, use_web_fallback=True)
            # Reset index once so positional arrays align with cached geometry order.
            base = base.reset_index(drop=True)
            _base_by_state[state_key] = base

            # Cache WGS84 geometry and names once per server lifetime.
            _geo_cache_by_state[state_key] = _build_geo_cache(base)
            _touch_state_cache(state_key)
            _enforce_cache_budget()
            log_stage(
                logger,
                "ensure_loaded_end",
                state=state_key,
                rows=len(base),
                geo_cache_features=len(_geo_cache_by_state[state_key]["geoms"]),
            )
        except FileNotFoundError as e:
            logger.exception("ensure_loaded_file_error state=%s", state_key)
            raise HTTPException(
                status_code=503,
                detail={
                    "message": str(e),
                    "shapefiles_path": str(SHAPEFILES_DIR / state_key),
                },
            )
    else:
        log_stage(logger, "ensure_loaded_cache_hit", state=state_key)
        _touch_state_cache(state_key)
    return _base_by_state[state_key]


def _build_geo_cache(base: gpd.GeoDataFrame) -> dict:
    """Build cached WGS84 geometries + names for fast GeoJSON generation."""
    log_stage(logger, "geo_cache_to_crs_begin", rows=len(base))
    gdf_wgs84 = base.to_crs(epsg=4326)
    log_stage(logger, "geo_cache_to_crs_end", rows=len(gdf_wgs84))
    # Trim geometry complexity/precision for memory + payload savings in web maps.
    if _simplify_tolerance > 0:
        gdf_wgs84["geometry"] = gdf_wgs84.geometry.simplify(_simplify_tolerance, preserve_topology=True)
        log_stage(logger, "geo_cache_simplified", tolerance=_simplify_tolerance)
    if _geometry_precision > 0:
        try:
            gdf_wgs84["geometry"] = gdf_wgs84.geometry.set_precision(_geometry_precision)
            log_stage(logger, "geo_cache_precision", precision=_geometry_precision)
        except Exception:
            # Older shapely/geopandas combinations may not expose set_precision.
            logger.warning("set_precision unavailable; skipping GEOMETRY_COORD_PRECISION")

    geoms: list[dict] = []
    names: list[str] = []
    for _, row in gdf_wgs84.iterrows():
        geoms.append(row.geometry.__geo_interface__)
        names.append(row.get("NAME") or row.get("COUNTY", ""))
    log_stage(logger, "geo_cache_built", features=len(geoms))
    return {"geoms": geoms, "names": names}


def _run_state_simulation(
    *,
    state_key: str,
    democrat_name: str,
    republican_name: str,
    bias_d_r: float,
    turnout: int,
    unpopularity_index: float,
    seed: int | None,
) -> dict:
    """Run and cache one state's simulation result (shared by single/presidential endpoints)."""
    log_stage(logger, "simulation_begin", state=state_key, seed=seed)
    base = _ensure_loaded(state_key)
    gdf = run_simulation(
        base.copy(),
        state=state_key,
        democrat_name=democrat_name,
        republican=republican_name,
        turnout=turnout,
        unpopularity_index=unpopularity_index,
        bias_d_r=bias_d_r,
        seed=seed,
    )
    log_stage(logger, "simulation_run_complete", state=state_key, rows=len(gdf))
    totals = get_state_totals(
        gdf,
        democrat_name=democrat_name,
        republican=republican_name,
    )
    dem_votes = int(totals[democrat_name])
    rep_votes = int(totals[republican_name])
    totals_dict = {
        "state": state_key,
        "state_label": get_state_label(state_key),
        "democrat": dem_votes,
        "republican": rep_votes,
        "other": int(totals["Other"]),
        "cast_ballots": int(totals["Cast_Ballots"]),
        "democrat_name": democrat_name,
        "republican_name": republican_name,
        "winner": democrat_name if dem_votes > rep_votes else republican_name,
    }
    payload = {
        "totals": totals_dict,
        "geojson": _gdf_to_geojson(gdf, state_key, democrat_name, republican_name),
    }
    _last_by_state[state_key] = payload
    log_stage(logger, "simulation_end", state=state_key)
    return payload


class RunSimulationRequest(BaseModel):
    state: str = "florida"
    democrat_name: str = DEMOCRAT_NAME
    republican_name: str = REPUBLICAN_NAME
    bias_d_r: float = 0.0
    turnout: int = DEFAULT_TURNOUT
    unpopularity_index: float = DEFAULT_UNPOPULARITY_INDEX
    seed: int | None = None


class RunSimulationResponse(BaseModel):
    totals: dict
    geojson: dict


@app.get("/api/states")
def api_states():
    """List supported states with labels and EC votes."""
    return {
        "states": [
            {"id": state_key, "label": get_state_label(state_key), "ec_votes": EC_VOTES.get(state_key, 0)}
            for state_key in SUPPORTED_STATES
        ],
        "total_ec": sum(EC_VOTES.get(state_key, 0) for state_key in SUPPORTED_STATES),
    }


@app.get("/api/load")
def api_load(state: str = "florida"):
    """Load counties for a state. Returns success and county count or error."""
    state_key = _validate_state_key(state)
    try:
        log_stage(logger, "api_load_begin", state=state_key)
        base = load_state_counties(state_key, use_web_fallback=True)
        base = base.reset_index(drop=True)
        _base_by_state[state_key] = base
        _geo_cache_by_state[state_key] = _build_geo_cache(base)
        _touch_state_cache(state_key)
        _enforce_cache_budget()
        log_stage(logger, "api_load_end", state=state_key, county_count=len(_base_by_state[state_key]))
        return {
            "success": True,
            "state": state_key,
            "state_label": get_state_label(state_key),
            "countyCount": len(_base_by_state[state_key]),
            "message": f"Loaded {len(_base_by_state[state_key])} {get_state_label(state_key)} counties.",
        }
    except FileNotFoundError as e:
        logger.warning("api_load_failed state=%s err=%s", state_key, e)
        return {
            "success": False,
            "state": state_key,
            "countyCount": 0,
            "message": str(e),
            "shapefiles_path": str(SHAPEFILES_DIR / state_key),
        }


@app.post("/api/simulation/run", response_model=RunSimulationResponse)
def api_simulation_run(body: RunSimulationRequest | None = None):
    """Run one state simulation. Returns state totals + GeoJSON."""
    body = body or RunSimulationRequest()
    state_key = _validate_state_key(body.state)
    log_stage(logger, "api_sim/run", state=state_key)
    payload = _run_state_simulation(
        state_key=state_key,
        democrat_name=body.democrat_name,
        republican_name=body.republican_name,
        bias_d_r=body.bias_d_r,
        turnout=body.turnout,
        unpopularity_index=body.unpopularity_index,
        seed=body.seed,
    )
    return RunSimulationResponse(**payload)


@app.get("/api/simulation")
def api_simulation_get(state: str = "florida"):
    """Return last simulation result for a state."""
    state_key = normalize_state_key(state)
    if state_key not in _last_by_state or _last_by_state[state_key] is None:
        raise HTTPException(status_code=404, detail=f"No simulation run yet for {state}. POST /api/simulation/run first.")
    return _last_by_state[state_key]


class PresidentialRunRequest(BaseModel):
    democrat_name: str = DEMOCRAT_NAME
    republican_name: str = REPUBLICAN_NAME
    bias_d_r: float = 0.0
    turnout: int = DEFAULT_TURNOUT
    unpopularity_index: float = DEFAULT_UNPOPULARITY_INDEX
    seed: int | None = None


@app.post("/api/presidential/run")
def api_presidential_run(body: PresidentialRunRequest | None = None):
    """Run simulation for all supported states with the same seed; return EC outcome."""
    body = body or PresidentialRunRequest()
    log_stage(logger, "api_presidential_begin", states=list(SUPPORTED_STATES), seed=body.seed)
    state_results = []
    ec_dem = 0
    ec_rep = 0

    for state_key in SUPPORTED_STATES:
        try:
            log_stage(logger, "api_presidential_state", state=state_key)
            payload = _run_state_simulation(
                state_key=state_key,
                democrat_name=body.democrat_name,
                republican_name=body.republican_name,
                bias_d_r=body.bias_d_r,
                turnout=body.turnout,
                unpopularity_index=body.unpopularity_index,
                seed=body.seed,
            )
        except HTTPException as exc:
            logger.warning("presidential_skip state=%s detail=%r", state_key, exc.detail)
            continue
        totals = payload["totals"]
        dem_v = int(totals["democrat"])
        rep_v = int(totals["republican"])
        winner = totals["winner"]
        ec = EC_VOTES.get(state_key, 0)
        if dem_v > rep_v:
            ec_dem += ec
        else:
            ec_rep += ec
        state_results.append({
            "state": state_key,
            "label": get_state_label(state_key),
            "democrat": dem_v,
            "republican": rep_v,
            "winner": winner,
            "ec_votes": ec,
        })

    total_ec = ec_dem + ec_rep
    majority = (total_ec // 2) + 1
    national_winner = body.democrat_name if ec_dem > ec_rep else body.republican_name
    if ec_dem == ec_rep:
        national_winner = "Tie"

    log_stage(logger, "api_presidential_end", states_done=len(state_results))
    return {
        "state_results": state_results,
        "electoral_college": {
            "democrat": ec_dem,
            "republican": ec_rep,
            "total": total_ec,
            "majority": majority,
            "winner": national_winner,
        },
        "democrat_name": body.democrat_name,
        "republican_name": body.republican_name,
    }


@app.exception_handler(MemoryError)
async def memory_error_handler(request: Request, exc: MemoryError):
    logger.critical("MemoryError on %s", request.url.path, exc_info=True)
    return JSONResponse(
        status_code=503,
        content={"detail": "Server ran out of memory; try again or reduce geometry settings."},
    )
