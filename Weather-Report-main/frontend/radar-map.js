/*
 * Real radar map for the "Hyper-Local Radar Map" screen.
 *
 * Replaces the previous hand-drawn SVG "map" (fictional coastline, three
 * hardcoded pins with fabricated +/-2° offsets, a decorative colored-ellipse
 * "radar plume") with:
 *   - An actual Leaflet map, centered on the real searched/GPS location
 *   - Live precipitation radar tiles from RainViewer (free, no API key)
 *   - A live infrared satellite layer, also from RainViewer
 *   - A single real marker showing the real current temperature
 *
 * RainViewer's public API (https://www.rainviewer.com/api.html) requires no
 * key and is explicitly designed for client-side embedding like this.
 *
 * Micro-Temp, Wind Vectors, and AQI Plume are REAL field layers sampled
 * from gridded model data (Open-Meteo forecast + air-quality) via the
 * backend /api/field endpoint: temperature and AQI render as a bilinear-
 * interpolated color canvas, wind as geo-anchored animated arrows. Points
 * the upstream model reports as unavailable render as gaps — values are
 * never fabricated. Each layer carries a legend with real units and the
 * actual min/max of the sampled data.
 */

(function () {
  const RAINVIEWER_API = "https://api.rainviewer.com/public/weather-maps.json";
  const TILE_SIZE = 256;
  const COLOR_SCHEME = 2; // RainViewer's "Universal Blue" scheme
  const SLIDER_STEP_MINUTES = 10;

  // CARTO's browser-embeddable basemap key is served by the backend via
  // /api/config so it can be rotated through the environment
  // (CARTO_API_KEY) without editing frontend code. The fallback below
  // only covers static hosting without the Flask backend.
  let CARTO_API_KEY = "";
  fetch("/api/config")
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (cfg && cfg.carto_api_key) {
        CARTO_API_KEY = cfg.carto_api_key;
        if (mapInitialized) refreshBasemap();
      }
    })
    .catch(() => {});

  let map = null;
  let tileLayer = null;
  let basemapLayer = null;
  let marker = null;
  let mapInitialized = false;

  let frames = { radar: [], satellite: [] };
  let framesLoadedAt = 0;
  const FRAMES_TTL_MS = 5 * 60 * 1000; // RainViewer publishes new frames every ~10 min

  let activeLayer = "precip"; // "precip" | "clouds" | "temp" | "wind" | "aqi"
  let activeFrameIndex = -1; // index into the active layer's frame array; -1 until loaded

  // ============================================================
  // REAL FIELD LAYERS — Micro-Temp / Wind Vectors / AQI Plume
  // ============================================================
  // Sampled from real gridded model data via the backend /api/field
  // endpoint (Open-Meteo forecast + air-quality grids). Null grid points
  // render as transparent gaps; nothing is interpolated into existence.

  const FIELD_METRICS = {
    temp: { label: "Micro-Temperature", opacity: 0.55 },
    wind: { label: "Wind", opacity: 1 },
    aqi: { label: "Air Quality", opacity: 0.55 },
  };

  // Fixed-value color ramps (color maps the real unit, not a per-grid
  // normalization, so hues are comparable across regions and refreshes).
  const TEMP_STOPS = [
    [-20, "#1e3a8a"], [-8, "#1d4ed8"], [0, "#0ea5e9"], [10, "#34d399"],
    [20, "#fbbf24"], [28, "#f97316"], [36, "#dc2626"], [45, "#7f1d1d"],
  ];
  const AQI_STOPS = [
    [0, "#22c55e"], [50, "#a3e635"], [100, "#facc15"], [150, "#f97316"],
    [200, "#ef4444"], [300, "#a855f7"], [500, "#7f1d1d"],
  ];

  function hexToRgb(hex) {
    return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
  }

  function colorForValue(stops, value) {
    if (value <= stops[0][0]) return hexToRgb(stops[0][1]);
    for (let i = 1; i < stops.length; i++) {
      if (value <= stops[i][0]) {
        const [v0, c0] = stops[i - 1];
        const [v1, c1] = stops[i];
        const t = (value - v0) / (v1 - v0);
        const a = hexToRgb(c0);
        const b = hexToRgb(c1);
        return a.map((ch, k) => Math.round(ch + (b[k] - ch) * t));
      }
    }
    return hexToRgb(stops[stops.length - 1][1]);
  }

  let fieldState = null; // { metric, image?, markers?, bounds }
  let fieldFetchToken = 0; // supersedes in-flight field fetches

  function emitFieldStatus(status, metric, detail) {
    window.dispatchEvent(new CustomEvent("radar-field-status", { detail: { status, metric, detail } }));
  }

  function hideFieldLegends() {
    ["temp", "wind", "aqi"].forEach((m) => {
      document.getElementById(`field-legend-${m}`)?.classList.add("hidden");
    });
  }

  function clearFieldLayer() {
    if (!map || !fieldState) return;
    if (fieldState.image) map.removeLayer(fieldState.image);
    if (fieldState.markers) map.removeLayer(fieldState.markers);
    fieldState = null;
    hideFieldLegends();
  }

  // Bilinear sample at fractional node coordinates; null nodes are
  // skipped (weights renormalized over the remaining real values). If no
  // contributing node has data the cell stays empty — never fabricated.
  function sampleBilinear(values, gx, gy) {
    const size = values.length;
    const x0 = Math.max(0, Math.min(Math.floor(gx), size - 1));
    const y0 = Math.max(0, Math.min(Math.floor(gy), size - 1));
    const x1 = Math.min(x0 + 1, size - 1);
    const y1 = Math.min(y0 + 1, size - 1);
    const fx = gx - x0;
    const fy = gy - y0;
    const nodes = [
      [values[y0][x0], (1 - fx) * (1 - fy)],
      [values[y0][x1], fx * (1 - fy)],
      [values[y1][x0], (1 - fx) * fy],
      [values[y1][x1], fx * fy],
    ];
    let sum = 0;
    let weight = 0;
    for (const [v, w] of nodes) {
      if (v === null || v === undefined) continue;
      sum += v * w;
      weight += w;
    }
    return weight > 0 ? sum / weight : null;
  }

  function renderScalarCanvas(grid, stops) {
    const spec = FIELD_METRICS[grid.metric];
    const size = grid.grid_size;
    const step = grid.grid_step_deg;
    const spanLat = (size - 1) * step;
    const spanLon = spanLat;
    const N = 81; // canvas resolution (~0.1° per pixel on a 1° lattice)
    const canvas = document.createElement("canvas");
    canvas.width = N;
    canvas.height = N;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(N, N);
    const alpha = Math.round(255 * spec.opacity);

    const EDGE_FADE = Math.max(1, Math.round(N * 0.08)); // soft plume boundary
    for (let py = 0; py < N; py++) {
      // Row 0 of the canvas is the northern edge (max lat); values[0] is
      // the min-lat row, so map geo -> node space explicitly.
      const lat = grid.min_lat + spanLat - ((py + 0.5) / N) * spanLat;
      const gy = (lat - grid.min_lat) / step;
      for (let px = 0; px < N; px++) {
        const lon = grid.min_lon + ((px + 0.5) / N) * spanLon;
        const gx = (lon - grid.min_lon) / step;
        const v = sampleBilinear(grid.values, gx, gy);
        const idx = (py * N + px) * 4;
        if (v === null) {
          img.data[idx + 3] = 0;
          continue;
        }
        const [r, g, b] = colorForValue(stops, v);
        img.data[idx] = r;
        img.data[idx + 1] = g;
        img.data[idx + 2] = b;
        // Linear alpha falloff over the outer rim so the lattice edge
        // blends into the map instead of ending in a hard rectangle.
        const rim = Math.min(px + 1, N - px, py + 1, N - py);
        img.data[idx + 3] = rim < EDGE_FADE ? Math.round(alpha * (rim / EDGE_FADE)) : alpha;
      }
    }
    ctx.putImageData(img, 0, 0);
    return canvas.toDataURL();
  }

  function renderWindMarkers(grid) {
    const group = L.layerGroup();
    const size = grid.grid_size;
    const step = grid.grid_step_deg;
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        const v = grid.values[r][c];
        if (!v) continue; // real gap in the model data
        const lat = grid.min_lat + r * step;
        const lon = grid.min_lon + c * step;
        const px = Math.round(14 + (Math.min(v.speed, 60) / 60) * 18); // 14–32px by speed
        // wind_direction_10m is the direction the wind blows FROM;
        // the arrow points where the wind goes (+180°).
        const angle = (v.direction + 180) % 360;
        group.addLayer(
          L.marker([lat, lon], {
            keyboard: false,
            icon: L.divIcon({
              className: "wind-arrow-icon",
              html:
                `<div class="wind-arrow" style="width:${px}px;height:${px}px;transform:rotate(${angle}deg)">` +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
                'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                '<path d="M12 21 L12 4" /><path d="M6 10 L12 4 L18 10" /></svg></div>',
              iconSize: [px, px],
              iconAnchor: [px / 2, px / 2],
            }),
          }).bindTooltip(`${v.speed} km/h`, {
            direction: "top",
            offset: [0, -8],
            className: "radar-marker-label",
          })
        );
      }
    }
    return group;
  }

  function showFieldLegend(metric, grid) {
    hideFieldLegends();
    const el = document.getElementById(`field-legend-${metric}`);
    if (!el) return;
    const flat = grid.values.flat().filter((v) => v !== null && v !== undefined);
    const nums = metric === "wind" ? flat.map((v) => v.speed) : flat;
    if (nums.length > 0) {
      const min = Math.round(Math.min(...nums));
      const max = Math.round(Math.max(...nums));
      const minEl = el.querySelector("[data-legend-min]");
      const maxEl = el.querySelector("[data-legend-max]");
      if (minEl) minEl.textContent = `${min}`;
      if (maxEl) maxEl.textContent = `${max}`;
    }
    el.classList.remove("hidden");
  }

  function renderFieldGrid(metric, grid) {
    const size = grid.grid_size;
    const step = grid.grid_step_deg;
    const maxLat = grid.min_lat + (size - 1) * step;
    const maxLon = grid.min_lon + (size - 1) * step;
    const bounds = L.latLngBounds([grid.min_lat, grid.min_lon], [maxLat, maxLon]);

    if (metric === "wind") {
      fieldState = { metric, markers: renderWindMarkers(grid).addTo(map), bounds };
    } else {
      const stops = metric === "temp" ? TEMP_STOPS : AQI_STOPS;
      const spec = FIELD_METRICS[metric];
      // pane:'tilePane' + explicit zIndex slots the field under the
      // RainViewer radar (zIndex 5) but above the basemap tiles.
      const image = L.imageOverlay(renderScalarCanvas(grid, stops), bounds, {
        opacity: 1,
        interactive: false,
        pane: "tilePane",
        zIndex: 4,
        className: "field-overlay",
      }).addTo(map);
      fieldState = { metric, image, bounds };
    }
    showFieldLegend(metric, grid);
  }

  async function loadFieldLayer(metric) {
    if (!map) return;
    clearFieldLayer();
    const token = ++fieldFetchToken;
    emitFieldStatus("loading", metric);

    let grid = null;
    try {
      const center = map.getCenter();
      const res = await fetch(
        `/api/field?metric=${metric}&latitude=${center.lat.toFixed(4)}&longitude=${center.lng.toFixed(4)}`
      );
      if (token !== fieldFetchToken) return; // superseded by a newer request
      if (res.status === 404) {
        emitFieldStatus("empty", metric);
        return;
      }
      if (!res.ok) {
        emitFieldStatus("error", metric, res.status);
        return;
      }
      grid = await res.json();
    } catch (error) {
      console.warn("Field layer fetch failed:", error);
      if (token === fieldFetchToken) emitFieldStatus("error", metric);
      return;
    }
    if (token !== fieldFetchToken) return;
    // Treat an all-null grid exactly like "no data": nothing fabricated,
    // nothing rendered, honest status message.
    const hasAnyValue = (grid.values || []).some((row) => row.some((v) => v !== null && v !== undefined));
    if (!hasAnyValue) {
      emitFieldStatus("empty", metric);
      return;
    }
    try {
      renderFieldGrid(metric, grid);
      emitFieldStatus("ready", metric);
    } catch (error) {
      console.warn("Field layer render failed:", error);
      emitFieldStatus("error", metric);
    }
  }

  // Panning beyond the sampled lattice silently re-centers the field so
  // the layer keeps following the viewport.
  function handleMapMoveEnd() {
    if (!fieldState || !map) return;
    if (fieldState.bounds.pad(0.25).contains(map.getCenter())) return;
    loadFieldLayer(fieldState.metric);
  }

  // Basemap follows the app theme: light paper tiles in light mode,
  // CARTO Dark Matter in dark mode (both free browser-embeddable rasters).
  function themeBasemapStyle() {
    const dark = document.documentElement.dataset.theme === "dark";
    return dark ? "dark_all" : "light_all";
  }

  function refreshBasemap() {
    if (!basemapLayer) return;
    basemapLayer.setUrl(
      `https://basemaps.cartocdn.com/rastertiles/${themeBasemapStyle()}/{z}/{x}/{y}.png?key=${CARTO_API_KEY}`
    );
  }

  function ensureMapInitialized() {
    if (mapInitialized) return;
    const container = document.getElementById("radar-map");
    if (!container || typeof L === "undefined") return;

    map = L.map(container, {
      zoomControl: false,
      attributionControl: true,
      center: [20, 0],
      zoom: 3,
    });

    // Basemap tiles follow the current theme (see themeBasemapStyle).
    basemapLayer = L.tileLayer(
      `https://basemaps.cartocdn.com/rastertiles/${themeBasemapStyle()}/{z}/{x}/{y}.png?key=${CARTO_API_KEY}`,
      {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19,
      }
    ).addTo(map);

    marker = L.circleMarker([0, 0], {
      radius: 8,
      color: "#B45309",
      weight: 2,
      fillColor: "#F59E0B",
      fillOpacity: 0.85,
    }).addTo(map);

    marker.bindTooltip("", {
      permanent: true,
      direction: "top",
      offset: [0, -6],
      className: "radar-marker-label",
    });

    marker.on("click", () => {
      window.dispatchEvent(new CustomEvent("radar-marker-clicked"));
    });

    map.on("moveend", handleMapMoveEnd);

    mapInitialized = true;
  }

  async function loadFramesIfNeeded() {
    const isStale = Date.now() - framesLoadedAt > FRAMES_TTL_MS;
    if (frames.radar.length > 0 && !isStale) return;

    try {
      const res = await fetch(RAINVIEWER_API);
      const data = await res.json();
      const host = data.host;

      const radarFrames = [...(data.radar?.past || []), ...(data.radar?.nowcast || [])].map((f) => ({
        time: f.time,
        url: `${host}${f.path}/${TILE_SIZE}/{z}/{x}/{y}/${COLOR_SCHEME}/1_1.png`,
      }));

      const satelliteFrames = (data.satellite?.infrared || []).map((f) => ({
        time: f.time,
        url: `${host}${f.path}/${TILE_SIZE}/{z}/{x}/{y}/0/0_0.png`,
      }));

      frames = { radar: radarFrames, satellite: satelliteFrames };
      framesLoadedAt = Date.now();

      // Default to the most recent real (non-forecast) frame, matching the "LIVE" label.
      activeFrameIndex = Math.max(0, (data.radar?.past || []).length - 1);
    } catch (error) {
      console.warn("RainViewer frame list unavailable:", error);
    }
  }

  function currentFrameSet() {
    return activeLayer === "clouds" ? frames.satellite : frames.radar;
  }

  function applyFrame(index) {
    const set = currentFrameSet();
    if (!map || set.length === 0) return;

    const clamped = Math.max(0, Math.min(index, set.length - 1));
    activeFrameIndex = clamped;
    const frame = set[clamped];

    if (tileLayer) {
      tileLayer.setUrl(frame.url);
    } else {
      // RainViewer tiles only exist up to zoom 7 — cap maxNativeZoom so
      // Leaflet upscales the zoom-7 tile instead of requesting
      // non-existent zoom-8+ tiles (which would just render blank).
      tileLayer = L.tileLayer(frame.url, { opacity: 0.75, zIndex: 5, maxNativeZoom: 7, maxZoom: 19 }).addTo(map);
    }
  }

  async function setLayer(layerName) {
    if (FIELD_METRICS[layerName]) {
      activeLayer = layerName;
      await loadFieldLayer(layerName);
      return;
    }

    if (layerName !== "precip" && layerName !== "clouds") {
      window.dispatchEvent(
        new CustomEvent("radar-layer-unavailable", { detail: { layer: layerName } })
      );
      return;
    }

    // Leaving a field layer: drop its overlays and restore the radar
    // timeline underneath the tiles.
    clearFieldLayer();

    activeLayer = layerName;
    await loadFramesIfNeeded();
    const set = currentFrameSet();
    const defaultIndex = layerName === "precip" ? Math.max(0, frames.radar.length - 1) : set.length - 1;
    applyFrame(defaultIndex);
  }

  // Maps the existing -60..60 minute slider onto whichever real frames are
  // available (older past frames for negative values, nowcast frames for
  // positive values where RainViewer provides them).
  function stepToOffsetMinutes(offsetMinutes) {
    // Field layers are current-conditions snapshots — the radar playback
    // slider has no meaning over them.
    if (activeLayer === "temp" || activeLayer === "wind" || activeLayer === "aqi") return;
    const set = currentFrameSet();
    if (set.length === 0) return;

    const liveIndex = activeLayer === "precip" ? Math.max(0, frames.radar.length - 1) : set.length - 1;
    const steps = Math.round(offsetMinutes / SLIDER_STEP_MINUTES);
    applyFrame(liveIndex + steps);
  }

  let stagedLocation = null; // location waiting for the map view to be shown

  async function applyLocation({ latitude, longitude, name, temp }) {
    if (activeLayer === "temp" || activeLayer === "wind" || activeLayer === "aqi") {
      // Keep the active field layer anchored on the new location.
      loadFieldLayer(activeLayer);
    }
    await loadFramesIfNeeded();
    if (!tileLayer) applyFrame(activeFrameIndex);

    map.setView([latitude, longitude], 8, { animate: true });
    marker.setLatLng([latitude, longitude]);
    marker.setTooltipContent(`${name || "Locating…"} · ${temp}`);
  }

  async function updateLocation(payload) {
    // Map is off-screen: do NOT initialize Leaflet or fetch map frames —
    // that would burn tile/frame requests nobody can see (and abort them
    // mid-flight when the location changes again). Stage it instead.
    if (payload.deferInit) {
      stagedLocation = payload;
      return;
    }
    ensureMapInitialized();
    if (!map) return;
    await applyLocation(payload);
  }

  function onViewShown() {
    ensureMapInitialized();
    const staged = stagedLocation;
    stagedLocation = null;
    // Leaflet can't size itself correctly while its container was
    // display:none, so nudge it once the view is actually visible.
    setTimeout(async () => {
      if (!map) return;
      map.invalidateSize();
      // Re-render the active field layer: its lattice was fetched for a
      // previous viewport and the map has just re-sized.
      if (activeLayer === "temp" || activeLayer === "wind" || activeLayer === "aqi") {
        loadFieldLayer(activeLayer);
      }
      if (staged) await applyLocation(staged);
    }, 80);
  }

  window.RadarMap = {
    onViewShown,
    updateLocation,
    setLayer,
    stepToOffsetMinutes,
  };

  // Re-theme the basemap when the app toggles Light/Dark. The MutationObserver
  // avoids any coupling to script.js (works even if the map is lazy-inited).
  let basemapTheme = null;
  const themeObserver = new MutationObserver(() => {
    const theme = document.documentElement.dataset.theme;
    if (!mapInitialized || !theme || theme === basemapTheme) return;
    basemapTheme = theme;
    refreshBasemap();
  });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
