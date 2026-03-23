"""
FastAPI backend for Election Simulator (Florida + New York + Presidential).
Run from project root: uvicorn backend.main:app --reload
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import geopandas as gpd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

app = FastAPI(title="Election Simulator API")

_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_frontend_origins_env = os.getenv("FRONTEND_ORIGINS", "")
_frontend_origins = [o.strip() for o in _frontend_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins or _default_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Electoral votes for supported states (subset of 538)
EC_VOTES = STATE_EC_VOTES

_base_by_state: dict[str, gpd.GeoDataFrame] = {}
_last_by_state: dict[str, dict] = {}
_geo_cache_by_state: dict[str, dict] = {}


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
    return {"type": "FeatureCollection", "features": features}


def _ensure_loaded(state_key: str) -> gpd.GeoDataFrame:
    if state_key not in _base_by_state or _base_by_state[state_key] is None:
        try:
            base = load_state_counties(state_key, use_web_fallback=True)
            # Reset index once so positional arrays align with cached geometry order.
            base = base.reset_index(drop=True)
            _base_by_state[state_key] = base

            # Cache WGS84 geometry and names once per server lifetime.
            _geo_cache_by_state[state_key] = _build_geo_cache(base)
        except FileNotFoundError as e:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": str(e),
                    "shapefiles_path": str(SHAPEFILES_DIR / state_key),
                },
            )
    return _base_by_state[state_key]


def _build_geo_cache(base: gpd.GeoDataFrame) -> dict:
    """Build cached WGS84 geometries + names for fast GeoJSON generation."""
    gdf_wgs84 = base.to_crs(epsg=4326)
    geoms: list[dict] = []
    names: list[str] = []
    for _, row in gdf_wgs84.iterrows():
        geoms.append(row.geometry.__geo_interface__)
        names.append(row.get("NAME") or row.get("COUNTY", ""))
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
        base = load_state_counties(state_key, use_web_fallback=True)
        base = base.reset_index(drop=True)
        _base_by_state[state_key] = base
        _geo_cache_by_state[state_key] = _build_geo_cache(base)
        return {
            "success": True,
            "state": state_key,
            "state_label": get_state_label(state_key),
            "countyCount": len(_base_by_state[state_key]),
            "message": f"Loaded {len(_base_by_state[state_key])} {get_state_label(state_key)} counties.",
        }
    except FileNotFoundError as e:
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
    state_results = []
    ec_dem = 0
    ec_rep = 0

    for state_key in SUPPORTED_STATES:
        try:
            payload = _run_state_simulation(
                state_key=state_key,
                democrat_name=body.democrat_name,
                republican_name=body.republican_name,
                bias_d_r=body.bias_d_r,
                turnout=body.turnout,
                unpopularity_index=body.unpopularity_index,
                seed=body.seed,
            )
        except HTTPException:
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
