/* ============================================================
   WeatherGPT • Atmospheric Intelligence Client Controller
   Seamless mobile application logic for Android, iOS & Web
   ============================================================ */

// 1. Service Worker for Offline PWA Capabilities
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js?v=23").catch((err) => {
      console.warn("ServiceWorker registration:", err);
    });
  });
}

// 1b. THEME MANAGER — Light/Dark with persistence.
// The inline bootstrap script in <head> already applied the saved theme
// before first paint (no flash); this adds the toggle, localStorage
// persistence and live prefers-color-scheme tracking for "system".
const THEME_KEY = "weathergpt-theme";
const LANG_KEY = "weathergpt-lang";

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(theme, { persist = false } = {}) {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.dataset.theme = theme;
  const btn = document.getElementById("theme-toggle-btn");
  if (btn) {
    btn.setAttribute("aria-pressed", String(theme === "dark"));
    btn.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (e) {
      /* private mode: theme still applies for the session */
    }
  }
}

function initThemeToggle() {
  const btn = document.getElementById("theme-toggle-btn");
  if (!btn) return;
  // Reflect the bootstrapped theme into the button state immediately.
  applyTheme(currentTheme());
  btn.addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark", { persist: true });
    // Keep the PWA status-bar tint and any live rain tinted per theme.
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", currentTheme() === "dark" ? "#0F1626" : "#3D7CC9");
    syncRainFxTheme();
    syncSceneFxTheme();
    syncCardSceneFxTheme();
  });
  // Keep "system-default" sessions in sync with OS changes until the
  // user explicitly picks a side (an explicit pick persists and wins).
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e) => {
      let saved = null;
      try {
        saved = localStorage.getItem(THEME_KEY);
      } catch (err) {
        /* ignore */
      }
      if (saved !== "light" && saved !== "dark") applyTheme(e.matches ? "dark" : "light");
      // Also refresh the PWA status bar color to match.
      const meta = document.querySelector('meta[name="theme-color"]:not([media])');
      if (meta) meta.setAttribute("content", e.matches ? "#0F1626" : "#3D7CC9");
      syncSceneFxTheme();
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
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
  voiceLang: (document.documentElement.__i18nBootLang === "hi" ||
    document.documentElement.__i18nBootLang === "te") ? document.documentElement.__i18nBootLang : "auto",
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
    cropHelp: "Select the crop growing in your field",
    stageHelp: "Select the current growth stage of your crop",
    emptyState: "Select your crop and stage to get today's farm advisory.",
    guideTitle: "Know your field",
    guideSubtitle: "Crops and key practices at a glance",
    guideCropsHeading: "Crops",
    guidePracticesHeading: "Key practices",
    guideAltPrefix: "Photo of",
    guidePhotoCredit: "Photos: Wikimedia Commons contributors (CC BY / CC BY-SA / public domain). Maize: 'Before Rain at a Corn Field' by Soumyabrata Roy, CC BY-SA 4.0. Cotton: 'Mature cotton boll in Raichur, Karnataka' by Nanditha Gogate, WELL Labs, CC BY-SA 4.0. Rain: 'Flooded paddy field Raichur' by Vraj Acharya, WELL Labs, CC BY-SA 4.0. Windbreak: 'Wind break' by Hugh Venables, CC BY-SA 2.0 via geograph.org.uk.",
    guideCardHint: "Tap for details",
    infoSubtitle: "Crop profile",
    infoClose: "Close",
    infoDisclaimer: "General agricultural information for awareness only. Conditions vary by region — verify locally before field decisions. Fertilizer/pesticide dosages are intentionally not included.",
    infoLabels: {
      climate: "Suitable climate",
      soil: "Soil",
      season: "Sowing season",
      bestTime: "Best sowing time",
      duration: "Growing duration",
      stages: "Growth stages",
      water: "Water & irrigation",
      rainfall: "Rainfall guidance",
      temperature: "Temperature range",
      sunlight: "Sunlight",
      harvestTime: "Harvesting time & signs",
      risks: "Common weather risks",
      pests: "Pest & disease awareness",
      storage: "Storage guidance",
      tips: "Weather-wise tips",
    },
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
    cropHelp: "अपने खेत में उगाई जाने वाली फसल चुनें",
    stageHelp: "फसल की वर्तमान अवस्था चुनें",
    emptyState: "आज की कृषि सलाह पाने के लिए अपनी फसल और अवस्था चुनें।",
    guideTitle: "अपने खेत को जानें",
    guideSubtitle: "फसलें और प्रमुख कृषि कार्य एक नज़र में",
    guideCropsHeading: "फसलें",
    guidePracticesHeading: "प्रमुख कृषि कार्य",
    guideAltPrefix: "फोटो:",
    guidePhotoCredit: "फोटो: Wikimedia Commons योगदानकर्ता (CC BY / CC BY-SA / सार्वजनिक डोमेन)। मक्का: 'Before Rain at a Corn Field', Soumyabrata Roy, CC BY-SA 4.0। कपास: 'Mature cotton boll in Raichur, Karnataka', Nanditha Gogate, WELL Labs, CC BY-SA 4.0। वर्षा: 'Flooded paddy field Raichur', Vraj Acharya, WELL Labs, CC BY-SA 4.0। वायु-अवरोध: 'Wind break', Hugh Venables, CC BY-SA 2.0, geograph.org.uk के माध्यम से।",
    guideCardHint: "विवरण के लिए टैप करें",
    infoSubtitle: "फसल परिचय",
    infoClose: "बंद करें",
    infoDisclaimer: "यह सामान्य कृषि जानकारी केवल जागरूकता के लिए है। स्थितियाँ क्षेत्र के अनुसार बदलती हैं — खेत के निर्णयों से पहले स्थानीय सत्यापन करें। उर्वरक/कीटनाशक की मात्रा जानबूझकर शामिल नहीं की गई है।",
    infoLabels: {
      climate: "उपयुक्त जलवायु",
      soil: "मिट्टी",
      season: "बुवाई का मौसम",
      bestTime: "सर्वोत्तम बुवाई समय",
      duration: "अवधि",
      stages: "वृद्धि अवस्थाएँ",
      water: "पानी व सिंचाई",
      rainfall: "वर्षा संबंधी मार्गदर्शन",
      temperature: "तापमान सीमा",
      sunlight: "धूप",
      harvestTime: "कटाई समय व संकेत",
      risks: "सामान्य मौसम जोखिम",
      pests: "कीट व रोग जागरूकता",
      storage: "भंडारण मार्गदर्शन",
      tips: "मौसम-अनुकूल सुझाव",
    },
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
    cropHelp: "మీ పొలంలో పండిస్తున్న పంటను ఎంచుకోండి",
    stageHelp: "మీ పంట ప్రస్తుత దశను ఎంచుకోండి",
    emptyState: "ఈరోజు వ్యవసాయ సలహా పొందడానికి మీ పంట మరియు దశను ఎంచుకోండి.",
    guideTitle: "మీ పొలాన్ని తెలుసుకోండి",
    guideSubtitle: "పంటలు మరియు ముఖ్య వ్యవసాయ పద్ధతులు ఒక చూపులో",
    guideCropsHeading: "పంటలు",
    guidePracticesHeading: "ముఖ్య వ్యవసాయ పద్ధతులు",
    guideAltPrefix: "ఫోటో:",
    guidePhotoCredit: "ఫోటోలు: Wikimedia Commons సహకారులు (CC BY / CC BY-SA / పబ్లిక్ డొమైన్). మొక్కజొన్న: 'Before Rain at a Corn Field', Soumyabrata Roy, CC BY-SA 4.0. పత్తి: 'Mature cotton boll in Raichur, Karnataka', Nanditha Gogate, WELL Labs, CC BY-SA 4.0. వర్షం: 'Flooded paddy field Raichur', Vraj Acharya, WELL Labs, CC BY-SA 4.0. గాలిఅడ్డు: 'Wind break', Hugh Venables, CC BY-SA 2.0, geograph.org.uk ద్వారా.",
    guideCardHint: "వివరాల కోసం నొక్కండి",
    infoSubtitle: "పంట ప్రొఫైల్",
    infoClose: "మూసివేయి",
    infoDisclaimer: "ఇది సాధారణ వ్యవసాయ సమాచారం — అవగాహన కోసం మాత్రమే. పరిస్థితులు ప్రాంతాన్ని బట్టి మారతాయి — పొల నిర్ణయాలకు ముందు స్థానికంగా సరిచూసుకోండి. ఎరువులు/పురుగుమందుల మోతాదులు ఉద్దేశపూర్వకంగా చేర్చలేదు.",
    infoLabels: {
      climate: "అనుకూల శీతోష్ణస్థితి",
      soil: "నేల",
      season: "విత్తుల కాలం",
      bestTime: "ఉత్తమ విత్తుల సమయం",
      duration: "పెరుగుదల వ్యవధి",
      stages: "పెరుగుదల దశలు",
      water: "నీరు & సాగునీరు",
      rainfall: "వర్షపాత మార్గదర్శకాలు",
      temperature: "ఉష్ణోగ్రత పరిధి",
      sunlight: "ఎండ",
      harvestTime: "కోత సమయం & సూచనలు",
      risks: "సాధారణ వాతావరణ ప్రమాదాలు",
      pests: "పురుగులు & తెగుళ్ల అవగాహన",
      storage: "నిల్వ మార్గదర్శకాలు",
      tips: "వాతావరణ-అనుకూల చిట్కాలు",
    },
  },
};

// ---------------------------------------------------------------
// FARM VISUAL GUIDE CONTENT
// Crop entries MIRROR the backend /api/crops catalogue: `id` is the
// stable internal identifier used by /api/farm-advice, `names` carry
// the localized display labels (must stay in sync with backend
// farm_advisor.CROPS). Practices are presentation-only on the client:
// each describes WHAT the practice is — no dosage, no yield or
// outcome claims. Photos are real photographs from Wikimedia Commons
// (free licences), served through Special:FilePath at 640px width.
// ---------------------------------------------------------------
const FARM_GUIDE_CROPS = [
  { id: "rice", names: { en: "Rice (Paddy)", hi: "धान (चावल)", te: "వరి" }, emoji: "🌾",
    photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Paddy%20field%20in%20Tamil%20Nadu%20India.jpg?width=640" },
  { id: "cotton", names: { en: "Cotton", hi: "कपास", te: "పత్తి" }, emoji: "🪴",
    photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Mature%20cotton%20boll%20in%20Raichur%2C%20Karnataka.jpg?width=640" },
  { id: "maize", names: { en: "Maize", hi: "मक्का", te: "మొక్కజొన్న" }, emoji: "🌽",
    photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Before%20Rain%20at%20a%20Corn%20Field.jpg?width=640" },
  { id: "groundnut", names: { en: "Groundnut", hi: "मूंगफली", te: "వేరుశనగ" }, emoji: "🥜",
    photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Groundnut%20crop%20in%20Chittoor%20district%2C%20Andhra%20Pradesh.jpg?width=640" },
  { id: "wheat", names: { en: "Wheat", hi: "गेहूँ", te: "గోధుమ" }, emoji: "🌾",
    photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Wheat%20close-up.JPG?width=640" },
  { id: "sugarcane", names: { en: "Sugarcane", hi: "गन्ना", te: "చెరకు" }, emoji: "🎍",
    photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Sugarcane%20Field%20Srirangapatna%20Karnataka%20Jul22%20R16%2006192.jpg?width=640" },
];

const FARM_GUIDE_PRACTICES = [
  { key: "sowing", photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Women%20Farmers%20Sowing%20in%20Karnataka%2C%20India.jpg?width=640",
    names: { en: "Sowing", hi: "बुवाई", te: "విత్తులు వేయడం" }, emoji: "🌱",
    desc: {
      en: "Placing seed in prepared soil at the right soil-moisture window.",
      hi: "तैयार मिट्टी में सही नमी के समय बीज बोना।",
      te: "సిద్ధమైన నేలలో సరైన తేమ సమయంలో విత్తనాలు వేయడం.",
    } },
  { key: "irrigation", photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Drip%20irrigation%20in%20Chinawal%201.jpg?width=640",
    names: { en: "Irrigation", hi: "सिंचाई", te: "నీటి పారుదల" }, emoji: "💧",
    desc: {
      en: "Supplying water to the crop; drip lines wet the root zone directly.",
      hi: "फसल को पानी देना; ड्रिप लाइनें जड़ क्षेत्र तक सीधे पानी पहुँचाती हैं।",
      te: "పంటకు నీరు అందించడం; డ్రిప్ లైన్లు వేర్ల వద్దకు నేరుగా నీరు చేరుస్తాయి.",
    } },
  { key: "rain", photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Flooded%20paddy%20field%20Raichur%20Karnataka%20India%20monsoon%20irrigation%20July%202025.jpg?width=640",
    names: { en: "Rain protection", hi: "वर्षा सुरक्षा", te: "వర్ష రక్షణ" }, emoji: "🌧️",
    desc: {
      en: "Covering and draining before wet spells so produce and soil stay protected.",
      hi: "गीले मौसम से पहले ढकना और जल निकासी ताकि फसल व मिट्टी सुरक्षित रहें।",
      te: "తడి కాలానికి ముందు కప్పడం మరియు నీరు పారుదల — పంట, నేల సురక్షితంగా ఉంటాయి.",
    } },
  { key: "heat", photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Cracked%20dry%20soil%20in%20Bangladesh.jpg?width=640",
    names: { en: "Heat care", hi: "गर्मी से बचाव", te: "వేడి నుండి రక్షణ" }, emoji: "☀️",
    desc: {
      en: "Managing crops and field work through hot, dry spells.",
      hi: "गर्म और शुष्क दौर में फसल व खेत के काम का प्रबंधन।",
      te: "వేడి, పొడి కాలాల్లో పంట మరియు పొల పనుల నిర్వహణ.",
    } },
  { key: "wind", photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Wind%20break%20-%20geograph.org.uk%20-%201047141.jpg?width=640",
    names: { en: "Wind safety", hi: "हवा से बचाव", te: "గాలి భద్రత" }, emoji: "💨",
    desc: {
      en: "Field operations and spraying need calm conditions; wind check comes first.",
      hi: "खेत के काम और छिड़काव शांत मौसम में ही; पहले हवा जाँचें।",
      te: "పొల పనులు, స్ప్రే చేయడం ప్రశాంత వాతావరణంలోనే; ముందుగా గాలిని తనిఖీ చేయండి.",
    } },
  { key: "harvest", photo: "https://commons.wikimedia.org/wiki/Special:FilePath/Combine%20harvester%20cutting%20wheat%20field%20-%20geograph.org.uk%20-%20520759.jpg?width=640",
    names: { en: "Harvesting", hi: "कटाई", te: "కోత" }, emoji: "🚜",
    desc: {
      en: "Cutting and collecting mature crop, ideally in a dry weather window.",
      hi: "पकी फसल को काटना और इकट्ठा करना, सूखे मौसम में सबसे उचित।",
      te: "పండిన పంటను కోత చేసి సేకరించడం; పొడి వాతావరణం ఉత్తమం.",
    } },
];

// ---------------------------------------------------------------
// CROP INFORMATION PROFILES — content for the crop-info panel that
// opens when a "Know your field" crop card is tapped/activated.
// Trustworthy, general, NON-PRESCRIPTIVE agronomy (no fertilizer or
// pesticide dosages, no yield promises); wording favors "varies by
// region — verify locally". Field order drives the panel layout.
// IDs mirror FARM_GUIDE_CROPS / backend farm_advisor.CROPS exactly.
// ---------------------------------------------------------------
const FARM_INFO_FIELDS = [
  "climate", "soil", "season", "bestTime", "duration", "stages",
  "water", "rainfall", "temperature", "sunlight", "harvestTime",
  "risks", "pests", "storage", "tips",
];

const FARM_CROP_INFO = {};

FARM_CROP_INFO.rice = {
  climate: { en: "Hot and humid; roughly 20-35°C through the season with abundant water.", hi: "गर्म और आर्द्र जलवायु; मौसम भर लगभग 20–35°C और प्रचुर पानी।", te: "వేడి, తేమ గల శీతోష్ణస్థితి; సీజన్‌లో సుమారు 20–35°C, సమృద్ధిగా నీరు." },
  soil: { en: "Water-retaining clay or clay-loam soils suit flooded paddy best.", hi: "पानी रोकने वाली चिकनी या दोमट मिट्टी धान के लिए सबसे उपयुक्त।", te: "నీరు నిలిచి ఉండే బంకమట్టి లేదా నేల వరికి అనుకూలం." },
  season: { en: "Main season is the monsoon (kharif); a second (rabi) crop needs irrigation.", hi: "मुख्य मौसम मानसून (खरीफ); दूसरी (रबी) फसल सिंचाई पर निर्भर।", te: "ప్రధాన సీజన్ వర్షాధార (ఖరీఫ్); రెండో పంటకు సాగునీరు అవసరం." },
  bestTime: { en: "With monsoon onset (around June-July) for rainfed fields.", hi: "वर्षा-आधारित खेतों में मानसून आगमन (लगभग जून–जुलाई) पर।", te: "వర్షాధార పొలాల్లో వర్షిత కాలం ప్రారంభంలో (జూన్–జూలై చుట్టూ)." },
  duration: { en: "About 120-150 days, depending on the variety.", hi: "किस्म के अनुसार लगभग 120–150 दिन।", te: "రకాన్ని బట్టి సుమారు 120–150 రోజులు." },
  stages: { en: "Nursery/transplanting → tillering → panicle initiation → flowering → grain filling → maturity.", hi: "नर्सरी/रोपाई → चीरती (टिलरिंग) → सूँड शुरुआत → फूल → दाना भरना → पकना।", te: "నారు/నాటడం → పిలకలు వచ్చు దశ → గడ్డ ఏర్పాటు → పూత → ధాన్యం నిండుదల → పండిన దశ." },
  water: { en: "Among the thirstiest crops — fields are usually kept flooded for much of the season.", hi: "सबसे अधिक पानी चाहने वाली फसलों में — मौसम का अधिकांश समय खेत में पानी भरा रहता है।", te: "అత్యధిక నీటి అవసరం గల పంట — సీజన్‌లో ఎక్కువ భాగం పొలంలో నీరు నిలిచి ఉంటుంది." },
  rainfall: { en: "Steady monsoon rain suits it; dry spells during flowering stress the crop, and flooding at ripening spoils grain.", hi: "नियमित मानसून वर्षा अनुकूल; फूल अवस्था में सूखा नुकसान करता है और पकते समय जलभराव दाना खराब करता है।", te: "సమతుల్య వర్షం మేలు; పూత సమయంలో తడి ఆరిపోవడం ఒత్తిడి, పండే సమయంలో ముంపు ధాన్యాన్ని పాడుచేస్తుంది." },
  temperature: { en: "Roughly 20-35°C; the flowering stage is the most sensitive.", hi: "लगभग 20–35°C; फूल अवस्था सबसे संवेदनशील।", te: "సుమారు 20–35°C; పూత దశ అతి సున్నితమైనది." },
  sunlight: { en: "Full sun throughout.", hi: "पूरे मौसम पूरी धूप।", te: "పూర్తి ఎండ అవసరం." },
  harvestTime: { en: "When grains harden and turn golden and panicles droop — usually 30-35 days after flowering.", hi: "दाने कड़े और सुनहरे होकर बालियाँ झुकने लगें — प्रायः फूल आने के 30–35 दिन बाद।", te: "ధాన్యం గట్టిపడి బంగారు రంగులోకి మారి, గడ్డలు వంగినప్పుడు — సాధారణంగా పూత తర్వాత 30–35 రోజులకు." },
  risks: { en: "Drought in the nursery, submergence of young plants, cloudy wet spells at flowering, and strong winds flattening the ripe crop.", hi: "नर्सरी में सूखा, युवा पौधों का डूबना, फूल अवस्था में बादल/गीला मौसम, और तेज़ हवा से पकी फसल का गिरना।", te: "నారు మడుల్లో ఎండలు, యువ మొక్కలు మునిగిపోవడం, పూత సమయంలో మేఘావృత/తడి వాతావరణం, బలిష్ఠ గాలులతో పంట పడిపోవడం." },
  pests: { en: "Common concerns: stem borer, brown planthopper and blast disease. Regular field inspection and local extension advice help catch problems early.", hi: "सामान्य चिंताएँ: तना छेदक, भूरा प्लांटहॉपर और ब्लास्ट रोग। नियमित खेत निरीक्षण और स्थानीय कृषि सलाह से समय रहते पहचानें।", te: "సాధారణ సమస్యలు: కాండం తొండు పురుగు, బ్రౌన్ ప్లాంట్ హాపర్, బ్లాస్ట్ తెగులు. క్రమం తప్పకు పొల పరిశీలన మరియు స్థానిక సలహా ఉపయోగకరం." },
  storage: { en: "Dry the produce well before storing; keep it cool, dry and protected from moisture and rodents.", hi: "भंडारण से पहले अच्छी तरह सुखाएँ; ठंडी, सूखी जगह पर नमी और चूहों से सुरक्षित रखें।", te: "నిల్వకు ముందు బాగా ఆరబెట్టండి; చల్లని, పొడి చోట తేమ మరియు ఎలుకల నుండి కాపాడండి." },
  tips: { en: "Time transplanting with a reliable rain window, drain fields before harvest, and avoid field work during thunderstorms.", hi: "भरोसेमंद बारिश की खिड़की में रोपाई करें, कटाई से पहले खेत का पानी निकालें, और आंधी-तूफान के समय खेत के काम टालें।", te: "నమ్మదగిన వర్షపు విండోలో నాటడం, కోతకు ముందు నీరు పారుదల చేయడం, ఉరుముల సమయంలో పొల పనులు వాయిదా వేయడం ఉత్తమం." },
};

FARM_CROP_INFO.cotton = {
  climate: { en: "Warm, sunny and frost-free; roughly 21-30°C with moderate humidity.", hi: "गर्म, धूपदार और पाला-रहित; लगभग 21–30°C और संतुलित नमी।", te: "వెచ్చని, ఎండతో కూడిన, మంచు లేని వాతావరణం; సుమారు 21–30°C, సమతుల్య తేమ." },
  soil: { en: "Deep, well-drained black-cotton (heavy) or alluvial soils.", hi: "गहरी, जल-निकासी युक्त काली (कॉटन) या जलोढ़ मिट्टी।", te: "లోతైన, నీరు పారే నల్ల నేల లేదా ఒండ్రు నేలలు." },
  season: { en: "Kharif — sown with the monsoon (May-June irrigated, June-July rainfed).", hi: "खरीफ — मानसून के साथ बुवाई (सिंचित मई–जून, वर्षा-आधारित जून–जुलाई)।", te: "ఖరీఫ్ — వర్షాధారంతో విత్తులు (సాగునీటిలో మే–జూన్, వర్షాధారంలో జూన్–జూలై)." },
  bestTime: { en: "After pre-monsoon showers warm and moisten the soil.", hi: "मानसून-पूर्व बौछारों के मिट्टी को गर्म व नम करने के बाद।", te: "వర్షం ముందస్తు జల్లులు నేలను వేడిచేసి తడిపెట్టిన తర్వాత." },
  duration: { en: "About 160-180 days.", hi: "लगभग 160–180 दिन।", te: "సుమారు 160–180 రోజులు." },
  stages: { en: "Emergence → seedling → squaring → flowering & boll formation → boll opening.", hi: "अंकुरण → पौध अवस्था → गाँठें बनना → फूल व डोडा बनना → डोडे खुलना।", te: "మొలకెత్తుట → మొక్క దశ → పూయటం → పూత & బోల్స్ ఏర్పాటు → బోల్స్ తెరుచుకోవడం." },
  water: { en: "Deep-rooted; needs moisture most during flowering and boll development.", hi: "गहरी जड़ों वाली; फूल और डोडा बनने के समय सबसे अधिक नमी चाहिए।", te: "లోతైన వేర్లు గలది; పూత మరియు బోల్స్ పెరుగుదల సమయంలో తేమ ఎక్కువగా కావాలి." },
  rainfall: { en: "Good early rain supports growth; excess rain at flowering causes boll rot, and a dry sunny spell is needed at picking.", hi: "शुरुआती अच्छी वर्षा वृद्धि में मदद; फूल अवस्था में अधिक वर्षा से डोडा सड़ता है और तुड़ाई के समय सूखा धूपदार मौसम चाहिए।", te: "మొదటి మంచి వర్షం పెరుగుదలకు మేలు; పూత సమయంలో అధిక వర్షం బోల్స్ కుళ్ళిపోవడానికి కారణం, పిక్కింగ్‌కు పొడి ఎండ వాతావరణం కావాలి." },
  temperature: { en: "About 21-30°C; cool spells slow growth and heavy rain at boll opening is harmful.", hi: "लगभग 21–30°C; ठंडे दौर से वृद्धि धीमी और डोडे खुलते समय भारी वर्षा हानिकारक।", te: "సుమారు 21–30°C; చల్లని కాలాల్లో పెరుగుదల నెమ్మది, బోల్స్ తెరుచుకునేటప్పుడు భారీ వర్షం హానికరం." },
  sunlight: { en: "Full sun across its long season.", hi: "लंबे मौसम में पूरी धूप।", te: "పొడవైన సీజన్ మొత్తంలో పూర్తి ఎండ." },
  harvestTime: { en: "Bolls split open showing white fluff; picked in several rounds as they open.", hi: "डोडे खुलकर सफेद रुई दिखाते हैं; खुलने के क्रम में कई चरणों में तुड़ाई।", te: "బోల్స్ తెరుచుకుని తెల్ల పత్తి కనిపిస్తాయి; తెరుచుకునే క్రమంలో పలు విడతలుగా పిక్కింగ్." },
  risks: { en: "Drought during boll formation, unseasonal rain at picking, hail, and strong winds.", hi: "डोडा बनने के समय सूखा, तुड़ाई में बिना मौसम की वर्षा, ओलावृष्टि और तेज़ हवाएँ।", te: "బోల్స్ ఏర్పడే సమయంలో ఎండలు, పిక్కింగ్‌లో అసమయ వర్షం, వడగళ్ళ వాన, బలిష్ఠ గాలులు." },
  pests: { en: "Common concerns: the bollworm complex, whitefly and leaf-curl virus. Regular scouting and removing affected plants per local guidance helps.", hi: "सामान्य चिंताएँ: बॉलवर्म समूह, सफेद मक्खी और पत्ती-कुंचन विषाणु। नियमित निगरानी और स्थानीय सलाह अनुसार प्रभावित पौधे हटाना लाभकारी।", te: "సాధారణ సమస్యలు: బోల్ వార్మ్ సమూహం, తెల్ల ఈగ, ఆకు కుంకుడు వైరస్. క్రమం తప్పకు పరిశీలించి, స్థానిక సలహా మేరకు ప్రభావిత మొక్కలు తొలగించడం మేలు." },
  storage: { en: "Keep picked cotton dry to prevent fungus; store seed separately, cool and dry.", hi: "तुड़ाई रुई को सूखा रखें ताकि फफूंद न लगे; बीज अलग, ठंडा और सूखा रखें।", te: "పిక్ చేసిన పత్తిని పొడిగా ఉంచండి — బూజు రాకుండా; విత్తనాన్ని వేరుగా, చల్లగా, పొడిగా నిల్వ చేయండి." },
  tips: { en: "Pick in dry weather, wait for bolls to dry after rain, and cover picked cotton before wet spells.", hi: "सूखे मौसम में तुड़ाई करें, वर्षा के बाद डोडे सूखने दें, और गीले मौसम से पहले तुड़ाई रुई ढक दें।", te: "పొడి వాతావరణంలో పిక్కింగ్ చేయండి, వర్షం తర్వాత బోల్స్ ఆరేవరకు వేచి ఉండండి, తడి కాలానికి ముందు పిక్ చేసిన పత్తిని కప్పండి." },
};

FARM_CROP_INFO.maize = {
  climate: { en: "Warm frost-free weather; roughly 21-30°C for good grain fill.", hi: "गर्म, पाला-रहित मौसम; अच्छे दाने के लिए लगभग 21–30°C।", te: "వెచ్చని, మంచు లేని వాతావరణం; మంచి ధాన్యం నింపుదలకు సుమారు 21–30°C." },
  soil: { en: "Well-drained loams rich in organic matter.", hi: "जैविक पदार्थ से भरपूर, जल-निकासी युक्त दोमट मिट्टी।", te: "సేంద్రీయ పదార్థం ఉన్న, నీరు పారే నేలలు." },
  season: { en: "Kharif with the monsoon; spring and rabi crops where irrigation exists.", hi: "खरीफ मानसून में; सिंचाई होने पर बसंत और रबी फसलें।", te: "వర్షాధార ఖరీఫ్; సాగునీరు ఉన్నచో వసంత, రబీ పంటలు." },
  bestTime: { en: "Just as the monsoon arrives, into warm moist soil.", hi: "मानसून आते ही, गर्म व नम मिट्टी में।", te: "వర్షాధారం వచ్చే సమయంలోనే, వెచ్చని తడి నేలలో." },
  duration: { en: "About 90-110 days (varies widely by variety).", hi: "लगभग 90–110 दिन (किस्म के अनुसार बहुत भिन्न)।", te: "సుమారు 90–110 రోజులు (రకాన్ని బట్టి ఎక్కువగా మారుతుంది)." },
  stages: { en: "Emergence → knee-high → knee-high tasseling → silking → grain fill → maturity.", hi: "अंकुरण → घुटने-ऊँचाई → गुच्छा (टैसेल) → रेशम (सिल्क) → दाना भरना → पकना।", te: "మొలకెత్తుట → మోకాల ఎత్తు → పూల గుత buck తెల్ల జుట్టు → ధాన్యం నింపుదల → పండిన దశ." },
  water: { en: "Highest demand around flowering (tasseling-silking); a dry spell then hurts most.", hi: "फूल आने (टैसेल-सिल्क) के समय सबसे अधिक आवश्यकता; उस समय सूखा सबसे नुकसानदेह।", te: "పూత (టాసెల్-సిల్క్) సమయంలో అత్యధిక అవసరం; అప్పుడు తడి లేకపోతే ఎక్కువ నష్టం." },
  rainfall: { en: "About 50-75 cm well spread; waterlogging at any stage harms the roots.", hi: "लगभग 50–75 सेमी अच्छी तरह फैली हुई; किसी भी अवस्था में जलभराव जड़ों को नुकसान।", te: "సుమారు 50–75 సెం.మీ సమానంగా పంపిణీ; ఏ దశలోనైనా నీరు నిలిచినా వేర్లకు హాని." },
  temperature: { en: "About 21-30°C; heat above ~35°C at silking reduces grain set.", hi: "लगभग 21–30°C; सिल्क के समय ~35°C से ऊपर गर्मी से दाना बनना घटता है।", te: "సుమారు 21–30°C; సిల్క్ సమయంలో ~35°C పైన వేడితో ధాన్యం ఏర్పాటు తగ్గుతుంది." },
  sunlight: { en: "Full sun; shade reduces cob size.", hi: "पूरी धूप; छाया से भुट्टा छोटा होता है।", te: "పూర్తి ఎండ; నీడలో మొక్కజొన్న చిన్నదవుతుంది." },
  harvestTime: { en: "Husks dry and brown, grains hard and shiny; moisture has dropped well below the milky stage.", hi: "पत्तियाँ (भूसा) सूखकर भूरे, दाने कड़े व चमकदार; दूध-अवस्था से बहुत आगे नमी घट जाती है।", te: "పొట్టు ఆరి గోధుమ రంగు, ధాన్యం గట్టిగా, మెరుస్తూ; పాల దశ దాటి తేమ బాగా తగ్గుతుంది." },
  risks: { en: "Mid-season drought, waterlogging, hail, and storms that lodge tall plants.", hi: "मध्य-मौसम सूखा, जलभराव, ओलावृष्टि, और लंबी फसल गिराने वाले तूफान।", te: "మధ్య సీజన్ ఎండలు, నీరు నిలిచి మునిగిపోవుట, వడగళ్ళ వాన, పొడవాటి మొక్కలను పడగొట్టే తుఫానులు." },
  pests: { en: "Common concerns: fall armyworm, stem borer and cob-damaging pests. Inspect leaves for feeding damage early and follow local extension advice.", hi: "सामान्य चिंताएँ: फॉल आर्मीवर्म, तना छेदक और भुट्टा-कीट। पत्तियों पर कटाव जल्दी जाँचें और स्थानीय कृषि सलाह अपनाएँ।", te: "సాధారణ సమస్యలు: ఫాల్ ఆర్మీ వార్మ్, కాండం తొండు పురుగు, మొక్కజొన్న పురుగులు. ఆకులపై తిన్న గుర్తులు ముందుగానే తనిఖీ చేసి, స్థానిక సలహా పాటించండి." },
  storage: { en: "Shell and dry the cobs well; store dry with good airflow to prevent mold.", hi: "भुट्टे छिलकर अच्छी तरह सुखाएँ; फफूंद रोकने हेतु हवादार सूखी जगह रखें।", te: "మొక్కజొన్న చిప్పలు తీసి బాగా ఆరబెట్టండి; బూజు రాకుండా గాలిసోయే పొడి చోట నిల్వ." },
  tips: { en: "Sow with the first reliable rains, keep fields drained before storms, and harvest before prolonged wet spells.", hi: "पहली भरोसेमंद बारिश में बुवाई करें, तूफान से पहले खेत की निकासी सुनिश्चित करें, और लंबे गीले दौर से पहले कटाई कर लें।", te: "మొదటి నమ్మకమైన వర్షాల్లో విత్తులు వేయండి, తుఫానులకు ముందు నీటి పారుదల చూసుకోండి, సుదీర్ఘ తడి కాలానికి ముందే కోత పూర్తి చేయండి." },
};

FARM_CROP_INFO.groundnut = {
  climate: { en: "Warm (25-30°C) with 50-100 cm of well-distributed rain.", hi: "गर्म (25–30°C) और 50–100 सेमी समवितरित वर्षा।", te: "వెచ్చని (25–30°C) వాతావరణం, 50–100 సెం.మీ సమానంగా పంపిణీ వర్షం." },
  soil: { en: "Light, well-drained sandy or sandy-loam soils — pods need loose soil to develop.", hi: "हल्की, जल-निकासी युक्त बलुई या दोमट मिट्टी — फली विकसित होने हेतु ढीली मिट्टी चाहिए।", te: "లేత, నీరు పారే ఇసుక లేదా ఇసుక-నేలలు — వేరుశనగ కాయలకు లూస్ నేల అవసరం." },
  season: { en: "Kharif (rainfed) is main; summer crops need irrigation.", hi: "खरीफ (वर्षा-आधारित) मुख्य; ग्रीष्म फसल सिंचाई पर।", te: "ఖరీఫ్ (వర్షాధార) ప్రధానం; వేసవి పంటకు సాగునీరు." },
  bestTime: { en: "At monsoon onset so the crop matures before the tail-end rains.", hi: "मानसून आगमन पर, ताकि फसल अंतिम वर्षाओं से पहले पक जाए।", te: "వర్షిత కాలం ప్రారంభంలో — చివరి వర్షాలకు ముందే పంట పండేలా." },
  duration: { en: "About 100-120 days for bunch varieties.", hi: "गुच्छा किस्मों के लिए लगभग 100–120 दिन।", te: "గుత్త రకాలకు సుమారు 100–120 రోజులు." },
  stages: { en: "Emergence → flowering → pegging (pegs push into soil) → pod development → maturity.", hi: "अंकुरण → फूल → पेगिंग (पेग मिट्टी में) → फली विकास → पकना।", te: "మొలకెత్తుట → పూత → పెగ్గింగ్ (పెగ్గులు నేలలోకి) → కాయల పెరుగుదల → పండిన దశ." },
  water: { en: "Moderate and steady; critical at flowering and pegging when pods form underground.", hi: "संतुलित और नियमित; फूल और पेगिंग के समय सबसे महत्वपूर्ण — तभी फलियाँ भूमि के भीतर बनती हैं।", te: "సమతుల్యంగా, క్రమంగా; పూత మరియు పెగ్గింగ్ సమయంలో చాలా కీలకం — అప్పుడే కాయలు నేలలో ఏర్పడతాయి." },
  rainfall: { en: "Even rain is ideal; waterlogging rots pods and a dry spell at pegging cuts yield.", hi: "समान वर्षा आदर्श; जलभराव से फलियाँ सड़ती हैं और पेगिंग के समय सूखा उपज घटाता है।", te: "సమాన వర్షం ఉత్తమం; నీరు నిలిచినా కాయలు కుళ్ళుతాయి, పెగ్గింగ్ సమయంలో తడి లేకుంటే దిగుబడి తగ్గుతుంది." },
  temperature: { en: "About 25-30°C; cool nights slow pod fill.", hi: "लगभग 25–30°C; ठंडी रातें फली भरना धीमा करती हैं।", te: "సుమారు 25–30°C; చల్లని రాత్రులు కాయల నింపుదల నెమ్మదిస్తాయి." },
  sunlight: { en: "Full sun.", hi: "पूरी धूप।", te: "పూర్తి ఎండ." },
  harvestTime: { en: "Leaves yellow, inner shell shows dark veins, pods rattle when shaken.", hi: "पत्तियाँ पीली, भीतरी छिलके पर गहरी नसें, झटकने पर फलियों की खड़-खड़ाहट।", te: "ఆకులు పసుపు, లోపలి పొట్టుపై ముదురు తీగలు, కదిలిస్తే కాయలు చప్పగొడతాయి." },
  risks: { en: "End-of-season drought, waterlogged pods, and rain at harvest causing aflatoxin risk in damp pods.", hi: "मौसम-अंत सूखा, जलभराव से फलियाँ, और कटाई में वर्षा से गीली फलियों में जहरीले फफूंद (एफ्लाटॉक्सिन) का जोखिम।", te: "సీజన్ చివరి ఎండలు, నీరు నిలిచిన కాయలు, కోత సమయంలో వర్షంతో తడి కాయల్లో ఆఫ్లాటాక్సిన్ ప్రమాదం." },
  pests: { en: "Common concerns: leaf miner, white grub and tikka leaf spot. Early inspection of leaves and pods helps.", hi: "सामान्य चिंताएँ: पत्ती सुरंग-कीट, सफेद बीटल (व्हाइट ग्रब) और टिक्का धब्बा। पत्तियों व फलियों की जल्दी जाँच लाभकारी।", te: "సాధారణ సమస్యలు: ఆకు నాటు పురుగు, తెల్ల పురుగు, టిక్కా ఆకు మచ్చ. ఆకులు, కాయలను ముందుగానే పరిశీలించడం మేలు." },
  storage: { en: "Dry pods thoroughly (safe moisture, low) before bagging; store cool, dry and rodent-proof.", hi: "थैलियों में भरने से पहले फलियाँ पूरी तरह सुखाएँ (सुरक्षित नमी); ठंडा, सूखा और चूहा-रहित भंडार।", te: "సంచుల్లో నింపే ముందు కాయలను పూర్తిగా ఆరబెట్టండి (సురక్షిత తేమ తక్కువగా); చల్లని, పొడి, ఎలుకలు రాని చోట నిల్వ." },
  tips: { en: "Harvest on a dry day, dry produce off the wet ground, and never store damp pods — mold risk is serious.", hi: "सूखे दिन कटाई करें, गीली ज़मीन से ऊपर सुखाएँ, और नम फलियाँ कभी न रखें — फफूंद का जोखिम गंभीर है।", te: "పొడి రోజున కోత, తడి నేల పైన కాకుండా ఆరబెట్టడం, తడి కాయలను ఎప్పటికీ నిల్వ చేయకూడదు — బూజు ప్రమాదం తీవ్రం." },
};

FARM_CROP_INFO.wheat = {
  climate: { en: "Cool, dry winter season (rabi); roughly 10-25°C.", hi: "ठंडा, शुष्क शीतकालीन (रबी) मौसम; लगभग 10–25°C।", te: "చల్లని, పొడి శీతాకాల (రబీ) సీజన్; సుమారు 10–25°C." },
  soil: { en: "Fertile well-drained loams; tolerates a wide range.", hi: "उपजाऊ, जल-निकासी युक्त दोमट; विस्तृत श्रेणी सहन करती है।", te: "సారవంతమైన, నీరు పారే నేలలు; విస్తృత శ్రేణికి అనుకూలం." },
  season: { en: "Rabi — sown in autumn as temperatures ease.", hi: "रबी — तापमान घटते ही शरद में बुवाई।", te: "రబీ — ఉష్ణోగ్రత తగ్గుతున్న శరదృతువులో విత్తులు." },
  bestTime: { en: "When daytime temperatures fall to roughly 20-25°C; late sowing shrinks the cool growing window.", hi: "जब दिन का तापमान लगभग 20–25°C तक घटे; देर से बुवाई से ठंडे मौसम की अवधि घटती है।", te: "పగటి ఉష్ణోగ్రత సుమారు 20–25°Cకి తగ్గినప్పుడు; ఆలస్యంగా విత్తిన చల్లని పెరుగుదల కాలం తక్కువ." },
  duration: { en: "About 100-130 days.", hi: "लगभग 100–130 दिन।", te: "సుమారు 100–130 రోజులు." },
  stages: { en: "Emergence → tillering → jointing → booting → heading & flowering → grain fill → ripening.", hi: "अंकुरण → चीरती → गाँठें → बूटिंग → बाली व फूल → दाना भरना → पकना।", te: "మొలకెత్తుట → పిలకలు → గణ్టు దశ → బూటింగ్ → గడ్డ పూత → ధాన్యం నింపుదల → పండిన దశ." },
  water: { en: "Needs irrigation in most of India; critical at crown-root, jointing and grain fill.", hi: "भारत के अधिकांश भागों में सिंचाई आवश्यक; क्राउन-जड़, गाँठें और दाना भरने के समय सबसे महत्वपूर्ण।", te: "భారతంలో చాలా ప్రాంతాల్లో సాగునీరు అవసరం; కిరీట-వేరు, గణ్టు, ధాన్యం నింపుదల సమయాల్లో కీలకం." },
  rainfall: { en: "A dry cool season with controlled irrigation is ideal; unseasonal rain at harvest ruins the grain.", hi: "नियंत्रित सिंचाई के साथ शुष्क ठंडा मौसम आदर्श; कटाई में बिना मौसम की वर्षा अनाज खराब करती है।", te: "నియంత్రిత సాగునీటితో పొడి చల్లని సీజన్ ఉత్తమం; కోత సమయంలో అసమయ వర్షం ధాన్యాన్ని పాడుచేస్తుంది." },
  temperature: { en: "About 10-25°C; heat during grain fill shrivels grains.", hi: "लगभग 10–25°C; दाना भरने के समय गर्मी से दाने सिकुड़ जाते हैं।", te: "సుమారు 10–25°C; ధాన్యం నింపుదల సమయంలో వేడితో గింజలు కుంచిపోతాయి." },
  sunlight: { en: "Full sun; bright days during grain fill help quality.", hi: "पूरी धूप; दाना भरने के दौरान धूपदार दिन गुणवत्ता में मदद।", te: "పూర్తి ఎండ; ధాన్యం నింపుదలలో ఎండ రోజులు నాణ్యతకు మేలు." },
  harvestTime: { en: "Straw golden and dry, grains hard and crumbly — before heat waves or rain arrive.", hi: "पराली सुनहरी-सूखी, दाने कड़े — लू या वर्षा आने से पहले।", te: "పొట్టు బంగారు-పొడి, గింజలు గట్టిగా — వేడి గాలులు లేదా వర్షం రాకముందే." },
  risks: { en: "Terminal heat waves, unseasonal rain/hail at harvest, and frost in the north.", hi: "अंतिम दौर की लू, कटाई में बिना मौसम की वर्षा/ओलावृष्टि, और उत्तर में पाला।", te: "చివరి దశ వేడి గాలులు, కోత సమయంలో అసమయ వర్షం/వడగళ్ళు, ఉత్తరాన మంచు." },
  pests: { en: "Common concerns: rust diseases, termite in light soils and aphids. Watch leaves for yellow rust stripes after cloudy spells.", hi: "सामान्य चिंताएँ: रतुआ रोग, हल्की मिट्टी में दीमक और माहू (चेपा कीट)। बादलों के बाद पत्तियों पर पीले रतुआ धब्बे देखें।", te: "సాధారణ సమస్యలు: తుప్పు తెగుళ్లు, తేలిక నేలల్లో చిమ్మటలు (టెర్మైట్), అఫిడ్లు. మేఘాల తర్వాత ఆకులపై పసుపు తుప్పు గీతలు గమనించండి." },
  storage: { en: "Store well-dried grain in cool, dry, pest-proof conditions.", hi: "अच्छी तरह सूखा अनाज ठंडी, सूखी, कीट-रहित जगह रखें।", te: "బాగా ఆరబెట్టిన ధాన్యాన్ని చల్లని, పొడి, పురుగులు రాని చోట నిల్వ." },
  tips: { en: "Avoid sowing too late, watch for heat waves during grain fill, and harvest promptly in dry weather windows.", hi: "बहुत देर बुवाई न करें, दाना भरने के दौरान लू पर नज़र रखें, और सूखे मौसम में तुरंत कटाई करें।", te: "చాలా ఆలస్యంగా విత్తులు వేయకండి, ధాన్యం నింపుదలలో వేడి గాలులపై నిఘా, పొడి వాతావరణంలో వెంటనే కోత." },
};

FARM_CROP_INFO.sugarcane = {
  climate: { en: "Hot and humid with a long frost-free season; roughly 20-35°C.", hi: "गर्म और आर्द्र, लंबा पाला-रहित मौसम; लगभग 20–35°C।", te: "వేడి, తేమ గల, పొడవైన మంచు లేని సీజన్; సుమారు 20–35°C." },
  soil: { en: "Deep, rich, well-drained loams that can hold moisture.", hi: "गहरी, उपजाऊ, जल-निकासी युक्त दोमट जो नमी रोक सके।", te: "లోతైన, సారవంతమైన, నీరు పారే మరియు తేమ నిలుపుకునే నేలలు." },
  season: { en: "Planted Oct-Nov (autumn) or Feb-Mar (spring) in most cane belts.", hi: "अधिकांश गन्ना-पट्टियों में अक्टूबर–नवंबर (शरद) या फरवरी–मार्च (वसंत) रोपण।", te: "చాలా చెరకు ప్రాంతాల్లో అక్టోబర్–నవంబర్ (శరదృతువు) లేదా ఫిబ్రవరి–మార్చి (వసంత) నాటడం." },
  bestTime: { en: "When soil is warm and moist — before monsoon or with irrigation.", hi: "मिट्टी गर्म व नम हो — मानसून से पहले या सिंचाई के साथ।", te: "నేల వెచ్చగా, తడిగా ఉన్నప్పుడు — వర్షానికి ముందు లేదా సాగునీటితో." },
  duration: { en: "About 10-12 months (varies by region and variety).", hi: "लगभग 10–12 महीने (क्षेत्र व किस्म के अनुसार)।", te: "సుమారు 10–12 నెలలు (ప్రాంతం, రకాన్ని బట్టి)." },
  stages: { en: "Germination → tillering → grand growth (the main cane-building phase) → maturity.", hi: "अंकुरण → चीरती → तीव्र वृद्धि (गन्ना बनने की मुख्य अवस्था) → पकना।", te: "మొలకెత్తుట → పిలకలు → తీవ్ర వృద్ధి (చెరకు పొడవు పెరిగే ముఖ్య దశ) → పండిన దశ." },
  water: { en: "A heavy, long-season water demand — usually irrigated; drip keeps moisture even.", hi: "लंबे मौसम की भारी पानी की मांग — प्रायः सिंचित; ड्रिप से समान नमी मिलती है।", te: "పొడవైన సీజన్‌కు అధిక నీటి అవసరం — సాధారణంగా సాగునీరు; డ్రిప్‌తో సమాన తేమ." },
  rainfall: { en: "Around 75-120 cm well spread; dry spells during grand growth slow cane build-up.", hi: "लगभग 75–120 सेमी अच्छी तरह फैली; तीव्र वृद्धि में सूखा से गन्ना वृद्धि धीमी।", te: "సుమారు 75–120 సెం.మీ సమానంగా; తీవ్ర వృద్ధి సమయంలో తడి ఆరితే చెరకు పెరుగుదల ఆలస్యం." },
  temperature: { en: "About 20-35°C; growth nearly stops below ~15°C.", hi: "लगभग 20–35°C; ~15°C से नीचे वृद्धि लगभग रुक जाती है।", te: "సుమారు 20–35°C; ~15°C కంటే తక్కువైతే పెరుగుదల దాదాపు ఆగిపోతుంది." },
  sunlight: { en: "Full sun for the whole long season.", hi: "पूरे लंबे मौसम पूरी धूप।", te: "పొడవైన సీజన్ మొత్తం పూర్తి ఎండ." },
  harvestTime: { en: "Canes heavy and juicy, lower leaves drying, sugar peak confirmed with a local test.", hi: "गन्ने भारी और रसीले, निचली पत्तियाँ सूखने लगें, मिठास चरम पर — स्थानीय जाँच से पुष्टि।", te: "చెరకు బరువుగా, రసంతో నిండి, కింది ఆకులు ఆరిపోవుట, తీపి శిఖరం — స్థానిక పరీక్షతో నిర్ధారణ." },
  risks: { en: "Cyclonic winds and storms flattening tall cane, drought during grand growth, waterlogging, and frost at the margins.", hi: "चक्रवाती हवाएँ और तूफान लंबे गन्ने गिराते हैं, तीव्र वृद्धि में सूखा, जलभराव, और सीमांत क्षेत्रों में पाला।", te: "తుఫాను గాలులతో పొడవాటి చెరకు పడిపోవుట, తీవ్ర వృద్ధిలో ఎండలు, నీరు నిలిచి మునిగిపోవుట, అంచు ప్రాంతాల్లో మంచు." },
  pests: { en: "Common concerns: early shoot borer, top borer and red rot disease. Use disease-free seed setts and inspect shoots regularly.", hi: "सामान्य चिंताएँ: प्रारंभिक अंकुर छेदक, शीर्ष छेदक और लाल सड़न रोग। रोग-मुक्त बीज-कलम उपयोग करें और अंकुरों की नियमित जाँच करें।", te: "సాధారణ సమస్యలు: ప్రారంభ రెబ్బ తొండు పురుగు, పై తొండు పురుగు, ఎర్ర కుళ్ళు తెగులు. వ్యాధి లేని విత్తన పొదలు వాడి, రెబ్బలను క్రమం తప్పకు పరిశీలించండి." },
  storage: { en: "Cane is best milled promptly; if stored, keep shaded, moist and off the ground — do not let it dry out.", hi: "गन्ना शीघ्र कुटाई सर्वोत्तम; रखना हो तो छाया में, नम और ज़मीन से ऊपर — सूखने न दें।", te: "చెరకును వీలైనంత త్వరగా నూరించడం ఉత్తమం; నిల్వ అవసరమైతే నీడలో, తడిగా, నేల నుండి ఎత్తుగా — ఆరిపోనివ్వకండి." },
  tips: { en: "Stake or bank up cane before storm season where winds are strong, irrigate steadily through grand growth, and cut before any forecast cyclone.", hi: "तेज़ हवाओं वाले क्षेत्रों में तूफान-मौसम से पहले गन्ने को मिट्टी से बाँधें/घेरें, तीव्र वृद्धि में नियमित सिंचाई, और चक्रवात की सूचना पर पहले कटाई।", te: "బలిష్ఠ గాలులున్న ప్రాంతాల్లో తుఫాను సీజన్‌కు ముందు చెరకును మట్టితో ఏర్పాటు చేయండి, తీవ్ర వృద్ధిలో క్రమం తప్పకు నీరు, తుఫాను అంచనా వుంటే ముందుగానే కోత." },
};

function farmLang() {
  return state.voiceLang === "hi" ? "hi" : state.voiceLang === "te" ? "te" : "en";
}

// Fallback stage catalogue mirroring backend farm_advisor.STAGES — used
// only if /api/crops is unreachable (IDs and localized names identical).
const FARM_STAGE_FALLBACK = [
  { id: "sowing", names: { en: "Sowing", hi: "बुवाई", te: "విత్తులు వేయడం" }, emoji: "🌱" },
  { id: "growing", names: { en: "Growing", hi: "बढ़ती अवस्था", te: "పెరుగుదల దశ" }, emoji: "🌿" },
  { id: "flowering", names: { en: "Flowering", hi: "फूल आना", te: "పూల దశ" }, emoji: "🌼" },
  { id: "harvesting", names: { en: "Harvesting", hi: "कटाई", te: "కోత" }, emoji: "🚜" },
];

// Live crop/stage catalogue from /api/crops (stable IDs + localized
// names). Cached for the session; falls back to FARM_GUIDE_CROPS and
// FARM_STAGE_FALLBACK, which mirror the backend definitions.
let farmCatalogue = null;

async function loadFarmCatalogue() {
  if (farmCatalogue) return farmCatalogue;
  try {
    const data = await getJSON("/api/crops");
    if (data && Array.isArray(data.crops) && Array.isArray(data.stages) && data.crops.length > 0) {
      farmCatalogue = data;
    }
  } catch (err) {
    console.warn("Crop catalogue unavailable, using bundled fallback:", err);
  }
  return farmCatalogue;
}

function localizedFarmName(kind, id, lang) {
  const list = farmCatalogue
    ? (kind === "crop" ? farmCatalogue.crops : farmCatalogue.stages)
    : (kind === "crop" ? FARM_GUIDE_CROPS : FARM_STAGE_FALLBACK);
  const entry = list.find((e) => e.id === id);
  if (!entry) return id;
  return entry.names[lang] || entry.names.en;
}

// Rebuild both <select> option lists with localized display labels.
// Option VALUES stay the stable internal IDs the API contract expects,
// and the user's current selection is preserved across re-renders.
function populateFarmSelectors(lang) {
  const cropSel = els.farmCropSelect;
  const stageSel = els.farmStageSelect;
  if (!cropSel || !stageSel) return;
  const t = FARM_I18N[lang] || FARM_I18N.en;
  const keepCrop = cropSel.value;
  const keepStage = stageSel.value;
  const crops = (farmCatalogue && farmCatalogue.crops) || FARM_GUIDE_CROPS;
  const stages = (farmCatalogue && farmCatalogue.stages) || FARM_STAGE_FALLBACK;

  cropSel.innerHTML = crops
    .map((c) => `<option value="${c.id}">${c.emoji || ""} ${escapeHTML(c.names[lang] || c.names.en)}</option>`)
    .join("");
  stageSel.innerHTML = stages
    .map((s) => `<option value="${s.id}">${s.emoji || ""} ${escapeHTML(s.names[lang] || s.names.en)}</option>`)
    .join("");

  if ([...cropSel.options].some((o) => o.value === keepCrop)) cropSel.value = keepCrop;
  if ([...stageSel.options].some((o) => o.value === keepStage)) stageSel.value = keepStage;

  const cropLabel = document.getElementById("farm-crop-label");
  const stageLabel = document.getElementById("farm-stage-label");
  if (cropLabel) cropLabel.textContent = t.cropLabel;
  if (stageLabel) stageLabel.textContent = t.stageLabel;
  const cropHelp = document.getElementById("farm-crop-help");
  const stageHelp = document.getElementById("farm-stage-help");
  if (cropHelp) cropHelp.textContent = t.cropHelp;
  if (stageHelp) stageHelp.textContent = t.stageHelp;
}

// Static farm-tab chrome: headline (before any advisory lands),
// subtitle, empty state, list aria-label, and the visual guide shell.
function applyFarmStaticText(lang) {
  const t = FARM_I18N[lang] || FARM_I18N.en;
  if (els.farmTitle && !state.farmAdvice) els.farmTitle.textContent = t.title;
  if (els.farmSubtitle) els.farmSubtitle.textContent = t.subtitle;
  if (els.farmEmptyState) els.farmEmptyState.textContent = t.emptyState;
  if (els.farmAdviceCards) els.farmAdviceCards.setAttribute("aria-label", t.advisory);
  if (els.farmGuideTitle) els.farmGuideTitle.textContent = t.guideTitle;
  if (els.farmGuideSubtitle) els.farmGuideSubtitle.textContent = t.guideSubtitle;
  if (els.farmGuideCropsHeading) els.farmGuideCropsHeading.textContent = t.guideCropsHeading;
  if (els.farmGuidePracticesHeading) els.farmGuidePracticesHeading.textContent = t.guidePracticesHeading;
  if (els.farmGuidePhotoCredit) els.farmGuidePhotoCredit.textContent = t.guidePhotoCredit;
}

// Replace a failed remote photo with a themed icon placeholder so the
// card keeps its layout — no broken-image glyph, no layout shift.
function farmGuidePhotoFailed(img) {
  const fig = img.closest("figure");
  if (!fig) return;
  const fallback = document.createElement("div");
  fallback.className = "farm-guide-photo-fallback";
  fallback.setAttribute("aria-hidden", "true");
  fallback.innerHTML = '<span class="material-symbols-outlined">image</span>';
  img.replaceWith(fallback);
}

function farmGuideCard(entry, title, sub, altText, opts = {}) {
  // Crop cards render as real <button>s (phrasing content only) so
  // keyboard/touch activation, focus and a11y semantics come for free.
  if (opts.button) {
    return `
    <button type="button" class="farm-guide-card is-action" data-crop-info="${escapeHTML(entry.id)}" aria-haspopup="dialog" aria-label="${escapeHTML(title)}">
      <img class="farm-guide-photo is-loading" src="${entry.photo}" alt="${escapeHTML(altText)}"
        loading="lazy" decoding="async" width="640" height="480"
        onload="this.classList.remove('is-loading')"
        onerror="farmGuidePhotoFailed(this)">
      <span class="farm-guide-caption">
        <span class="farm-guide-title">${entry.emoji || ""} ${escapeHTML(title)}</span>
        ${sub ? `<span class="farm-guide-sub">${escapeHTML(sub)}</span>` : ""}
        ${opts.hint ? `<span class="farm-guide-hint" aria-hidden="true">${escapeHTML(opts.hint)}</span>` : ""}
      </span>
    </button>`;
  }
  return `
    <figure class="farm-guide-card" data-guide-key="${escapeHTML(entry.id || entry.key || "")}">
      <img class="farm-guide-photo is-loading" src="${entry.photo}" alt="${escapeHTML(altText)}"
        loading="lazy" decoding="async" width="640" height="480"
        onload="this.classList.remove('is-loading')"
        onerror="farmGuidePhotoFailed(this)">
      <figcaption class="farm-guide-caption">
        <span class="farm-guide-title">${entry.emoji || ""} ${escapeHTML(title)}</span>
        ${sub ? `<span class="farm-guide-sub">${escapeHTML(sub)}</span>` : ""}
      </figcaption>
    </figure>`;
}

// Render the visual guide grids in the selected language. Crop cards
// show the localized crop name; practice cards add a one-line, claim-
// free description of what the practice is.
function renderFarmGuide(lang) {
  const cropGrid = els.farmGuideCropGrid;
  const practiceGrid = els.farmGuidePracticeGrid;
  if (!cropGrid || !practiceGrid) return;
  const t = FARM_I18N[lang] || FARM_I18N.en;
  cropGrid.innerHTML = FARM_GUIDE_CROPS.map((c) => {
    const name = c.names[lang] || c.names.en;
    return farmGuideCard(c, name, "", `${t.guideAltPrefix} ${name}`, { button: true, hint: t.guideCardHint });
  }).join("");
  practiceGrid.innerHTML = FARM_GUIDE_PRACTICES.map((p) => {
    const name = p.names[lang] || p.names.en;
    return farmGuideCard(p, name, p.desc[lang] || p.desc.en, `${t.guideAltPrefix} ${name}`);
  }).join("");
}

// ============================================================
// CROP INFO PANEL — opens when a "Know your field" crop card is
// activated. Localized live (EN/HI/TE), themed via app tokens, fully
// keyboard accessible (Tab cycle, Esc, backdrop click, visible Close),
// and responsive: bottom sheet on phones, centered dialog ≥sm.
// Focus is trapped while open; the opener regains focus on close.
// ============================================================

const cropInfoState = {
  open: false,
  cropId: null,
  lang: "en",
  lastFocus: null,
};

function cropInfoEls() {
  return {
    overlay: document.getElementById("crop-info-overlay"),
    panel: document.getElementById("crop-info-panel"),
    backdrop: document.getElementById("crop-info-backdrop"),
    closeBtn: document.getElementById("crop-info-close-btn"),
    emoji: document.getElementById("crop-info-emoji"),
    title: document.getElementById("crop-info-title"),
    subtitle: document.getElementById("crop-info-subtitle"),
    body: document.getElementById("crop-info-body"),
  };
}

function cropInfoBodyRow(label, value) {
  return `
    <div class="crop-info-row">
      <span class="crop-info-label">${escapeHTML(label)}</span>
      <p class="crop-info-value">${escapeHTML(value)}</p>
    </div>`;
}

function cropInfoTipRow(label, value) {
  return `
    <div class="crop-info-row crop-info-row-tip">
      <span class="crop-info-label">${escapeHTML(label)}</span>
      <p class="crop-info-value">${escapeHTML(value)}</p>
    </div>`;
}

function openCropInfo(cropId) {
  const { overlay, panel, closeBtn, emoji, title, subtitle, body } = cropInfoEls();
  const info = FARM_CROP_INFO[cropId];
  const cropMeta = FARM_GUIDE_CROPS.find((c) => c.id === cropId);
  if (!overlay || !panel || !info || !cropMeta) return;

  const lang = farmLang();
  const t = FARM_I18N[lang] || FARM_I18N.en;
  cropInfoState.open = true;
  cropInfoState.cropId = cropId;
  cropInfoState.lang = lang;
  cropInfoState.lastFocus = document.activeElement;

  emoji.textContent = cropMeta.emoji || "🌾";
  title.textContent = cropMeta.names[lang] || cropMeta.names.en;
  subtitle.textContent = t.infoSubtitle;
  closeBtn.setAttribute("aria-label", t.infoClose);
  closeBtn.title = t.infoClose;

  if (body) {
    body.innerHTML = FARM_INFO_FIELDS.map((f) => {
      const entry = info[f];
      if (!entry) return "";
      const value = entry[lang] || entry.en;
      return f === "tips" ? cropInfoTipRow(t.infoLabels[f], value) : cropInfoBodyRow(t.infoLabels[f], value);
    }).join("");
    body.scrollTop = 0;
  }

  overlay.classList.remove("hidden");
  // Double rAF lets the browser paint the hidden→shown state before the
  // slide-up transition starts, so the animation always plays.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      overlay.classList.add("is-open");
      panel.classList.remove("translate-y-full");
    });
  });
  document.body.style.overflow = "hidden";
  closeBtn.focus();
}

function closeCropInfo() {
  const { overlay, panel } = cropInfoEls();
  if (!overlay || !cropInfoState.open) return;
  cropInfoState.open = false;
  overlay.classList.remove("is-open");
  panel.classList.add("translate-y-full");
  document.body.style.overflow = "";
  const done = () => {
    overlay.classList.add("hidden");
    if (cropInfoState.lastFocus && document.contains(cropInfoState.lastFocus)) {
      cropInfoState.lastFocus.focus();
    }
    cropInfoState.lastFocus = null;
  };
  if (prefersReducedMotion()) done();
  else setTimeout(done, 320); // match the panel transition duration
}

function initCropInfo() {
  const { overlay, backdrop, closeBtn, panel } = cropInfoEls();
  if (!overlay || overlay.dataset.bound) return;
  overlay.dataset.bound = "1";
  overlay.addEventListener("click", (e) => {
    if (e.target === backdrop) closeCropInfo();
  });
  closeBtn.addEventListener("click", closeCropInfo);
  document.addEventListener("keydown", (e) => {
    if (!cropInfoState.open) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeCropInfo();
      return;
    }
    if (e.key === "Tab") {
      // Focus trap: keep Tab cycling inside the open panel.
      const focusables = panel.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
      const list = [...focusables].filter((el) => el.offsetParent !== null);
      if (list.length === 0) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });
  // Delegate clicks on guide crop cards (works across re-renders).
  if (els.farmGuideCropGrid) {
    els.farmGuideCropGrid.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-crop-info]");
      if (btn) openCropInfo(btn.dataset.cropInfo);
    });
  }
}

function cropInfoOpenFor(cropId) {
  openCropInfo(cropId);
}

function cropInfoCloseFor(cropId) {
  closeCropInfo();
}

// ============================================================
// I18N STRINGS FOR THE SMART RAIN ALERT + RAIN TIMELINE UI
// (the backend computes the evidence; the client renders it in the
//  selected language so switching is instant, no refetch needed)
// ============================================================

// Must mirror RAIN_ALERT_HORIZON_H in backend/app.py (track width).
const R18_HOURS = 18;

const RAIN_I18N = {
  en: {
    alertTitle: "Smart Rain Alert",
    badge: "Live",
    rainIn: (h, peak) => `Rain expected ${h} — peak chance ${peak}%. Carry an umbrella.`, // h = "in 3h (14:00)"
    rainNow: (dur, peak) => `Rain likely now for about ${dur}h — peak chance ${peak}%. Carry an umbrella.`,
    inHours: (h) => `in ${h}h`,
    peak: (p) => `Peak ${p}%`,
    window: (s, e, d) => `Rain window ${s}–${e} (${d}h)`,
    noRain: "No rain expected in the next 18 hours.",
    alertDaily: (p) => `Heavy rain probability today: ${p}% likelihood of rain.`,
    trackLabel: "Rain probability over the next 18 hours",
    nowLabel: "now",
    endLabel: "+18h",
  },
  hi: {
    alertTitle: "स्मार्ट बारिश अलर्ट",
    badge: "लाइव",
    rainIn: (h, peak) => `${h} बारिश संभावित — अधिकतम संभावना ${peak}%। छाता साथ रखें।`, // h = "3 घंटे बाद (14:00)"
    rainNow: (dur, peak) => `अभी लगभग ${dur} घंटे बारिश संभावित — अधिकतम संभावना ${peak}%। छाता साथ रखें।`,
    inHours: (h) => `${h} घंटे बाद`,
    peak: (p) => `अधिकतम ${p}%`,
    window: (s, e, d) => `बारिश की विंडो ${s}–${e} (${d} घंटे)`,
    noRain: "अगले 18 घंटों में बारिश की संभावना नहीं है।",
    alertDaily: (p) => `आज भारी बारिश की संभावना: ${p}%।`,
    trackLabel: "अगले 18 घंटों की वर्षा संभावना",
    nowLabel: "अभी",
    endLabel: "+18 घंटे",
  },
  te: {
    alertTitle: "స్మార్ట్ వర్ష హెచ్చరిక",
    badge: "లైవ్",
    rainIn: (h, peak) => `${h} వర్షం సాధ్యమే — గరిష్ఠ అవకాశం ${peak}%। గొడుగు తీసుకోండి।`, // h = "3 గంటల్లో (14:00)"
    rainNow: (dur, peak) => `ఇప్పుడే ${dur} గంటలు వర్షం సాధ్యమే — గరిష్ఠ అవకాశం ${peak}%। గొడుగు తీసుకోండి।`,
    inHours: (h) => `${h} గంటల్లో`,
    peak: (p) => `గరిష్ఠ ${p}%`,
    window: (s, e, d) => `వర్ష కాలం ${s}–${e} (${d} గంటలు)`,
    noRain: "తదుపరి 18 గంటల్లో వర్షం అవకాశం లేదు.",
    alertDaily: (p) => `ఈరోజు భారీ వర్ష అవకాశం: ${p}%.`,
    trackLabel: "తదుపరి 18 గంటల వర్ష అవకాశం",
    nowLabel: "ఇప్పుడు",
    endLabel: "+18 గం.",
  },
};

// ============================================================
// UI CHROME I18N — header, top controls, navigation, sections and
// modals translate IMMEDIATELY on language switch (no refresh). Static
// markup carries data-i18n keys; dynamic surfaces re-render separately.
// ============================================================

// The dictionaries live in frontend/i18n.js (bundled locally, preloaded
// before this script) — switching language never touches the network.

// Dictionary lookup with graceful English fallback; a missing key can
// never break a render.
function T(key) {
  const dicts = window.CHROME_I18N;
  if (!dicts) return "";
  return (dicts[farmLang()] && dicts[farmLang()][key]) ?? (dicts.en && dicts.en[key]) ?? "";
}

// Template lookup: replaces {n}/{d}/{m}/{s}/{t}/{a}/{b}/{loc} placeholders.
function tf(key, vars = {}) {
  let s = T(key);
  for (const [k, v] of Object.entries(vars)) s = s.split("{" + k + "}").join(String(v));
  return s;
}

function applyChromeI18n() {
  const t = CHROME_I18N[farmLang()] || CHROME_I18N.en;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const v = t[el.dataset.i18n];
    if (v !== undefined) el.textContent = v;
  });
  // Attribute translations (e.g. location button title/aria-label).
  document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
    for (const pair of el.dataset.i18nAttr.split(",")) {
      const [attr, key] = pair.split(":").map((s) => s.trim());
      const v = t[key];
      if (attr && key && v !== undefined) el.setAttribute(attr, v);
    }
  });
  const searchInput = document.getElementById("city-search-input");
  if (searchInput) searchInput.placeholder = t.searchPlaceholder;
  const unifiedInput = document.getElementById("unified-query-input");
  if (unifiedInput) {
    unifiedInput.placeholder = t.askGpt;
    unifiedInput.setAttribute("aria-label", t.askGpt);
  }
  const satNote = document.getElementById("satellite-note");
  if (satNote) satNote.textContent = t.satNote;
  const satHelp = document.getElementById("satellite-help");
  if (satHelp) satHelp.textContent = t.satHelp;
}

function rainLang() {
  return farmLang(); // same EN/HI/TE mapping as the farm advisor
}

// Smart Rain Alert in the current UI language, rebuilt from the real
// rain timeline evidence (same numbers the backend audited).
function smartRainAlertLocalized() {
  const tl = state.rainTimeline;
  if (!tl || !tl.has_event) return null;
  const t = RAIN_I18N[rainLang()];
  const startsIn = tl.starts_in_h || 0;
  const dur = tl.duration_h || 0;
  const peak = tl.peak_probability;
  let text;
  if (startsIn <= 1) {
    text = t.rainNow(dur, peak);
  } else {
    // starts_in_h >= 2 here; the relative phrase embeds the clock time too.
    text = t.rainIn(`${t.inHours(startsIn)} (${tl.start_label})`, peak);
  }
  return {
    severity: "Advisory",
    type: "Smart Rain Alert",
    text,
    snapshot: { starts_in_h: startsIn, duration_h: dur, peak_probability: peak, start_iso: tl.start_iso },
  };
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

// Localized condition titles — same real WMO categories, worded per UI
// language. Falls back to the English map for unlisted keys.
const CONDITION_TITLES_I18N = {
  hi: {
    "clear-day": "साफ़ आसमान", "clear-night": "साफ़ आसमान",
    "partly-cloudy-day": "आंशिक बादल", "partly-cloudy-night": "आंशिक बादल",
    "cloudy": "बादल छाए", "fog": "घना कोहरा", "drizzle": "बूँदाबाँदी",
    "rain": "वर्षा", "snow": "हिमपात", "thunder": "आंधी-तूफ़ान",
  },
  te: {
    "clear-day": "స్పష్టమైన ఆకాశం", "clear-night": "స్పష్టమైన ఆకాశం",
    "partly-cloudy-day": "పాక్షికంగా మేఘావృతం", "partly-cloudy-night": "పాక్షికంగా మేఘావృతం",
    "cloudy": "మేఘావృతం", "fog": "దట్టమైన పొగమంచు", "drizzle": "జల్లు",
    "rain": "వర్షం", "snow": "మంచు", "thunder": "ఉరుముల తుఫాను",
  },
};

function conditionTitleFor(cat) {
  const localized = CONDITION_TITLES_I18N[farmLang()];
  return (localized && localized[cat]) || CONDITION_TITLES[cat] || "Clear";
}

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

// ============================================================
// ATMOSPHERE FX STATE — drives the premium depth layer (#sky-fx).
// All inputs come from the REAL condition category; the density
// tiers map to how much cloud/depth each sky should carry.
// ============================================================

const SKY_FX_STATE = {
  // fair = clear / partly cloudy (gets sun glow + rays);
  // cloudy/rain/drizzle/thunder/snow/fog get clouds, snow gets sparkle.
  skyKind(condition) {
    if (condition === "clear-day" || condition === "clear-night" ||
        condition === "partly-cloudy-day" || condition === "partly-cloudy-night") return "fair";
    return condition; // cloudy | rain | drizzle | thunder | snow | fog
  },
  clouds(condition) {
    // Condition purity: clouds only on skies that actually have clouds.
    // Clear skies stay clean (no drifting band above the hero); hot/cold
    // bands inherit the fair-day gating (also 0). Partly cloudy keeps
    // only the whisper tier; snow gets a whisper under the sparkle.
    if (condition === "partly-cloudy-day" || condition === "partly-cloudy-night") return "1";
    if (condition === "cloudy" || condition === "fog") return "2";
    if (condition === "rain" || condition === "drizzle" || condition === "thunder") return "3";
    if (condition === "snow") return "1";
    return "0";
  },
};

function applySkyFx(condition, isDay = 1) {
  const fx = document.getElementById("sky-fx");
  if (!fx) return;
  fx.dataset.sky = SKY_FX_STATE.skyKind(condition);
  fx.dataset.clouds = SKY_FX_STATE.clouds(condition);
  fx.dataset.day = isDay !== 0 ? "1" : "0";
}

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

function syncRainFxTheme() {
  const layer = document.querySelector("#rain-fx .rain-fx-layer");
  if (!layer) return;
  const dark = document.documentElement.dataset.theme === "dark";
  layer.style.setProperty(
    "--rain-color",
    dark ? "rgba(205, 224, 245, 0.8)" : null // null -> CSS tier default
  );
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
  const onRmChange = () => {
    applyPrecipFx(document.body.dataset.condition || "clear-day");
    // Scene pools follow: static mode parks drops/ripples/stars.
    applySceneFx(document.body.dataset.condition || "clear-day", sceneFx.lastIsDay);
    // Card scene pools follow: static mode parks in-card particles.
    applyCardSceneFx(document.body.dataset.condition || "clear-day", sceneFx.lastIsDay, cardScene.lastTempBand);
  };
  if (rmQuery.addEventListener) rmQuery.addEventListener("change", onRmChange);
  else if (rmQuery.addListener) rmQuery.addListener(onRmChange);
}

// ============================================================
// WEATHER SCENE ANIMATIONS — illustrated scene layer merged into the
// Living Sky (#weather-scene), adapted from the standalone "Weather
// Scene Animations" mock (sun orb + rotating rays, drifting SVG
// clouds, storm cloud + drop/ripple pools, moon + twinkling stars).
// The scene is chosen from the REAL condition category; the mock's
// per-scene backgrounds are intentionally NOT ported — the existing
// Living Sky gradients + rain-fx/lightning-fx stay authoritative and
// this layer only adds the illustrated elements on top. Pools are
// seeded lazily ONCE (nodes persist across scene switches; hidden
// parts simply stop compositing via display:none). Dark theme remaps
// the sun scene to night. Reduced-motion users get instant switches
// and static pools.
// ============================================================

const sceneFx = {
  current: null,        // scene currently applied ("sun"|"clouds"|"rain"|"night")
  lastIsDay: 1,         // last seen daylight flag (for reduced-motion rebuilds)
  seededDrops: false,   // pool built at least once?
  seededDropCount: 0,   // intensity of the current pool (reseed on change)
  seededRipples: false,
  seededStars: false,
};

const SCENE_BY_CONDITION = {
  "clear-day": "sun",
  "partly-cloudy-day": "clouds",
  "cloudy": "clouds",
  "fog": "clouds",
  "drizzle": "rain",
  "rain": "rain",
  "thunder": "rain",
  "clear-night": "night",
  "partly-cloudy-night": "night",
};

function sceneForCondition(condition, isDay = 1) {
  const mapped = SCENE_BY_CONDITION[condition];
  if (mapped) return mapped;
  // Snow and the hot/cold temp-band skies keep the existing snow
  // sparkle / painted gradients authoritative; the illustrated layer
  // only covers the four mock scenes.
  return isDay !== 0 ? "sun" : "night";
}

function seedSceneDrops(count = 70, lenMin = 22, lenSpan = 30) {
  const field = document.getElementById("scene-drop-field");
  if (!field) return;
  // Intensity-aware: reseed only when the requested count changes so the
  // DOM stays bounded (max 95 nodes) across condition transitions.
  if (sceneFx.seededDrops && sceneFx.seededDropCount === count) return;
  if (sceneFx.seededDrops) field.textContent = ""; // clear previous pool
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const drop = document.createElement("div");
    drop.className = "scene-drop";
    const length = lenMin + Math.random() * lenSpan; // longer = heavier rain
    const duration = 0.45 + Math.random() * 0.5;     // s to fall (mock values)
    drop.style.left = (Math.random() * 104).toFixed(2) + "%";
    drop.style.height = length.toFixed(1) + "px";
    drop.style.opacity = (0.4 + Math.random() * 0.5).toFixed(2);
    drop.style.animationDuration = duration.toFixed(2) + "s";
    drop.style.animationDelay = (-Math.random() * 1.2).toFixed(2) + "s";
    frag.appendChild(drop);
  }
  field.appendChild(frag);
  sceneFx.seededDrops = true;
  sceneFx.seededDropCount = count;
}

function seedSceneRipples() {
  const pool = document.getElementById("scene-ripple-pool");
  if (!pool || sceneFx.seededRipples) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < 10; i++) {
    const ripple = document.createElement("div");
    ripple.className = "scene-ripple";
    ripple.style.left = (Math.random() * 90).toFixed(2) + "%";
    ripple.style.animationDuration = (1.1 + Math.random() * 0.8).toFixed(2) + "s";
    ripple.style.animationDelay = (-Math.random() * 2).toFixed(2) + "s";
    frag.appendChild(ripple);
  }
  pool.appendChild(frag);
  sceneFx.seededRipples = true;
}

function seedSceneStars() {
  const field = document.getElementById("scene-star-field");
  if (!field || sceneFx.seededStars) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < 40; i++) {
    const star = document.createElement("div");
    star.className = "scene-star";
    const size = 1 + Math.random() * 2;
    star.style.width = size.toFixed(2) + "px";
    star.style.height = size.toFixed(2) + "px";
    star.style.top = (Math.random() * 70).toFixed(2) + "%";
    star.style.left = (Math.random() * 100).toFixed(2) + "%";
    star.style.animationDelay = (-Math.random() * 3).toFixed(2) + "s";
    frag.appendChild(star);
  }
  field.appendChild(frag);
  sceneFx.seededStars = true;
}

// Public entry: pick the scene for the REAL condition and apply it.
// Called from renderForecastScreen with the live payload and at boot.
function applySceneFx(condition, isDay = 1) {
  const host = document.getElementById("weather-scene");
  if (!host) return;
  sceneFx.lastIsDay = isDay !== 0 ? 1 : 0;
  const wanted = sceneForCondition(condition, isDay);
  // Dark theme: sun → night (a bright orb on navy skies reads wrong;
  // moon + stars match the moonlit palette).
  const scene = currentTheme() === "dark" && wanted === "sun" ? "night" : wanted;
  // Intensity-aware drop pool lives BEFORE the scene-change early return:
  // drizzle/rain/thunder all map to the "rain" scene, so intensity must
  // reseed even when the scene itself doesn't change. Reduced motion:
  // the existing rain-fx static streak field already covers precipitation
  // — skip the animated scene pools entirely.
  if (scene === "rain" && !prefersReducedMotion()) {
    if (condition === "drizzle") seedSceneDrops(40, 22, 30);      // light
    else if (condition === "thunder") seedSceneDrops(95, 44, 36); // heavy
    else seedSceneDrops(70, 50, 36);                              // steady
    seedSceneRipples();
  }
  if (host.dataset.scene === scene && sceneFx.current === scene) return;
  host.dataset.scene = scene;
  sceneFx.current = scene;
  if (scene === "night") seedSceneStars(); // stars render fine static
}

// If the theme flips mid-session, remap sun→night / night→sun so the
// illustrated layer always matches the active theme's palette.
function syncSceneFxTheme() {
  if (!sceneFx.current) return;
  applySceneFx(document.body.dataset.condition || "clear-day", sceneFx.lastIsDay);
}

// ============================================================
// CARD SCENE — animated weather scene clipped entirely inside the
// hero card (#card-scene). Driven by the SAME REAL condition as the
// page Living Sky (never fabricated): each condition gets its own
// in-card sky gradient + matching animated elements (sun, clouds,
// rain, storm+lightning, mist, snow, moon+stars). The layer paints
// below the card's content (z-0 vs z-10) and never touches the page
// background layers, so the two scenes stay fully separate.
// Perf: pooled drops/flakes/stars seeded lazily and reused across
// switches (DOM bounded), transform/opacity-only animations, zero
// per-frame JS. Reduced-motion parks the pools static inside the
// card (the page rain-fx static field does not cover the card).
// ============================================================

const CARD_SKY_CLASS_BY_CONDITION = {
  "clear-day": "cs-clear-day",
  "clear-night": "cs-clear-night",
  "partly-cloudy-day": "cs-partly-cloudy-day",
  "partly-cloudy-night": "cs-partly-cloudy-night",
  "cloudy": "cs-cloudy",
  "fog": "cs-fog",
  "drizzle": "cs-drizzle",
  "rain": "cs-rain",
  "snow": "cs-snow",
  "thunder": "cs-thunder",
};

const CARD_SKY_CLASS_BY_TEMP_BAND = {
  hot: "cs-hot",
  cold: "cs-cold",
};

// Fair skies keep the page scene's rule: temperature bands override
// clear/partly-cloudy only; rain/snow/fog/storm carry their own mood.
function cardSkyClassFor(condition, tempBand = "mild") {
  if ((tempBand === "hot" || tempBand === "cold") && (condition === "clear-day" || condition === "partly-cloudy-day")) {
    return CARD_SKY_CLASS_BY_TEMP_BAND[tempBand];
  }
  return CARD_SKY_CLASS_BY_CONDITION[condition] || CARD_SKY_CLASS_BY_CONDITION["clear-day"];
}

// 7 illustrated scenes: rain and drizzle share the rain scene,
// thunder gets the storm scene with its gated flash, everything
// else maps 1:1 to its scene (snow and mist included).
const CARD_SCENE_BY_CONDITION = {
  "clear-day": "sun",
  "partly-cloudy-day": "clouds",
  "cloudy": "clouds",
  "fog": "mist",
  "drizzle": "rain",
  "rain": "rain",
  "snow": "snow",
  "thunder": "storm",
};

const cardScene = {
  front: 0,           // which cs-sky layer is live (crossfade alternation)
  retireTimer: null,
  currentSky: null,
  currentScene: null,
  lastIsDay: 1,
  lastTempBand: "mild",
  lastCondition: "clear-day",
  seededDrops: false,
  seededDropCount: 0,
  seededRipples: false,
  seededStormDrops: false,
  seededStormRipples: false,
  seededSnow: false,
  seededStars: false,
};

function seedCardDropsInto(field, count, lenMin, lenSpan) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const drop = document.createElement("span");
    drop.className = "cs-drop";
    const length = lenMin + Math.random() * lenSpan;
    drop.style.left = (Math.random() * 100).toFixed(2) + "%";
    drop.style.height = length.toFixed(1) + "px";
    drop.style.setProperty("--cs-dur", (0.6 + Math.random() * 0.5).toFixed(2) + "s");
    drop.style.setProperty("--cs-delay", (-Math.random() * 1.4).toFixed(2) + "s");
    drop.style.setProperty("--cs-o", (0.35 + Math.random() * 0.45).toFixed(2));
    frag.appendChild(drop);
  }
  field.textContent = "";
  field.appendChild(frag);
}

function seedCardRipples(poolId) {
  const pool = document.getElementById(poolId);
  if (!pool) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < 7; i++) {
    const ripple = document.createElement("span");
    ripple.className = "cs-ripple";
    ripple.style.left = (Math.random() * 84).toFixed(2) + "%";
    ripple.style.setProperty("--cs-delay", (-Math.random() * 1.4).toFixed(2) + "s");
    frag.appendChild(ripple);
  }
  pool.textContent = "";
  pool.appendChild(frag);
}

function seedCardSnow(count = 30) {
  const field = document.getElementById("cs-snow-field");
  if (!field) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const flake = document.createElement("span");
    flake.className = "cs-snowflake";
    const size = 2.5 + Math.random() * 3.5;
    flake.style.width = size.toFixed(1) + "px";
    flake.style.height = size.toFixed(1) + "px";
    flake.style.left = (Math.random() * 100).toFixed(2) + "%";
    flake.style.setProperty("--cs-dur", (3.6 + Math.random() * 2.8).toFixed(2) + "s");
    flake.style.setProperty("--cs-delay", (-Math.random() * 6).toFixed(2) + "s");
    flake.style.setProperty("--cs-o", (0.5 + Math.random() * 0.5).toFixed(2));
    frag.appendChild(flake);
  }
  field.textContent = "";
  field.appendChild(frag);
}

function seedCardStars(count = 26) {
  const field = document.getElementById("cs-star-field");
  if (!field) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const star = document.createElement("span");
    star.className = "cs-star";
    const size = 1 + Math.random() * 1.6;
    star.style.width = size.toFixed(2) + "px";
    star.style.height = size.toFixed(2) + "px";
    star.style.top = (Math.random() * 68).toFixed(2) + "%";
    star.style.left = (Math.random() * 100).toFixed(2) + "%";
    star.style.setProperty("--cs-delay", (-Math.random() * 3.4).toFixed(2) + "s");
    frag.appendChild(star);
  }
  field.textContent = "";
  field.appendChild(frag);
}

// Reduced motion: park pooled particles as a faint static scatter
// inside the card (their fall/float animations are disabled by CSS;
// without a parked top they would all bunch at the container top).
function parkCardPoolsStatic() {
  document.querySelectorAll("#card-scene .cs-drop").forEach((d) => {
    d.style.top = (Math.random() * 70 + 6).toFixed(2) + "%";
  });
  document.querySelectorAll("#card-scene .cs-snowflake").forEach((f) => {
    f.style.top = (Math.random() * 70 + 6).toFixed(2) + "%";
  });
}

function seedCardPools(condition, reduced) {
  if (condition === "drizzle" || condition === "rain" || condition === "thunder") {
    const rainCount = condition === "thunder" ? 42 : condition === "drizzle" ? 20 : 30;
    const rainFieldId = condition === "thunder" ? "cs-storm-drop-field" : "cs-drop-field";
    const rainRippleId = condition === "thunder" ? "cs-storm-ripple-pool" : "cs-ripple-pool";
    const storm = condition === "thunder";
    const alreadySeeded = storm ? cardScene.seededStormDrops : cardScene.seededDrops;
    const wantedCount = storm ? cardScene.seededStormDropCount : cardScene.seededDropCount;
    if (!alreadySeeded || wantedCount !== rainCount || reduced) {
      seedCardDropsInto(document.getElementById(rainFieldId), rainCount, storm ? 16 : 12, storm ? 8 : 8);
      if (storm) { cardScene.seededStormDrops = true; cardScene.seededStormDropCount = rainCount; }
      else { cardScene.seededDrops = true; cardScene.seededDropCount = rainCount; }
    }
    const ripplesSeeded = storm ? cardScene.seededStormRipples : cardScene.seededRipples;
    if (!ripplesSeeded) {
      seedCardRipples(rainRippleId);
      if (storm) cardScene.seededStormRipples = true;
      else cardScene.seededRipples = true;
    }
  }
  if (condition === "snow") {
    if (!cardScene.seededSnow) {
      seedCardSnow(30);
      cardScene.seededSnow = true;
    }
  }
  if (cardScene.currentScene === "night" || condition === "clear-night" || condition === "partly-cloudy-night") {
    if (!cardScene.seededStars) {
      seedCardStars(26);
      cardScene.seededStars = true;
    }
  }
  if (reduced) parkCardPoolsStatic();
}

// Public entry: apply the card scene for the REAL condition. Called from
// renderForecastScreen with the live payload, on theme flips and at boot.
function applyCardSceneFx(condition, isDay = 1, tempBand = "mild") {
  const host = document.getElementById("card-scene");
  if (!host) return;
  const day = isDay !== 0;
  cardScene.lastIsDay = day ? 1 : 0;
  cardScene.lastCondition = condition;
  cardScene.lastTempBand = tempBand;

  // Same real-condition + theme mapping as the page scene: in dark theme
  // the sun scene never shows — moon + stars match the moonlit palette.
  let scene = CARD_SCENE_BY_CONDITION[condition] || (day ? "sun" : "night");
  if (currentTheme() === "dark" && scene === "sun") scene = "night";
  if (!day && scene === "sun") scene = "night";

  // In-card sky gradient follows the SAME condition + temp band as the
  // page Living Sky (hot/cold override fair skies only).
  const skyClass = cardSkyClassFor(condition, tempBand);
  const layers = [host.querySelector(".cs-sky-a"), host.querySelector(".cs-sky-b")];
  if (layers[0] && layers[1] && cardScene.currentSky !== skyClass) {
    const incoming = layers[(cardScene.front + 1) % 2];
    const outgoing = layers[cardScene.front];
    cardScene.front = (cardScene.front + 1) % 2;
    incoming.classList.remove(...Object.values(CARD_SKY_CLASS_BY_CONDITION), ...Object.values(CARD_SKY_CLASS_BY_TEMP_BAND));
    incoming.classList.add(skyClass);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        incoming.classList.add("is-live");
        clearTimeout(cardScene.retireTimer);
        cardScene.retireTimer = setTimeout(() => outgoing.classList.remove("is-live"), 1800);
      });
    });
    cardScene.currentSky = skyClass;
  }

  if (host.dataset.scene === scene && cardScene.currentScene === scene) {
    // Same scene: keep pools intensity-fresh (drizzle↔rain↔thunder
    // reseed when the tier changes even though "rain" stays).
    seedCardPools(condition, prefersReducedMotion());
    return;
  }

  host.dataset.scene = scene;
  cardScene.currentScene = scene;
  seedCardPools(condition, prefersReducedMotion());
}

// Theme flip: re-run the mapping so dark theme swaps sun→night (and
// light theme restores it) without waiting for the next weather fetch.
function syncCardSceneFxTheme() {
  if (cardScene.currentScene === null) return;
  applyCardSceneFx(cardScene.lastCondition, cardScene.lastIsDay, cardScene.lastTempBand);
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
  nextHoursStrip: document.getElementById("next-hours-strip"),
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
  voiceOutputControls: document.getElementById("voice-output-controls"),
  voiceSpeakBtn: document.getElementById("voice-speak-btn"),
  voiceSpeakIcon: document.getElementById("voice-speak-icon"),
  voiceSpeakLabel: document.getElementById("voice-speak-label"),
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
  farmSubtitle: document.getElementById("farm-advisor-subtitle"),
  farmEmptyState: document.getElementById("farm-empty-state"),
  farmGuideTitle: document.getElementById("farm-guide-title"),
  farmGuideSubtitle: document.getElementById("farm-guide-subtitle"),
  farmGuideCropsHeading: document.getElementById("farm-guide-crops-heading"),
  farmGuidePracticesHeading: document.getElementById("farm-guide-practices-heading"),
  farmGuideCropGrid: document.getElementById("farm-guide-crop-grid"),
  farmGuidePracticeGrid: document.getElementById("farm-guide-practice-grid"),
  farmGuidePhotoCredit: document.getElementById("farm-guide-photo-credit"),

  // Smart Rain Alert + Rain Timeline
  smartRainAlert: document.getElementById("smart-rain-alert"),
  smartRainTitle: document.getElementById("smart-rain-title"),
  smartRainBadge: document.getElementById("smart-rain-badge"),
  smartRainText: document.getElementById("smart-rain-text"),
  rainTimelineHeading: document.getElementById("rain-timeline-heading"),
  rainTimelinePeak: document.getElementById("rain-timeline-peak"),
  rainTimelineTrack: document.getElementById("rain-timeline-track"),
  rainTimelineBar: document.getElementById("rain-timeline-bar"),
  rainTimelineStartLabel: document.getElementById("rain-timeline-start-label"),
  rainTimelineEndLabel: document.getElementById("rain-timeline-end-label"),
  rainTimelineNote: document.getElementById("rain-timeline-note"),

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
    try { localStorage.setItem(LANG_KEY, state.voiceLang); } catch (e) {}
    document.documentElement.lang = state.voiceLang === "auto" ? "en" : state.voiceLang;
    // Spoken output follows the same selector: en-US / hi-IN / te-IN.
    window.VoiceOutput?.setLanguage(state.voiceLang);

    updatePromptChipsForLanguage(state.voiceLang);

    // Re-render farm advisory in the selected language (server-side
    // translation is keyed by language) — refetch when its view is open,
    // otherwise mark stale so it picks the new language on next open.
    // Selectors, static farm chrome, and the visual guide re-render
    // immediately in every tab state.
    const farmLangCode = farmLang();
    populateFarmSelectors(farmLangCode);
    applyFarmStaticText(farmLangCode);
    renderFarmGuide(farmLangCode);
    // Header, nav, section titles, placeholders — immediate, no refresh.
    applyChromeI18n();
    syncVoiceOutputControls();
    // Pre-paint boot translator is done once the app is live.
    disconnectI18nBootObserver();
    syncLanguagePillActive();
    if (state.weather) renderNextHoursStrip();
    state.farmCacheKey = null;
    if (state.weather && state.activeView === "view-farmer") {
      refreshFarmAdvice();
    } else if (state.weather) {
      state.farmStale = true;
    }

    // Re-render rain surfaces in the selected language (no refetch —
    // the evidence is already in state, only wording changes).
    if (state.rainTimeline) {
      renderSmartRainAlert();
      renderRainTimeline();
    }

    // Insights re-renders fully from cached state in the new language.
    if (state.weather) renderInsightsScreen();

    // Radar map surface (sensor-card title, marker tooltip, address bar)
    // re-words immediately; the active field layer keeps its own data.
    if (state.weather) renderMapScreen();

    // Hero condition title + daily labels use the new language.
    if (state.weather) renderForecastScreen();

    // Refresh weather synopsis in new language if data already loaded
    if (state.weather) {
      generateAiReport();
    }
  });
});

// Boot-time language restore: the pre-paint inline translator already
// rendered the chrome in the persisted language; here we sync the runtime
// state and the active pill so behavior matches a real user click.
(function restoreLanguageAtBoot() {
  const bootLang = document.documentElement.__i18nBootLang;
  if (bootLang === "hi" || bootLang === "te") {
    state.voiceLang = bootLang;
  }
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === "en" || saved === "hi" || saved === "te") state.voiceLang = saved;
  } catch (e) {}
  syncLanguagePillActive();
})();

// Keep the EN/Auto/HI/TE pill row consistent with the active language
// (Auto and EN render identically, so only HI/TE need explicit moves).
function syncLanguagePillActive() {
  const lang = state.voiceLang;
  document.querySelectorAll(".lang-btn").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.lang === lang ||
      (lang === "auto" && b.dataset.lang === "auto"));
  });
}

// The inline pre-paint translator hands control to the app after load.
function disconnectI18nBootObserver() {
  try {
    const obs = document.documentElement.__i18nBootObserver;
    if (obs) { obs.disconnect(); document.documentElement.__i18nBootObserver = null; }
  } catch (e) {}
}

// ============================================================
// NEXT FEW HOURS — compact real hourly outlook strip
// ============================================================
// A lightweight band directly below the hero card: ~6 upcoming slots
// from the SAME real hourly payload the 24h scroller uses. Fully
// localized; hidden entirely when hourly data is unavailable (never
// fabricated), with an honest localized empty note otherwise.
function renderNextHoursStrip() {
  const strip = els.nextHoursStrip;
  if (!strip) return;
  const hourly = state.weather?.hourly || {};
  if (!hourly.time || !hourly.time.length) {
    strip.innerHTML = `<span class="font-body-dim text-[11px] text-ink-tertiary px-1 py-1">${T("nfhUnavailable")}</span>`;
    return;
  }
  const nowMs = Date.now();
  let startIdx = hourly.time.findIndex((tStr) => new Date(tStr).getTime() >= nowMs - 1800e3);
  if (startIdx < 0) startIdx = 0;
  const temps = hourly.temperature_2m || [];
  const probs = hourly.precipitation_probability || hourly.precipitation || [];
  const codes = hourly.weather_code || [];
  const locale = farmLang() === "hi" ? "hi-IN" : farmLang() === "te" ? "te-IN" : undefined;

  const slots = [];
  for (let i = startIdx; i < hourly.time.length && slots.length < 6; i++) {
    const d = new Date(hourly.time[i]);
    if (d.getTime() < nowMs - 3600e3) continue;
    slots.push({
      label: slots.length === 0 ? T("now") : d.toLocaleTimeString(locale, { hour: "numeric" }),
      temp: Math.round(temps[i] ?? ""),
      rain: Math.round(probs[i] ?? 0),
      icon: CONDITION_ICONS[getConditionCategory(codes[i] ?? 0, 1)] || "wb_sunny",
      cat: getConditionCategory(codes[i] ?? 0, 1),
    });
  }
  if (!slots.length) {
    strip.innerHTML = `<span class="font-body-dim text-[11px] text-ink-tertiary px-1 py-1">${T("nfhUnavailable")}</span>`;
    return;
  }

  strip.innerHTML = slots
    .map(
      (s, idx) => `
      <div role="listitem" class="flex flex-col items-center justify-between min-w-[52px] flex-1 px-1.5 py-0.5 rounded-xl overflow-hidden ${
        idx === 0 ? "bg-amber-glow-surface/70" : ""
      }">
        <span class="font-label-caps text-[10px] leading-tight ${idx === 0 ? "text-primary font-bold" : "text-ink-tertiary"} uppercase">${s.label}</span>
        <span class="material-symbols-outlined text-[16px] leading-none ${idx === 0 ? "text-primary" : "text-ink-primary"}" role="img" aria-label="${conditionTitleFor(s.cat)}">${s.icon}</span>
        <span class="flex items-baseline justify-center gap-1 leading-tight">
          <span class="font-headline-card text-[12px] text-ink-primary font-bold leading-none">${s.temp === "" ? "–" : `${s.temp}°`}</span>
          <span class="font-label-caps text-[9px] text-secondary font-medium leading-none" aria-label="${T("rainChance")} ${s.rain}%">${s.rain}%</span>
        </span>
      </div>`
    )
    .join("");
}

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

function showStatus(msg, badge = "Live") {
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
    showStatus(`Searching for '${query}'...`);
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
    showStatus(`Finding '${query}'...`);
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
  showStatus("Getting your location...");

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

  showStatus(`Fetching weather for ${name}...`);

  try {
    const data = await getJSON(`/api/weather?latitude=${latitude}&longitude=${longitude}&forecast_days=7`);

    // A newer location request started while this fetch was in flight —
    // discard the stale response so it can never override fresh data.
    if (requestId !== state.locationRequestId) return;

    state.weather = data.weather;
    state.alerts = data.alerts || [];
    state.rainTimeline = data.rain_timeline || null;
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

  // Rain drop tint follows the theme (dark skies need paler drops).
  syncRainFxTheme();
  } catch (err) {
    if (requestId === state.locationRequestId) {
      console.error("Weather fetch failed:", err);
      showStatus(`Couldn't load weather: ${err.message}`, "Offline");
    }
  } finally {
    // A superseded request must not hide the active request's status bar.
    if (requestId === state.locationRequestId) hideStatus();
  }
}

function renderForecastScreen() {
  renderNextHoursStrip();
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
  // Clouds / sun glow / rays / sparkle depth layer, same real condition.
  applySkyFx(cat, isDay);
  // Illustrated scene layer (sun/clouds/rain/night), same real condition.
  applySceneFx(cat, isDay);
  // In-card hero scene (sun/clouds/rain/storm/mist/snow/night), same
  // real condition + temperature band — clipped inside the hero card.
  applyCardSceneFx(cat, isDay, tempBandFor(temp));
  // Precipitation overlay + storm lightning, from the same real payload.
  applyPrecipFx(cat, current.precipitation, current.wind_direction_10m);
  // Smart Rain Alert + Rain Timeline — same real hourly payload.
  renderSmartRainAlert();
  renderRainTimeline();

  if (els.heroTemperature) els.heroTemperature.textContent = temp;
  if (els.heroTempFeels) els.heroTempFeels.textContent = `${feels}°`;
  if (els.heroTempHigh) els.heroTempHigh.textContent = `${high}°`;
  if (els.heroTempLow) els.heroTempLow.textContent = `${low}°`;
  if (els.heroConditionText) els.heroConditionText.textContent = conditionTitleFor(cat);
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
  // Colors read the theme tokens so dark mode keeps AA contrast.
  const aqiVal = state.airQuality?.us_aqi ?? state.airQuality?.european_aqi ?? null;
  const aqiLabel = aqiVal == null ? "—" : aqiVal <= 50 ? "Good" : aqiVal <= 100 ? "Moderate" : "Unhealthy";
  const aqiColor = aqiVal == null
    ? "rgb(var(--ink-tertiary))"
    : aqiVal <= 50 ? "rgb(var(--tertiary-fixed-dim))" : aqiVal <= 100 ? "rgb(var(--primary))" : "rgb(var(--alert-coral))";

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
      { name: "Downtown", delta: 0, icon: "wb_sunny" },
      { name: "By the water", delta: -3, icon: "mist" },
      { name: "On the hills", delta: -1, icon: "air" },
      { name: "In the valley", delta: +2, icon: "clear_day" },
    ];
    els.microclimateChipsScroll.innerHTML = deltas
      .map(
        (m, idx) => `
      <div class="microclimate-chip flex items-center gap-2 px-3 py-2 rounded-xl ${
        idx === 0 ? "bg-amber-glow-surface" : "bg-surface-container-high"
      } shrink-0 cursor-pointer">
        <div class="flex flex-col">
          <span class="font-pill-text text-xs text-ink-primary">${m.name}</span>
          <span class="font-headline-card text-xs text-primary leading-none font-bold">${temp + m.delta}°</span>
        </div>
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
        const timeLabel = i === 0 ? T("now") : d.toLocaleTimeString([], { hour: "numeric" });
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
        const dayLabel = i === 0 ? (CHROME_I18N[farmLang()] || CHROME_I18N.en).today
          : d.toLocaleDateString(farmLang() === "hi" ? "hi-IN" : farmLang() === "te" ? "te-IN" : undefined, { weekday: "short" });
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

// ============================================================
// SMART RAIN ALERT + RAIN TIMELINE RENDERING
// Both read the backend-computed rain timeline (same real hourly
// payload as every other surface). No numbers are invented here.
// ============================================================

function renderSmartRainAlert() {
  const t = RAIN_I18N[rainLang()];
  if (!els.smartRainAlert || !els.smartRainText) return;

  // Title/badge stay localized even while the banner is hidden, so a
  // language switch never leaves stale text in the DOM.
  if (els.smartRainTitle) els.smartRainTitle.textContent = t.alertTitle;
  if (els.smartRainBadge) {
    els.smartRainBadge.textContent = t.badge;
    // Badge text must stay readable on the accent fill in BOTH themes
    // (dark --secondary is a light sky-blue, so ink beats white).
    els.smartRainBadge.style.color = "rgb(var(--on-primary))";
  }

  const alert = smartRainAlertLocalized();
  if (!alert) {
    els.smartRainAlert.classList.add("hidden");
    return;
  }
  els.smartRainText.textContent = alert.text;
  els.smartRainAlert.classList.remove("hidden");
}

function renderRainTimeline() {
  const t = RAIN_I18N[rainLang()];
  if (!els.rainTimelineTrack || !els.rainTimelineBar || !els.rainTimelineNote) return;

  if (els.rainTimelineTrack) {
    els.rainTimelineTrack.setAttribute("aria-label", t.trackLabel);
  }
  if (els.rainTimelineStartLabel) els.rainTimelineStartLabel.textContent = t.nowLabel;
  if (els.rainTimelineEndLabel) els.rainTimelineEndLabel.textContent = t.endLabel;

  const tl = state.rainTimeline;
  const peak = tl && tl.peak_probability;
  if (els.rainTimelinePeak) {
    els.rainTimelinePeak.textContent = peak != null ? t.peak(peak) : "";
  }

  if (!tl || !tl.has_event) {
    els.rainTimelineBar.style.left = "0%";
    els.rainTimelineBar.style.width = "0%";
    els.rainTimelineNote.textContent = t.noRain;
    return;
  }

  // Bar spans start→end within the fixed 18h track.
  const horizon = tl.horizon_h || R18_HOURS;
  const left = 100 * (tl.starts_in_h || 0) / horizon;
  const width = 100 * (tl.duration_h || 0) / horizon;
  els.rainTimelineBar.style.left = `${left}%`;
  els.rainTimelineBar.style.width = `${Math.max(width, 3)}%`;
  els.rainTimelineNote.textContent = t.window(tl.start_label, tl.end_label, tl.duration_h);
}

function renderMapScreen() {
  const current = state.weather?.current || {};
  const temp = Math.round(current.temperature_2m ?? 0);
  const hasTemp = current.temperature_2m !== undefined;
  const mapT = (CHROME_I18N[farmLang()] || CHROME_I18N.en);

  if (els.sensorCardTitle) {
    els.sensorCardTitle.textContent = `${locationDisplayName()} — ${mapT.liveReading || "Live Reading"}`;
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
      els.insightsScopeSubtitle.textContent = T("insightsScope48h");
    } else if (scope === "7d") {
      els.insightsScopeSubtitle.textContent = T("insightsScope7d");
    } else {
      els.insightsScopeSubtitle.textContent = T("insightsScopeToday");
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
        { label: T("dayRange"), val: `${todayMin}° / ${todayMax}°`, sub: T("lowHighToday"), icon: "device_thermostat", color: "text-primary" },
        { label: T("rainChance"), val: `${todayRain}%`, sub: T("highestToday"), icon: "rainy", color: "text-secondary" },
        { label: T("wind"), val: `${todayWind} km/h`, sub: T("strongestToday"), icon: "air", color: "text-alert-coral" },
        { label: T("humidity"), val: `${todayHum}%`, sub: T("avgMoisture"), icon: "water_drop", color: "text-tertiary" },
      ];
    } else if (scope === "48h") {
      cards = [
        { label: T("range48"), val: `${minTemp48}° – ${maxTemp48}°`, sub: T("lowHigh48"), icon: "thermostat", color: "text-primary" },
        { label: T("rainPeak48"), val: `${maxRain48}%`, sub: T("wettest48"), icon: "umbrella", color: "text-secondary" },
        { label: T("windPeak48"), val: `${maxWind48} km/h`, sub: T("gusts48"), icon: "air", color: "text-alert-coral" },
        { label: T("avgHum48"), val: `${avgHum48}%`, sub: T("over48"), icon: "humidity_mid", color: "text-tertiary" },
      ];
    } else {
      cards = [
        { label: T("range7d"), val: `${minTemp7d}° – ${maxTemp7d}°`, sub: T("lowHigh7d"), icon: "calendar_today", color: "text-primary" },
        { label: T("totalRain"), val: `${totalRain7d} mm`, sub: T("expectedWeek"), icon: "water", color: "text-secondary" },
        { label: T("wettestDay"), val: `${maxRain7d}%`, sub: T("highestChance"), icon: "grain", color: "text-alert-coral" },
        { label: T("windPeak7d"), val: `${maxWind7d} km/h`, sub: T("strongestDay"), icon: "cyclone", color: "text-tertiary" },
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
      // Localize the generic daily-rain alert the backend emits in English
      // (peak % is re-read from the real daily payload, never invented).
      const rainPeak = Math.max(...(daily.precipitation_probability_max || [0]));
      alertItems = (state.alerts || []).map((a) =>
        a.type === "High Precipitation Probability"
          ? { ...a, text: RAIN_I18N[rainLang()].alertDaily(rainPeak) }
          : a
      );
    } else if (scope === "48h") {
      if (maxRain48 >= 50) {
        alertItems.push({
          severity: "Advisory",
          type: T("type48Rain"),
          text: tf("alert48Rain", { n: maxRain48 }),
          protocol: tf("alert48RainP").split("|"),
        });
      }
      if (maxWind48 >= 35) {
        alertItems.push({
          severity: "Notice",
          type: T("type48Wind"),
          text: tf("alert48Wind", { n: maxWind48 }),
          protocol: tf("alert48WindP").split("|"),
        });
      }
      if (maxTemp48 >= 36) {
        alertItems.push({
          severity: "Advisory",
          type: T("type48Heat"),
          text: tf("alert48Heat", { n: maxTemp48 }),
          protocol: tf("alert48HeatP").split("|"),
        });
      }
    } else {
      if (maxRain7d >= 40) {
        const wetDays = dRainProb.filter((p) => p >= 35).length;
        alertItems.push({
          severity: "Advisory",
          type: T("type7dRain"),
          text: tf("alert7dRain", { d: wetDays, n: maxRain7d, m: totalRain7d }),
          protocol: tf("alert7dRainP").split("|"),
        });
      }
      if (maxTemp7d >= 37) {
        alertItems.push({
          severity: "Warning",
          type: T("type7dHeat"),
          text: tf("alert7dHeat", { n: maxTemp7d }),
          protocol: tf("alert7dHeatP").split("|"),
        });
      }
      if (maxWind7d >= 45) {
        alertItems.push({
          severity: "Notice",
          type: T("type7dWind"),
          text: tf("alert7dWind", { n: maxWind7d }),
          protocol: tf("alert7dWindP").split("|"),
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
                <span class="px-2 py-0.5 rounded-full font-label-caps text-[9px] bg-alert-coral text-white uppercase font-bold tracking-wider">${escapeHTML(alert.severity || T("advisory"))}</span>
                <span class="font-headline-card text-xs text-alert-coral-text font-semibold">${escapeHTML(alert.type || T("notice"))}</span>
              </div>
              <p class="font-body-base text-xs text-alert-coral-text/90">${escapeHTML(alert.text || "")}</p>
              ${
                alert.protocol
                  ? `
              <div class="mt-3 pt-2.5 bg-surface-container-lowest/40 -mx-4 -mb-4 px-4 py-2.5 rounded-b-2xl flex flex-col gap-1.5">
                <span class="font-label-caps text-[9px] text-alert-coral uppercase tracking-wider font-semibold">${T("canDo")}</span>
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
      const scopeLabel = scope === "48h" ? T("for48h") : scope === "7d" ? T("for7d") : T("scopeToday");
      els.insightsAlertsContainer.innerHTML = `
        <div class="rounded-2xl bg-surface-container-high border border-glass-border/20 p-4 flex items-center gap-3">
          <div class="w-8 h-8 rounded-full bg-emerald-500/15 text-emerald-700 flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-[20px]">verified_user</span>
          </div>
          <div>
            <h4 class="font-headline-card text-xs text-ink-primary font-semibold">${T("noAlerts")} ${scopeLabel}</h4>
            <p class="font-body-dim text-xs text-ink-tertiary">${T("allClear")}</p>
          </div>
        </div>`;
    }
  }

  // 4. Microclimate Variance Bars
  if (els.insightsDeltaBars) {
    let bars = [];

    if (scope === "today") {
      if (els.insightsBaselineLabel) {
        els.insightsBaselineLabel.textContent = tf("baselineNow", { loc: locationName, t: temp });
      }
      bars = [
        { name: `${T("valBar")} ${T("illustrative")}`, delta: "+2.4°C", width: "48%", color: "bg-primary-container" },
        { name: `${T("dtBar")} ${T("illustrative")}`, delta: "+1.1°C", width: "24%", color: "bg-secondary" },
        { name: `${T("coastBar")} ${T("illustrative")}`, delta: "-3.2°C", width: "56%", color: "bg-tertiary-container" },
        { name: `${T("hillBar")} ${T("illustrative")}`, delta: "-1.6°C", width: "32%", color: "bg-secondary-fixed" },
      ];
    } else if (scope === "48h") {
      const swing48 = Math.max(3, Math.abs(maxTemp48 - minTemp48));
      if (els.insightsBaselineLabel) {
        els.insightsBaselineLabel.textContent = tf("baseline48", { s: swing48 });
      }
      bars = [
        { name: T("warmHour"), delta: `${maxTemp48}°C`, width: `${Math.min(100, Math.abs(maxTemp48) * 2.5)}%`, color: "bg-secondary" },
        { name: T("coolHour"), delta: `${minTemp48}°C`, width: `${Math.min(100, Math.abs(minTemp48) * 2.5)}%`, color: "bg-tertiary-container" },
        { name: T("rainPeakBar"), delta: `${maxRain48}%`, width: `${Math.max(15, Math.min(100, maxRain48))}%`, color: "bg-secondary-fixed" },
        { name: T("avgHum48"), delta: `${avgHum48}%`, width: `${Math.min(100, Math.max(10, avgHum48))}%`, color: "bg-primary-container" },
      ];
    } else {
      const swing7d = Math.max(4, Math.abs(maxTemp7d - minTemp7d));
      if (els.insightsBaselineLabel) {
        els.insightsBaselineLabel.textContent = tf("baseline7d", { a: minTemp7d, b: maxTemp7d });
      }
      bars = [
        { name: T("lowHigh7d"), delta: `Δ ${swing7d}°C`, width: `${Math.min(100, swing7d * 7)}%`, color: "bg-primary-container" },
        { name: T("rainPeakBar"), delta: `${maxRain7d}%`, width: `${Math.max(15, Math.min(100, maxRain7d))}%`, color: "bg-secondary" },
        { name: T("totalRain"), delta: `${totalRain7d} mm`, width: `${Math.min(100, Math.max(20, parseFloat(totalRain7d) * 4))}%`, color: "bg-tertiary-container" },
        { name: T("windPeak7d"), delta: `${maxWind7d} km/h`, width: `${Math.min(100, maxWind7d * 2)}%`, color: "bg-secondary-fixed" },
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

async function initFarmAdvisor() {
  if (!els.farmCropSelect || !els.farmStageSelect) return;
  // Localize the selectors from the live catalogue (bundled fallback
  // until/unless it responds), then render the visual guide.
  await loadFarmCatalogue();
  populateFarmSelectors(farmLang());
  applyFarmStaticText(farmLang());
  renderFarmGuide(farmLang());
  initCropInfo(); // binds crop-card clicks → info panel (delegated)
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

  // The synopsis is UI, not a fetch gate: render whatever we already have
  // for this language instantly. Language switching NEVER blocks on or
  // waits for the LLM — a localized report is generated lazily in the
  // background on first need and cached per location + language.
  const cacheKey = `${state.currentLocation.latitude},${state.currentLocation.longitude}:${language}`;
  const cached = state.reportCache && state.reportCache.key === cacheKey ? state.reportCache : null;
  if (cached) {
    if (els.heroSynopsisText) els.heroSynopsisText.innerHTML = renderMarkdownLite(cached.report);
    if (els.heroSynopsisMeta) els.heroSynopsisMeta.textContent = tf("synopsisMeta", { lang: language });
    return;
  }

  // No report yet for this language: keep the previous synopsis visible
  // (stale but real) and refresh in the background — once per key.
  if (state.reportInflight === cacheKey) return;
  state.reportInflight = cacheKey;

  try {
    const tick = ++state.reportAiTick;
    const payload = {
      location: state.currentLocation,
      weather_data: state.weather,
      alerts: state.alerts,
      language: language,
    };

    const res = await postJSON("/api/report", payload);
    if (requestId !== state.locationRequestId) return; // stale — a newer location won
    if (tick !== state.reportAiTick) return; // stale — a newer AI report superseded this one
    state.reportCache = { key: cacheKey, report: res.report };
    state.synopsis = res.report;
    if (els.heroSynopsisText) {
      els.heroSynopsisText.innerHTML = renderMarkdownLite(res.report);
    }
    if (els.heroSynopsisMeta) {
      els.heroSynopsisMeta.textContent = tf("synopsisMeta", { lang: language });
    }
  } catch (err) {
    // Keep the previous language's synopsis on screen rather than blanking
    // the card — it is still real data for this location.
    console.warn("AI report synthesis skipped:", err);
  } finally {
    if (state.reportInflight === cacheKey) state.reportInflight = null;
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
    // Remember the answer for Replay (success or failure alike).
    rememberAnswerForReplay(res.answer);
  } catch (err) {
    removeThinkingBubble(thinkingId);
    const realTemp = state.weather?.current?.temperature_2m;
    const fallbackLine =
      realTemp != null
        ? `${Math.round(realTemp)}°C, ${CONDITION_TITLES[document.body.dataset.condition] || "current conditions"}`
        : T("liveDataUnavailable");
    const fallbackAnswer = tf("gptFallback", { err: err.message, loc: locationDisplayName(), line: fallbackLine });
    rememberAnswerForReplay(fallbackAnswer);
    appendAssistantResponseNode({
      answer: fallbackAnswer,
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

  // Action Card 1: Best time window
  if (data.optimal_window && data.optimal_window.time_range) {
    actionCardsHtml += `
    <div class="rounded-xl bg-surface-bright/70 border border-glass-border-subtle p-3 shadow-inner space-y-1.5">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1.5">
          <span class="material-symbols-outlined text-primary text-[18px]">timelapse</span>
          <span class="font-headline-card text-xs text-ink-primary font-semibold">${escapeHTML(
            data.optimal_window.title || "Best time"
          )}</span>
        </div>
        <span class="px-2 py-0.5 rounded-full bg-primary-container/20 text-primary font-label-caps text-[9px] font-semibold">
          ${escapeHTML(data.optimal_window.reliability || "High Confidence")}
        </span>
      </div>
      <div class="grid grid-cols-2 gap-2 pt-1">
        <div class="bg-surface-container-high/80 rounded-lg p-2 flex flex-col">
          <span class="font-label-caps text-[9px] text-ink-tertiary uppercase">Good time</span>
          <span class="font-headline-card text-xs text-ink-primary font-bold mt-0.5">${escapeHTML(
            data.optimal_window.time_range
          )}</span>
          <span class="font-body-dim text-[10px] text-secondary mt-0.5">${escapeHTML(
            data.optimal_window.favorable_note || "Favorable conditions"
          )}</span>
        </div>
        <div class="bg-surface-container-high/80 rounded-lg p-2 flex flex-col">
          <span class="font-label-caps text-[9px] text-ink-tertiary uppercase">Watch out for</span>
          <span class="font-headline-card text-xs text-alert-coral font-bold mt-0.5">${escapeHTML(
            data.optimal_window.caution_note || "Later hours"
          )}</span>
          <span class="font-body-dim text-[10px] text-ink-secondary mt-0.5">Plan around it</span>
        </div>
      </div>
    </div>`;
  }

  // Action Card 2: Along the way
  if (data.route_progression && data.route_progression.length > 0) {
    actionCardsHtml += `
    <div class="rounded-xl bg-surface-bright/70 border border-glass-border-subtle p-3 shadow-inner space-y-2">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1.5">
          <span class="material-symbols-outlined text-secondary text-[18px]">route</span>
          <span class="font-headline-card text-xs text-ink-primary font-semibold">Along the way</span>
        </div>
        <span class="font-body-dim text-[10px] text-ink-tertiary">Start, midway, destination</span>
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

  // Action Card 3: What to wear
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
      <span class="font-label-section text-xs text-ink-primary font-semibold">WeatherGPT</span>
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
// VOICE OUTPUT (Web Speech API) — accessible spoken answers
// ============================================================
// Wiring between window.VoiceOutput (voice-output.js) and the app:
// ONE stateful Speak button (Speak answer / Stop / Replay answer).
// Nothing is ever spoken automatically — speaking happens only when
// the user presses the button.

// Tailwind's "hidden" class loses to the flex/display utilities on the
// controls bar, so visibility is owned here via inline style.
const VOICE_PHASE_UI = {
  idle: { icon: "volume_up", label: "speakAnswer", aria: "speakAnswer", disabled: true },
  ready: { icon: "volume_up", label: "speakAnswer", aria: "speakAnswer", disabled: false },
  replay: { icon: "replay", label: "speakReplay", aria: "speakReplayA11y", disabled: false },
  speaking: { icon: "stop", label: "speakStop", aria: "speakStop", disabled: false },
};

function syncVoiceOutputControls() {
  const Vo = window.VoiceOutput;
  if (!Vo || !els.voiceOutputControls) return;
  const hide = !Vo.supported();
  els.voiceOutputControls.style.display = hide ? "none" : "flex";
  els.voiceOutputControls.classList.toggle("hidden", hide);

  if (hide) return;
  if (els.voiceSpeakBtn) {
    const phase = Vo.phase; // idle | ready | replay | speaking
    const ui = VOICE_PHASE_UI[phase];
    els.voiceSpeakBtn.dataset.phase = phase;
    els.voiceSpeakBtn.disabled = ui.disabled;
    els.voiceSpeakBtn.setAttribute("aria-label", T(ui.aria));
    if (els.voiceSpeakIcon) els.voiceSpeakIcon.textContent = ui.icon;
    if (els.voiceSpeakLabel) els.voiceSpeakLabel.textContent = T(ui.label);
  }
}

// Screen-reader announcement for user-driven speak/stop transitions
// (mirrors the aria-live region voice-output.js uses internally).
function announceVoiceOutput(message) {
  const region = document.getElementById("voice-speech-status");
  if (region) region.textContent = message;
}

function initVoiceOutput() {
  const Vo = window.VoiceOutput;
  if (!Vo) return; // script missing/blocked — app works without speech

  if (!Vo.supported()) {
    syncVoiceOutputControls(); // hides the whole controls bar
    return;
  }

  Vo.setLanguage(state.voiceLang);
  if (els.voiceSpeakBtn) {
    els.voiceSpeakBtn.addEventListener("click", () => {
      if (Vo.phase === "speaking") {
        Vo.stop();
        announceVoiceOutput("Speech stopped.");
      } else if (Vo.phase === "replay" || Vo.phase === "ready") {
        Vo.speakLast();
      }
      syncVoiceOutputControls();
    });
  }

  // Speaking state changes drive the button's Stop phase.
  document.addEventListener("voice-output-state", syncVoiceOutputControls);
  syncVoiceOutputControls();
}

// Never speaks automatically — speaking is strictly user-controlled via
// the Speak button. Every answer (voice-origin or typed) is only
// remembered, which arms the button; the user decides when to listen.
function rememberAnswerForReplay(answer) {
  const Vo = window.VoiceOutput;
  if (!Vo || !Vo.supported()) return;
  if (!answer) return;
  Vo.remember(answer);
  syncVoiceOutputControls(); // arm the Speak button for this answer
}

// ------------------------------------------------------------
// GROQ WHISPER VOICE RECORDING MODULE
// ------------------------------------------------------------

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
  if (els.voiceCountdownLabel) els.voiceCountdownLabel.textContent = `${secondsLeft} ${T("secondsRemaining")}`;

  state.countdownInterval = setInterval(() => {
    secondsLeft -= 1;
    if (els.voiceCountdownLabel) els.voiceCountdownLabel.textContent = `${secondsLeft} ${T("secondsRemaining")}`;
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

  showStatus("Listening to your recording...");

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
      showStatus(`You said: "${transcript}"`);
      // Route query to assistant view
      switchView("view-weathergpt");
      // Nothing is spoken automatically — the answer is only remembered,
      // and the user presses the Speak button to hear it.
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

// First paint: translate the static chrome in the persisted language.
applyChromeI18n();

// Layer Toggle — ALL pills are wired to real data: precip + satellite
// ("clouds") stream RainViewer tiles, while Micro-Temp / Wind Vectors /
// AQI Plume sample real gridded model data through the backend
// /api/field endpoint (status surfaced via the radar-field-status event).
const FIELD_LAYER_LABELS = { temp: "Micro-Temp", wind: "Wind Vectors", aqi: "AQI Plume" };

// The map view starts on the precipitation layer, matching the markup.
state.activeMapLayer = state.activeMapLayer || "precip";

function setMapLayerPillActive(layer) {
  document.querySelectorAll(".map-layer-pill").forEach((p) => {
    const isActive = p.dataset.layer === layer;
    p.classList.toggle("active", isActive);
    p.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function showMapFieldStatus(message) {
  showStatus(message, "Field data");
  clearTimeout(showMapFieldStatus._timer);
  showMapFieldStatus._timer = setTimeout(hideStatus, 3200);
}

window.addEventListener("radar-field-status", (event) => {
  const { status, metric } = event.detail || {};
  const label = FIELD_LAYER_LABELS[metric] || "Field";
  if (status === "loading") {
    showMapFieldStatus(`Loading ${label} field data…`);
  } else if (status === "empty") {
    showMapFieldStatus(`${label}: no model data available for this region.`);
  } else if (status === "error") {
    // Satellite (clouds) has its own localized unavailable wording.
    if (metric === "clouds") {
      showMapFieldStatus((CHROME_I18N[farmLang()] || CHROME_I18N.en).satUnavailable);
    } else {
      showMapFieldStatus(`${label}: field data temporarily unavailable.`);
    }
  } else if (status === "ready") {
    hideStatus();
  }
});

// A pill wired to real data reported itself unavailable (e.g. unknown
// layer name) — say so instead of silently doing nothing.
window.addEventListener("radar-layer-unavailable", (event) => {
  showMapFieldStatus(`${event.detail?.layer || "This layer"}: not available on this map.`);
});

// Satellite explainer: expandable "why it matters" help text.
document.getElementById("satellite-help-btn")?.addEventListener("click", () => {
  const btn = document.getElementById("satellite-help-btn");
  const panel = document.getElementById("satellite-help");
  if (!btn || !panel) return;
  const open = panel.classList.toggle("hidden") === false;
  btn.setAttribute("aria-expanded", open ? "true" : "false");
});

// Read-only mirror of the app state for decoupled modules (radar-map.js
// anchors its field grids on the CURRENT location, the same coordinates
// the main weather view uses — never the panned map center).
window.WeatherState = state;

document.querySelectorAll(".map-layer-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    if (state.activeMapLayer === pill.dataset.layer) return; // already active
    setMapLayerPillActive(pill.dataset.layer);
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
  initThemeToggle();
  bindAssistantChips();
  bindInsightsScopeChips();
  initFarmAdvisor();
  initVoiceOutput();
  // Render the default location's labels immediately (the old pipeline set
  // them inside refreshWeatherData; label updates now live in
  // updateLocationDisplay and must run once at startup too).
  updateLocationDisplay();
  // Paint the initial sky before the first weather payload lands.
  applyDynamicSky("clear-day", "mild");
  applySkyFx("clear-day", 1); // clouds/sun-glow/rays layer starts on the fair-day default
  applySceneFx("clear-day", 1); // illustrated scene starts on the fair-day default
  applyCardSceneFx("clear-day", 1, "mild"); // in-card hero scene starts fair
  applyPrecipFx("clear-day"); // rain/lightning layers start hidden
  // Auto-request GPS on first load (graceful fallback to the default
  // city on denial/timeout/unsupported) instead of always loading SF.
  autoGpsOnFirstLoad();
});
