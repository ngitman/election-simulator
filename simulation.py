"""
Florida election simulation with county base leans, statewide swing, and county noise.
Produces per-county votes and winner/margin for choropleth and bar chart.
"""
import random
from typing import Optional

import geopandas as gpd
import pandas as pd


# Default simulation parameters
DEMOCRAT_NAME = "Democrat"
REPUBLICAN_NAME = "Republican"
# Plurality winner label when third-party votes lead (must match API/UI).
OTHER_PLURALITY_LABEL = "Other"


def plurality_winner_votes(
    dem_v: int,
    rep_v: int,
    other_v: int,
    democrat_name: str,
    republican_name: str,
) -> str:
    """True plurality among D, R, and other votes; ties return 'Tie'."""
    m = max(dem_v, rep_v, other_v)
    at_max: list[str] = []
    if dem_v == m:
        at_max.append(democrat_name)
    if rep_v == m:
        at_max.append(republican_name)
    if other_v == m:
        at_max.append(OTHER_PLURALITY_LABEL)
    if len(at_max) > 1:
        return "Tie"
    return at_max[0]


def major_party_winner_votes(
    dem_v: int,
    rep_v: int,
    democrat_name: str,
    republican_name: str,
) -> str:
    """Winner if only Democrat vs Republican ballots counted (same vote totals, ignore Other)."""
    if dem_v > rep_v:
        return democrat_name
    if rep_v > dem_v:
        return republican_name
    return "Tie"

# County categories by state (historically D-leaning, swing). Others use population-size tiers.
STATE_LIKELY_D = {
    "florida": [
        "Broward", "Palm Beach", "Hillsborough", "Pinellas",
        "Gadsden", "Leon", "Alachua", "Orange",
    ],
    "new_york": [
        "Kings", "Queens", "Bronx", "New York", "Richmond",  # NYC
        "Westchester", "Suffolk", "Nassau", "Rockland", "Albany",
    ],
}
STATE_SWING = {
    "florida": [
        "Monroe", "Osceola", "Seminole", "St. Lucie", "Miami-Dade",
        "Duval", "Jefferson",
    ],
    "new_york": [
        "Erie", "Onondaga", "Monroe", "Orange", "Dutchess",
        "Oneida", "Saratoga", "Ulster",
    ],
}

DEFAULT_TURNOUT = 55
DEFAULT_UNPOPULARITY_INDEX = 0.0
# Effective third-party % uses (scale ** 2) × baseline random share (capped), so high
# slider values are much more dramatic while scale=1.0 matches the original model.
DEFAULT_THIRD_PARTY_SCALE = 1.0
THIRD_PARTY_PCT_CAP = 55.0

# Simulation structure
STATEWIDE_SWING_RANGE = 8.0   # ± points applied to all counties each run
COUNTY_NOISE_RANGE = 5.0      # ± points per county (random)
SWING_COUNTY_NOISE_RANGE = 9.0  # swing counties are more volatile
BASE_LEAN_SPREAD = 5         # ± spread from category center (deterministic from name)


def _county_base_lean(
    full_county_name: str,
    total_county_pop: float,
    likely_d: list[str],
    swing: list[str],
) -> float:
    """
    Two-party D share (0–100) for this county before swing/noise.
    Uses category + deterministic offset from name so geography is stable across runs.
    """
    if full_county_name in likely_d:
        center = 60.0
    elif full_county_name in swing:
        center = 50.0
    elif total_county_pop < 400_000:
        center = 30.0
    elif total_county_pop < 900_000:
        center = 42.0
    else:
        center = 48.0

    name_hash = hash(full_county_name) % (2 * BASE_LEAN_SPREAD + 1)
    offset = (name_hash - BASE_LEAN_SPREAD)
    return max(5.0, min(95.0, center + offset))


def run_simulation(
    gdf: gpd.GeoDataFrame,
    *,
    state: str = "florida",
    democrat_name: str = DEMOCRAT_NAME,
    republican: str = REPUBLICAN_NAME,
    turnout: int = DEFAULT_TURNOUT,
    unpopularity_index: float = DEFAULT_UNPOPULARITY_INDEX,
    bias_d_r: float = 0.0,
    third_party_scale: float = DEFAULT_THIRD_PARTY_SCALE,
    increase_urban: bool = False,
    decrease_rural: bool = False,
    pop_column: str = "TOT_POP22",
    county_column: str = "COUNTY",
    seed: Optional[int] = None,
) -> gpd.GeoDataFrame:
    """
    Run one election simulation. state: "florida" or "new_york" for county lean lists.
    """
    if seed is not None:
        random.seed(seed)

    state_key = state.lower().replace(" ", "_")
    likely_d = STATE_LIKELY_D.get(state_key, [])
    swing = STATE_SWING.get(state_key, [])

    if county_column not in gdf.columns and "NAME" in gdf.columns:
        county_column = "NAME"

    statewide_swing = random.uniform(-STATEWIDE_SWING_RANGE, STATEWIDE_SWING_RANGE)

    for i, row in gdf.iterrows():
        total_county_pop = float(row[pop_column])
        if total_county_pop <= 0:
            total_county_pop = 50000.0

        full_county_name = str(row[county_column]).replace("County", "").strip()
        is_swing = full_county_name in swing

        # ---- Turnout ----
        turnout_band = random.uniform(-8, 8)
        if increase_urban and total_county_pop > 1_000_000:
            turnout_band += random.uniform(2, 8)
        elif decrease_rural and total_county_pop < 400_000:
            turnout_band -= random.uniform(2, 6)
        elif not decrease_rural and total_county_pop < 400_000:
            turnout_band += random.uniform(-4, 4)
        if is_swing:
            turnout_band += random.uniform(0, 3)  # mobilization in close areas
        county_turnout_pct = max(35.0, min(85.0, turnout + turnout_band))
        gdf.at[i, "CountyTurnout"] = round(county_turnout_pct, 1)

        # Voting-eligible proxy; total votes
        vap = total_county_pop / 1.5
        total_county_votes = max(100, round(vap * (county_turnout_pct / 100)))
        gdf.at[i, "Cast_Ballots"] = total_county_votes
        cast_ballots_copy = total_county_votes

        # ---- Other / third party ----
        if is_swing:
            other_pct = random.uniform(1.0, 2.5)
        else:
            other_pct = random.uniform(0.5, 2.0)
        tps = max(0.0, float(third_party_scale))
        if tps <= 0:
            other_pct = 0.0
        else:
            # Quadratic in scale: max slider (×5) behaves like ×25 on baseline → big visible swings.
            other_pct = min(THIRD_PARTY_PCT_CAP, other_pct * (tps * tps))
        other_votes = round(total_county_votes * (other_pct / 100))
        other_votes = min(other_votes, total_county_votes - 2)
        gdf.at[i, "Other_Votes"] = other_votes
        two_party_votes = total_county_votes - other_votes

        # ---- Two-party D share ----
        base_lean = _county_base_lean(full_county_name, total_county_pop, likely_d, swing)
        noise_range = SWING_COUNTY_NOISE_RANGE if is_swing else COUNTY_NOISE_RANGE
        county_noise = random.uniform(-noise_range, noise_range)
        d_share = (
            base_lean
            + statewide_swing
            + county_noise
            + bias_d_r
            + float(unpopularity_index)
        )
        d_share = max(1.0, min(99.0, d_share))

        dem_votes = round(two_party_votes * (d_share / 100))
        dem_votes = max(0, min(two_party_votes, dem_votes))
        rep_votes = two_party_votes - dem_votes
        gdf.at[i, "Democrat_Votes"] = dem_votes
        gdf.at[i, f"{republican}_Votes"] = rep_votes

        # ---- Percentages and margin ----
        dem_pct = round((dem_votes / cast_ballots_copy) * 100, 2)
        repub_p = round((rep_votes / cast_ballots_copy) * 100, 2)
        other_p = round((other_votes / cast_ballots_copy) * 100, 2)
        margin = dem_pct - repub_p
        gdf.at[i, "Democrat_Percentage"] = dem_pct
        gdf.at[i, f"{republican}_Percentage"] = repub_p
        gdf.at[i, "Other_Percentage"] = other_p
        gdf.at[i, "Size_Lead"] = dem_votes - rep_votes
        gdf.at[i, "Margin"] = margin

        # ---- Choropleth color ----
        if other_p > max(dem_pct, repub_p):
            other_margin = other_p - max(dem_pct, repub_p)
            if other_p < 50:
                color = "#ffdac7"
            elif other_margin < 5:
                color = "#ffbe9e"
            elif other_margin < 15:
                color = "#ff9763"
            else:
                color = "#ff722b"
        elif margin < 0:
            if repub_p < 50:
                color = "#fcc5c5"
            elif abs(margin) < 5:
                color = "#ff8080"
            elif abs(margin) < 15:
                color = "#ff3636"
            else:
                color = "#b80000"
        elif margin > 0:
            if dem_pct < 50:
                color = "#c7dcff"
            elif abs(margin) < 5:
                color = "#abcbff"
            elif abs(margin) < 15:
                color = "#84b1fa"
            else:
                color = "#004cb8"
        else:
            color = "#ba9eff"

        gdf.at[i, "Color"] = color
        gdf.at[i, "Winner"] = plurality_winner_votes(
            dem_votes, rep_votes, other_votes, democrat_name, republican
        )
        gdf.at[i, "Major_Party_Winner"] = major_party_winner_votes(
            dem_votes, rep_votes, democrat_name, republican
        )

    return gdf


def get_state_totals(
    gdf: gpd.GeoDataFrame,
    democrat_name: str = DEMOCRAT_NAME,
    republican: str = REPUBLICAN_NAME,
) -> pd.Series:
    """Return total votes per candidate and cast ballots."""
    return pd.Series({
        democrat_name: gdf["Democrat_Votes"].sum(),
        republican: gdf[f"{republican}_Votes"].sum(),
        "Other": gdf["Other_Votes"].sum(),
        "Cast_Ballots": gdf["Cast_Ballots"].sum(),
    })
