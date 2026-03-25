<script>
  import { onMount, tick } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';

  const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '');
  const MAP_CENTERS = { florida: [28.5, -82.5], new_york: [43.0, -75.5] };
  /** Set true to show incumbent unpopularity control again. */
  const SHOW_INCUMBENT_UNPOPULARITY = false;

  /** Dev uses Vite proxy to localhost:8000; empty body means proxy error / API down. */
  function apiOfflineHint() {
    return API === ''
      ? 'Backend not reachable (is uvicorn running on port 8000? Try: uvicorn backend.main:app --reload)'
      : 'API returned an empty or invalid response.';
  }

  /**
   * Parse fetch Response as JSON. Avoids "Unexpected end of JSON input" when the proxy
   * returns 502 with an empty body (ECONNREFUSED to backend).
   */
  async function parseJsonResponse(res) {
    const text = await res.text();
    if (!text?.trim()) {
      throw new Error(`${apiOfflineHint()} (HTTP ${res.status})`);
    }
    try {
      return JSON.parse(text);
    } catch {
      throw new Error(`${apiOfflineHint()} (HTTP ${res.status}, not JSON)`);
    }
  }

  let mapContainer = $state(null);
  let map = $state(null);
  let geoLayer = $state(null);
  let result = $state(null);
  let loading = $state(false);
  /** Full-screen splash while fetching /api/states on first paint. */
  let initialBoot = $state(true);
  let error = $state(null);

  let stateList = $state([
    { id: 'florida', label: 'Florida', ec_votes: 30 },
    { id: 'new_york', label: 'New York', ec_votes: 28 },
  ]);
  /** Empty until the user picks a state on the welcome screen. */
  let currentState = $state('');
  /** After Continue: show map + controls; simulation runs only when user clicks Run. */
  let showSimulator = $state(false);
  /** Last state that successfully loaded on the server (for reverting the dropdown on load failure). */
  let lastLoadedState = $state('');
  let activeGeoState = $state('');
  let presidentialMode = $state(false);
  let presidentialResult = $state(null);

  let democratName = $state('Democrat');
  let republicanName = $state('Republican');
  let biasDR = $state(0);
  let turnout = $state(55);
  let incumbentUnpopularity = $state(0);
  /** 0–1000 → API third_party_scale 0–10 (100 = default; effect is quadratic in the model). */
  let thirdPartyStrength = $state(100);

  function thirdPartyMeta(v) {
    const n = Number(v) ?? 0;
    if (n <= 0) return 'No third-party votes — full ballot to D vs R';
    if (n === 100) return 'Default — modest third-party share (same as original model)';
    if (n < 100) return 'Lower — less “Other”; D and R use more of each ballot';
    if (n <= 300) return 'Stronger third-party share; bar shows wider orange band';
    return 'Very high — often caps near ~55% “Other” in some counties (dramatic)';
  }

  function stateLabel(stateKey) {
    if (!stateKey) return 'Select a state';
    const item = stateList.find((s) => s.id === stateKey);
    return item?.label || stateKey;
  }

  function getSharedSimParams() {
    const raw = Number(thirdPartyStrength);
    const third_party_scale = Math.max(0, Math.min(10, (Number.isFinite(raw) ? raw : 100) / 100));
    return {
      democrat_name: democratName || 'Democrat',
      republican_name: republicanName || 'Republican',
      bias_d_r: Number(biasDR) || 0,
      turnout: Math.round(Number(turnout)) || 55,
      unpopularity_index: Number(incumbentUnpopularity) || 0,
      third_party_scale,
    };
  }

  function getSimulationBody() {
    return { state: currentState, ...getSharedSimParams() };
  }

  function setMapView(stateKey) {
    if (!map) return;
    map.setView(MAP_CENTERS[stateKey] || MAP_CENTERS.florida, 6);
  }

  async function runSimulation() {
    if (!currentState) return;
    loading = true;
    error = null;
    presidentialResult = null;
    try {
      const res = await fetch(`${API}/api/simulation/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(getSimulationBody()),
      });
      if (!res.ok) {
        const data = await parseJsonResponse(res);
        const d = data.detail;
        let msg = (typeof d === 'object' ? d?.message : d) || res.statusText;
        if (typeof d === 'object' && d?.shapefiles_path) msg += ` Put a file in ${d.shapefiles_path}.`;
        throw new Error(msg);
      }
      const data = await parseJsonResponse(res);
      result = data;
      setMapView(currentState);
      updateMap(data.geojson, currentState);
    } catch (e) {
      error = e.message || String(e);
    } finally {
      loading = false;
    }
  }

  /** Load county geometry on the server only (no simulation). */
  async function loadStateOnly(stateKey) {
    loading = true;
    error = null;
    try {
      const loadRes = await fetch(`${API}/api/load?state=${encodeURIComponent(stateKey)}`);
      const loadData = await parseJsonResponse(loadRes);
      if (!loadData.success) {
        error =
          loadData.message + (loadData.shapefiles_path ? ` (Put a file in ${loadData.shapefiles_path})` : '');
        if (lastLoadedState) currentState = lastLoadedState;
        return false;
      }
      currentState = stateKey;
      lastLoadedState = stateKey;
      result = null;
      presidentialResult = null;
      if (map && geoLayer) {
        map.removeLayer(geoLayer);
        geoLayer = null;
      }
      activeGeoState = '';
      return true;
    } catch (e) {
      error = e.message || String(e);
      if (lastLoadedState) currentState = lastLoadedState;
      return false;
    } finally {
      loading = false;
    }
  }

  async function continueToSimulator() {
    if (!currentState) return;
    const ok = await loadStateOnly(currentState);
    if (!ok) return;
    showSimulator = true;
    await tick();
    initMapIfNeeded();
  }

  function initMapIfNeeded() {
    if (map || !mapContainer) return;
    map = L.map(mapContainer).setView([28.5, -82.5], 6);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap, &copy; CARTO',
    }).addTo(map);
    setMapView(currentState);
  }

  async function runPresidential() {
    if (!currentState) return;
    loading = true;
    error = null;
    result = null;
    presidentialResult = null;
    try {
      const res = await fetch(`${API}/api/presidential/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(getSharedSimParams()),
      });
      if (!res.ok) {
        const data = await parseJsonResponse(res);
        const d = data.detail;
        throw new Error((typeof d === 'object' ? d?.message : d) || res.statusText);
      }
      presidentialResult = await parseJsonResponse(res);
      const simRes = await fetch(`${API}/api/simulation?state=${encodeURIComponent(currentState)}`);
      if (simRes.ok) {
        result = await parseJsonResponse(simRes);
        setMapView(currentState);
        if (result?.geojson) updateMap(result.geojson, currentState);
      }
    } catch (e) {
      error = e.message || String(e);
    } finally {
      loading = false;
    }
  }

  function countyTooltip(props, dName, rName) {
    const p = props || {};
    const mp = p.major_party_winner
      ? `<br/><span style="font-size:0.88em;color:#c4c2d0">Major-party: ${p.major_party_winner}</span>`
      : '';
    return `<div class="tooltip-popup">
      <strong>${p.name || 'County'}</strong><br/>
      ${dName}: ${(p.democrat_pct ?? 0).toFixed(1)}% (${(p.democrat_votes ?? 0).toLocaleString()})<br/>
      ${rName}: ${(p.rep_pct ?? 0).toFixed(1)}% (${(p.rep_votes ?? 0).toLocaleString()})<br/>
      Other: ${(p.other_pct ?? 0).toFixed(1)}% (${(p.other_votes ?? 0).toLocaleString()})<br/>
      <strong>Winner: ${p.winner ?? '—'}</strong>${mp}
    </div>`;
  }

  function updateMap(geojson, stateKey) {
    if (!map || !geojson) return;
    if (geoLayer && activeGeoState === stateKey) {
      const layers = geoLayer.getLayers();
      const features = geojson.features || [];
      const n = Math.min(layers.length, features.length);
      for (let idx = 0; idx < n; idx += 1) {
        const layer = layers[idx];
        const props = features[idx]?.properties || {};
        layer.feature.properties = { ...(layer.feature.properties || {}), ...props };
        layer.setStyle({ fillColor: props.color || '#ccc' });
      }
      return;
    }
    if (geoLayer) map.removeLayer(geoLayer);
    activeGeoState = stateKey;
    geoLayer = L.geoJSON(geojson, {
      style: (feature) => ({
        fillColor: feature.properties?.color || '#5a5a62',
        color: '#0f1116',
        weight: 0.7,
        fillOpacity: 0.9,
      }),
      onEachFeature: (feature, layer) => {
        layer.bindTooltip(() => countyTooltip(layer.feature?.properties, democratName, republicanName), {
          sticky: true,
          className: 'county-tooltip',
          direction: 'top',
          offset: [0, -8],
        });
      },
    }).addTo(map);
  }

  onMount(async () => {
    await tick();
    try {
      const statesRes = await fetch(`${API}/api/states`);
      if (statesRes.ok) {
        const data = await parseJsonResponse(statesRes);
        stateList = data.states || stateList;
      }
    } catch (e) {
      error = e.message || 'Backend not reachable. Start it with: uvicorn backend.main:app --reload';
    } finally {
      initialBoot = false;
    }
  });
</script>

<svelte:head>
  <title>Gitman's Political Simulator</title>
</svelte:head>

{#if initialBoot}
  <div
    class="boot-overlay"
    role="status"
    aria-live="polite"
    aria-busy="true"
    aria-label="Loading election simulator"
  >
    <div class="boot-card">
      <div class="boot-spinner" aria-hidden="true"></div>
      <p class="boot-title">Loading simulator</p>
      <p class="boot-sub">Fetching available states from the API…</p>
    </div>
  </div>
{:else if !showSimulator}
  <div class="pick-shell">
    <header class="pick-topbar">
      <div class="brand">Gitman's Political Simulator</div>
    </header>
    <main class="pick-main">
      <div class="pick-card">
        <h1 class="pick-heading">Choose a state</h1>
        <p class="pick-lead">Pick where to run the model, then open the map. The simulation does not start until you click Run.</p>
        <div class="field-group">
          <label for="pick-state">State</label>
          <select id="pick-state" class="state-select pick-select" bind:value={currentState} disabled={loading}>
            <option value="" disabled>Select state…</option>
            {#each stateList as s}
              <option value={s.id}>{s.label}</option>
            {/each}
          </select>
        </div>
        <button
          type="button"
          class="run-btn pick-continue"
          onclick={continueToSimulator}
          disabled={!currentState || loading}
        >
          {loading ? 'Loading counties…' : 'Continue to simulator'}
        </button>
      </div>
      {#if error}
        <section class="error-card pick-error">{error}</section>
      {/if}
    </main>
  </div>
{:else}
<div class="shell">
  <header class="topbar">
    <div class="brand">Gitman's Political Simulator</div>
    <div class="top-actions">
      <label class="state-select-wrap">
        <span class="state-select-label">State</span>
        <select
          class="state-select"
          bind:value={currentState}
          onchange={(e) => loadStateOnly(e.currentTarget.value)}
          disabled={loading}
        >
          {#each stateList as s}
            <option value={s.id}>{s.label}</option>
          {/each}
        </select>
      </label>
      <label class="presidential-toggle">
        <input type="checkbox" bind:checked={presidentialMode} />
        Presidential
      </label>
    </div>
  </header>

  <div class="content-wrap">
    <aside class="control-rail">
      <div class="rail-head">
        <div class="rail-title">Simulation Control</div>
        <div class="rail-sub">Live model inputs</div>
      </div>

      <div class="field-group">
        <label for="dem-name">Democrat candidate</label>
        <input id="dem-name" type="text" bind:value={democratName} placeholder="Democrat" />
      </div>
      <div class="field-group">
        <label for="rep-name">Republican candidate</label>
        <input id="rep-name" type="text" bind:value={republicanName} placeholder="Republican" />
      </div>
      <div class="field-group">
        <label for="turnout">Turnout %</label>
        <input id="turnout" type="number" min="30" max="90" step="1" bind:value={turnout} />
      </div>
      <div class="field-group">
        <label for="bias">Bias: D ← → R</label>
        <input id="bias" type="range" min="-20" max="20" step="1" bind:value={biasDR} />
        <div class="meta">{biasDR > 0 ? 'D' : biasDR < 0 ? 'R' : 'Even'} {biasDR !== 0 ? `${Math.abs(biasDR)} pts` : ''}</div>
      </div>
      {#if SHOW_INCUMBENT_UNPOPULARITY}
        <div class="field-group">
          <label for="unpopularity">Incumbent unpopularity</label>
          <input id="unpopularity" type="number" min="0" max="50" step="1" bind:value={incumbentUnpopularity} />
          <div class="meta">0 = neutral</div>
        </div>
      {/if}
      <div class="field-group">
        <label for="third-party">Third party strength (0–10×, quadratic)</label>
        <input id="third-party" type="range" min="0" max="1000" step="10" bind:value={thirdPartyStrength} />
        <div class="meta">{thirdPartyMeta(thirdPartyStrength)}</div>
      </div>

      {#if presidentialMode}
        <button class="run-btn" onclick={runPresidential} disabled={loading || !currentState}>{loading ? 'Running…' : 'Run Presidential'}</button>
      {:else}
        <button class="run-btn" onclick={runSimulation} disabled={loading || !currentState}>{loading ? 'Running…' : 'Run Simulation'}</button>
      {/if}
    </aside>

    <main class="main-canvas">
      <section class="disclaimer-card">
        Disclaimer: This tool is a randomized simulation for demonstration purposes only.
        Results are not real election data and should not be treated as factual forecasts.
      </section>

      {#if error}
        <section class="error-card">{error}</section>
      {/if}

      {#if presidentialMode && presidentialResult}
        {@const ec = presidentialResult.electoral_college}
        {@const dName = presidentialResult.democrat_name}
        {@const rName = presidentialResult.republican_name}
        <section class="ec-card">
          <div class="ec-head">Electoral College</div>
          <div class="ec-score">
            {dName} {ec.democrat} — {ec.republican} {rName}{#if (ec.other ?? 0) > 0}
              · Other {ec.other}{/if}
          </div>
          <div class="ec-meta">Winner: <strong>{ec.winner}</strong> · Majority {ec.majority}</div>
          <div class="ec-list">
            {#each presidentialResult.state_results as sr}
              <div class="ec-item">
                <span>{sr.label}</span>
                <span class="ec-winner-cell">
                  {sr.winner}
                  {#if sr.winner === 'Other' && sr.major_party_winner && sr.major_party_winner !== 'Tie'}
                    <span class="ec-mpw">(majors: {sr.major_party_winner})</span>
                  {/if}
                </span>
                <span>{sr.ec_votes} EV</span>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <section class="map-card">
        <div class="map-header">
          {stateLabel(currentState)} county results
          {#if !result}
            <span class="map-hint">— Run simulation to paint counties</span>
          {/if}
        </div>
        <div class="map-stage">
          <div class="map-wrap" bind:this={mapContainer}></div>
          <div class="legend-card">
            {#if result}
              {@const legendTotals = result.totals}
              {@const legendD = legendTotals.democrat_name ?? 'Democrat'}
              {@const legendR = legendTotals.republican_name ?? 'Republican'}
              <div><span class="sw blue"></span>{legendD} safe</div>
              <div><span class="sw blue-dim"></span>{legendD} lean</div>
              <div><span class="sw red-dim"></span>{legendR} lean</div>
              <div><span class="sw red"></span>{legendR} safe</div>
            {:else}
              <div><span class="sw blue"></span>Democrat safe</div>
              <div><span class="sw blue-dim"></span>Democrat lean</div>
              <div><span class="sw red-dim"></span>Republican lean</div>
              <div><span class="sw red"></span>Republican safe</div>
            {/if}
            <div><span class="sw orange"></span>Other</div>
          </div>
        </div>
      </section>

      {#if result}
        {@const t = result.totals}
        {@const dName = t.democrat_name ?? 'Democrat'}
        {@const rName = t.republican_name ?? 'Republican'}
        {@const cast = t.cast_ballots || 1}
        {@const twoParty = (t.democrat ?? 0) + (t.republican ?? 0) || 1}
        {@const demPctTotal = ((t.democrat ?? 0) / cast) * 100}
        {@const repPctTotal = ((t.republican ?? 0) / cast) * 100}
        {@const otherPctTotal = ((t.other ?? 0) / cast) * 100}
        {@const demTwoParty = ((t.democrat ?? 0) / twoParty) * 100}
        {@const repTwoParty = ((t.republican ?? 0) / twoParty) * 100}
        {@const winner = t.winner ?? (t.democrat === t.republican ? 'Tie' : (t.democrat > t.republican ? dName : rName))}
        {@const mpw = t.major_party_winner ?? (t.democrat === t.republican ? 'Tie' : (t.democrat > t.republican ? dName : rName))}
        {@const isTie = winner === 'Tie'}
        {@const otherWon = winner === 'Other'}
        {@const demWonPlurality = winner === dName}
        {@const repWonPlurality = winner === rName}
        {@const mpDemWon = mpw === dName}
        {@const mpRepWon = mpw === rName}

        <section class="stats-grid">
          <article
            class="stat-card"
            class:blue={demWonPlurality}
            class:red={repWonPlurality}
            class:tie={isTie}
            class:other={otherWon}
          >
            <div class="label">Projected winner (plurality)</div>
            <div class="value">{winner}</div>
            <div class="stat-sub">Major-party only: {mpw}</div>
          </article>
          <article class="stat-card">
            <div class="label">D vs R margin (of all ballots)</div>
            <div class="value">{Math.abs((t.democrat / cast * 100) - (t.republican / cast * 100)).toFixed(1)}%</div>
          </article>
          <article class="stat-card">
            <div class="label">Total ballots</div>
            <div class="value">{cast.toLocaleString()}</div>
          </article>
        </section>

        <section class="bar-card">
          <div class="bar-head">
            <div class="bar-title">{t.state_label ? `${t.state_label} vote totals` : 'Statewide vote totals'}</div>
            <div
              class="winner-pill"
              class:dem={demWonPlurality}
              class:rep={repWonPlurality}
              class:tie={isTie}
              class:other={otherWon}
            >
              {winner}
            </div>
          </div>

          <div class="bar-graphic-wrap">
            <div class="bar-label left">{dName}: {t.democrat?.toLocaleString()} ({demPctTotal.toFixed(1)}% of all ballots)</div>
            <div
              class="bar-track"
              role="img"
              aria-label="Share of all ballots: {dName} {demPctTotal.toFixed(1)}%, Other {otherPctTotal.toFixed(1)}%, {rName} {repPctTotal.toFixed(1)}%"
            >
              <div class="bar-seg bar-dem" class:winner-bar={mpDemWon} style="width: {demPctTotal}%"></div>
              <div class="bar-seg bar-other-seg" class:winner-bar={otherWon} style="width: {otherPctTotal}%"></div>
              <div class="bar-seg bar-rep" class:winner-bar={mpRepWon} style="width: {repPctTotal}%"></div>
            </div>
            <div class="bar-label right">{t.republican?.toLocaleString()} ({repPctTotal.toFixed(1)}% of all ballots) {rName}</div>
          </div>
          <div class="bar-caption">
            Segments are share of <strong>all</strong> ballots (third party shrinks the blue and red slices). D vs R among major-party votes only: {demTwoParty.toFixed(1)}%–{repTwoParty.toFixed(1)}% (unchanged by this slider — it does not tilt D vs R).
            · Other: {t.other?.toLocaleString()} ({otherPctTotal.toFixed(1)}%) · Total: {cast.toLocaleString()}
          </div>
        </section>
      {:else if loading}
        <section class="loading-card">Working…</section>
      {/if}
    </main>
  </div>
</div>
{/if}

<style>
  .boot-overlay {
    position: fixed;
    inset: 0;
    z-index: 2000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(9, 9, 11, 0.88);
    backdrop-filter: blur(10px);
  }
  .boot-card {
    text-align: center;
    padding: 1.75rem 2rem;
    max-width: 22rem;
    background: #131317;
    border: 1px solid #2b2d34;
    border-radius: 0.35rem;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.45);
  }
  .boot-spinner {
    width: 2.5rem;
    height: 2.5rem;
    margin: 0 auto 1rem;
    border: 3px solid #2b2d34;
    border-top-color: #adc6ff;
    border-radius: 50%;
    animation: boot-spin 0.75s linear infinite;
  }
  @keyframes boot-spin {
    to { transform: rotate(360deg); }
  }
  .boot-title {
    margin: 0;
    font-size: 1rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: #e6e4ef;
  }
  .boot-sub {
    margin: 0.5rem 0 0;
    font-size: 0.78rem;
    line-height: 1.45;
    color: #abaab4;
  }

  .pick-shell {
    min-height: 100vh;
    background: #0e0e11;
    color: #e6e4ef;
    font-family: Inter, system-ui, sans-serif;
    display: flex;
    flex-direction: column;
  }
  .pick-topbar {
    height: 3rem;
    display: flex;
    align-items: center;
    padding: 0 1.25rem;
    background: #09090b;
    border-bottom: 1px solid #1f2026;
  }
  .pick-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
    gap: 1rem;
  }
  .pick-card {
    width: 100%;
    max-width: 26rem;
    background: #131317;
    border: 1px solid #2b2d34;
    border-radius: 0.35rem;
    padding: 1.5rem 1.35rem;
  }
  .pick-heading {
    margin: 0 0 0.5rem;
    font-size: 1.15rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }
  .pick-lead {
    margin: 0 0 1.1rem;
    font-size: 0.82rem;
    line-height: 1.45;
    color: #abaab4;
  }
  .pick-select {
    width: 100%;
    max-width: 100%;
  }
  .pick-continue {
    width: 100%;
    margin-top: 0.35rem;
  }
  .pick-error {
    max-width: 26rem;
    width: 100%;
  }
  .pick-card .field-group label {
    font-size: 0.72rem;
    color: #abaab4;
    letter-spacing: 0.02em;
    margin-bottom: 0.35rem;
    display: block;
  }

  .shell { min-height: 100vh; background: #0e0e11; color: #e6e4ef; font-family: Inter, system-ui, sans-serif; position: relative; }
  .topbar { height: 3rem; display: flex; align-items: center; justify-content: space-between; padding: 0 1.25rem; background: #09090b; border-bottom: 1px solid #1f2026; }
  .brand { font-size: 1rem; font-weight: 700; letter-spacing: 0.01em; }
  .top-actions { display: flex; align-items: center; gap: 0.75rem; }
  .state-select-wrap { display: flex; align-items: center; gap: 0.45rem; }
  .state-select-label { font-size: 0.72rem; color: #abaab4; letter-spacing: 0.03em; text-transform: uppercase; }
  .state-select {
    border: 1px solid #2b2d34;
    background: #1a1b22;
    color: #e6e4ef;
    border-radius: 0.2rem;
    padding: 0.35rem 0.5rem;
    font-size: 0.75rem;
    min-width: 140px;
  }
  .state-select:disabled { opacity: 0.6; cursor: not-allowed; }
  .presidential-toggle { font-size: 0.75rem; display: flex; align-items: center; gap: 0.35rem; color: #abaab4; }

  .content-wrap { display: grid; grid-template-columns: 260px 1fr; min-height: calc(100vh - 3rem); }
  .control-rail { background: #0a0b0f; border-right: 1px solid #1f2026; padding: 1.25rem; display: flex; flex-direction: column; gap: 0.9rem; }
  .rail-head { margin-bottom: 0.35rem; }
  .rail-title { font-size: 0.88rem; font-weight: 700; }
  .rail-sub { font-size: 0.68rem; color: #75757e; text-transform: uppercase; letter-spacing: 0.08em; }
  .field-group { display: flex; flex-direction: column; gap: 0.35rem; }
  .field-group label { font-size: 0.72rem; color: #abaab4; letter-spacing: 0.02em; }
  .field-group input[type='text'], .field-group input[type='number'] { background: #000; color: #e6e4ef; border: 1px solid #2e2f39; border-radius: 0.2rem; padding: 0.45rem 0.5rem; font-size: 0.8rem; }
  .field-group input[type='range'] { accent-color: #adc6ff; }
  .meta { font-size: 0.68rem; color: #75757e; }
  .run-btn { margin-top: 0.35rem; border: none; border-radius: 0.25rem; padding: 0.65rem 0.75rem; background: linear-gradient(180deg, #adc6ff 0%, #004395 100%); color: #d8e2ff; font-size: 0.74rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; cursor: pointer; }
  .run-btn:disabled { opacity: 0.65; cursor: not-allowed; }

  .main-canvas { padding: 1.4rem 1.6rem; display: flex; flex-direction: column; gap: 1.25rem; }
  .disclaimer-card {
    background: #1f1720;
    color: #f5c2d0;
    border-left: 3px solid #ff716a;
    padding: 0.7rem 0.9rem;
    font-size: 0.78rem;
    line-height: 1.35;
  }
  .error-card { background: #40141a; color: #ff97a3; padding: 0.8rem 1rem; border-radius: 0.25rem; }
  .loading-card { background: #19191e; color: #abaab4; padding: 1rem; border-radius: 0.25rem; }

  .stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.9rem; }
  .stat-card { background: #1f1f26; padding: 0.9rem 1rem; border-left: 3px solid #75757e; }
  .stat-card.blue { border-left-color: #adc6ff; }
  .stat-card.red { border-left-color: #ff716a; }
  .stat-card.tie { border-left-color: #6b6b78; }
  .stat-card.other { border-left-color: #ff9a5c; }
  .stat-card .label { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.09em; color: #abaab4; }
  .stat-card .value { margin-top: 0.35rem; font-size: 1.22rem; font-weight: 700; }
  .stat-sub { margin-top: 0.45rem; font-size: 0.74rem; color: #abaab4; line-height: 1.35; }

  .map-card { background: #131317; padding: 1rem; }
  .map-header { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.1em; color: #abaab4; margin-bottom: 0.75rem; }
  .map-hint {
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0.02em;
    color: #75757e;
    font-size: 0.68rem;
    margin-left: 0.35rem;
  }
  .map-stage { position: relative; }
  .map-wrap { height: 470px; border-radius: 0.25rem; overflow: hidden; background: #19191e; }
  .legend-card { position: absolute; right: 0.75rem; bottom: 0.75rem; background: rgba(25,25,30,0.86); backdrop-filter: blur(8px); padding: 0.6rem 0.7rem; border: 1px solid rgba(71,71,80,0.35); font-size: 0.68rem; display: grid; gap: 0.25rem; }
  .sw { width: 0.65rem; height: 0.65rem; display: inline-block; margin-right: 0.35rem; }
  .sw.blue { background: #004cb8; } .sw.blue-dim { background: #84b1fa; } .sw.red-dim { background: #ff8080; } .sw.red { background: #b80000; } .sw.orange { background: #ff722b; }

  .bar-card { background: #19191e; padding: 1rem; }
  .bar-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.7rem; gap: 0.5rem; }
  .bar-title { font-size: 0.86rem; font-weight: 600; letter-spacing: 0.01em; }
  .winner-pill { padding: 0.26rem 0.55rem; border-radius: 999px; font-size: 0.68rem; font-weight: 700; background: #474750; color: #e6e4ef; }
  .winner-pill.dem { background: #1f4ba0; }
  .winner-pill.rep { background: #8e2530; }
  .winner-pill.tie { background: #4f525c; }
  .winner-pill.other { background: #7a3d12; color: #ffe8d6; }

  .bar-graphic-wrap { display: grid; grid-template-columns: minmax(130px, auto) 1fr minmax(130px, auto); gap: 0.8rem; align-items: center; }
  .bar-label { font-size: 0.8rem; color: #cfd0d8; }
  .bar-label.left { text-align: right; } .bar-label.right { text-align: left; }
  .bar-track { width: 100%; height: 38px; display: flex; border-radius: 0.25rem; overflow: hidden; background: #000; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.05); }
  .bar-seg { height: 100%; min-width: 4px; transition: width 150ms ease-out; }
  .bar-dem { background: linear-gradient(180deg, #adc6ff 0%, #004395 100%); }
  .bar-other-seg { background: linear-gradient(180deg, #ffc9a8 0%, #ff722b 100%); }
  .bar-rep { background: linear-gradient(180deg, #ff716a 0%, #b11e1e 100%); }
  .winner-bar { box-shadow: 0 0 0 2px rgba(0,0,0,0.35) inset; }
  .bar-caption { margin-top: 0.6rem; color: #abaab4; font-size: 0.74rem; }

  .ec-card { background: #19191e; padding: 1rem; }
  .ec-head { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.09em; color: #abaab4; }
  .ec-score { margin-top: 0.3rem; font-size: 1.1rem; font-weight: 700; }
  .ec-meta { margin-top: 0.2rem; color: #abaab4; font-size: 0.8rem; }
  .ec-list { margin-top: 0.65rem; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.45rem; }
  .ec-item { display: flex; justify-content: space-between; gap: 0.5rem; background: #131317; padding: 0.4rem 0.55rem; font-size: 0.74rem; }
  .ec-mpw { font-size: 0.65rem; color: #abaab4; font-weight: 400; margin-left: 0.2rem; }
  .ec-winner-cell { text-align: right; }

  @media (max-width: 1100px) {
    .content-wrap { grid-template-columns: 1fr; }
    .control-rail { border-right: none; border-bottom: 1px solid #1f2026; }
    .stats-grid { grid-template-columns: 1fr; }
  }

  @media (max-width: 760px) {
    .main-canvas { padding: 1rem; }
    .topbar { padding: 0 0.8rem; }
    .brand { font-size: 0.9rem; }
    .bar-graphic-wrap { grid-template-columns: 1fr; }
    .bar-label.left, .bar-label.right { text-align: left; }
    .map-wrap { height: 360px; }
  }

  :global(.county-tooltip) {
    padding: 0.45rem 0.6rem;
    font-size: 0.74rem;
    line-height: 1.35;
    background: #19191e;
    color: #e6e4ef;
    border: 1px solid rgba(71,71,80,0.4);
    border-radius: 0.2rem;
    box-shadow: 0 12px 28px rgba(0,0,0,0.4);
    max-width: 240px;
  }
  :global(.county-tooltip .tooltip-popup strong) { color: #e6e4ef; }
</style>
