/* ============================================================
   WeatherGPT • Atmospheric Intelligence Client Controller
   Seamless mobile application logic for Android, iOS & Web
   ============================================================ */

// 1. Service Worker for Offline PWA Capabilities
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch((err) => {
      console.warn("ServiceWorker registration:", err);
    });
  });
}

const API_BASE = "";

// Startup location used until GPS resolves (or as the graceful fallback
// when geolocation is denied, unsupported, or times out).
const DEFAULT_LOCATION = {
  name: "San Francisco",
  admin1: "California",
  country: "United States",
  latitude: 37.7749,
  longitude: -122.4194,
};

// ============================================================
// APP STATE
// ============================================================

const state = {
  currentLocation: { ...DEFAULT_LOCATION },
  weather: null,
  alerts: [],
  airQuality: null,
  synopsis: "",
  voiceLang: "auto",
  activeView: "view-forecast",
  activeMapLayer: "precip",
  insightsScope: "today",
  isRecording: false,
  mediaRecorder: null,
  mediaStream: null,
  recordedChunks: [],
  recordTimer: null,
  countdownInterval: null,
  reportCache: null,
  reportCache: null,
  farmAdvice: null,
  farmCacheKey: null,
  farmStale: true,
  reportAiTick: 0, // incremented per request; only the newest AI report may commit
  locationRequestId: 0, // monotonic token — only the newest location request may commit
  gpsInFlight: false,
};

// ============================================================
// GPS / CURRENT LOCATION HELPERS
// ============================================================

// Human-readable, actionable messages per GeolocationPositionError code.
const GEO_ERROR_MESSAGES = {
  1: "Location access was denied. Allow location permission for this site (padlock icon in the address bar) and try again.",
  2: "Your location is currently unavailable (no GPS fix or network location). Check that location services are on, then try again.",
  3: "Getting your location timed out. Please try again.",
};

function geoErrorMessage(err) {
  if (err && GEO_ERROR_MESSAGES[err.code]) return GEO_ERROR_MESSAGES[err.code];
  return (err && err.message) || "Unknown geolocation error.";
}

// Shape used when no reverse-geocoded name is available, so every consumer
// still gets name/admin1/country keys (never "undefined" in the UI). The
// name stays EMPTY until a real locality is resolved — the display layer
// shows coordinates/"Locating…" rather than a fabricated label.
function fallbackLocation(latitude, longitude) {
  return {
    name: "",
    admin1: "",
    country: "",
    latitude: Number(latitude),
    longitude: Number(longitude),
  };
}

// Set a new location, update the display immediately, then refresh all
// weather views. Resolves once the weather pipeline for THIS location
// request settles (or is superseded by a newer one).
async function setCurrentLocation(loc) {
  if (!loc) return;
  const requestId = ++state.locationRequestId;
  state.currentLocation = {
    name: loc.name || "",
    admin1: loc.admin1 || "",
    country: loc.country || "",
    latitude: loc.latitude,
    longitude: loc.longitude,
  };
  // Header + hero labels update right away — no waiting on the weather fetch.
  updateLocationDisplay();
  await refreshWeatherData(requestId);
}

function updateLocationDisplay() {
  const { name, country } = state.currentLocation;
  // Empty name = coordinates known but locality not resolved yet (or
  // unresolvable) — show "Locating…" rather than a fabricated label.
  const label = name || "Locating…";
  if (els.headerLocationName) els.headerLocationName.textContent = label;
  if (els.heroPlaceLabel) els.heroPlaceLabel.textContent = name ? `${name}${country ? ", " + country : ""}` : "Locating…";
  if (els.mapAddressSearchInput && name && document.activeElement !== els.mapAddressSearchInput) {
    // Don't clobber the address bar while the user is typing a search.
    els.mapAddressSearchInput.value = `${name}${country ? ", " + country : ""}`;
  }
}

// Best available human-readable location name for secondary UI (map
// tooltip, sensor card, assistant context). Never a fabricated city —
// falls back to coordinates, then a neutral phrase.
function locationDisplayName() {
  const loc = state.currentLocation || {};
  if (loc.name) return loc.name;
  if (Number.isFinite(loc.latitude) && Number.isFinite(loc.longitude)) {
    return `${loc.latitude.toFixed(2)}, ${loc.longitude.toFixed(2)}`;
  }
  return "Local Area";
}

// ============================================================
// I18N STRINGS FOR THE FARM ADVISOR UI
// (advice text itself is translated server-side)
// ============================================================

const FARM_I18N = {
  en: {
    title: "What should I do today?",
    subtitle: "Smart Farm Weather Advisor",
    cropLabel: "Crop",
    stageLabel: "Crop Stage",
    advisory: "Farm Advisory",
    loading: "Preparing today's advisory...",
    error: "Advisory unavailable right now. Please try again shortly.",
    sevLabels: { action: "Act Now", caution: "Caution", info: "Info" },
    alertCount: (n) => `${n} Farm Alert${n === 1 ? "" : "s"}`,
    cropNoteLabel: "Crop note",
    disclaimer: "Advice is generated from local forecast data and standard agronomy thresholds. Verify locally before major field decisions.",
  },
  hi: {
    title: "आज मुझे क्या करना चाहिए?",
    subtitle: "स्मार्ट फार्म मौसम सलाहकार",
    cropLabel: "फसल",
    stageLabel: "फसल अवस्था",
    advisory: "कृषि सलाह",
    loading: "आज की सलाह तैयार हो रही है...",
    error: "सलाह अभी उपलब्ध नहीं है। कृपया थोड़ी देर बाद पुनः प्रयास करें।",
    sevLabels: { action: "तुरंत करें", caution: "सावधानी", info: "जानकारी" },
    alertCount: (n) => `${n} कृषि चेतावनी`,
    cropNoteLabel: "फसल टिप्पणी",
    disclaimer: "सलाह स्थानीय पूर्वानुमान और मानक कृषि-विज्ञान सीमाओं पर आधारित है। बड़े निर्णयों से पहले स्थानीय सत्यापन करें।",
  },
  te: {
    title: "ఈరోజు నేను ఏమి చేయాలి?",
    subtitle: "స్మార్ట్ ఫారం వాతావరణ సలహాదారు",
    cropLabel: "పంట",
    stageLabel: "పంట దశ",
    advisory: "వ్యవసాయ సలహా",
    loading: "ఈరోజు సలహా సిద్ధమవుతోంది...",
    error: "సలహా ఇప్పుడు అందుబాటులో లేదు. దయచేసి కొంచెం తర్వాత ప్రయత్నించండి.",
    sevLabels: { action: "వెంటనే చేయండి", caution: "జాగ్రత్త", info: "సమాచారం" },
    alertCount: (n) => `${n} వ్యవసాయ హెచ్చరికలు`,
    cropNoteLabel: "పంట గమనిక",
    disclaimer: "సలహా స్థానిక అంచనా డేటా మరియు ప్రామాణిక వ్యవసాయ ప్రమాణాల ఆధారంగా. పెద్ద నిర్ణయాలకు ముందు స్థానికంగా సరిచూసుకోండి.",
  },
};

function farmLang() {
  return state.voiceLang === "hi" ? "hi" : state.voiceLang === "te" ? "te" : "en";
}

// Condition category mapping for WMO codes
function getConditionCategory(code, isDay = 1) {
  const day = isDay !== 0;
  if (code === 0) return day ? "clear-day" : "clear-night";
  if (code === 1 || code === 2) return day ? "partly-cloudy-day" : "partly-cloudy-night";
  if (code === 3) return "cloudy";
  if (code === 45 || code === 48) return "fog";
  if ([51, 53, 55, 56, 57].includes(code)) return "drizzle";
  if ([61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return "rain";
  if ([71, 73, 75, 77, 85, 86].includes(code)) return "snow";
  if ([95, 96, 99].includes(code)) return "thunder";
  return day ? "clear-day" : "clear-night";
}

const CONDITION_TITLES = {
  "clear-day": "Clear Sky",
  "clear-night": "Clear Sky",
  "partly-cloudy-day": "Partly Cloudy",
  "partly-cloudy-night": "Partly Cloudy",
  "cloudy": "Overcast",
  "fog": "Dense Fog",
  "drizzle": "Light Drizzle",
  "rain": "Rain Showers",
  "snow": "Snowfall",
  "thunder": "Thunderstorm",
};

const CONDITION_ICONS = {
  "clear-day": "wb_sunny",
  "clear-night": "nights_stay",
  "partly-cloudy-day": "partly_cloudy_day",
  "partly-cloudy-night": "partly_cloudy_night",
  "cloudy": "cloud",
  "fog": "mist",
  "drizzle": "water_drop",
  "rain": "rainy",
  "snow": "ac_unit",
  "thunder": "thunderstorm",
};

// ============================================================
// LIVING SKY — dynamic background driven by REAL weather data
// Maps the existing WMO condition category (+ actual temperature
// band) onto full-viewport sky gradients. Two layers crossfade,
// so condition changes dissolve smoothly instead of snapping.
// ============================================================

const SKY_CLASS_BY_CONDITION = {
  "clear-day": "sky-clear-day",
  "clear-night": "sky-clear-night",
  "partly-cloudy-day": "sky-partly-cloudy-day",
  "partly-cloudy-night": "sky-partly-cloudy-night",
  "cloudy": "sky-cloudy",
  "fog": "sky-fog",
  "drizzle": "sky-drizzle",
  "rain": "sky-rain",
  "snow": "sky-snow",
  "thunder": "sky-thunder",
};

// Temperature bands only override FAIR skies (clear/partly cloudy):
// rain, snow, fog, and storms already carry their own mood.
const SKY_CLASS_BY_TEMP_BAND = {
  hot: "sky-hot",
  cold: "sky-cold",
};

const NIGHT_CONDITIONS = new Set(["clear-night", "partly-cloudy-night", "thunder"]);

function skyClassFor(condition, tempBand) {
  if ((tempBand === "hot" || tempBand === "cold") && (condition === "clear-day" || condition === "partly-cloudy-day")) {
    return SKY_CLASS_BY_TEMP_BAND[tempBand];
  }
  return SKY_CLASS_BY_CONDITION[condition] || SKY_CLASS_BY_CONDITION["clear-day"];
}

function tempBandFor(celsius) {
  if (!Number.isFinite(celsius)) return "mild";
  if (celsius >= 33) return "hot";
  if (celsius <= 0) return "cold";
  return "mild";
}

// Swaps the live sky: the incoming layer fades in over the outgoing
// one, then the old layer is parked under it ready for next time.
// Tracks which layer is currently in front (0 = layer-a, 1 = layer-b),
// so consecutive swaps alternate deterministically instead of guessing
// from the DOM mid-transition.
let skyFront = 0;
let skyRetireTimer = null;

// Skies that already end pale — the bottom mist veil would be invisible.
const PALE_SKIES = new Set(["sky-snow", "sky-fog", "sky-cloudy", "sky-drizzle"]);

function applyDynamicSky(condition, tempBand = "mild") {
  const nextClass = skyClassFor(condition, tempBand);
  const layers = [document.getElementById("sky-layer-a"), document.getElementById("sky-layer-b")];
  const stars = document.getElementById("sky-stars");
  const veil = document.getElementById("sky-veil");
  if (!layers[0] || !layers[1]) return;

  if (stars) stars.classList.toggle("is-visible", NIGHT_CONDITIONS.has(condition));
  if (veil) veil.classList.toggle("is-visible", !PALE_SKIES.has(nextClass));

  const front = layers[skyFront];
  // Same sky already showing — nothing to animate.
  if (front.classList.contains(nextClass) && front.classList.contains("is-live")) return;

  const outgoing = layers[skyFront];
  const incoming = layers[(skyFront + 1) % 2];
  skyFront = (skyFront + 1) % 2;

  // The incoming layer sits ON TOP with opacity 0, gets the new gradient,
  // then fades in over the old sky. Explicit z-index keeps the stacking
  // correct regardless of DOM order.
  incoming.style.zIndex = "2";
  outgoing.style.zIndex = "1";
  incoming.classList.remove("is-live");
  incoming.classList.remove(...Object.values(SKY_CLASS_BY_CONDITION), ...Object.values(SKY_CLASS_BY_TEMP_BAND));
  incoming.classList.add(nextClass);

  // Double rAF: guarantee the gradient change paints before the fade starts.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      incoming.classList.add("is-live");
      // Once fully faded in, retire the old layer underneath it.
      clearTimeout(skyRetireTimer);
      skyRetireTimer = setTimeout(() => outgoing.classList.remove("is-live"), 2600);
    });
  });
}

// ============================================================
// RAIN FX — precipitation overlay driven by REAL weather data
// Drizzle/rain/thunder conditions (from the actual WMO code) get a
// pooled raindrop overlay; the drop count and fall speed also scale
// with the real precipitation amount. Thunderstorms additionally get
// occasional lightning flashes with synthesized thunder (WebAudio,
// lazily created — browsers that block autoplay keep the context
// suspended, which is caught and ignored). Reduced-motion users get
// a still, faint streak field instead of any animation.
// Perf: drops animate transform-only on the compositor; the pool is
// rebuilt only when the tier/count changes — zero JS per frame.
// ============================================================

const RAIN_TIER_BY_CONDITION = {
  drizzle: { count: 42, durMin: 1.35, durMax: 1.95, lenMin: 10, lenMax: 15, wMin: 1.0, wMax: 1.5, oMin: 0.20, oMax: 0.40, color: "rgba(226, 239, 250, 0.55)" },
  rain:    { count: 90, durMin: 0.85, durMax: 1.35, lenMin: 14, lenMax: 22, wMin: 1.2, wMax: 2.0, oMin: 0.32, oMax: 0.60, color: "rgba(224, 238, 250, 0.66)" },
  thunder: { count: 140, durMin: 0.60, durMax: 1.00, lenMin: 18, lenMax: 28, wMin: 1.4, wMax: 2.4, oMin: 0.42, oMax: 0.72, color: "rgba(228, 240, 252, 0.78)" },
};

const rainFx = {
  builtTier: null,     // condition tier the current pool was built for
  builtCount: 0,       // drop count the current pool was built for
  builtStatic: false,  // whether the pool was built for reduced motion
  lightningTimer: null,
  flashTimer: null,
  audioCtx: null,
  thunderBuffer: null,
};

function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function buildRainPool(container, tier, count, reduced) {
  let layer = container.querySelector(".rain-fx-layer");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "rain-fx-layer";
    container.appendChild(layer);
  }
  layer.style.setProperty("--rain-color", tier.color);

  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const drop = document.createElement("span");
    drop.className = "rain-drop";
    // Natural variation in width, length, speed, opacity, position;
    // negative delays start drops mid-fall so the field is full at t=0.
    const w = tier.wMin + Math.random() * (tier.wMax - tier.wMin);
    const h = tier.lenMin + Math.random() * (tier.lenMax - tier.lenMin);
    const dur = tier.durMin + Math.random() * (tier.durMax - tier.durMin);
    const o = tier.oMin + Math.random() * (tier.oMax - tier.oMin);
    drop.style.left = (Math.random() * 100).toFixed(2) + "%";
    drop.style.setProperty("--w", w.toFixed(2) + "px");
    drop.style.setProperty("--h", h.toFixed(1) + "px");
    drop.style.setProperty("--dur", dur.toFixed(2) + "s");
    drop.style.setProperty("--delay", (-Math.random() * 2.5).toFixed(2) + "s");
    drop.style.setProperty("--o", o.toFixed(2));
    if (reduced) {
      // No animation: freeze each streak at a random height, very faint.
      drop.style.top = (Math.random() * 100).toFixed(2) + "%";
      drop.style.setProperty("--o-static", (0.08 + Math.random() * 0.08).toFixed(2));
    }
    frag.appendChild(drop);
  }
  layer.textContent = "";
  layer.appendChild(frag);
}

function applyPrecipFx(condition, precipMm, windDirFrom) {
  const container = document.getElementById("rain-fx");
  if (!container) return;

  const reduced = prefersReducedMotion();
  const tier = RAIN_TIER_BY_CONDITION[condition] || null;

  if (!tier) {
    container.classList.remove("is-visible");
    rainFx.builtTier = null;
    // Empty the pool so hidden drops stop compositing entirely.
    const staleLayer = container.querySelector(".rain-fx-layer");
    if (staleLayer) staleLayer.textContent = "";
    stopLightningCycle();
    return;
  }

  // Real precipitation amount modulates density (heavier = more drops).
  const amount = Number.isFinite(precipMm) ? precipMm : null;
  const density = amount == null ? 1 : amount <= 0.4 ? 0.7 : amount >= 5 ? 1.3 : 1;
  const wantCount = Math.max(10, Math.round(tier.count * density));

  if (rainFx.builtTier !== condition || rainFx.builtCount !== wantCount || rainFx.builtStatic !== reduced) {
    buildRainPool(container, tier, wantCount, reduced);
    rainFx.builtTier = condition;
    rainFx.builtCount = wantCount;
    rainFx.builtStatic = reduced;
  }
  container.classList.add("is-visible");

  // Wind slant from the REAL wind_direction_10m (meteorological: the
  // direction the wind blows FROM — drops slant the way it blows TO).
  const layer = container.querySelector(".rain-fx-layer");
  if (layer && Number.isFinite(windDirFrom)) {
    let to = ((windDirFrom + 180) % 360 + 360) % 360;
    if (to > 180) to -= 360;
    const angle = Math.max(-18, Math.min(18, to));
    layer.style.setProperty("--rain-angle", angle.toFixed(1) + "deg");
  }

  if (condition === "thunder" && !reduced) startLightningCycle();
  else stopLightningCycle();
}

function startLightningCycle() {
  if (rainFx.lightningTimer) return; // already running
  scheduleNextStrike(2500 + Math.random() * 5000);
}

function scheduleNextStrike(delayMs) {
  rainFx.lightningTimer = setTimeout(() => {
    rainFx.lightningTimer = null;
    if (document.hidden) {
      // Tab in background: skip the invisible strike silently.
    } else {
      fireLightningStrike();
    }
    scheduleNextStrike(6000 + Math.random() * 9000);
  }, delayMs);
}

function stopLightningCycle() {
  clearTimeout(rainFx.lightningTimer);
  rainFx.lightningTimer = null;
  clearTimeout(rainFx.flashTimer);
  rainFx.flashTimer = null;
  const fx = document.getElementById("lightning-fx");
  if (fx) fx.classList.remove("is-flash");
}

function fireLightningStrike() {
  const fx = document.getElementById("lightning-fx");
  if (!fx) return;
  fx.style.setProperty("--flash-x", (15 + Math.random() * 70).toFixed(0) + "%");
  fx.style.setProperty("--flash-y", (8 + Math.random() * 30).toFixed(0) + "%");
  fx.style.setProperty("--flash-i", (0.45 + Math.random() * 0.5).toFixed(2));
  const dur = Math.round(420 + Math.random() * 380);
  fx.style.setProperty("--flash-dur", dur + "ms");
  // Restart the CSS animation even if one is already mid-flight.
  fx.classList.remove("is-flash");
  void fx.offsetWidth;
  fx.classList.add("is-flash");
  clearTimeout(rainFx.flashTimer);
  rainFx.flashTimer = setTimeout(() => fx.classList.remove("is-flash"), dur + 80);
  // Thunder arrives after the flash — the farther strike, the longer.
  playThunder(400 + Math.random() * 1600);
}

// Synthesized thunder: filtered noise burst with exponential decay,
// generated ONCE into a cached AudioBuffer. The AudioContext is created
// lazily and only resumed on strike; browsers that block autoplay keep
// it suspended and the resume rejection is swallowed — the flash still
// runs, nothing throws, and audio starts after the first user gesture.
function playThunder(delayMs) {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!rainFx.audioCtx) rainFx.audioCtx = new AudioCtx();
    const ctx = rainFx.audioCtx;
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const src = ctx.createBufferSource();
    src.buffer = getThunderBuffer(ctx);
    const gain = ctx.createGain();
    gain.gain.value = 0.22; // subtle, not startling
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 320;
    src.connect(filter); filter.connect(gain); gain.connect(ctx.destination);
    src.start(ctx.currentTime + delayMs / 1000);
  } catch (err) { /* audio unavailable — the visual flash still runs */ }
}

function getThunderBuffer(ctx) {
  if (rainFx.thunderBuffer) return rainFx.thunderBuffer;
  const sampleRate = ctx.sampleRate;
  const frames = Math.floor(sampleRate * (2.6 + Math.random() * 1.4));
  const buf = ctx.createBuffer(1, frames, sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < frames; i++) {
    const t = i / sampleRate;
    const crack = (Math.random() * 2 - 1) * Math.exp(-t * 3.2) * 0.5;
    const rumble = (Math.random() * 2 - 1) * Math.exp(-t * 0.9) * 0.5;
    const low = Math.sin(2 * Math.PI * (55 + Math.sin(t * 1.7) * 12) * t) * Math.exp(-t * 1.1) * 0.35;
    const envelope = Math.exp(-t * 0.85) * (1 - Math.exp(-t * 12));
    data[i] = (crack + rumble + low) * envelope;
  }
  rainFx.thunderBuffer = buf;
  return buf;
}

// If the OS-level reduced-motion preference flips mid-session, rebuild
// the pool (animated field ↔ static field) for the current condition.
if (window.matchMedia) {
  const rmQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const onRmChange = () => applyPrecipFx(document.body.dataset.condition || "clear-day");
  if (rmQuery.addEventListener) rmQuery.addEventListener("change", onRmChange);
  else if (rmQuery.addListener) rmQuery.addListener(onRmChange);
}

// ============================================================
// DOM ELEMENTS
// ============================================================

const els = {
  headerLocationName: document.getElementById("header-location-name"),
  locationTriggerBtn: document.getElementById("location-trigger-btn"),
  searchModal: document.getElementById("search-modal"),
  closeSearchBtn: document.getElementById("close-search-btn"),
  citySearchForm: document.getElementById("city-search-form"),
  citySearchInput: document.getElementById("city-search-input"),
  useGpsBtn: document.getElementById("use-gps-btn"),
  appStatusBar: document.getElementById("app-status-bar"),
  statusMessage: document.getElementById("status-message"),
  statusModelBadge: document.getElementById("status-model-badge"),

  // Voice Overlay
  voiceOverlay: document.getElementById("voice-overlay"),
  voiceStopBtn: document.getElementById("voice-stop-btn"),
  voiceCancelBtn: document.getElementById("voice-cancel-btn"),
  voiceCountdownLabel: document.getElementById("voice-countdown-label"),
  voiceLanguageHint: document.getElementById("voice-language-hint"),

  // Forecast Screen
  heroPlaceLabel: document.getElementById("hero-place-label"),
  heroTemperature: document.getElementById("hero-temperature"),
  heroConditionIcon: document.getElementById("hero-condition-icon"),
  heroConditionText: document.getElementById("hero-condition-text"),
  heroTempHigh: document.getElementById("hero-temp-high"),
  heroTempLow: document.getElementById("hero-temp-low"),
  heroTempFeels: document.getElementById("hero-temp-feels"),
  heroSynopsisText: document.getElementById("hero-synopsis-text"),
  heroSynopsisMeta: document.getElementById("hero-synopsis-meta"),
  askAiQuickBtn: document.getElementById("ask-ai-quick-btn"),
  microclimateChipsScroll: document.getElementById("microclimate-chips-scroll"),
  hourlyForecastScroll: document.getElementById("hourly-forecast-scroll"),
  dailyForecastContainer: document.getElementById("daily-forecast-container"),
  telemetryWindSpeed: document.getElementById("telemetry-wind-speed"),
  telemetryWindDir: document.getElementById("telemetry-wind-dir"),
  telemetryWindGusts: document.getElementById("telemetry-wind-gusts"),
  telemetryAqiNum: document.getElementById("telemetry-aqi-num"),
  telemetryAqiLabel: document.getElementById("telemetry-aqi-label"),
  telemetryAqiBar: document.getElementById("telemetry-aqi-bar"),
  telemetryAqiSub: document.getElementById("telemetry-aqi-sub"),
  telemetryHumidity: document.getElementById("telemetry-humidity"),
  telemetryDewpoint: document.getElementById("telemetry-dewpoint"),
  telemetryPressure: document.getElementById("telemetry-pressure"),
  telemetryPressureTrend: document.getElementById("telemetry-pressure-trend"),
  forecastQuickQueryForm: null,
  forecastQuickInput: null,

  // Unified Query Input (All Tabs)
  unifiedQueryForm: document.getElementById("unified-query-form"),
  unifiedQueryInput: document.getElementById("unified-query-input"),
  unifiedMicBtn: document.querySelector(".unified-mic-btn"),

  // Map Screen
  mapAddressSearchInput: document.getElementById("map-address-search-input"),
  mapGpsBtn: document.getElementById("map-gps-btn"),
  mapSensorCard: document.getElementById("map-sensor-card"),
  closeSensorCardBtn: document.getElementById("close-sensor-card-btn"),
  sensorCardTitle: document.getElementById("sensor-card-title"),
  sensorMetricTemp: document.getElementById("sensor-metric-temp"),
  sensorMetricPrecip: document.getElementById("sensor-metric-precip"),
  sensorMetricWind: document.getElementById("sensor-metric-wind"),
  sensorMetricAqi: document.getElementById("sensor-metric-aqi"),
  radarPlayBtn: document.getElementById("radar-play-btn"),
  radarTimeSlider: document.getElementById("radar-time-slider"),

  // WeatherGPT Assistant Screen
  chatStream: document.getElementById("chat-stream"),
  assistantChatForm: document.getElementById("assistant-chat-form"),
  assistantInputText: document.getElementById("assistant-input-text"),
  assistantMicBtn: document.getElementById("assistant-mic-btn"),
  assistantPromptChips: document.getElementById("assistant-prompt-chips"),

  // Smart Farm Advisor
  farmSection: document.getElementById("farm-advisor-section"),
  farmTitle: document.getElementById("farm-advisor-title"),
  farmCropSelect: document.getElementById("farm-crop-select"),
  farmStageSelect: document.getElementById("farm-stage-select"),
  farmAdviceCards: document.getElementById("farm-advice-cards"),
  farmCropNote: document.getElementById("farm-crop-note"),
  farmDisclaimer: document.getElementById("farm-disclaimer"),
  farmAlertCount: document.getElementById("farm-alert-count"),
  farmCropEmoji: document.getElementById("farm-crop-emoji"),

  // Insights Screen
  insightsTimeScope: document.getElementById("insights-time-scope"),
  insightsScopeSubtitle: document.getElementById("insights-scope-subtitle"),
  insightsScopeMetrics: document.getElementById("insights-scope-metrics"),
  insightsAlertsContainer: document.getElementById("insights-alerts-container"),
  insightsBaselineLabel: document.getElementById("insights-baseline-label"),
  insightsDeltaBars: document.getElementById("insights-delta-bars"),
  insightsPressureVal: document.getElementById("insights-pressure-val"),
  insightsPressureDesc: document.getElementById("insights-pressure-desc"),
  insightsUvVal: document.getElementById("insights-uv-val"),
  insightsUvDesc: document.getElementById("insights-uv-desc"),
};

// ============================================================
// NAVIGATION & VIEW SWITCHER
// ============================================================

function switchView(targetViewId) {
  state.activeView = targetViewId;

  document.querySelectorAll(".app-view").forEach((view) => {
    view.classList.add("hidden");
  });

  const activeViewEl = document.getElementById(targetViewId);
  if (activeViewEl) {
    activeViewEl.classList.remove("hidden");
  }

  if (targetViewId === "view-map" && window.RadarMap) {
    window.RadarMap.onViewShown();
  }

  if (targetViewId === "view-insights") {
    renderInsightsScreen();
  }

  // Farm advisor loads lazily: fetch when its view is opened and the
  // advisory is missing or stale (location/weather changed since last view).
  if (targetViewId === "view-farmer" && state.farmStale) {
    state.farmStale = false;
    refreshFarmAdvice();
  }

  // Update bottom navigation tabs
  document.querySelectorAll(".nav-tab").forEach((tab) => {
    const isTarget = tab.dataset.view === targetViewId;
    tab.classList.toggle("is-active", isTarget);
    if (isTarget) {
      tab.classList.add("text-primary", "bg-amber-glow-surface");
      tab.classList.remove("text-ink-tertiary");
    } else {
      tab.classList.remove("text-primary", "bg-amber-glow-surface");
      tab.classList.add("text-ink-tertiary");
    }
  });

  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    switchView(tab.dataset.view);
  });
});

document.getElementById("header-logo-btn")?.addEventListener("click", () => {
  switchView("view-forecast");
});

document.getElementById("view-map-matrix-btn")?.addEventListener("click", () => {
  switchView("view-map");
});

els.askAiQuickBtn?.addEventListener("click", () => {
  switchView("view-weathergpt");
  els.assistantInputText?.focus();
});

// ============================================================
// LANGUAGE SELECTION
// ============================================================

document.querySelectorAll(".lang-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".lang-btn").forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    state.voiceLang = btn.dataset.lang;
    document.documentElement.lang = state.voiceLang === "auto" ? "en" : state.voiceLang;

    updatePromptChipsForLanguage(state.voiceLang);

    // Re-render farm advisory in the selected language (server-side
    // translation is keyed by language) — refetch when its view is open,
    // otherwise mark stale so it picks the new language on next open.
    state.farmCacheKey = null;
    if (state.weather && state.activeView === "view-farmer") {
      refreshFarmAdvice();
    } else if (state.weather) {
      state.farmStale = true;
    }

    // Refresh weather synopsis in new language if data already loaded
    if (state.weather) {
      generateAiReport();
    }
  });
});

function updatePromptChipsForLanguage(lang) {
  if (!els.assistantPromptChips) return;

  const chips = {
    hi: [
      { icon: "translate", text: "क्या आज शाम बारिश होगी?" },
      { icon: "directions_bike", text: "क्या दोपहर में बाहर साइकिल चलाना ठीक रहेगा?" },
      { icon: "thermostat", text: "कल का तापमान कैसा रहेगा?" },
      { icon: "checkroom", text: "आज मुझे क्या कपड़े पहनने चाहिए?" },
    ],
    te: [
      { icon: "translate", text: "ఈ రోజు వర్షం పడుతుందా?" },
      { icon: "directions_run", text: "సాయంత్రం బయటకు వెళ్లడం సురక్షితమేనా?" },
      { icon: "thermostat", text: "రేపటి వాతావరణం ఎలా ఉంటుంది?" },
      { icon: "checkroom", text: "ఈ రోజు ఎలాంటి దుస్తులు ధరించాలి?" },
    ],
    en: [
      { icon: "directions_bike", text: "Can I bike outside this afternoon?" },
      { icon: "umbrella", text: "Will it rain during evening commute?" },
      { icon: "directions_run", text: "Best outdoor run time tomorrow?" },
      { icon: "checkroom", text: "What should I wear today?" },
    ],
  };

  const selected = chips[lang] || chips.en;
  els.assistantPromptChips.innerHTML = selected
    .map(
      (c) => `
    <button class="assistant-chip shrink-0 px-3 py-1.5 rounded-full bg-surface-container border border-glass-border-subtle hover:bg-surface-bright text-ink-secondary hover:text-ink-primary text-xs transition-all flex items-center gap-1.5 active:scale-95">
      <span class="material-symbols-outlined text-primary text-[15px]">${c.icon}</span>
      <span>${escapeHTML(c.text)}</span>
    </button>`
    )
    .join("");

  bindAssistantChips();
}

function bindAssistantChips() {
  document.querySelectorAll(".assistant-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const text = chip.querySelector("span:last-child")?.textContent?.trim();
      if (text) {
        if (els.assistantInputText) els.assistantInputText.value = text;
        if (els.unifiedQueryInput) els.unifiedQueryInput.value = text;
        switchView("view-weathergpt");
        submitAssistantQuery(text);
      }
    });
  });
}

// ============================================================
// STATUS HELPERS
// ============================================================

function showStatus(msg, badge = "Live update") {
  if (els.appStatusBar && els.statusMessage) {
    els.statusMessage.textContent = msg;
    if (els.statusModelBadge) els.statusModelBadge.textContent = badge;
    els.appStatusBar.classList.remove("hidden");
  }
}

function hideStatus() {
  if (els.appStatusBar) {
    els.appStatusBar.classList.add("hidden");
  }
}

// ============================================================
// SEARCH & LOCATION MODAL
// ============================================================

els.locationTriggerBtn?.addEventListener("click", () => {
  els.searchModal?.classList.remove("hidden");
  els.citySearchInput?.focus();
});

els.closeSearchBtn?.addEventListener("click", () => {
  els.searchModal?.classList.add("hidden");
});

// Escape closes the search modal / voice overlay (keyboard a11y)
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (els.searchModal && !els.searchModal.classList.contains("hidden")) {
    els.searchModal.classList.add("hidden");
  } else if (state.isRecording) {
    cancelVoiceRecording();
  }
});

els.citySearchForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = els.citySearchInput.value.trim();
  if (!query) return;

  try {
    showStatus(`Geocoding '${query}' with Open-Meteo...`);
    const loc = await getJSON(`/api/geocode?location=${encodeURIComponent(query)}`);
    els.searchModal?.classList.add("hidden");
    els.citySearchInput.value = "";
    await setCurrentLocation(loc);
  } catch (err) {
    alert(`Could not find '${query}': ${err.message}`);
  } finally {
    hideStatus();
  }
});

document.querySelectorAll(".popular-city-chip").forEach((chip) => {
  chip.addEventListener("click", async () => {
    const city = chip.dataset.city;
    if (!city) return;
    try {
      showStatus(`Loading ${city}...`);
      const loc = await getJSON(`/api/geocode?location=${encodeURIComponent(city)}`);
      els.searchModal?.classList.add("hidden");
      await setCurrentLocation(loc);
    } catch (err) {
      alert(err.message);
    } finally {
      hideStatus();
    }
  });
});

els.useGpsBtn?.addEventListener("click", () => {
  requestDeviceGps();
  els.searchModal?.classList.add("hidden");
});

els.mapGpsBtn?.addEventListener("click", () => {
  requestDeviceGps();
});

// The map screen's own address bar previously just displayed the current
// location with no way to actually search — wire it to the same
// geocode-then-refresh flow the main search uses.
els.mapAddressSearchInput?.addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const query = els.mapAddressSearchInput.value.trim();
  if (!query) return;

  try {
    showStatus(`Geocoding '${query}'...`);
    const loc = await getJSON(`/api/geocode?location=${encodeURIComponent(query)}`);
    await setCurrentLocation(loc);
  } catch (err) {
    alert(`Could not find '${query}': ${err.message}`);
  } finally {
    hideStatus();
  }
});

async function requestDeviceGps(options = {}) {
  if (!navigator.geolocation) {
    if (!options.auto) {
      alert("Geolocation is not supported by your browser/device. Search for your city instead.");
    }
    return false;
  }

  // Guard against double-taps and overlapping GPS sessions.
  if (state.gpsInFlight) return false;
  state.gpsInFlight = true;

  // First-load auto mode may DEFER the request token: the default-city
  // refresh that is already in flight keeps its token and still commits
  // if the GPS attempt never resolves (denied, timeout, ignored prompt).
  const requestId = options.deferRequestId ? state.locationRequestId : ++state.locationRequestId;
  showStatus("Acquiring your current location...");

  // Watchdog: some browsers never invoke the error callback while a
  // permission prompt sits unanswered, so guarantee the fallback fires.
  let settled = false;
  const watchdog = setTimeout(() => {
    if (settled) return;
    settled = true;
    state.gpsInFlight = false;
    if (requestId !== state.locationRequestId) return;
    hideStatus();
    if (options.auto) {
      startDefaultCityIfIdle(); // graceful: keep the default city loading
    } else {
      alert("Getting your location timed out. Please try again.");
    }
  }, options.auto ? 9000 : 15000);

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      // A watchdog-rescued session ignores late fixes — except in deferred
      // auto mode, where the user may have granted AFTER the watchdog fired
      // and an explicit grant should still be honored.
      if (settled && !options.deferRequestId) return;
      settled = true;
      clearTimeout(watchdog);
      // Abort if a newer location request was started while the GPS fix
      // was pending — never commit a stale position.
      if (requestId !== state.locationRequestId) {
        state.gpsInFlight = false;
        return;
      }
      // Deferred mode takes its token only now, at commit time.
      const commitId = options.deferRequestId ? ++state.locationRequestId : requestId;

      const lat = pos?.coords?.latitude;
      const lon = pos?.coords?.longitude;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        state.gpsInFlight = false;
        hideStatus();
        alert("Your location could not be determined (no coordinates returned). Please try again.");
        return;
      }

      try {
        // Reverse-geocode (for the city name) and fetch weather run in
        // PARALLEL — the weather pipeline must not wait on geocoding.
        showStatus(`Using your location (${lat.toFixed(3)}, ${lon.toFixed(3)})...`);

        const coordsLocation = fallbackLocation(lat, lon);
        // Show real coordinates immediately; the city name replaces it
        // the moment reverse-geocoding responds.
        state.currentLocation = coordsLocation;
        updateLocationDisplay();

        const revPromise = getJSON(`/api/reverse-geocode?latitude=${lat}&longitude=${lon}`)
          .then((rev) => ({
            // Empty (not a placeholder) when the provider could not resolve —
            // the UI keeps "Locating…" instead of inventing a name. Legacy
            // placeholder strings from older backends are filtered too.
            name: rev.name && rev.name !== "Current Location" && rev.name !== "My Location" ? rev.name : "",
            admin1: rev.admin1 || "",
            country: rev.country || "",
            latitude: lat,
            longitude: lon,
          }))
          .catch(() => coordsLocation);

        // Kick the weather fetch with the raw coordinates right away.
        await refreshWeatherData(commitId);

        // Adopt the resolved city name (or the coordinate fallback) and
        // update labels/map; weather is already current for these coords.
        const rev = await revPromise;
        if (commitId === state.locationRequestId) {
          state.currentLocation = rev;
          updateLocationDisplay();
          renderMapScreen(); // marker tooltip + sensor card title use the real city
        }
      } finally {
        // Always release the GPS slot — a superseded session must not
        // permanently block future GPS attempts.
        state.gpsInFlight = false;
      }
    },
    (err) => {
      if (settled && !options.deferRequestId) return;
      settled = true;
      clearTimeout(watchdog);
      state.gpsInFlight = false;
      if (requestId !== state.locationRequestId) return;
      hideStatus();
      // Manual attempts explain the failure; automatic first-load attempts
      // fail silently into the already-loading default location.
      if (options.auto) {
        startDefaultCityIfIdle();
      } else {
        alert(`Could not get your location: ${geoErrorMessage(err)}`);
      }
    },
    {
      enableHighAccuracy: true,
      timeout: 10000,
      maximumAge: 0, // never satisfy with a cached/stale position
    }
  );
}

// ============================================================
// WEATHER DATA REFRESH & PIPELINE
// ============================================================

async function refreshWeatherData(requestId = state.locationRequestId) {
  const { latitude, longitude, name } = state.currentLocation;
  // Accept 0/0 (Gulf of Guinea) as valid coordinates; only reject missing ones.
  if (latitude == null || longitude == null || Number.isNaN(Number(latitude)) || Number.isNaN(Number(longitude))) return;

  showStatus(`Connecting to Open-Meteo & WeatherAPI for ${name}...`);

  try {
    const data = await getJSON(`/api/weather?latitude=${latitude}&longitude=${longitude}&forecast_days=7`);

    // A newer location request started while this fetch was in flight —
    // discard the stale response so it can never override fresh data.
    if (requestId !== state.locationRequestId) return;

    state.weather = data.weather;
    state.alerts = data.alerts || [];
    state.airQuality = data.air_quality || data.weather?.air_quality || {};
    state.reportCache = null; // new location → invalidate cached synopsis

    renderForecastScreen();
    renderMapScreen();
    renderInsightsScreen();

    // Smart Farm Advisor follows the same real data pipeline — invalidate
    // and refetch only if its view is currently open (lazy loading).
    state.farmCacheKey = null;
    state.farmStale = true;
    if (state.activeView === "view-farmer") {
      state.farmStale = false;
      refreshFarmAdvice();
    }

    // Trigger AI report synthesis asynchronously
    generateAiReport(requestId);
  } catch (err) {
    if (requestId === state.locationRequestId) {
      console.error("Weather fetch failed:", err);
      showStatus(`Weather sync error: ${err.message}`, "Offline");
    }
  } finally {
    // A superseded request must not hide the active request's status bar.
    if (requestId === state.locationRequestId) hideStatus();
  }
}

function renderForecastScreen() {
  const current = state.weather?.current || {};
  const daily = state.weather?.daily || {};
  const hourly = state.weather?.hourly || {};

  const temp = Math.round(current.temperature_2m ?? 20);
  const feels = Math.round(current.apparent_temperature ?? temp);
  const high = Math.round(daily.temperature_2m_max?.[0] ?? temp + 3);
  const low = Math.round(daily.temperature_2m_min?.[0] ?? temp - 4);
  const wCode = current.weather_code ?? 0;
  const isDay = current.is_day ?? 1;
  const cat = getConditionCategory(wCode, isDay);

  document.body.dataset.condition = cat;
  document.body.dataset.tempBand = tempBandFor(temp);
  applyDynamicSky(cat, tempBandFor(temp));
  // Precipitation overlay + storm lightning, from the same real payload.
  applyPrecipFx(cat, current.precipitation, current.wind_direction_10m);

  if (els.heroTemperature) els.heroTemperature.textContent = temp;
  if (els.heroTempFeels) els.heroTempFeels.textContent = `${feels}°`;
  if (els.heroTempHigh) els.heroTempHigh.textContent = `${high}°`;
  if (els.heroTempLow) els.heroTempLow.textContent = `${low}°`;
  if (els.heroConditionText) els.heroConditionText.textContent = CONDITION_TITLES[cat] || "Clear";
  if (els.heroConditionIcon) els.heroConditionIcon.textContent = CONDITION_ICONS[cat] || "wb_sunny";

  // Telemetry Grid
  const windKph = Math.round(current.wind_speed_10m ?? 12);
  const gustsKph = Math.round(current.wind_gusts_10m ?? windKph * 1.3);
  const rh = Math.round(current.relative_humidity_2m ?? 65);
  const dew = Math.round(current.dew_point_2m ?? temp - 5);
  const pressure = (current.surface_pressure ?? 1013.2).toFixed(1);

  if (els.telemetryWindSpeed) els.telemetryWindSpeed.textContent = windKph;
  if (els.telemetryWindGusts) els.telemetryWindGusts.textContent = `${gustsKph} km/h`;
  if (els.telemetryHumidity) els.telemetryHumidity.textContent = `${rh}%`;
  if (els.telemetryDewpoint) els.telemetryDewpoint.textContent = `${dew}°C`;
  if (els.telemetryPressure) els.telemetryPressure.textContent = pressure;

  // Air Quality — US AQI is the primary scale; fall back to European AQI
  // only if US AQI is genuinely absent, and never fabricate a value.
  const aqiVal = state.airQuality?.us_aqi ?? state.airQuality?.european_aqi ?? null;
  const aqiLabel = aqiVal == null ? "—" : aqiVal <= 50 ? "Good" : aqiVal <= 100 ? "Moderate" : "Unhealthy";
  const aqiColor = aqiVal == null ? "#64748B" : aqiVal <= 50 ? "#15803D" : aqiVal <= 100 ? "#B45309" : "#C0392B";

  if (els.telemetryAqiNum) {
    els.telemetryAqiNum.textContent = aqiVal == null ? "—" : aqiVal;
    els.telemetryAqiNum.style.color = aqiColor;
  }
  if (els.telemetryAqiLabel) els.telemetryAqiLabel.textContent = aqiLabel;
  if (els.telemetryAqiBar) {
    if (aqiVal == null) {
      els.telemetryAqiBar.style.width = "0%";
    } else {
      els.telemetryAqiBar.style.width = `${Math.min(100, Math.max(5, aqiVal))}%`;
    }
    els.telemetryAqiBar.style.backgroundColor = aqiColor;
  }
  if (els.telemetryAqiSub) {
    const pm25 = state.airQuality?.pm2_5;
    els.telemetryAqiSub.textContent = pm25 != null ? `PM2.5: ${pm25} µg/m³ • ${aqiLabel}` : aqiLabel;
  }

  // Microclimate Chips
  if (els.microclimateChipsScroll) {
    const deltas = [
      { name: "Downtown Core", delta: 0, icon: "wb_sunny" },
      { name: "Coastal / Lake", delta: -3, icon: "mist" },
      { name: "Hilltop / Ridge", delta: -1, icon: "air" },
      { name: "Valley Sub-basin", delta: +2, icon: "clear_day" },
    ];
    els.microclimateChipsScroll.innerHTML = deltas
      .map(
        (m, idx) => `
      <div class="microclimate-chip flex items-center gap-2 px-3 py-2 rounded-xl ${
        idx === 0 ? "bg-amber-glow-surface border border-primary/40 shadow-sm" : "bg-surface-container-high border border-glass-border-subtle"
      } shrink-0 cursor-pointer">
        <span class="w-2 h-2 rounded-full ${idx === 0 ? "bg-primary-container" : "bg-secondary"}"></span>
        <div class="flex flex-col">
          <span class="font-pill-text text-xs text-ink-primary">${m.name}</span>
          <span class="font-headline-card text-xs text-primary leading-none font-bold">${temp + m.delta}°</span>
        </div>
        <span class="material-symbols-outlined text-primary text-[16px] ml-1">${m.icon}</span>
      </div>`
      )
      .join("");
  }

  // Hourly Forecast Scroller — start at the current hour (Open-Meteo
  // returns the full day from midnight; showing past hours confuses users).
  if (els.hourlyForecastScroll && hourly.time) {
    const nowMs = Date.now();
    let startIdx = hourly.time.findIndex((tStr) => new Date(tStr).getTime() >= nowMs - 3600e3);
    if (startIdx < 0) startIdx = 0;
    const endIdx = startIdx + 24;
    const times = hourly.time.slice(startIdx, endIdx);
    const temps = hourly.temperature_2m?.slice(startIdx, endIdx) || [];
    const rains = hourly.precipitation_probability?.slice(startIdx, endIdx) || [];
    const codes = hourly.weather_code?.slice(startIdx, endIdx) || [];
    const uvs = hourly.uv_index?.slice(startIdx, endIdx) || [];

    els.hourlyForecastScroll.innerHTML = times
      .map((tStr, i) => {
        const d = new Date(tStr);
        const timeLabel = i === 0 ? "Now" : d.toLocaleTimeString([], { hour: "numeric" });
        const hTemp = Math.round(temps[i] ?? temp);
        const hRain = rains[i] ?? 0;
        const hCode = codes[i] ?? 0;
        const hUv = Math.round(uvs[i] ?? 0);
        const hCat = getConditionCategory(hCode, 1);
        const isActive = i === 0;

        return `
        <div class="forecast-hourly-card flex flex-col items-center justify-between w-20 lg:w-24 py-3 rounded-2xl ${
          isActive
            ? "bg-amber-glow-surface border border-primary/40 shadow-md"
            : "bg-surface-container-high border border-glass-border/10"
        } shrink-0">
          <span class="font-label-caps text-[11px] ${isActive ? "text-primary font-bold" : "text-ink-tertiary"} uppercase">${timeLabel}</span>
          <span class="material-symbols-outlined ${isActive ? "text-primary" : "text-ink-primary"} text-[22px] my-1.5">${
          CONDITION_ICONS[hCat] || "wb_sunny"
        }</span>
          <span class="font-headline-card text-xs text-ink-primary font-bold">${hTemp}°</span>
          <div class="mt-1.5 flex flex-col items-center">
            <span class="font-label-caps text-[10px] text-secondary font-medium">${hRain}%</span>
            <span class="font-label-caps text-[9px] text-ink-tertiary">UV ${hUv}</span>
          </div>
        </div>`;
      })
      .join("");
  }

  // 7-Day Synoptic Outlook
  if (els.dailyForecastContainer && daily.time) {
    const days = daily.time;
    els.dailyForecastContainer.innerHTML = days
      .map((dStr, i) => {
        const d = new Date(dStr + "T00:00:00");
        const dayLabel = i === 0 ? "Today" : d.toLocaleDateString([], { weekday: "short" });
        const dCode = daily.weather_code?.[i] ?? 0;
        const dCat = getConditionCategory(dCode, 1);
        const dMax = Math.round(daily.temperature_2m_max?.[i] ?? temp + 2);
        const dMin = Math.round(daily.temperature_2m_min?.[i] ?? temp - 3);
        const dRain = daily.precipitation_probability_max?.[i] ?? 10;

        return `
        <div class="flex items-center justify-between py-2 px-1 border-b border-glass-border-subtle last:border-b-0">
          <div class="w-16 shrink-0">
            <span class="font-pill-text text-xs ${i === 0 ? "text-primary font-bold" : "text-ink-secondary"}">${dayLabel}</span>
          </div>
          <div class="flex items-center gap-1.5 w-20 justify-start shrink-0">
            <span class="material-symbols-outlined ${i === 0 ? "text-primary" : "text-secondary"} text-[19px]">${
          CONDITION_ICONS[dCat] || "wb_sunny"
        }</span>
            <span class="font-label-caps text-[10px] text-secondary">${dRain}%</span>
          </div>
          <div class="flex-1 flex items-center justify-end gap-2.5 md:gap-4">
            <span class="font-body-dim text-xs text-ink-tertiary w-6 text-right shrink-0">${dMin}°</span>
            <div class="w-24 md:w-56 h-1.5 bg-surface-container rounded-full relative overflow-hidden">
              <div class="absolute inset-y-0 left-[20%] right-[15%] bg-gradient-to-r from-secondary to-primary-container rounded-full"></div>
            </div>
            <span class="font-pill-text text-xs text-ink-primary w-6 text-left font-semibold shrink-0">${dMax}°</span>
          </div>
        </div>`;
      })
      .join("");
  }
}

function renderMapScreen() {
  const current = state.weather?.current || {};
  const temp = Math.round(current.temperature_2m ?? 0);
  const hasTemp = current.temperature_2m !== undefined;

  if (els.sensorCardTitle) {
    els.sensorCardTitle.textContent = `${locationDisplayName()} — Live Reading`;
  }
  if (els.sensorMetricTemp) els.sensorMetricTemp.textContent = hasTemp ? `${temp}°C` : "—";
  if (els.sensorMetricWind) {
    els.sensorMetricWind.textContent =
      current.wind_speed_10m !== undefined ? `${Math.round(current.wind_speed_10m)} km/h` : "—";
  }
  if (els.sensorMetricPrecip) {
    els.sensorMetricPrecip.textContent =
      current.precipitation !== undefined ? `${current.precipitation.toFixed(1)} mm` : "—";
  }
  if (els.sensorMetricAqi) {
    els.sensorMetricAqi.textContent =
      state.airQuality?.us_aqi !== undefined ? state.airQuality.us_aqi : "—";
  }

  if (els.mapAddressSearchInput && state.currentLocation.name && document.activeElement !== els.mapAddressSearchInput) {
    // Don't clobber the address bar while the user is typing a search.
    const country = state.currentLocation.country || "";
    els.mapAddressSearchInput.value = `${state.currentLocation.name}${country ? ", " + country : ""}`;
  }

  if (window.RadarMap && state.currentLocation.latitude != null && state.currentLocation.longitude != null) {
    window.RadarMap.updateLocation({
      latitude: state.currentLocation.latitude,
      longitude: state.currentLocation.longitude,
      name: locationDisplayName(),
      temp: hasTemp ? `${temp}°C` : "—",
      deferInit: state.activeView !== "view-map", // lazy: no Leaflet/tiles off-screen
    });
  }
}

function bindInsightsScopeChips() {
  document.querySelectorAll("#insights-time-scope .scope-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const scope = chip.dataset.scope || "today";
      state.insightsScope = scope;

      document.querySelectorAll("#insights-time-scope .scope-chip").forEach((btn) => {
        const isSelected = btn.dataset.scope === scope;
        if (isSelected) {
          btn.className = "scope-chip px-3.5 py-1 rounded-full font-pill-text text-xs bg-amber-glow-surface border border-primary/40 text-primary font-semibold shadow-sm transition-all shrink-0";
        } else {
          btn.className = "scope-chip px-3.5 py-1 rounded-full font-pill-text text-xs bg-surface-container-high border border-glass-border-subtle text-ink-secondary hover:text-ink-primary font-normal transition-all shrink-0";
        }
      });

      renderInsightsScreen();
    });
  });
}

function renderInsightsScreen() {
  const scope = state.insightsScope || "today";
  const current = state.weather?.current || {};
  const hourly = state.weather?.hourly || {};
  const daily = state.weather?.daily || {};
  const locationName = state.currentLocation?.name || "Local Area";
  const temp = Math.round(current.temperature_2m ?? 22);

  // 1. Update Scope Subtitle
  if (els.insightsScopeSubtitle) {
    if (scope === "48h") {
      els.insightsScopeSubtitle.textContent = "48-hour synoptic trajectory, micro-climate trends & extended alerts";
    } else if (scope === "7d") {
      els.insightsScopeSubtitle.textContent = "7-day macro climate projections, barometric envelope & weekly variances";
    } else {
      els.insightsScopeSubtitle.textContent = "Atmospheric variance, early warning protocols & telemetry gradients for today";
    }
  }

  // Pre-calculate 48h and 7d aggregated values
  const hTemps = (hourly.temperature_2m || []).slice(0, 48);
  const hRain = (hourly.precipitation_probability || []).slice(0, 48);
  const hWind = (hourly.wind_speed_10m || []).slice(0, 48);
  const hHum = (hourly.relative_humidity_2m || []).slice(0, 48);

  const minTemp48 = hTemps.length ? Math.round(Math.min(...hTemps)) : Math.round(daily.temperature_2m_min?.[0] ?? 18);
  const maxTemp48 = hTemps.length ? Math.round(Math.max(...hTemps)) : Math.round(daily.temperature_2m_max?.[0] ?? 28);
  const maxRain48 = hRain.length ? Math.round(Math.max(...hRain)) : Math.round(Math.max(daily.precipitation_probability_max?.[0] || 0, daily.precipitation_probability_max?.[1] || 0));
  const maxWind48 = hWind.length ? Math.round(Math.max(...hWind)) : Math.round(Math.max(daily.wind_speed_10m_max?.[0] || 0, daily.wind_speed_10m_max?.[1] || 0));
  const avgHum48 = hHum.length ? Math.round(hHum.reduce((a, b) => a + b, 0) / hHum.length) : Math.round(current.relative_humidity_2m ?? 60);

  const dMaxTemps = daily.temperature_2m_max || [];
  const dMinTemps = daily.temperature_2m_min || [];
  const dRainProb = daily.precipitation_probability_max || [];
  const dPrecipSum = daily.precipitation_sum || [];
  const dWindMax = daily.wind_speed_10m_max || [];

  const minTemp7d = dMinTemps.length ? Math.round(Math.min(...dMinTemps)) : 16;
  const maxTemp7d = dMaxTemps.length ? Math.round(Math.max(...dMaxTemps)) : 32;
  const totalRain7d = dPrecipSum.length ? dPrecipSum.reduce((a, b) => a + (b || 0), 0).toFixed(1) : "0.0";
  const maxRain7d = dRainProb.length ? Math.round(Math.max(...dRainProb)) : 0;
  const maxWind7d = dWindMax.length ? Math.round(Math.max(...dWindMax)) : 24;

  // 2. Render Scope Telemetry Metrics Grid (4 cards)
  if (els.insightsScopeMetrics) {
    let cards = [];
    if (scope === "today") {
      const todayMax = Math.round(daily.temperature_2m_max?.[0] ?? (temp + 3));
      const todayMin = Math.round(daily.temperature_2m_min?.[0] ?? (temp - 4));
      const todayRain = Math.round(daily.precipitation_probability_max?.[0] ?? current.precipitation ?? 0);
      const todayWind = Math.round(daily.wind_speed_10m_max?.[0] ?? current.wind_speed_10m ?? 12);
      const todayHum = Math.round(current.relative_humidity_2m ?? 58);

      cards = [
        { label: "Diurnal Range", val: `${todayMin}° / ${todayMax}°`, sub: "Today's Min / Max", icon: "device_thermostat", color: "text-primary" },
        { label: "Precip Risk", val: `${todayRain}%`, sub: "Max Rain Prob", icon: "rainy", color: "text-secondary" },
        { label: "Peak Velocity", val: `${todayWind} km/h`, sub: "Max Sustained Wind", icon: "air", color: "text-alert-coral" },
        { label: "Humidity Index", val: `${todayHum}%`, sub: "Relative Density", icon: "water_drop", color: "text-tertiary" },
      ];
    } else if (scope === "48h") {
      cards = [
        { label: "48h Envelope", val: `${minTemp48}° – ${maxTemp48}°`, sub: "Min to Peak Temp", icon: "thermostat", color: "text-primary" },
        { label: "48h Rain Peak", val: `${maxRain48}%`, sub: "Peak Rain Chance", icon: "umbrella", color: "text-secondary" },
        { label: "48h Max Gust", val: `${maxWind48} km/h`, sub: "Peak Wind Speed", icon: "air", color: "text-alert-coral" },
        { label: "Mean Moisture", val: `${avgHum48}%`, sub: "48h Mean Humidity", icon: "humidity_mid", color: "text-tertiary" },
      ];
    } else {
      cards = [
        { label: "Weekly Spread", val: `${minTemp7d}° – ${maxTemp7d}°`, sub: "7-Day Min to Max", icon: "calendar_today", color: "text-primary" },
        { label: "Cumulative Rain", val: `${totalRain7d} mm`, sub: "7-Day Total Precip", icon: "water", color: "text-secondary" },
        { label: "Peak Rain Risk", val: `${maxRain7d}%`, sub: "Highest Rain Day", icon: "grain", color: "text-alert-coral" },
        { label: "Weekly Max Wind", val: `${maxWind7d} km/h`, sub: "Peak Jet Velocity", icon: "cyclone", color: "text-tertiary" },
      ];
    }

    els.insightsScopeMetrics.innerHTML = cards
      .map(
        (c) => `
      <div class="rounded-2xl bg-surface-container-high/90 border border-glass-border/20 p-3 flex flex-col justify-between shadow-md">
        <div class="flex items-center justify-between">
          <span class="font-label-caps text-[9px] text-ink-tertiary uppercase tracking-wider">${escapeHTML(c.label)}</span>
          <span class="material-symbols-outlined text-[16px] ${c.color}">${c.icon}</span>
        </div>
        <div class="mt-2">
          <span class="font-headline-card text-base sm:text-lg lg:text-xl font-bold text-ink-primary tracking-tight">${escapeHTML(c.val)}</span>
          <span class="font-body-dim text-[10px] text-ink-tertiary block mt-0.5">${escapeHTML(c.sub)}</span>
        </div>
      </div>`
      )
      .join("");
  }

  // 3. Early Warning Alert Ledger
  if (els.insightsAlertsContainer) {
    let alertItems = [];

    if (scope === "today") {
      alertItems = state.alerts || [];
    } else if (scope === "48h") {
      if (maxRain48 >= 50) {
        alertItems.push({
          severity: "Advisory",
          type: "48-Hour Precipitation Window",
          text: `Precipitation probability peaks at ${maxRain48}% over the upcoming 48 hours. Showers likely during peak transit intervals.`,
          protocol: ["Carry high-durability waterproof gear", "Check live radar plume before evening commute"],
        });
      }
      if (maxWind48 >= 35) {
        alertItems.push({
          severity: "Notice",
          type: "Wind Shift & Elevated Gusts",
          text: `Sustained winds topping ${maxWind48} km/h expected in the next 48 hours. Minor crosswind resistance for cyclists.`,
          protocol: ["Secure lightweight outdoor gear", "Maintain caution during high-speed highway travel"],
        });
      }
      if (maxTemp48 >= 36) {
        alertItems.push({
          severity: "Advisory",
          type: "48h Thermal Index Advisory",
          text: `Temperatures climbing up to ${maxTemp48}°C during peak afternoon daylight in the next 48 hours.`,
          protocol: ["Ensure hydration and electrolyte balance", "Plan outdoor exertion during morning hours"],
        });
      }
    } else {
      if (maxRain7d >= 40) {
        const wetDays = dRainProb.filter((p) => p >= 35).length;
        alertItems.push({
          severity: "Advisory",
          type: "Extended Synoptic Rain Pattern",
          text: `Active moisture corridor expected: Rain projected across ${wetDays} of the next 7 days, with peak precipitation risk reaching ${maxRain7d}%. Cumulative expected rainfall: ${totalRain7d} mm.`,
          protocol: ["Plan outdoor projects around dry intervals", "Inspect drainage systems ahead of rain days"],
        });
      }
      if (maxTemp7d >= 37) {
        alertItems.push({
          severity: "Warning",
          type: "Multi-Day Thermal Stress",
          text: `Weekly high reaches ${maxTemp7d}°C with strong insolation. Heat indices will be elevated on warmest days.`,
          protocol: ["Shift heavy activities to early morning", "Utilize shaded corridors"],
        });
      }
      if (maxWind7d >= 45) {
        alertItems.push({
          severity: "Notice",
          type: "Synoptic Jet Wind Velocity",
          text: `Strong regional pressure gradient will generate weekly peak wind gusts up to ${maxWind7d} km/h.`,
          protocol: ["Monitor coastal and ridge-top wind advisories"],
        });
      }
    }

    if (alertItems.length > 0) {
      els.insightsAlertsContainer.innerHTML = alertItems
        .map(
          (alert) => `
        <div class="relative overflow-hidden rounded-2xl bg-alert-coral-bg border border-alert-coral-border shadow-lg p-4">
          <div class="flex items-start gap-3">
            <div class="w-9 h-9 rounded-full bg-alert-coral/20 flex items-center justify-center shrink-0 text-alert-coral">
              <span class="material-symbols-outlined text-[22px]" style="font-variation-settings: 'FILL' 1;">warning</span>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-1 flex-wrap">
                <span class="px-2 py-0.5 rounded-full font-label-caps text-[9px] bg-alert-coral text-white uppercase font-bold tracking-wider">${alert.severity || "Advisory"}</span>
                <span class="font-headline-card text-xs text-alert-coral-text font-semibold">${escapeHTML(alert.type || "Meteorological Notice")}</span>
              </div>
              <p class="font-body-base text-xs text-alert-coral-text/90">${escapeHTML(alert.text || "")}</p>
              ${
                alert.protocol
                  ? `
              <div class="mt-3 pt-2.5 bg-surface-container-lowest/40 -mx-4 -mb-4 px-4 py-2.5 rounded-b-2xl flex flex-col gap-1.5">
                <span class="font-label-caps text-[9px] text-alert-coral uppercase tracking-wider font-semibold">Precautionary Safety Protocol</span>
                ${alert.protocol
                  .map(
                    (p) => `
                  <div class="flex items-center gap-2 text-ink-primary font-body-dim text-xs">
                    <span class="material-symbols-outlined text-[15px] text-alert-coral">check_circle</span>
                    <span>${escapeHTML(p)}</span>
                  </div>`
                  )
                  .join("")}
              </div>`
                  : ""
              }
            </div>
          </div>
        </div>`
        )
        .join("");
    } else {
      const scopeLabel = scope === "48h" ? "for Next 48 Hours" : scope === "7d" ? "for Extended 7-Day Forecast" : "Today";
      els.insightsAlertsContainer.innerHTML = `
        <div class="rounded-2xl bg-surface-container-high border border-glass-border/20 p-4 flex items-center gap-3">
          <div class="w-8 h-8 rounded-full bg-emerald-500/15 text-emerald-700 flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-[20px]">verified_user</span>
          </div>
          <div>
            <h4 class="font-headline-card text-xs text-ink-primary font-semibold">No Severe Atmospheric Alerts ${scopeLabel}</h4>
            <p class="font-body-dim text-xs text-ink-tertiary">All telemetry sensors report meteorological indices within nominal safe thresholds.</p>
          </div>
        </div>`;
    }
  }

  // 4. Microclimate Variance Bars
  if (els.insightsDeltaBars) {
    let bars = [];

    if (scope === "today") {
      if (els.insightsBaselineLabel) {
        els.insightsBaselineLabel.textContent = `Variance vs ${locationName} Baseline (${temp}°C)`;
      }
      bars = [
        { name: "Valley / Urban Basin (illustrative)", delta: "+2.4°C", width: "48%", color: "bg-primary-container" },
        { name: "Downtown Canyon Core (illustrative)", delta: "+1.1°C", width: "24%", color: "bg-secondary" },
        { name: "Coastal Headlands (illustrative)", delta: "-3.2°C", width: "56%", color: "bg-tertiary-container" },
        { name: "Elevated Hill Ridge (illustrative)", delta: "-1.6°C", width: "32%", color: "bg-secondary-fixed" },
      ];
    } else if (scope === "48h") {
      const swing48 = Math.max(3, Math.abs(maxTemp48 - minTemp48));
      if (els.insightsBaselineLabel) {
        els.insightsBaselineLabel.textContent = `48-Hour Micro-Atmospheric Shift Dynamics (Δ ${swing48}°C Swing)`;
      }
      bars = [
        { name: "Diurnal Thermal Oscillation", delta: `Δ ${swing48}°C`, width: `${Math.min(100, swing48 * 8)}%`, color: "bg-primary-container" },
        { name: "Warmest Hour (next 48h)", delta: `${maxTemp48}°C`, width: `${Math.min(100, Math.abs(maxTemp48) * 2.5)}%`, color: "bg-secondary" },
        { name: "Coolest Hour (next 48h)", delta: `${minTemp48}°C`, width: `${Math.min(100, Math.abs(minTemp48) * 2.5)}%`, color: "bg-tertiary-container" },
        { name: "Precipitation Flux Probability", delta: `${maxRain48}%`, width: `${Math.max(15, Math.min(100, maxRain48))}%`, color: "bg-secondary-fixed" },
      ];
    } else {
      const swing7d = Math.max(4, Math.abs(maxTemp7d - minTemp7d));
      if (els.insightsBaselineLabel) {
        els.insightsBaselineLabel.textContent = `7-Day Synoptic Variance & Macro Envelope (Spread: ${minTemp7d}° to ${maxTemp7d}°C)`;
      }
      bars = [
        { name: "Weekly Thermal Amplitude", delta: `Δ ${swing7d}°C`, width: `${Math.min(100, swing7d * 7)}%`, color: "bg-primary-container" },
        { name: "Rainiest Day Probability", delta: `${maxRain7d}%`, width: `${Math.max(15, Math.min(100, maxRain7d))}%`, color: "bg-secondary" },
        { name: "Cumulative Rainfall Volume", delta: `${totalRain7d} mm`, width: `${Math.min(100, Math.max(20, parseFloat(totalRain7d) * 4))}%`, color: "bg-tertiary-container" },
        { name: "Weekly Max Wind", delta: `${maxWind7d} km/h`, width: `${Math.min(100, maxWind7d * 2)}%`, color: "bg-secondary-fixed" },
      ];
    }

    els.insightsDeltaBars.innerHTML = bars
      .map(
        (b) => `
      <div class="flex flex-col gap-1">
        <div class="flex justify-between items-center text-ink-primary text-xs">
          <span class="font-body-base font-medium">${b.name}</span>
          <span class="font-headline-card text-primary font-semibold">${b.delta}</span>
        </div>
        <div class="h-1.5 w-full rounded-full bg-surface-container overflow-hidden flex">
          <div class="h-full ${b.color} rounded-full" style="width: ${b.width};"></div>
        </div>
      </div>`
      )
      .join("");
  }

  // 5. Barometric Gradient
  if (els.insightsPressureVal) {
    if (scope === "today") {
      if (els.insightsPressureVal) {
        els.insightsPressureVal.textContent =
          current.surface_pressure != null ? Number(current.surface_pressure).toFixed(1) : "—";
      }
      if (els.insightsPressureDesc) {
        els.insightsPressureDesc.textContent = "Live surface pressure reading (hPa).";
      }
    } else {
      // Hourly pressure series is not fetched; show the live reading and
      // say so instead of inventing a trend.
      if (els.insightsPressureVal) {
        els.insightsPressureVal.textContent =
          current.surface_pressure != null ? Number(current.surface_pressure).toFixed(1) : "—";
      }
      if (els.insightsPressureDesc) {
        els.insightsPressureDesc.textContent = "Live reading shown — extended pressure series is not part of the current data feed.";
      }
    }
  }

  // 6. Solar Radiation & UV
  if (els.insightsUvVal) {
    if (scope === "today") {
      if (els.insightsUvVal) {
        els.insightsUvVal.textContent = daily.uv_index_max?.[0] != null ? Math.round(daily.uv_index_max[0]) : "—";
      }
      if (els.insightsUvDesc) {
        els.insightsUvDesc.textContent = "Peak UV index forecast for today.";
      }
    } else if (scope === "48h") {
      const uvVals = (daily.uv_index_max || []).slice(0, 2).filter((v) => v != null);
      const uv48 = uvVals.length ? Math.round(Math.max(...uvVals)) : null;
      if (els.insightsUvVal) els.insightsUvVal.textContent = uv48 == null ? "—" : uv48;
      if (els.insightsUvDesc) {
        els.insightsUvDesc.textContent = uv48 == null ? "UV data unavailable." : `Peak UV index of ${uv48} expected across the next 48 hours.`;
      }
    } else {
      const uvVals = (daily.uv_index_max || []).filter((v) => v != null);
      const uv7d = uvVals.length ? Math.round(Math.max(...uvVals)) : null;
      if (els.insightsUvVal) els.insightsUvVal.textContent = uv7d == null ? "—" : uv7d;
      if (els.insightsUvDesc) {
        els.insightsUvDesc.textContent = uv7d == null ? "UV data unavailable." : `7-day peak solar radiation reaching UV ${uv7d}.`;
      }
    }
  }
}

// ============================================================
// SMART FARM WEATHER ADVISOR
// ============================================================

function farmAdviceCacheKey(lang) {
  const { latitude, longitude } = state.currentLocation;
  return `${latitude?.toFixed(2)},${longitude?.toFixed(2)}:${state.farmCropSelect?.value}:${state.farmStageSelect?.value}:${lang}`;
}

async function refreshFarmAdvice({ force = false } = {}) {
  if (!els.farmAdviceCards || !els.farmCropSelect || !els.farmStageSelect) return;
  const lang = farmLang();
  const key = farmAdviceCacheKey(lang);
  if (!force && state.farmCacheKey === key && state.farmAdvice) {
    renderFarmAdvice(); // same inputs -> reuse cached advisory
    return;
  }

  els.farmAdviceCards.innerHTML = `
    <div class="farm-advice-card rounded-2xl bg-surface-container-high/80 border border-glass-border/20 p-3" role="listitem">
      <p class="font-body-base text-xs text-ink-tertiary">${escapeHTML(FARM_I18N[lang].loading)}</p>
    </div>`;

  try {
    const lat = state.currentLocation.latitude;
    const lon = state.currentLocation.longitude;
    const data = await getJSON(
      `/api/farm-advice?crop=${encodeURIComponent(els.farmCropSelect.value)}&stage=${encodeURIComponent(
        els.farmStageSelect.value
      )}&latitude=${lat}&longitude=${lon}&language=${lang}`
    );
    state.farmAdvice = data;
    state.farmCacheKey = key;
    renderFarmAdvice();
  } catch (err) {
    console.warn("Farm advisory failed:", err);
    els.farmAdviceCards.innerHTML = `
      <div class="farm-advice-card rounded-2xl bg-surface-container-high/80 border border-glass-border/20 p-3" role="listitem">
        <p class="font-body-base text-xs text-ink-secondary">${escapeHTML(FARM_I18N[lang].error)}</p>
      </div>`;
  }
}

function renderFarmAdvice() {
  const lang = farmLang();
  const t = FARM_I18N[lang];
  const data = state.farmAdvice;
  if (!data || !els.farmAdviceCards) return;

  if (els.farmTitle) els.farmTitle.textContent = data.headline || t.title;
  if (els.farmCropEmoji) els.farmCropEmoji.textContent = data.crop_emoji || "🌾";

  const actionable = (data.advice || []).filter((a) => a.severity !== "info");
  if (els.farmAlertCount) {
    if (actionable.length > 0) {
      els.farmAlertCount.textContent = t.alertCount(actionable.length);
      els.farmAlertCount.classList.remove("hidden");
    } else {
      els.farmAlertCount.classList.add("hidden");
    }
  }

  els.farmAdviceCards.innerHTML = (data.advice || [])
    .map(
      (a) => `
    <div class="farm-advice-card sev-${a.severity} rounded-2xl p-3 flex flex-col gap-1.5" role="listitem">
      <div class="flex items-center justify-between gap-2">
        <span class="font-label-caps text-[10px] uppercase tracking-wider ${
        a.severity === "action" ? "text-alert-coral" : a.severity === "caution" ? "text-primary" : "text-secondary"
      } font-semibold">${escapeHTML(a.topic_label || "")}</span>
        <span class="farm-sev-badge sev-${a.severity}">${escapeHTML(t.sevLabels[a.severity] || a.severity)}</span>
      </div>
      <p class="font-body-base text-xs text-ink-primary leading-relaxed">${escapeHTML(a.text || "")}</p>
    </div>`
    )
    .join("");

  if (els.farmCropNote) {
    els.farmCropNote.innerHTML = `<span class="text-primary font-semibold">${escapeHTML(
      data.crop_note_label || t.cropNoteLabel
    )}:</span> ${escapeHTML(data.crop_note || "")}`;
  }
  if (els.farmDisclaimer) {
    els.farmDisclaimer.textContent = data.disclaimer || t.disclaimer;
  }
}

function initFarmAdvisor() {
  if (!els.farmCropSelect || !els.farmStageSelect) return;
  els.farmCropSelect.addEventListener("change", () => {
    state.farmCacheKey = null;
    refreshFarmAdvice();
  });
  els.farmStageSelect.addEventListener("change", () => {
    state.farmCacheKey = null;
    refreshFarmAdvice();
  });
}

// ============================================================
// OLLAMA AI REPORT SYNTHESIS
// ============================================================

async function generateAiReport(requestId = state.locationRequestId) {
  const language = state.voiceLang === "hi" ? "Hindi" : state.voiceLang === "te" ? "Telugu" : "English";

  try {
    const tick = ++state.reportAiTick;
    const payload = {
      location: state.currentLocation,
      weather_data: state.weather,
      alerts: state.alerts,
      language: language,
    };

    // Skip redundant LLM calls: cache the synopsis per location + language.
    const cacheKey = `${state.currentLocation.latitude},${state.currentLocation.longitude}:${language}`;
    if (state.reportCache && state.reportCache.key === cacheKey) {
      els.heroSynopsisText.innerHTML = renderMarkdownLite(state.reportCache.report);
      els.heroSynopsisMeta.textContent = `Synthesized in ${language} • Ready`;
      return;
    }

    const res = await postJSON("/api/report", payload);
    if (requestId !== state.locationRequestId) return; // stale — a newer location won
    if (tick !== state.reportAiTick) return; // stale — a newer AI report superseded this one
    state.reportCache = { key: cacheKey, report: res.report };
    state.synopsis = res.report;
    if (els.heroSynopsisText) {
      els.heroSynopsisText.innerHTML = renderMarkdownLite(res.report);
    }
    if (els.heroSynopsisMeta) {
      els.heroSynopsisMeta.textContent = `Synthesized in ${language} • Ready`;
    }
  } catch (err) {
    console.warn("AI report synthesis skipped:", err);
    state.reportCache = null;
    if (els.heroSynopsisText) {
      const realTemp = state.weather?.current?.temperature_2m;
      els.heroSynopsisText.textContent =
        realTemp != null
          ? `Current temperature is ${Math.round(realTemp)}°C with ${CONDITION_TITLES[document.body.dataset.condition] || "current conditions"}.`
          : `Weather synopsis unavailable right now.`;
    }
  }
}

// ============================================================
// WEATHERGPT AI ASSISTANT CHAT
// ============================================================

// Unified input form (appears in all tabs)
els.unifiedQueryForm?.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = els.unifiedQueryInput.value.trim();
  if (!q) return;
  els.unifiedQueryInput.value = "";
  switchView("view-weathergpt");
  submitAssistantQuery(q);
});

els.assistantChatForm?.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = els.assistantInputText.value.trim();
  if (!q) return;
  submitAssistantQuery(q);
});

els.forecastQuickQueryForm?.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = els.forecastQuickInput.value.trim();
  if (!q) return;
  els.forecastQuickInput.value = "";
  switchView("view-weathergpt");
  submitAssistantQuery(q);
});

async function submitAssistantQuery(userQuery) {
  if (!userQuery) return;
  if (els.assistantInputText) els.assistantInputText.value = "";
  if (els.unifiedQueryInput) els.unifiedQueryInput.value = "";

  // Append user bubble
  appendUserChatBubble(userQuery);

  // Show thinking indicator
  const thinkingId = appendThinkingBubble();

  const language = state.voiceLang === "hi" ? "Hindi" : state.voiceLang === "te" ? "Telugu" : "English";
  const weatherSummary = state.weather?.current
    ? `${Math.round(state.weather.current.temperature_2m)}°C, ${
        CONDITION_TITLES[document.body.dataset.condition] || "Normal"
      }, Wind: ${Math.round(state.weather.current.wind_speed_10m)} km/h`
    : "Clear, 22°C";

  try {
    const res = await postJSON("/api/assistant", {
      query: userQuery,
      location_name: locationDisplayName(),
      weather_summary: weatherSummary,
      language: language,
    });

    removeThinkingBubble(thinkingId);
    appendAssistantResponseNode(res);
  } catch (err) {
    removeThinkingBubble(thinkingId);
    const realTemp = state.weather?.current?.temperature_2m;
    const fallbackLine =
      realTemp != null
        ? `${Math.round(realTemp)}°C, ${CONDITION_TITLES[document.body.dataset.condition] || "current conditions"}`
        : "Live weather data unavailable";
    appendAssistantResponseNode({
      answer: `Unable to reach the AI service right now (${err.message}). Current conditions in ${locationDisplayName()}: ${fallbackLine}.`,
    });
  }
}

function appendUserChatBubble(text) {
  if (!els.chatStream) return;
  const div = document.createElement("div");
  div.className = "flex flex-col items-end w-full pl-6 space-y-1 animate-fade-in";
  div.innerHTML = `
    <div class="flex items-end gap-2 max-w-full">
      <div class="bg-surface-bright text-ink-primary rounded-2xl rounded-tr-xs px-4 py-2.5 shadow-md">
        <p class="font-body-base text-xs leading-relaxed">${escapeHTML(text)}</p>
      </div>
      <div class="w-7 h-7 rounded-full bg-primary/20 text-primary flex items-center justify-center shrink-0 ring-1 ring-glass-border">
        <span class="material-symbols-outlined text-[16px]">person</span>
      </div>
    </div>
    <span class="font-label-caps text-[9px] text-ink-tertiary pr-9">${new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    })}</span>
  `;
  els.chatStream.appendChild(div);
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function appendThinkingBubble() {
  const id = `thinking-${Date.now()}`;
  const div = document.createElement("div");
  div.id = id;
  div.className = "flex items-center gap-2 text-ink-tertiary text-xs p-2";
  div.innerHTML = `
    <span class="w-2 h-2 rounded-full bg-secondary animate-bounce"></span>
    <span class="w-2 h-2 rounded-full bg-primary animate-bounce [animation-delay:0.2s]"></span>
    <span class="w-2 h-2 rounded-full bg-secondary-fixed animate-bounce [animation-delay:0.4s]"></span>
    <span class="font-label-caps text-[10px] uppercase tracking-wider text-secondary font-mono">Thinking...</span>
  `;
  els.chatStream?.appendChild(div);
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  return id;
}

function removeThinkingBubble(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function appendAssistantResponseNode(data) {
  if (!els.chatStream) return;
  const div = document.createElement("div");
  div.className = "ai-chat-node flex flex-col items-start w-full space-y-2.5 animate-fade-in";

  let actionCardsHtml = "";

  // Action Card 1: Optimal Window
  if (data.optimal_window && data.optimal_window.time_range) {
    actionCardsHtml += `
    <div class="rounded-xl bg-surface-bright/70 border border-glass-border-subtle p-3 shadow-inner space-y-1.5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1.5">
          <span class="material-symbols-outlined text-primary text-[18px]">timelapse</span>
          <span class="font-headline-card text-xs text-ink-primary font-semibold">${escapeHTML(
            data.optimal_window.title || "Optimal Activity Window"
          )}</span>
        </div>
        <span class="px-2 py-0.5 rounded-full bg-primary-container/20 text-primary font-label-caps text-[9px] font-semibold">
          ${escapeHTML(data.optimal_window.reliability || "High Confidence")}
        </span>
      </div>
      <div class="grid grid-cols-2 gap-2 pt-1">
        <div class="bg-surface-container-high/80 rounded-lg p-2 flex flex-col">
          <span class="font-label-caps text-[9px] text-ink-tertiary uppercase">Recommended Window</span>
          <span class="font-headline-card text-xs text-ink-primary font-bold mt-0.5">${escapeHTML(
            data.optimal_window.time_range
          )}</span>
          <span class="font-body-dim text-[10px] text-secondary mt-0.5">${escapeHTML(
            data.optimal_window.favorable_note || "Favorable conditions"
          )}</span>
        </div>
        <div class="bg-surface-container-high/80 rounded-lg p-2 flex flex-col">
          <span class="font-label-caps text-[9px] text-ink-tertiary uppercase">Thermal / Weather Shift</span>
          <span class="font-headline-card text-xs text-alert-coral font-bold mt-0.5">${escapeHTML(
            data.optimal_window.caution_note || "Later hours"
          )}</span>
          <span class="font-body-dim text-[10px] text-ink-secondary mt-0.5">Prepare accordingly</span>
        </div>
      </div>
    </div>`;
  }

  // Action Card 2: Route Micro-Climate Progression
  if (data.route_progression && data.route_progression.length > 0) {
    actionCardsHtml += `
    <div class="rounded-xl bg-surface-bright/70 border border-glass-border-subtle p-3 shadow-inner space-y-2">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1.5">
          <span class="material-symbols-outlined text-secondary text-[18px]">route</span>
          <span class="font-headline-card text-xs text-ink-primary font-semibold">Route Micro-Climate Progression</span>
        </div>
        <span class="font-body-dim text-[10px] text-ink-tertiary">Transect Telemetry</span>
      </div>
      <div class="grid grid-cols-3 gap-1.5 pt-1">
        ${data.route_progression
          .map(
            (step, idx) => `
        <div class="flex flex-col bg-surface-container-high/60 rounded-lg p-2">
          <span class="font-label-caps text-[9px] text-ink-tertiary uppercase">${escapeHTML(step.label || `Step ${idx + 1}`)}</span>
          <span class="font-label-section text-xs text-ink-primary font-bold truncate mt-0.5">${escapeHTML(step.place || "")}</span>
          <span class="font-headline-card text-xs text-primary font-bold">${escapeHTML(step.temp || "")}</span>
          <span class="font-body-dim text-[9px] text-ink-secondary truncate">${escapeHTML(step.condition || "")}</span>
        </div>`
          )
          .join("")}
      </div>
    </div>`;
  }

  // Action Card 3: Smart Apparel Guidance
  if (data.attire_guidance && data.attire_guidance.headline) {
    actionCardsHtml += `
    <div class="rounded-xl bg-surface-bright/70 border border-glass-border-subtle p-3 shadow-inner space-y-1.5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1.5">
          <span class="material-symbols-outlined text-primary text-[18px]">checkroom</span>
          <span class="font-headline-card text-xs text-ink-primary font-semibold">${escapeHTML(
            data.attire_guidance.headline
          )}</span>
        </div>
        <span class="px-2 py-0.5 rounded-full bg-surface-container text-ink-secondary font-label-caps text-[9px]">
          ${escapeHTML(data.attire_guidance.thermal_rating || "Comfortable")}
        </span>
      </div>
      <div class="flex flex-wrap gap-1.5 pt-1">
        ${(data.attire_guidance.layers || [])
          .map((l) => `<span class="px-2 py-1 rounded-lg bg-surface-container text-ink-primary text-[10px] font-medium">• ${escapeHTML(l)}</span>`)
          .join("")}
        ${(data.attire_guidance.accessories || [])
          .map((a) => `<span class="px-2 py-1 rounded-lg bg-surface-container text-secondary text-[10px] font-medium">★ ${escapeHTML(a)}</span>`)
          .join("")}
      </div>
    </div>`;
  }

  div.innerHTML = `
    <div class="flex items-center gap-2">
      <div class="w-6 h-6 rounded-full bg-amber-glow-surface text-primary flex items-center justify-center">
        <span class="material-symbols-outlined text-[15px]">auto_awesome</span>
      </div>
      <span class="font-label-section text-xs text-ink-primary font-semibold">WeatherGPT Synoptic Intelligence</span>
      <span class="font-label-caps text-[9px] text-secondary font-mono px-1.5 py-1 rounded bg-secondary-container/40">AI</span>
    </div>
    <div class="w-full bg-surface-container/70 border border-glass-border/30 backdrop-blur-xl rounded-2xl p-4 shadow-xl space-y-3">
      <p class="font-body-base text-xs text-ink-primary leading-relaxed">${escapeHTML(data.answer || "")}</p>
      ${actionCardsHtml}
    </div>
  `;

  els.chatStream.appendChild(div);
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

// ============================================================
// GROQ WHISPER VOICE RECORDING MODULE
// ============================================================

function pickSupportedMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
    "audio/m4a",
    "audio/wav",
  ];
  if (typeof MediaRecorder === "undefined") return null;
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

async function startVoiceRecording() {
  if (state.isRecording) return;

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
    alert("Microphone voice recording is not supported in this browser environment.");
    return;
  }

  const mimeType = pickSupportedMimeType();
  if (mimeType === null) {
    alert("Audio recorder codec not supported in this browser.");
    return;
  }

  try {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (err) {
    alert(`Microphone permission denied: ${err.message}`);
    return;
  }

  state.mediaRecorder = mimeType
    ? new MediaRecorder(state.mediaStream, { mimeType })
    : new MediaRecorder(state.mediaStream);
  state.recordedChunks = [];

  state.mediaRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) {
      state.recordedChunks.push(event.data);
    }
  };

  state.mediaRecorder.onstop = handleVoiceRecordingFinished;

  state.mediaRecorder.start();
  state.isRecording = true;

  // Show voice overlay
  if (els.voiceOverlay) els.voiceOverlay.classList.remove("hidden");

  let secondsLeft = 20;
  if (els.voiceCountdownLabel) els.voiceCountdownLabel.textContent = `${secondsLeft}s remaining`;

  state.countdownInterval = setInterval(() => {
    secondsLeft -= 1;
    if (els.voiceCountdownLabel) els.voiceCountdownLabel.textContent = `${secondsLeft}s remaining`;
    if (secondsLeft <= 0) {
      stopVoiceRecording();
    }
  }, 1000);

  state.recordTimer = setTimeout(() => {
    if (state.isRecording) stopVoiceRecording();
  }, 20000);
}

function stopVoiceRecording() {
  if (!state.isRecording) return;
  clearInterval(state.countdownInterval);
  clearTimeout(state.recordTimer);
  state.isRecording = false;

  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }

  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach((track) => track.stop());
  }

  if (els.voiceOverlay) els.voiceOverlay.classList.add("hidden");
}

function cancelVoiceRecording() {
  clearInterval(state.countdownInterval);
  clearTimeout(state.recordTimer);
  state.isRecording = false;

  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach((track) => track.stop());
  }
  state.recordedChunks = [];
  if (els.voiceOverlay) els.voiceOverlay.classList.add("hidden");
}

async function handleVoiceRecordingFinished() {
  if (state.isRecording || state.recordedChunks.length === 0) return;
  state.isRecording = true; // guard: MediaRecorder 'onstop' can fire twice

  showStatus("Transcribing your recording...");

  try {
    const mime = state.mediaRecorder?.mimeType || "audio/webm";
    const audioBlob = new Blob(state.recordedChunks, { type: mime });

    const form = new FormData();
    form.append("audio", audioBlob, "voice_input.webm");
    form.append("preferred_language", state.voiceLang);

    const res = await fetch(API_BASE + "/api/transcribe", { method: "POST", body: form, signal: AbortSignal.timeout(30000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Groq transcription failed");

    const transcript = data.transcript;
    if (transcript) {
      showStatus(`Heard (${data.language}): "${transcript}"`);
      // Route query to assistant view
      switchView("view-weathergpt");
      submitAssistantQuery(transcript);
    }
  } catch (err) {
    alert(`Voice transcription error: ${err.message}`);
  } finally {
    hideStatus();
    state.recordedChunks = [];
    state.isRecording = false;
  }
}

document.querySelectorAll(".forecast-mic-btn").forEach((b) => {
  b.addEventListener("click", startVoiceRecording);
});
els.assistantMicBtn?.addEventListener("click", startVoiceRecording);
els.unifiedMicBtn?.addEventListener("click", startVoiceRecording);
els.voiceStopBtn?.addEventListener("click", stopVoiceRecording);
els.voiceCancelBtn?.addEventListener("click", cancelVoiceRecording);

// ============================================================
// MAP SCREEN INTERACTIONS
// ============================================================

// Layer Toggle — precip and satellite ("clouds") are wired to real
// RainViewer tile data; temp/wind/aqi are marked data-soon in the HTML
// and show a status message instead of pretending to do something.
document.querySelectorAll(".map-layer-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    if (pill.dataset.soon === "true") {
      // Use the visible label span only — the icon span's ligature text
      // (e.g. "device_thermostat") would otherwise leak into the message.
      const labelText = pill.querySelector("span:nth-of-type(2)")?.textContent?.trim() || "This";
      showStatus(`${labelText} live layer is coming soon.`);
      setTimeout(hideStatus, 2500);
      return;
    }

    document.querySelectorAll(".map-layer-pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    state.activeMapLayer = pill.dataset.layer;

    window.RadarMap?.setLayer(state.activeMapLayer);
  });
});

// Real marker click — opens the sensor card with the same real data
// already shown elsewhere in the app (populated by renderMapScreen()).
window.addEventListener("radar-marker-clicked", () => {
  if (els.mapSensorCard) {
    els.mapSensorCard.classList.remove("translate-y-48", "opacity-0");
  }
});

els.closeSensorCardBtn?.addEventListener("click", () => {
  if (els.mapSensorCard) {
    els.mapSensorCard.classList.add("translate-y-48", "opacity-0");
  }
});

// Radar Playback — steps through REAL RainViewer frames (past radar
// history going negative, nowcast frames going positive), instead of
// animating a decorative shape.
let isRadarPlaying = false;
let radarInterval = null;

els.radarPlayBtn?.addEventListener("click", () => {
  isRadarPlaying = !isRadarPlaying;
  const icon = els.radarPlayBtn.querySelector(".material-symbols-outlined");

  if (isRadarPlaying) {
    if (icon) icon.textContent = "pause";
    radarInterval = setInterval(() => {
      let val = parseInt(els.radarTimeSlider.value, 10);
      val = val >= 60 ? -60 : val + 10;
      els.radarTimeSlider.value = val;
      window.RadarMap?.stepToOffsetMinutes(val);
    }, 900);
  } else {
    if (icon) icon.textContent = "play_arrow";
    clearInterval(radarInterval);
  }
});

els.radarTimeSlider?.addEventListener("input", () => {
  if (isRadarPlaying) return; // playback loop already drives it
  window.RadarMap?.stepToOffsetMinutes(parseInt(els.radarTimeSlider.value, 10));
});

// ============================================================
// HELPER UTILITIES
// ============================================================

function escapeHTML(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}

function renderMarkdownLite(rawText) {
  const lines = escapeHTML(rawText).split("\n");
  const html = [];
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  };

  const inlineBold = (t) => t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      closeList();
      continue;
    }
    const heading = line.match(/^\*\*(.+)\*\*$/);
    if (heading) {
      closeList();
      html.push(`<h4>${heading[1]}</h4>`);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${inlineBold(bullet[1])}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${inlineBold(line)}</p>`);
  }
  closeList();
  return html.join("");
}

async function getJSON(path) {
  const res = await fetch(API_BASE + path, { signal: AbortSignal.timeout(20000) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function postJSON(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(45000), // AI synthesis can take a while
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// ============================================================
// INITIALIZATION
// ============================================================

// Fallback used by auto-GPS watchdog/error paths: loads the default city
// only when the pipeline is still idle (nothing newer, nothing committed).
function startDefaultCityIfIdle() {
  // Nothing newer owns the pipeline and no weather has landed: restore
  // the default city fully (labels + weather). If weather DID land while
  // GPS was pending, just restore its labels (name may still be "Locating…").
  if (state.locationRequestId === 0 && !state.weather) {
    state.currentLocation = { ...DEFAULT_LOCATION };
    updateLocationDisplay();
    refreshWeatherData();
  } else if (state.currentLocation && !state.currentLocation.name) {
    state.currentLocation = { ...DEFAULT_LOCATION };
    updateLocationDisplay();
  }
}

// ----------------------------------------------------------
// FIRST-LOAD AUTO GPS
// Prompts at most once per browser profile (Permissions API
// gate), shows the status-bar "Locating…" state, loads the
// default city in the meantime, and swaps to the detected
// locality if the user grants. Denied/unsupported/error paths
// fall back silently — the user is never nagged twice.
// ----------------------------------------------------------
function autoGpsOnFirstLoad() {
  // Manual interactions may have already started a location session —
  // never race them.
  if (state.gpsInFlight || state.locationRequestId > 0) return;

  if (!(navigator.geolocation && navigator.geolocation.getCurrentPosition)) {
    refreshWeatherData(); // unsupported: straight to the default city
    return;
  }

  // Offline (e.g. PWA offline relaunch): neither the GPS fix's reverse
  // geocode nor its weather fetch can succeed — load the default city
  // and skip the prompt entirely. The GPS button still works online.
  if (navigator.onLine === false) {
    refreshWeatherData();
    return;
  }

  const startDefaultCity = () => {
    // Only if nothing newer owns the pipeline and no weather has landed
    // yet — avoids a duplicate fetch when the default city is already loading.
    if (state.locationRequestId === 0 && !state.weather) refreshWeatherData();
  };
  // Small "Locating…" state in header/hero while the fix resolves (empty
  // name renders as "Locating…" via updateLocationDisplay).
  const showLocatingState = () => {
    if (!state.weather) {
      state.currentLocation = { name: "", admin1: "", country: "", latitude: DEFAULT_LOCATION.latitude, longitude: DEFAULT_LOCATION.longitude };
      updateLocationDisplay();
    }
  };
  const startAutoGps = () => requestDeviceGps({ auto: true, deferRequestId: true });

  if (navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: "geolocation" }).then((perm) => {
      if (perm.state === "granted") {
        showLocatingState();
        startAutoGps(); // silent fix, no prompt
      } else if (perm.state === "prompt") {
        // Keep the app useful while the user decides: load the default
        // city now; GPS supersedes it if granted (deferred token).
        startDefaultCity();
        showLocatingState();
        startAutoGps(); // the one allowed automatic prompt
        // If the user answers the prompt only after the watchdog has
        // already fallen back to the default city, an explicit grant
        // still resolves the fix.
        perm.onchange = () => {
          if (perm.state === "granted" && !state.gpsInFlight && state.locationRequestId === 0) {
            startAutoGps();
          }
        };
      } else {
        startDefaultCity(); // denied: never prompt
      }
    }).catch(() => startDefaultCity());
  } else {
    startDefaultCity(); // conservative: no Permissions API support
  }
}

window.addEventListener("DOMContentLoaded", () => {
  bindAssistantChips();
  bindInsightsScopeChips();
  initFarmAdvisor();
  // Render the default location's labels immediately (the old pipeline set
  // them inside refreshWeatherData; label updates now live in
  // updateLocationDisplay and must run once at startup too).
  updateLocationDisplay();
  // Paint the initial sky before the first weather payload lands.
  applyDynamicSky("clear-day", "mild");
  applyPrecipFx("clear-day"); // rain/lightning layers start hidden
  // Auto-request GPS on first load (graceful fallback to the default
  // city on denial/timeout/unsupported) instead of always loading SF.
  autoGpsOnFirstLoad();
});
