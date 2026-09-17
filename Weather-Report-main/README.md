# WeatherGPT • Atmospheric Intelligence

A mobile-first, voice-enabled, multilingual weather Progressive Web App (PWA) that combines live meteorological telemetry with AI-generated narrative forecasts, conversational Q&A, and safety alerts — in **English, Hindi, and Telugu**.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Problem Statement](#2-problem-statement)
3. [Purpose of the Application](#3-purpose-of-the-application)
4. [Major Features](#4-major-features)
5. [Feature Details](#5-feature-details)
6. [Key Backend Modules & Functions](#6-key-backend-modules--functions)
7. [Key Frontend Modules & Functions](#7-key-frontend-modules--functions)
8. [How the Application Works — User Flow](#8-how-the-application-works--user-flow)
9. [How to Use the Application](#9-how-to-use-the-application)
10. [Technologies, Frameworks, APIs & Libraries](#10-technologies-frameworks-apis--libraries)
11. [Project Architecture / Structure](#11-project-architecture--structure)
12. [Installation and Setup](#12-installation-and-setup)
13. [Environment Variables / Configuration](#13-environment-variables--configuration)
14. [Running the Application Locally](#14-running-the-application-locally)
15. [Deployment Information](#15-deployment-information)
16. [API Endpoints](#16-api-endpoints)
17. [Important Dependencies & Requirements](#17-important-dependencies--requirements)
18. [Team / Contributions](#18-team--contributions)
19. [Future Improvements](#19-future-improvements)
20. [Conclusion](#20-conclusion)

---

## 1. Overview

**WeatherGPT** is a single-page, installable web application that presents real-time and forecast weather data through a dark, glassmorphic "Atmospheric Intelligence" interface. It layers an AI reasoning engine (via **Ollama**) and a speech-to-text engine (via **Groq Whisper**) on top of raw meteorological data (via **Open-Meteo**, with **WeatherAPI.com** as an automatic fallback), so users can read, hear, or ask about the weather in their own language.

The backend is a Python **Flask** application (`backend/app.py`) that serves the static frontend and exposes a small set of JSON API endpoints. The frontend is plain **HTML/CSS/JavaScript** (no build step, no framework) styled with the **Tailwind CSS** CDN build.

## 2. Problem Statement

Standard weather apps typically:
- Present raw numbers (temperature, humidity, wind) without practical, human-readable guidance.
- Are limited to a single language, excluding non-English speakers.
- Require typing/reading, which is inconvenient for hands-free or low-literacy use cases.
- Give little proactive warning about hazardous conditions (heat, cold, wind, UV, rain).

WeatherGPT addresses this by combining accurate telemetry with an LLM that translates the data into plain-language, localized, and actionable advice — reachable by both typed and spoken queries.

## 3. Purpose of the Application

The application's purpose is to act as a **conversational, multilingual "atmospheric intelligence" assistant** that:
- Fetches and normalizes live weather/air-quality data for any searched or GPS-detected location.
- Synthesizes that data into a natural-language forecast summary using an AI model.
- Lets users ask free-form questions about the weather (by typing or speaking) and receive structured, practical answers (activity windows, route conditions, clothing advice).
- Surfaces safety alerts (heat, cold, wind, rain, UV) automatically based on thresholds.
- Works as an installable, offline-shell-capable PWA on Android and iOS.

## 4. Major Features

| # | Feature | Screen |
|---|---------|--------|
| 1 | Live weather telemetry (current, hourly, 7-day) with automatic provider fallback | Forecast |
| 2 | AI-generated narrative weather synopsis | Forecast |
| 3 | Automatic hazard alerts (heat, cold, wind, rain, UV) | Forecast / Insights |
| 4 | Location search, popular-city shortcuts, and device GPS detection | Header / Search Modal |
| 5 | Multilingual voice queries transcribed via Groq Whisper (English / Hindi / Telugu / Auto) | Global (mic button) |
| 6 | Conversational AI assistant chat with structured responses | WeatherGPT tab |
| 7 | Live interactive radar map (Leaflet + real RainViewer precipitation/satellite tiles) centered on the real location | Radar Map tab |
| 8 | Precision analytics / insights dashboard (alerts ledger, pressure, UV, microclimate variance) | Insights tab |
| 9 | Installable Progressive Web App with offline app-shell caching | Global |
| 10 | **Smart Farm Weather Advisor** — crop & stage-aware action advice (irrigation, rain, harvest windows, wind/heat safety, field operations) in EN/HI/TE | Farmer tab |
| 11 | Illustrative "regional microclimate" chips derived from the current temperature | Forecast tab |
| 12 | **Smart Rain Alert** — evidence-backed, localized rain advisory + **Rain Timeline** window bar from the real hourly payload | Forecast tab |

## 5. Feature Details

### 5.1 Live Weather Telemetry
The Flask backend calls the **Open-Meteo Forecast API** for current conditions, hourly data (temperature, humidity, precipitation probability, weather code, UV index, wind), and a daily 7‑day outlook. It also calls the **Open-Meteo Air Quality API** for PM2.5, PM10, US AQI, and European AQI. Results are cached in-memory for **15 minutes** per rounded latitude/longitude/forecast-day combination to reduce redundant calls. Requests to Open-Meteo support up to 7 forecast days. If Open-Meteo responds with an HTTP 429 (rate limited), the backend automatically retries (up to 3 attempts with backoff) and, if still failing, transparently falls back to **WeatherAPI.com** (only when `WEATHERAPI_KEY` is configured), normalizing its response into the same internal data shape. Note that the WeatherAPI.com fallback is capped at **3 forecast days** (vs. 7 for Open-Meteo), so a 7‑day request served via the fallback path will return fewer daily entries.

### 5.2 AI-Generated Narrative Synopsis
On load (and whenever the location/weather refreshes), the frontend calls `/api/report`, sending the current location, full weather payload, alerts, and target language. The backend forwards this to an **Ollama**-hosted model (default `gpt-oss:120b`) with a system prompt (`REPORT_PROMPT`) instructing it to produce a bullet-point "Atmospheric Summary," "Key Meteorological Factors," and "Recommendations" in the requested language. The response is rendered into the hero card using a lightweight Markdown-to-HTML renderer.

### 5.3 Automatic Hazard Alerts
The backend's `build_alerts()` function evaluates the fetched weather data against fixed thresholds and returns a list of structured alerts, each with a severity, type, human-readable text, and a "protocol" (short precautionary steps):
- **Extreme Heat Alert** — current temperature ≥ 40 °C.
- **High Temperature Advisory** — current temperature ≥ 35 °C.
- **Freeze Warning** — current temperature ≤ 0 °C.
- **Gale / Strong Wind Advisory** — sustained wind ≥ 50 km/h or gusts ≥ 65 km/h.
- **High Precipitation Probability** — any forecast day with ≥ 80% rain probability.
- **Extreme UV Index** — any forecast day with a UV index ≥ 8.

These alerts populate both the Forecast screen and a dedicated "Early Warning Alert Ledger" on the Insights screen. When the hourly data shows an upcoming rain window, a **Smart Rain Alert** is prepended to this list (see [5.13](#513-smart-rain-alert--rain-timeline)).

### 5.4 Regional Microclimate Chips
The "Regional Microclimates" scroller on the Forecast tab displays four named chips (Downtown Core, Coastal / Lake, Hilltop / Ridge, Valley Sub-basin). These are **not independently fetched locations** — each chip's temperature is the current fetched temperature plus a fixed, hardcoded offset (0°, −3°, −1°, +2° respectively) defined directly in the frontend code. This is a stylistic/illustrative touch, not a multi-location data feed.

### 5.5 Location Search, Popular Cities & GPS
A search modal lets the user type a city name (resolved via `/api/geocode`, which proxies the Open-Meteo Geocoding API) or pick from a preset list of "Popular Cities" (Hyderabad, San Francisco, New Delhi, London, Tokyo, Bengaluru). A "Use Live Device GPS" button uses the browser's Geolocation API and resolves the coordinates to a place name via `/api/reverse-geocode` (which proxies BigDataCloud's reverse-geocoding client API).

### 5.6 Multilingual Voice Queries (Groq Whisper)
Tapping the microphone icon (header or assistant input) opens a full-screen recording overlay with a 20-second countdown and pulsing "aura" animation. The browser's `MediaRecorder` API records audio (codec auto-negotiated across Chrome/Android `audio/webm;codecs=opus` and Safari/iOS `audio/mp4`). On stop, the audio blob is POSTed to `/api/transcribe`, which sends it to **Groq's Whisper Large v3 Turbo** model. The detected transcript and language are returned, the UI automatically switches to the WeatherGPT chat tab, and the transcript is submitted as a chat query.

### 5.7 WeatherGPT Conversational Assistant
Users can type (or speak) natural-language questions such as "Can I bike outside this afternoon?" or "कल का मौसम कैसा रहेगा?". Each query is sent to `/api/assistant` along with the current location, a short weather summary, and the target language. The backend prompts Ollama to return a strict JSON object containing:
- `answer` — a natural-language response.
- `optimal_window` — a recommended activity time window with a reliability estimate and notes.
- `route_progression` — origin/midway/destination condition notes (used for trip-style queries).
- `attire_guidance` — recommended clothing layers, accessories, and a thermal rating.

The frontend renders this structured JSON as rich chat cards. If the model's JSON response fails to parse, the backend automatically retries with a simpler plain-text prompt and wraps the result in a default card structure so the UI never breaks.

### 5.8 Radar Map View
The Radar Map tab renders a real interactive **Leaflet** map (`frontend/radar-map.js`), on a **CARTO Dark Matter** basemap (requires a free client-side API key — see [Section 13](#13-environment-variables--configuration)), centered on the actual searched/GPS-resolved location with a single real marker labeled with the real current temperature.

Two of the five layer-toggle pills are backed by genuinely live data from **RainViewer's** public API (no key required):
- **Precipitation** — real global radar tiles.
- **Satellite** — real infrared satellite tiles.

The bottom playback slider and play/pause button step through RainViewer's actual past-frame history (roughly the last 2 hours, in 10-minute steps) and, where available, its short-range nowcast frames — not a decorative animation. Radar/satellite tiles are only published up to zoom level 7 (`maxNativeZoom: 7`); the map upscales gracefully beyond that rather than requesting non-existent tiles.

The remaining three pills — **Micro-Temp**, **Wind Vectors**, and **AQI Plume** — are honestly marked with a "Soon" badge (`data-soon="true"`) and show a status message rather than silently doing nothing; they are not yet wired to a live tile source. Wiring them up would need separate per-layer providers (e.g. OpenWeatherMap's tile layers for temperature/wind).

The map screen's address search bar searches via the same `/api/geocode` flow as the header search.

### 5.9 Precision Analytics / Insights
The Insights tab combines real and illustrative elements:
- **Early Warning Alert Ledger** — lists all currently active hazard alerts (from `build_alerts()`) for the selected scope, with severity badges and precaution steps. Today/48h/7d alerts are computed from the real forecast payload.
- **Scope Telemetry Metrics** — Today/48h/7d cards are aggregated from the real current/hourly/daily forecast arrays.
- **Barometric Gradient** and **Solar Radiation & UV** cards — show the live current surface pressure and the forecast peak UV index from the fetched weather payload. (An extended hourly pressure series is not part of the current data feed; the card labels this.)
- **Microclimate Delta Variance Grid** — the "Today" rows are fixed, illustrative placeholder values (labeled as such in the UI), not real per-neighborhood sensor data. The 48h/7d rows are aggregated from real forecast data.

### 5.10 Progressive Web App (Installable)
A `manifest.json` defines the app's name, icons, theme colors, start URL, and Home-Screen shortcuts (Forecast, Radar Map, WeatherGPT AI). A service worker (`sw.js`) pre-caches the app shell (HTML, CSS, JS, icons) using a cache-first strategy for static assets while explicitly bypassing the cache for any `/api/*` request, so live data is always fetched fresh even though the UI itself can load offline.

### 5.11 Smart Farm Weather Advisor

A dedicated **Farmer** tab in the bottom navigation opens the **"What should I do today?"** panel, which turns the same real Open-Meteo payload already powering the app into simple, actionable farm guidance:

1. The farmer selects a **crop** (Rice/Paddy, Cotton, Maize, Groundnut, Wheat, Sugarcane) and **stage** (Sowing, Growing, Flowering, Harvesting).
2. The backend (`backend/farm_advisor.py`) runs a **deterministic, transparent rules engine** over the live forecast — current temp/feels-like, wind + gusts, 3-day rainfall sum, max daily rain probability, and 3-day minimum temperature — and emits topic cards for:
   - **Irrigation** — skip / light / plan, relative to forecast rain vs the crop's typical water need (a phase reference, never a dosage).
   - **Rainfall / sowing windows** — sow before or after a rain, based on the real 3-day sum.
   - **Harvesting** — pause/cover/dry guidance driven by actual rain probability, plus dry-window go-aheads.
   - **Strong wind** — no-spray drift warnings above 30 km/h sustained (45 km/h gusts), with lodging cautions for tall crops; thresholds are safety guidance only — **no pesticide or fertilizer products or dosages are ever suggested**.
   - **Heat** — crop-specific heat-stress threshold (e.g. wheat 32 °C, cotton 38 °C) with worker/livestock safety shifts.
   - **Field operations** — go/hold for ploughing, weeding and input application based on rain and wind; frost protection note when the real 3-day minimum approaches 0 °C.
3. Every number shown is echoed in the response's `weather_snapshot`, so advice is fully auditable against the source forecast.

The engine is translated at the data level — each rule carries English, Hindi and Telugu text — so switching the app language instantly re-renders the advisory via `GET /api/farm-advice` (validated: crop/stage whitelists, lat/lon range, forecast_days integer, 120 req/h rate limit). Unit + API tests for the engine and endpoint live in `backend/test_app.py`.

### 5.12 Hero Weather Glyph

The forecast hero card displays a lightweight inline-SVG cloud/sun glyph whose colors reflect the current condition category. It is decorative and independent of live data.

### 5.12b Premium Living Sky & Light/Dark Themes

The background is a layered, weather-aware atmosphere built entirely from the real Open-Meteo payload (no new frameworks — pure CSS + a few lines of JS):

- **Layered depth** — per-condition multi-stop gradients (12 skies × 2 themes) with embedded radial warm-horizon / electric-storm glows, up to four slowly drifting cloud bands, a sun/moon glow, gently swaying crepuscular rays on fair days, a twinkling sparkle field on snow, plus the existing stars, mist veil, rain and lightning. All state derives from the real WMO condition (`applySkyFx` writes `data-sky`/`data-clouds`/`data-day` on `#sky-fx`).
- **Light/Dark toggle** — a header button (between location and language pills) flips `<html data-theme>`. The choice persists in `localStorage` (`weathergpt-theme`); with no saved choice the app follows the OS `prefers-color-scheme` and keeps following it until the user explicitly toggles. An inline head script applies the theme **before first paint**, so dark mode never flashes white on load. The PWA `theme-color` status-bar tint follows the active theme.
- **Full-surface theming** — the entire Tailwind palette is mapped to rgb-triplet CSS variables re-declared under `html[data-theme="dark"]`, so every tab (Forecast, Radar Map, WeatherGPT, Farmer, Insights), the header, modals, the bottom dock, charts, cards and ink re-theme consistently. AQI status colors and the radar marker label read theme tokens; the radar basemap switches between CARTO light tiles and Dark Matter to match.
- **Readability & motion care** — dark-theme accents are lightened for AA contrast, content sits on a dark paper wash below the hero, and `prefers-reduced-motion` freezes all atmosphere motion (clouds park, rays stop swaying, sparkle stills) alongside the existing rain/lightning rules. Rain drop tint follows the theme (paler drops on dark skies); the rain and thunderstorm effects remain fully functional in both themes.

### 5.13 Smart Rain Alert & Rain Timeline

The Forecast tab gains two data-backed rain surfaces, both derived from the **same real hourly Open-Meteo payload** already powering the app (deterministic logic in `backend/app.py` — no invented numbers):

- **Rain Timeline** — `build_rain_timeline()` scans the next 18 hours of hourly `precipitation_probability` (anchored to the location's own timezone via `utc_offset_seconds`) and finds the longest consecutive run at ≥ 60% lasting at least 2 hours. The frontend renders the window as a bar on a fixed 18 h track (`now → +18h`) with the start/end clock labels and the peak probability; with no qualifying window it truthfully says "No rain expected in the next 18 hours."
- **Smart Rain Alert** — when a window exists, `build_smart_rain_alerts()` emits an advisory ("Rain expected in 3h (14:00) for about 3h — peak chance 80%…", EN/HI/TE) with a `snapshot` echoing every number used (thresholds, start/end ISO times, peak %). It is prepended to `alerts` in `/api/weather`, shown as a blue banner card on the Forecast tab, and localized in the "Today" Insights ledger.

Like the Farm Advisor, this feature is transparent and auditable: thresholds (`RAIN_ALERT_PROB_THRESHOLD=60`, `RAIN_ALERT_MIN_DURATION_H=2`, `RAIN_ALERT_HORIZON_H=18`) are module constants and the raw evidence ships with the alert. Unit tests live in `backend/test_app.py` (`TestRainTimelineAndSmartRainAlerts`).

---

## 6. Key Backend Modules & Functions

All backend logic lives in `backend/app.py` (a single Flask application file).

### Configuration & Setup
- **Environment loading** — searches a list of candidate `.env` file paths (backend-local `.env`, `Weather-Report.env`, and a couple of parent-directory variants) and loads whichever exist via `python-dotenv`.
- **`ollama_client` / `groq_client`** — Ollama and Groq SDK clients, only instantiated if the corresponding API key is present; otherwise AI/voice endpoints report a clear "not configured" error instead of crashing.
- **Flask app** — configured to serve the `frontend/` directory as static content, with CORS restricted to the `/api/*` path prefix (origins configurable via `ALLOWED_ORIGINS`).

### Caching
- `_cache_get(cache_key)` / `_cache_set(cache_key, data)` — simple in-memory dictionary cache with a 15-minute TTL (`_WEATHER_CACHE_TTL_SECONDS`), keyed by rounded latitude/longitude and forecast-day count.

### Weather Data Services
- `_fetch_open_meteo(latitude, longitude, forecast_days)` — calls Open-Meteo's `/v1/forecast` endpoint for current/hourly/daily data, with up to 3 retry attempts and exponential-ish backoff on HTTP 429, then merges in air-quality data.
- `_fetch_air_quality(latitude, longitude)` — calls Open-Meteo's Air Quality API; returns safe default values if the call fails.
- `_fetch_weatherapi(latitude, longitude, forecast_days)` — fallback provider; calls WeatherAPI.com and **normalizes** its response into the same schema used for Open-Meteo data (current/hourly/daily/air_quality), so the rest of the app doesn't need to know which provider served the data.
- `_condition_text_to_wmo(text)` — maps WeatherAPI's plain-text condition descriptions (e.g. "thunder," "drizzle," "overcast") to the numeric WMO weather codes used elsewhere in the app.
- `fetch_weather(latitude, longitude, forecast_days)` — the main orchestrator: checks the cache, tries Open-Meteo first, and transparently falls back to WeatherAPI.com on a 429 if a fallback key is configured.
- `build_alerts(weather_data)` — evaluates temperature, wind, rain-probability, and UV thresholds and returns a list of structured hazard alerts (see [Section 5.3](#53-automatic-hazard-alerts)).
- `build_rain_timeline(weather_data)` — scans the next 18 h of hourly precipitation probability (location-local clock) and returns the next rain-window summary (`has_event`, `starts_in_h`, `duration_h`, `start_label`/`end_label`, `peak_probability`, …) or a truthful "no rain" shape (see [5.13](#513-smart-rain-alert--rain-timeline)).
- `build_smart_rain_alerts(timeline, language)` — turns a rain timeline into a localized advisory with a fully auditable `snapshot`.

### Voice / AI Helpers
- `guess_suffix(uploaded_file)` — infers an audio file extension (`.ogg`, `.wav`, `.m4a`, or default `.webm`) from the uploaded file's MIME type/name, used when writing a temp file for transcription.
- `normalize_language(raw_value, fallback_code)` — normalizes a language code or name (English/Hindi/Telugu) into a canonical `(code, name)` pair.
- `transcribe_audio(audio_path, preferred_language)` — sends the recorded audio file to Groq's Whisper API (`whisper-large-v3-turbo` by default) and returns the transcript plus detected language.
- `clean_json(raw_text)` — strips Markdown code fences and extracts the first `{...}` JSON object from an LLM response, then parses it — used to robustly parse structured Ollama output.

### Prompt Templates
- `ANALYZE_PROMPT` — instructs the model to extract structured intent (location, language, forecast days, weather focus, time reference) from a free-text query.
- `REPORT_PROMPT` — instructs the model to produce the bullet-point atmospheric summary described in [5.2](#52-ai-generated-narrative-synopsis).
- `ASSISTANT_PROMPT` — instructs the model to return the structured JSON chat response described in [5.7](#57-weathergpt-conversational-assistant).

## 7. Key Frontend Modules & Functions

All client logic lives in `frontend/script.js` (vanilla JavaScript, no bundler).

- **`state`** — a single in-memory object holding the current location, fetched weather/alerts/air-quality data, selected voice language, active view/tab, active map layer, and voice-recording state.
- **`getConditionCategory(code, isDay)`** — maps a WMO weather code (plus day/night flag) to a visual condition category (e.g. `clear-day`, `cloudy-night`, `rain`) used to theme the background glow and hero glyph.
- **`switchView(targetViewId)`** — toggles which of the four `<section>` "screens" (Forecast, Radar Map, WeatherGPT, Insights) is visible and updates the bottom nav's active tab.
- **`refreshWeatherData()`** — calls `/api/weather` for the current coordinates and updates `state.weather`, `state.alerts`, and `state.airQuality`, then triggers all screen render functions.
- **`renderForecastScreen()` / `renderMapScreen()` / `renderInsightsScreen()`** — populate each tab's DOM elements from the current `state`.
- **`generateAiReport()`** — calls `/api/report` to fetch the AI narrative synopsis and injects it into the hero card.
- **`submitAssistantQuery(userQuery)`** — appends the user's message to the chat stream, shows a "thinking" placeholder, calls `/api/assistant`, and renders the structured response (or a graceful fallback message on error).
- **`appendUserChatBubble` / `appendThinkingBubble` / `removeThinkingBubble` / `appendAssistantResponseNode`** — chat-stream DOM rendering helpers.
- **`pickSupportedMimeType()` / `startVoiceRecording()` / `stopVoiceRecording()` / `cancelVoiceRecording()` / `handleVoiceRecordingFinished()`** — the full voice-capture lifecycle: microphone permission, `MediaRecorder` setup, 20-second countdown, and posting the recorded clip to `/api/transcribe`.
- **`renderMarkdownLite(rawText)` / `escapeHTML(str)`** — a minimal, dependency-free Markdown-to-HTML renderer (bold, bullet lists, line breaks) with HTML-escaping for safety.
- **`getJSON(path)` / `postJSON(path, body)`** — small `fetch()` wrappers used by every API call above.
- **`radar-map.js`** — initializes the real Leaflet radar map, fetches and caches RainViewer's live frame list, switches between the precipitation/satellite layers, and steps the playback slider through real frames (see [5.8](#58-radar-map-view)).
- **`sw.js`** — the service worker implementing the cache-first app-shell / network-first API strategy described in [5.10](#510-progressive-web-app-installable).

---

## 8. How the Application Works — Step-by-Step User Flow

1. **App load** — The browser loads `index.html`, registers the service worker, and JavaScript initializes with a default location (San Francisco) while it attempts to detect the user's real location.
2. **Location resolution** — The app either uses the default/last-searched location, a location chosen via the search modal or popular-city chips, or the device's GPS (reverse-geocoded via `/api/reverse-geocode`).
3. **Weather fetch** — `refreshWeatherData()` calls `/api/weather`, which (server-side) fetches from Open-Meteo (or WeatherAPI.com on rate-limit fallback), computes hazard alerts, and returns weather + alerts + air quality.
4. **Screen render** — The Forecast, Radar Map, and Insights screens are populated from this single weather payload (including the Rain Timeline bar and Smart Rain Alert banner on the Forecast tab).
5. **AI synopsis** — In parallel, `generateAiReport()` sends the same weather payload to `/api/report`, and the AI-written summary appears in the hero card.
6. **User interaction — typed question** — The user types a question in the WeatherGPT tab (or a quick-ask box on the Forecast tab) and submits it; `submitAssistantQuery()` sends it to `/api/assistant` and renders the structured AI answer as a chat card.
7. **User interaction — voice question** — The user taps the mic icon, speaks for up to 20 seconds, and the recording is transcribed via `/api/transcribe` (Groq Whisper); the transcript is automatically routed into the same assistant flow as step 6.
8. **Language switching** — Selecting EN / HI / TE in the header changes `state.voiceLang`, which is sent as the target language on all subsequent `/api/report` and `/api/assistant` calls and as the transcription language hint.
9. **Alerts surfacing** — If any hazard alert was generated in step 3, it is shown on both the Forecast screen and the Insights "Early Warning Alert Ledger"; an upcoming rain window additionally raises the localized Smart Rain Alert.
10. **Offline shell** — On repeat visits (or with no network), the service worker serves the cached app shell instantly; only the live API calls (weather, report, assistant, transcribe) require connectivity.

## 9. How to Use the Application

1. Open the app in a browser (or launch it from the home screen if installed as a PWA).
2. Allow location access when prompted, or tap the location pill in the header to search for a city, pick a popular city, or use GPS.
3. Choose a language (EN / HI / TE / Auto) using the pills in the header.
4. Review the Forecast tab for current conditions, the AI synopsis, hourly trajectory, and the 2×2 telemetry grid (wind, AQI, humidity, pressure).
5. On the Forecast tab, open the **\"What should I do today?\"** card, pick your crop and crop stage, and read the farm advisory (irrigation, rain, harvest, wind/heat safety, field operations) — it follows the selected app language.
6. Switch to the **Radar Map** tab to view the live radar map and tap the marker for a sensor readout of the current location.
7. Ask questions from any tab using the bottom "Ask anything" bar, or open the **WeatherGPT** tab, type a question, or tap the microphone to ask by voice; review the AI's answer, optimal activity window, route notes, and clothing guidance.
8. Switch to the **Insights** tab to review any active hazard alerts and pressure/UV trends in more detail.
9. Toggle **Light/Dark mode** with the moon/sun button in the header — the choice is remembered across visits (and defaults to your device's own light/dark setting until you pick).
10. Install the app to your home screen (Android: browser menu → "Install app"/"Add to Home screen"; iOS Safari: Share → "Add to Home Screen") for a full-screen, app-like experience.

## 10. Technologies, Frameworks, APIs & Libraries

**Backend**

| Technology | Purpose |
|---|---|
| Python 3 | Runtime |
| Flask | Web framework / API server |
| Flask-CORS | Cross-origin request handling for `/api/*` |
| python-dotenv | Loads API keys from `.env` files |
| requests | HTTP client for outbound API calls |
| ollama (Python SDK) | Client for the Ollama-hosted LLM |
| groq (Python SDK) | Client for Groq's Whisper transcription API |
| gunicorn | Production WSGI server |

**Frontend**

| Technology | Purpose |
|---|---|
| HTML5 / vanilla JavaScript | App structure and logic (no framework, no bundler) |
| Tailwind CSS (CDN build) | Utility-first styling, loaded and configured directly in `index.html` |
| Google Fonts (Outfit, Inter, Noto Sans Devanagari, Noto Sans Telugu, Material Symbols) | Typography and icons |
| Service Worker API | Offline app-shell caching |
| Web Manifest (`manifest.json`) | PWA installability, icons, shortcuts |
| CSS custom properties + `prefers-color-scheme` | Dual-theme token system (Light/Dark) without a framework |
| MediaRecorder / getUserMedia (Web APIs) | In-browser audio recording for voice queries |
| Geolocation API | Device GPS lookup |
| Leaflet.js (CDN) | Interactive map rendering for the Radar Map tab |

**External / Third-Party APIs**

| API | Used For |
|---|---|
| Open-Meteo Forecast API | Current, hourly, and 7-day weather data |
| Open-Meteo Air Quality API | PM2.5, PM10, US AQI, European AQI |
| Open-Meteo Geocoding API | City name → coordinates |
| BigDataCloud Reverse Geocode Client API | Coordinates → place name (for GPS) |
| WeatherAPI.com | Fallback weather + air quality provider when Open-Meteo is rate-limited |
| Ollama Cloud (model: `gpt-oss:120b` by default) | AI reasoning for report synthesis and conversational assistant |
| Groq API (model: `whisper-large-v3-turbo` by default) | Speech-to-text voice transcription |
| RainViewer Weather Maps API | Live precipitation radar and infrared satellite map tiles (no key required) |
| CARTO Basemaps (Dark Matter, raster tiles) | Dark-themed base map tiles for the Radar Map tab (free, browser-embeddable key — set via the `CARTO_API_KEY` env var, served to the client through `/api/config`) |

## 11. Project Architecture / Structure

```
Weather-Report/
├── README.md
├── .gitignore
├── backend/
│   ├── app.py                 # Flask app: routes, weather fetch/fallback, alerts, AI & voice integration
│   └── requirements.txt       # Python dependencies
└── frontend/
    ├── index.html              # Single-page app markup (4 views: Forecast, Map, WeatherGPT, Insights)
    ├── style.css                # Custom styling supplementing Tailwind
    ├── script.js                # Client state, rendering, and all API integrations
    ├── radar-map.js               # Real Leaflet + RainViewer radar/satellite map
    ├── sw.js                     # Service worker (offline app-shell caching)
    ├── manifest.json             # PWA manifest
    └── icons/                    # App icons (192px, 512px, maskable, favicon, logo)
```

**Architecture pattern:** A single Flask process serves both the static frontend (via `send_from_directory`) and a small JSON REST API under `/api/*`. There is no database — all "state" is either transient (the 15-minute in-memory weather cache) or held client-side in the browser's `state` object. The frontend never calls third-party weather/AI APIs directly; it only talks to the Flask backend, which proxies and normalizes all external calls server-side (keeping API keys off the client).

## 12. Installation and Setup

### Prerequisites
- Python 3.9+ (compiled bytecode found in the project indicates Python 3.14 was used in development, but any modern Python 3 should work)
- `pip`
- API credentials for Ollama and Groq (required for AI/voice features — see [Section 13](#13-environment-variables--configuration))
- An internet connection (all weather/geocoding/AI calls are made to external services)

### Steps
1. Clone or extract the project so you have the `backend/` and `frontend/` folders side by side.
2. Install Python dependencies:
   ```bash
   cd backend
   pip install -r requirements.txt
   ```
3. Create a `.env` file (see next section) inside `backend/` with your API keys.
4. Run the server (see [Section 14](#14-running-the-application-locally)).

## 13. Environment Variables / Configuration

The backend automatically searches several candidate paths for an env file (a `.env` inside `backend/`, a `Weather-Report.env` or `.env` one level up, etc.) and loads whichever is found. Create a `.env` file in `backend/` with the following keys (values omitted for security — replace with your own credentials):

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OLLAMA_API_KEY` | Yes, for AI report/assistant features | — | Auth token for the Ollama client |
| `OLLAMA_HOST` | No | `https://ollama.com` | Ollama API host |
| `OLLAMA_MODEL` | No | `gpt-oss:120b` | Model used for report synthesis and the assistant |
| `GROQ_API_KEY` | Yes, for voice transcription | — | Auth token for the Groq client |
| `GROQ_WHISPER_MODEL` | No | `whisper-large-v3-turbo` | Whisper model used for transcription |
| `WEATHERAPI_KEY` | No | — | Enables automatic fallback to WeatherAPI.com when Open-Meteo is rate-limited |
| `ALLOWED_ORIGINS` | No | _(empty — CORS disabled)_ | Comma-separated list of allowed CORS origins for `/api/*`; leave empty when the frontend is served by the same Flask app |
| `PORT` | No | `5000` | Port the Flask app listens on |
| `FLASK_DEBUG` | No | `_(off)_` | Set to `1` **only for local development** — enables Flask's interactive debugger |
| `CARTO_API_KEY` | No | _(bundled fallback)_ | CARTO basemaps key served via `/api/config` for the Radar Map tab |
| `MAX_UPLOAD_MB` | No | `10` | Maximum accepted upload size for `/api/transcribe` (MB) |
| `RATE_*` | No | _(see app.py)_ | Per-IP hourly rate limits for the AI/voice/geocode/farm endpoints (`RATE_TRANSCRIBE`, `RATE_ANALYZE`, `RATE_REPORT`, `RATE_ASSISTANT`, `RATE_GEOCODE`, `RATE_FARM_ADVICE`) |

If `OLLAMA_API_KEY` or `GROQ_API_KEY` are missing, the server still starts (with a console warning) but the corresponding endpoints (`/api/report`, `/api/assistant`, `/api/analyze`, `/api/transcribe`) will return an explicit error response instead of failing silently.

**Never commit real API key values to version control.** Add `.env` to `.gitignore`.

**Note on the CARTO basemap key:** the Radar Map tab's basemap requires a separate, free CARTO API key (get one at [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey), no account needed). This key is set directly in `frontend/radar-map.js` (`CARTO_API_KEY` constant), **not** in `.env` — CARTO designs this key to be public/client-side-embeddable (like a Google Maps browser key), unlike the Ollama/Groq keys above, which must stay server-side.

## 14. Running the Application Locally

From the `backend/` directory:
```bash
python app.py
```
This starts the Flask development server on `http://0.0.0.0:5000` by default (or the port set in `PORT`). The interactive debugger is **off** by default — set `FLASK_DEBUG=1` only for local development. Open `http://localhost:5000` in a browser — the Flask app serves the frontend directly from the same origin, so no separate frontend server is needed.

For a production-style run, use the included `gunicorn` dependency instead of the Flask dev server, for example:
```bash
cd backend
gunicorn app:app --bind 0.0.0.0:5000
```

## 15. Deployment Information

### 15.1 Backend + Web App — Render

The live site is deployed as a single **Render Web Service**, using the `gunicorn` dependency already in `backend/requirements.txt` as the production WSGI server (rather than Flask's own development server):

- **Root directory:** `backend`
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`
- **Environment variables:** all keys from [Section 13](#13-environment-variables--configuration) are set directly in Render's dashboard (Environment tab) rather than committing a `.env` file.

Since Flask serves the frontend as static files from the same app (`send_from_directory`), this single web service handles both the API and the UI — no separate static-site hosting is required. Render's free tier spins the service down after periods of inactivity, so the first request after idle time can take 20–50 seconds while it wakes back up.

### 15.2 Android & iOS Packaging — PWABuilder

Because the app is a fully installable PWA (manifest + service worker, see [5.10](#510-progressive-web-app-installable)), the Android and iOS app packages are generated from the **live deployed URL** using [PWABuilder](https://www.pwabuilder.com/) rather than hand-written native projects:

- **Android:** PWABuilder produces a signed `.apk` (for direct sideload/testing) and a `.aab` (for Google Play submission), built around a Trusted Web Activity that opens the deployed Render URL full-screen with no browser chrome. A `signing.keystore` and its key info are generated alongside — these must be kept private and backed up, since they're required to publish any future update to the same Play Store listing.
- **iOS:** PWABuilder produces an Xcode project (`.xcworkspace` + `Podfile`) wrapping the same URL. This still requires a Mac with Xcode (and CocoaPods, via `pod install`) to actually build and sign, since Apple doesn't allow distributing pre-built iOS binaries outside their own tooling — free Apple ID builds are limited to your own connected device with a 7-day-expiring signature; App Store or TestFlight distribution needs the paid Apple Developer Program.

Both packages depend on the Render deployment already being live, since neither bundles the app's code directly — they're thin native shells pointed at the deployed site.

## 16. API Endpoints

All endpoints are served by the same Flask app; all except `/` are prefixed with `/api/`.

| Method | Endpoint | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/` | Serves the frontend `index.html` | — | HTML |
| GET | `/api/health` | Health/status check | — | `{status, groq_whisper, groq_model, ollama, ollama_model, weatherapi_fallback, timestamp}` |
| POST | `/api/transcribe` | Transcribes recorded audio via Groq Whisper | multipart form: `audio` file, `preferred_language` | `{transcript, language_code, language}` |
| POST | `/api/analyze` | Extracts structured intent (location, language, forecast days, focus) from free text via Ollama | JSON: `{query}` | Parsed JSON object per `ANALYZE_PROMPT` |
| GET | `/api/geocode` | Resolves a place name to coordinates (Open-Meteo Geocoding) | query: `location` | `{name, admin1, country, country_code, latitude, longitude, timezone}` |
| GET | `/api/reverse-geocode` | Resolves coordinates to a place name (BigDataCloud) | query: `latitude`, `longitude` | `{name, admin1, country, latitude, longitude}` |
| GET | `/api/weather` | Fetches current/hourly/daily weather, alerts (incl. Smart Rain Alert), rain timeline, and air quality | query: `latitude`, `longitude`, `forecast_days` (optional, default 7) | `{weather, alerts, air_quality, rain_timeline}` |
| GET | `/api/farm-advice` | Deterministic crop & stage farm advisory from real forecast data | query: `crop`, `stage`, `latitude`, `longitude`, `language` (en/hi/te), `forecast_days` (optional) | `{crop, stage, headline, advice[], crop_note, disclaimer, weather_snapshot}` |
| GET | `/api/crops` | Crop & stage catalogue for the advisor selectors | — | `{crops[], stages[]}` with EN/HI/TE names |
| POST | `/api/report` | Generates the AI narrative weather synopsis | JSON: `{location, weather_data, alerts, language}` | `{report}` (Markdown-style text) |
| POST | `/api/assistant` | Conversational AI assistant | JSON: `{query, location_name, weather_summary, language}` | `{answer, optimal_window, route_progression, attire_guidance}` |

All endpoints return a JSON `{"error": "..."}` body with an appropriate HTTP status code (400/404/502/503) on failure.

## 17. Important Dependencies & Requirements

**Python packages** (from `backend/requirements.txt`):
```
Flask>=3.0
flask-cors>=4.0
requests>=2.31
python-dotenv>=1.0
ollama>=0.4
groq>=0.13.0
gunicorn>=23.0
```

**Browser requirements:** A modern browser supporting the Geolocation API, `MediaRecorder`, `getUserMedia`, and Service Workers is required for the full feature set (voice input and offline shell). Voice recording requires microphone permission and a supported audio codec (Chrome/Android: `audio/webm;codecs=opus`; Safari/iOS: `audio/mp4`).

## 18. Team / Contributions

> _Team information was not present in the provided project files. Add your team details below before submission._

| Name | Role | Contribution |
|---|---|---|
| _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ |
| _TBD_ | _TBD_ | _TBD_ |

## 19. Future Improvements

- Persist user preferences (location, language) across sessions instead of resetting on reload.
- Replace the illustrative microclimate values with genuine per-neighborhood model or sensor data.
- Wire the remaining "Soon" radar layers (Micro-Temp, Wind Vectors, AQI Plume) to real tile providers (e.g. OpenWeatherMap tile layers), now that Precipitation and Satellite are live.
- Replace the hardcoded microclimate chip offsets and the Insights "Delta Variance" bars with genuine per-neighborhood sensor or model data, and wire up the currently non-functional "Today / 48 Hours / 7 Days" scope chips.
- ~~Add automated tests for the alert-threshold logic~~ — done: run `cd backend && python test_app.py` (62 tests covering alert thresholds, the rain timeline / Smart Rain Alert logic, input validation, cache eviction, the API contract, and the farm advisor engine + endpoint).
- Add a `render.yaml` for infrastructure-as-code alongside the existing dashboard-configured Render deployment, plus a CI pipeline.
- Expand supported languages beyond English, Hindi, and Telugu.
- Add server-side rate limiting and input validation hardening on all `/api/*` endpoints.

## 20. Conclusion

WeatherGPT demonstrates how live meteorological data can be made more accessible and actionable by pairing it with an LLM-driven reasoning layer and multilingual voice input. Its Flask backend cleanly separates data acquisition (with automatic provider fallback and caching) from AI synthesis (report generation and conversational assistant), while the dependency-free frontend delivers an installable, app-like experience across Android and iOS. The project provides a solid, extensible foundation for a genuinely useful, localized weather assistant.
