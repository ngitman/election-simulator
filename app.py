"""
Florida Election Simulator — desktop app with choropleth and electoral bar chart.
Run: python app.py [path/to/florida_counties.shp]
If no shapefile path is given, loads Florida counties from Census TIGER (requires internet).
"""
import argparse
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import geopandas as gpd

from data_loader import load_florida_counties, SHAPEFILES_DIR
from simulation import run_simulation, get_state_totals, REPUBLICAN_NAME, DEMOCRAT_NAME


def build_choropleth_axes(fig: Figure, gdf: gpd.GeoDataFrame) -> None:
    """Draw choropleth on the top subplot (axes already created)."""
    ax = fig.axes[0]
    ax.clear()
    gdf.plot(ax=ax, color=gdf["Color"], edgecolor="white", linewidth=0.4)
    ax.set_axis_off()
    ax.set_title("Florida county results (by margin)", fontsize=12)
    # Legend
    patches = [
        mpatches.Patch(color="#004cb8", label=f"{DEMOCRAT_NAME} safe"),
        mpatches.Patch(color="#84b1fa", label=f"{DEMOCRAT_NAME} lean"),
        mpatches.Patch(color="#ff8080", label=f"{REPUBLICAN_NAME} lean"),
        mpatches.Patch(color="#b80000", label=f"{REPUBLICAN_NAME} safe"),
        mpatches.Patch(color="#ff722b", label="Other"),
    ]
    ax.legend(handles=patches, loc="lower left", fontsize=8)


def build_bar_chart_axes(fig: Figure, gdf: gpd.GeoDataFrame, republican: str) -> None:
    """Draw side-by-side horizontal bar chart (electoral style) on bottom subplot."""
    ax = fig.axes[1]
    ax.clear()
    totals = get_state_totals(gdf, democrat_name=DEMOCRAT_NAME, republican=republican)
    cast = totals["Cast_Ballots"]
    gitman = int(totals[DEMOCRAT_NAME])
    rep = int(totals[republican])
    other = int(totals["Other"])
    two_party = gitman + rep

    # Classic electoral: one row, back-to-back bar (Democrat left, Republican right from center)
    y_center = 0.5
    bar_height = 0.35
    max_side = max(gitman, rep)
    ax.barh(y_center, gitman, height=bar_height, left=-gitman, color="#1a5fb4", edgecolor="white", linewidth=0.8)
    ax.barh(y_center, rep, height=bar_height, left=0, color="#c01c28", edgecolor="white", linewidth=0.8)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks([y_center])
    ax.set_yticklabels([f"{DEMOCRAT_NAME}  \u2190  |  \u2192  {republican}"], fontsize=10)
    ax.set_xlim(-max_side * 1.15, max_side * 1.15)
    ax.set_xlabel("Votes", fontsize=10)
    ax.set_title("Statewide vote totals (side-by-side)", fontsize=12)
    # Vote labels at bar ends
    pct_g = (gitman / cast * 100) if cast else 0
    pct_r = (rep / cast * 100) if cast else 0
    ax.text(-gitman - max_side * 0.02, y_center, f"{gitman:,} ({pct_g:.1f}%)", va="center", ha="right", fontsize=9)
    ax.text(rep + max_side * 0.02, y_center, f"{rep:,} ({pct_r:.1f}%)", va="center", ha="left", fontsize=9)
    # Other votes in subtitle
    ax.text(0.5, -0.25, f"Other: {other:,} ({other/cast*100:.1f}%)  |  Total cast: {cast:,}", transform=ax.transAxes, ha="center", fontsize=9, color="gray")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()


def main():
    parser = argparse.ArgumentParser(description="Florida Election Simulator")
    parser.add_argument(
        "shapefile",
        nargs="?",
        default=None,
        help="Path to Florida county shapefile (.shp or directory). Optional.",
    )
    parser.add_argument("--no-web", action="store_true", help="Disable loading shapefile from Census (require local file)")
    args = parser.parse_args()

    root = tk.Tk()
    root.title("Florida Election Simulator")
    root.geometry("900x750")
    root.minsize(700, 600)

    # State: current GeoDataFrame and republican name
    state = {"gdf": None, "republican": REPUBLICAN_NAME}

    def load_data():
        path = args.shapefile
        if not path and state.get("shapefile_path"):
            path = state["shapefile_path"]
        try:
            gdf = load_florida_counties(
                shapefile_path=path,
                use_web_fallback=not args.no_web,
            )
            state["gdf"] = gdf
            return True
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return False

    def run_and_plot():
        if state["gdf"] is None:
            if not load_data():
                return
        gdf = state["gdf"].copy()
        gdf = run_simulation(gdf, republican=state["republican"], seed=None)
        state["gdf"] = gdf

        fig = state["figure"]
        build_choropleth_axes(fig, gdf)
        build_bar_chart_axes(fig, gdf, state["republican"])
        state["canvas"].draw()

    # Figure: two subplots
    fig = Figure(figsize=(8.5, 9), dpi=100)
    fig.add_subplot(2, 1, 1)  # choropleth
    fig.add_subplot(2, 1, 2)  # bar chart
    state["figure"] = fig

    # Toolbar and canvas
    toolbar_frame = ttk.Frame(root)
    toolbar_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

    def open_shapefiles_folder():
        folder = SHAPEFILES_DIR / "florida"
        folder.mkdir(parents=True, exist_ok=True)
        path = str(folder.resolve())
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    def open_shapefile():
        path = filedialog.askopenfilename(
            title="Select Florida county shapefile",
            filetypes=[("Shapefile", "*.shp"), ("All", "*")],
        )
        if path:
            state["shapefile_path"] = path
            if load_data():
                run_and_plot()

    ttk.Button(toolbar_frame, text="Run simulation", command=run_and_plot).pack(side=tk.LEFT, padx=2)
    ttk.Button(
        toolbar_frame,
        text="Open shapefile…",
        command=open_shapefile,
    ).pack(side=tk.LEFT, padx=2)
    ttk.Button(
        toolbar_frame,
        text="Open shapefiles folder",
        command=open_shapefiles_folder,
    ).pack(side=tk.LEFT, padx=2)

    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)
    state["canvas"] = canvas

    # Initial load and run (optional shapefile from CLI)
    if args.shapefile and load_data():
        run_and_plot()
    elif not args.shapefile:
        # No CLI path: try Census TIGER (requires network)
        if load_data():
            run_and_plot()
        else:
            state["figure"].axes[0].set_title(
                f"Put a county .shp or .zip in {SHAPEFILES_DIR / 'florida'} or use 'Open shapefile…'."
            )
            state["canvas"].draw()
    else:
        state["figure"].axes[0].set_title("Failed to load shapefile. Use 'Open shapefile…' to choose one.")
        state["canvas"].draw()

    root.mainloop()


if __name__ == "__main__":
    main()
