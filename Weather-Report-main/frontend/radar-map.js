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
 * Micro-Temp, Wind Vectors, and AQI Plume layers are NOT wired to live tile
 * data yet — those pills are marked data-soon="true" in index.html and
 * show a status message rather than silently doing nothing. Wiring them up
 * would mean separate providers per layer (e.g. OpenWeatherMap tile layers
 * for temperature/wind); ask if you want that built out next.
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

  let activeLayer = "precip"; // "precip" | "clouds" (satellite infrared)
  let activeFrameIndex = -1; // index into the active layer's frame array; -1 until loaded

  function refreshBasemap() {
    if (!basemapLayer) return;
    // Light basemap matches the Living Sky redesign's paper surfaces.
    basemapLayer.setUrl(
      `https://basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}.png?key=${CARTO_API_KEY}`
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

    // Light basemap (CARTO "Voyager"-style light tiles) to match the
    // Living Sky redesign's paper surfaces.
    basemapLayer = L.tileLayer(
      `https://basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}.png?key=${CARTO_API_KEY}`,
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
    if (layerName !== "precip" && layerName !== "clouds") {
      // temp / wind / aqi — not wired to live data yet.
      window.dispatchEvent(
        new CustomEvent("radar-layer-unavailable", { detail: { layer: layerName } })
      );
      return;
    }

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
    const set = currentFrameSet();
    if (set.length === 0) return;

    const liveIndex = activeLayer === "precip" ? Math.max(0, frames.radar.length - 1) : set.length - 1;
    const steps = Math.round(offsetMinutes / SLIDER_STEP_MINUTES);
    applyFrame(liveIndex + steps);
  }

  let stagedLocation = null; // location waiting for the map view to be shown

  async function applyLocation({ latitude, longitude, name, temp }) {
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
      if (staged) await applyLocation(staged);
    }, 80);
  }

  window.RadarMap = {
    onViewShown,
    updateLocation,
    setLayer,
    stepToOffsetMinutes,
  };
})();
