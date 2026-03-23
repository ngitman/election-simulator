<script>
  import { onMount, tick } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';

  const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '');
  const MAP_CENTERS = { florida: [28.5, -82.5], new_york: [43.0, -75.5] };

  let mapContainer = $state(null);
  let map = $state(null);
  let geoLayer = $state(null);
  let result = $state(null);
  let loading = $state(true);
  let error = $state(null);

  let stateList = $state([
    { id: 'florida', label: 'Florida', ec_votes: 30 },
    { id: 'new_york', label: 'New York', ec_votes: 28 },
  ]);
  let currentState = $state('florida');
  let activeGeoState = $state('');
  let presidentialMode = $state(false);
  let presidentialResult = $state(null);

  let democratName = $state('Democrat');
  let republicanName = $state('Republican');
  let biasDR = $state(0);
  let turnout = $state(55);
  let incumbentUnpopularity = $state(0);

  function stateLabel(stateKey) {
    const item = stateList.find((s) => s.id === stateKey);
    return item?.label || stateKey;
  }

  function getSimulationBody() {
    return {
      state: currentState,
      democrat_name: democratName || 'Democrat',
      republican_name: republicanName || 'Republican',
      bias_d_r: Number(biasDR) || 0,
      turnout: Math.round(Number(turnout)) || 55,
      unpopularity_index: Number(incumbentUnpopularity) || 0,
    };
  }

  function setMapView(stateKey) {
    if (!map) return;
    map.setView(MAP_CENTERS[stateKey] || MAP_CENTERS.florida, 6);
  }

  async function runSimulation() {
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
        const data = await res.json().catch(() => ({}));
        const d = data.detail;
        let msg = (typeof d === 'object' ? d?.message : d) || res.statusText;
        if (typeof d === 'object' && d?.shapefiles_path) msg += ` Put a file in ${d.shapefiles_path}.`;
        throw new Error(msg);
      }
      const data = await res.json();
      result = data;
      setMapView(currentState);
      updateMap(data.geojson, currentState);
    } catch (e) {
      error = e.message || String(e);
    } finally {
      loading = false;
    }
  }

  async function loadThenRun(stateKey) {
    loading = true;
    error = null;
    try {
      const loadRes = await fetch(`${API}/api/load?state=${encodeURIComponent(stateKey)}`);
      const loadData = await loadRes.json();
      if (!loadData.success) {
        error = loadData.message + (loadData.shapefiles_path ? ` (Put a file in ${loadData.shapefiles_path})` : '');
        loading = false;
        return;
      }
      currentState = stateKey;
      await runSimulation();
    } catch (e) {
      error = e.message || String(e);
      loading = false;
    }
  }

  async function runPresidential() {
    loading = true;
    error = null;
    result = null;
    presidentialResult = null;
    try {
      const res = await fetch(`${API}/api/presidential/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          democrat_name: democratName || 'Democrat',
          republican_name: republicanName || 'Republican',
          bias_d_r: Number(biasDR) || 0,
          turnout: Math.round(Number(turnout)) || 55,
          unpopularity_index: Number(incumbentUnpopularity) || 0,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const d = data.detail;
        throw new Error((typeof d === 'object' ? d?.message : d) || res.statusText);
      }
      presidentialResult = await res.json();
      const simRes = await fetch(`${API}/api/simulation?state=${encodeURIComponent(currentState)}`);
      if (simRes.ok) {
        result = await simRes.json();
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
    return `<div class="tooltip-popup">
      <strong>${p.name || 'County'}</strong><br/>
      ${dName}: ${(p.democrat_pct ?? 0).toFixed(1)}% (${(p.democrat_votes ?? 0).toLocaleString()})<br/>
      ${rName}: ${(p.rep_pct ?? 0).toFixed(1)}% (${(p.rep_votes ?? 0).toLocaleString()})<br/>
      Other: ${(p.other_pct ?? 0).toFixed(1)}% (${(p.other_votes ?? 0).toLocaleString()})<br/>
      <strong>Winner: ${p.winner ?? '—'}</strong>
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
    if (!mapContainer) return;
    map = L.map(mapContainer).setView([28.5, -82.5], 6);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap, &copy; CARTO',
    }).addTo(map);
    try {
      const statesRes = await fetch(`${API}/api/states`);
      if (statesRes.ok) {
        const data = await statesRes.json();
        stateList = data.states || stateList;
      }
      await loadThenRun('florida');
    } catch (e) {
      error = e.message || 'Backend not reachable. Start it with: uvicorn backend.main:app --reload';
      loading = false;
    }
  });
</script>

<svelte:head>
  <title>Gitman's Political Simulator</title>
</svelte:head>

<div class="shell">
  <header class="topbar">
    <div class="brand">Gitman's Political Simulator</div>
    <div class="top-actions">
      <label class="state-select-wrap">
        <span class="state-select-label">State</span>
        <select
          class="state-select"
          bind:value={currentState}
          onchange={(e) => loadThenRun(e.currentTarget.value)}
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
      <div class="field-group">
        <label for="unpopularity">Incumbent unpopularity</label>
        <input id="unpopularity" type="number" min="0" max="50" step="1" bind:value={incumbentUnpopularity} />
        <div class="meta">0 = neutral</div>
      </div>

      {#if presidentialMode}
        <button class="run-btn" onclick={runPresidential} disabled={loading}>{loading ? 'Running…' : 'Run Presidential'}</button>
      {:else}
        <button class="run-btn" onclick={runSimulation} disabled={loading}>{loading ? 'Running…' : 'Run Simulation'}</button>
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
          <div class="ec-score">{dName} {ec.democrat} — {ec.republican} {rName}</div>
          <div class="ec-meta">Winner: <strong>{ec.winner}</strong> · Majority {ec.majority}</div>
          <div class="ec-list">
            {#each presidentialResult.state_results as sr}
              <div class="ec-item">
                <span>{sr.label}</span>
                <span>{sr.winner}</span>
                <span>{sr.ec_votes} EV</span>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <section class="map-card">
        <div class="map-header">{stateLabel(currentState)} county results</div>
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
        {@const demShare = ((t.democrat ?? 0) / twoParty) * 100}
        {@const repShare = ((t.republican ?? 0) / twoParty) * 100}
        {@const isTie = t.democrat === t.republican}
        {@const winner = isTie ? 'Tie' : (t.winner ?? ((t.democrat > t.republican) ? dName : rName))}
        {@const demWon = !isTie && winner === dName}

        <section class="stats-grid">
          <article class="stat-card blue">
            <div class="label">Projected winner</div>
            <div class="value">{winner}</div>
          </article>
          <article class="stat-card red">
            <div class="label">Margin</div>
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
            <div class="winner-pill" class:dem={demWon} class:rep={!demWon && !isTie} class:tie={isTie}>{winner}</div>
          </div>

          <div class="bar-graphic-wrap">
            <div class="bar-label left">{dName}: {t.democrat?.toLocaleString()} ({(t.democrat / cast * 100).toFixed(1)}%)</div>
            <div class="bar-track" role="img" aria-label="Vote share: {dName} {demShare.toFixed(1)}%, {rName} {repShare.toFixed(1)}%">
              <div class="bar-seg bar-dem" class:winner-bar={demWon} style="width: {demShare}%"></div>
              <div class="bar-divider"></div>
              <div class="bar-seg bar-rep" class:winner-bar={!demWon && !isTie} style="width: {repShare}%"></div>
            </div>
            <div class="bar-label right">{t.republican?.toLocaleString()} ({(t.republican / cast * 100).toFixed(1)}%) {rName}</div>
          </div>
          <div class="bar-caption">{dName} ← | → {rName} · Other: {t.other?.toLocaleString()} ({(t.other / cast * 100).toFixed(1)}%) · Total: {cast.toLocaleString()}</div>
        </section>
      {:else if loading}
        <section class="loading-card">Loading simulation...</section>
      {/if}
    </main>
  </div>
</div>

<style>
  .shell { min-height: 100vh; background: #0e0e11; color: #e6e4ef; font-family: Inter, system-ui, sans-serif; }
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
  .stat-card .label { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.09em; color: #abaab4; }
  .stat-card .value { margin-top: 0.35rem; font-size: 1.22rem; font-weight: 700; }

  .map-card { background: #131317; padding: 1rem; }
  .map-header { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.1em; color: #abaab4; margin-bottom: 0.75rem; }
  .map-stage { position: relative; }
  .map-wrap { height: 470px; border-radius: 0.25rem; overflow: hidden; background: #19191e; }
  .legend-card { position: absolute; right: 0.75rem; bottom: 0.75rem; background: rgba(25,25,30,0.86); backdrop-filter: blur(8px); padding: 0.6rem 0.7rem; border: 1px solid rgba(71,71,80,0.35); font-size: 0.68rem; display: grid; gap: 0.25rem; }
  .sw { width: 0.65rem; height: 0.65rem; display: inline-block; margin-right: 0.35rem; }
  .sw.blue { background: #004cb8; } .sw.blue-dim { background: #84b1fa; } .sw.red-dim { background: #ff8080; } .sw.red { background: #b80000; } .sw.orange { background: #ff722b; }

  .bar-card { background: #19191e; padding: 1rem; }
  .bar-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.7rem; gap: 0.5rem; }
  .bar-title { font-size: 0.86rem; font-weight: 600; letter-spacing: 0.01em; }
  .winner-pill { padding: 0.26rem 0.55rem; border-radius: 999px; font-size: 0.68rem; font-weight: 700; background: #474750; color: #e6e4ef; }
  .winner-pill.dem { background: #1f4ba0; } .winner-pill.rep { background: #8e2530; } .winner-pill.tie { background: #4f525c; }

  .bar-graphic-wrap { display: grid; grid-template-columns: minmax(130px, auto) 1fr minmax(130px, auto); gap: 0.8rem; align-items: center; }
  .bar-label { font-size: 0.8rem; color: #cfd0d8; }
  .bar-label.left { text-align: right; } .bar-label.right { text-align: left; }
  .bar-track { width: 100%; height: 38px; display: flex; border-radius: 0.25rem; overflow: hidden; background: #000; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.05); }
  .bar-seg { height: 100%; min-width: 4px; transition: width 150ms ease-out; }
  .bar-dem { background: linear-gradient(180deg, #adc6ff 0%, #004395 100%); }
  .bar-rep { background: linear-gradient(180deg, #ff716a 0%, #b11e1e 100%); }
  .bar-divider { width: 2px; background: rgba(0, 0, 0, 0.45); }
  .winner-bar { box-shadow: 0 0 0 2px rgba(0,0,0,0.35) inset; }
  .bar-caption { margin-top: 0.6rem; color: #abaab4; font-size: 0.74rem; }

  .ec-card { background: #19191e; padding: 1rem; }
  .ec-head { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.09em; color: #abaab4; }
  .ec-score { margin-top: 0.3rem; font-size: 1.1rem; font-weight: 700; }
  .ec-meta { margin-top: 0.2rem; color: #abaab4; font-size: 0.8rem; }
  .ec-list { margin-top: 0.65rem; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.45rem; }
  .ec-item { display: flex; justify-content: space-between; gap: 0.5rem; background: #131317; padding: 0.4rem 0.55rem; font-size: 0.74rem; }

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
