import os
import json
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from ollama import Client
from groq import Groq

from farm_advisor import CROPS, STAGES, VALID_CROPS, VALID_STAGES, build_farm_advice, validate_params

# ============================================================
# CONFIGURATION & ENV LOADING
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

env_candidates = [
    os.path.join(BASE_DIR, ".env"),
    os.path.join(BASE_DIR, "..", "Weather-Report.env"),
    os.path.join(BASE_DIR, "..", ".env"),
    os.path.join(BASE_DIR, "..", "..", "Weather-Report", "GroqVersion", "Weather-Report.env"),
    os.path.join(BASE_DIR, "..", "..", ".env"),
]
for candidate in env_candidates:
    if os.path.exists(candidate):
        load_dotenv(candidate, override=False)

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "https://ollama.com")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY")
# CARTO's browser-embeddable basemap key (designed to be public, like a
# Google Maps browser key) — served via /api/config so it can be rotated
# through the environment without editing frontend code.
CARTO_API_KEY = os.getenv("CARTO_API_KEY", "cb1_32f8_1_a8e4293f180f1d30d3508676")

if not OLLAMA_API_KEY:
    print("[WARNING] OLLAMA_API_KEY is not set. AI reasoning endpoints will return error.")
if not GROQ_API_KEY:
    print("[WARNING] GROQ_API_KEY is not set. Groq Whisper transcription will return error.")

ollama_client = Client(host=OLLAMA_HOST, headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"}) if OLLAMA_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
# Reject oversized uploads (e.g. huge files POSTed to /api/transcribe).
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024

# The frontend is served same-origin, so cross-origin API access is never
# needed. Only opt in explicitly (ALLOWED_ORIGINS=origin1,origin2) — the
# previous default of "*" let any website call (and read) the paid
# Ollama/Groq-backed endpoints.
_allowed = os.getenv("ALLOWED_ORIGINS", "").strip()
if _allowed:
    _origins = [o.strip() for o in _allowed.split(",") if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": _origins}})

# ============================================================
# SIMPLE IN-PROCESS RATE LIMITER (per client IP)
# Protects the paid AI/voice endpoints from quota-draining loops.
# ============================================================

_RATE_LIMITS = {
    "/api/transcribe": (int(os.getenv("RATE_TRANSCRIBE", "10")), 3600),
    "/api/analyze": (int(os.getenv("RATE_ANALYZE", "60")), 3600),
    "/api/report": (int(os.getenv("RATE_REPORT", "60")), 3600),
    "/api/assistant": (int(os.getenv("RATE_ASSISTANT", "60")), 3600),
    "/api/geocode": (int(os.getenv("RATE_GEOCODE", "120")), 3600),
    "/api/farm-advice": (int(os.getenv("RATE_FARM_ADVICE", "120")), 3600),
}
_rate_lock = threading.Lock()
_RATE_HITS = {}


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"

def _check_rate_limit():
    limit, window = _RATE_LIMITS.get(request.path, (None, None))
    if not limit:
        return None
    key = (request.path, _client_ip())
    now = time.time()
    with _rate_lock:
        hits = _RATE_HITS.get(key)
        if hits is None:
            _RATE_HITS[key] = (now, 1)
            return None
        if now - hits[0] >= window:
            _RATE_HITS[key] = (now, 1)
            return None
        _RATE_HITS[key] = (hits[0], hits[1] + 1)
        if hits[1] + 1 > limit:
            retry_after = int(window - (now - hits[0])) + 1
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429, {"Retry-After": retry_after}
    return None


@app.before_request
def _enforce_rate_limit():
    if request.path.startswith("/api/"):
        limited = _check_rate_limit()
        if limited is not None:
            return limited


@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.errorhandler(413)
def _upload_too_large(_error):
    return jsonify({"error": "Uploaded file is too large (max 10 MB)."}), 413


@app.errorhandler(404)
def _not_found(_error):
    # Distinguish missing static assets from unknown API paths.
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def _internal_error(error):
    app.logger.exception("Internal server error: %s", error)
    return jsonify({"error": "Internal server error"}), 500

LANG_CODE_TO_NAME = {"en": "English", "hi": "Hindi", "te": "Telugu"}
LANG_NAME_TO_CODE = {"english": "en", "hindi": "hi", "telugu": "te"}

ANALYZE_PROMPT = """Extract weather request data. Return ONLY JSON with location, language, forecast_days, weather_focus, time_reference. User language may be English, Hindi or Telugu. If no location, location is null. forecast_days: current/today=1, tomorrow=2, next 3 days=3, next 5 days=5, week=7. weather_focus: general,rain,temperature,humidity,uv,wind,clothing,alerts."""

REPORT_PROMPT = """You are WeatherGPT, a meteorological intelligence assistant. Using the provided request, location, Open-Meteo weather data and alerts, answer accurately.
Respond ONLY in {language}.
Format your response using concise bullet points and bold section labels:
- **Atmospheric Summary**: Brief condition and temperature trend.
- **Key Meteorological Factors**: Rain chance, wind, humidity, UV index.
- **Recommendations**: Practical advice for travel, outdoor activities, or clothing.
Use Celsius, km/h and mm. Be concise and practical."""

ASSISTANT_PROMPT = """You are WeatherGPT, an advanced Atmospheric Intelligence Assistant.
The user is asking: "{query}".
Current Location: {location_name}
Current Weather Telemetry: {weather_summary}
Language: Respond in {language}.

Return a valid JSON object with the following keys:
{{
  "answer": "A clear, natural-language conversational response addressing the user query in {language}. Explain what the conditions mean practically for their activity or question.",
  "optimal_window": {{
    "title": "Optimal Activity Window",
    "time_range": "e.g., 1:30 PM – 4:15 PM or Morning Hours",
    "reliability": "e.g., 94% Reliability",
    "favorable_note": "e.g., Winds < 12 km/h, pleasant humidity",
    "caution_note": "e.g., Gusts or cooling after 5:00 PM"
  }},
  "route_progression": [
    {{"label": "Origin", "place": "{location_name}", "temp": "Current Temp", "condition": "Condition notes"}},
    {{"label": "Midway", "place": "En route", "temp": "Midway Temp", "condition": "Wind/exposure notes"}},
    {{"label": "Destination", "place": "Target Zone", "temp": "Target Temp", "condition": "Arrival conditions"}}
  ],
  "attire_guidance": {{
    "headline": "Recommended Attire",
    "layers": ["Base layer description", "Mid/Outer layer description"],
    "accessories": ["Hat, sunglasses, or rain gear"],
    "thermal_rating": "Comfortable / Mild / Chilly / Cold / Hot"
  }}
}}
Return ONLY the JSON object. Do not wrap in markdown quotes if possible."""

# ============================================================
# IN-MEMORY CACHE
# ============================================================

_WEATHER_CACHE = OrderedDict()  # LRU: most-recently-used at the end
_WEATHER_CACHE_TTL_SECONDS = 900  # 15 minutes
_WEATHER_CACHE_MAX_ENTRIES = 500
_cache_lock = threading.Lock()


def _cache_get(cache_key):
    with _cache_lock:
        cached = _WEATHER_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _WEATHER_CACHE_TTL_SECONDS:
            _WEATHER_CACHE.move_to_end(cache_key)
            return cached[1]
        if cache_key in _WEATHER_CACHE:
            del _WEATHER_CACHE[cache_key]  # expired
    return None


def _cache_set(cache_key, data):
    with _cache_lock:
        _WEATHER_CACHE[cache_key] = (time.time(), data)
        _WEATHER_CACHE.move_to_end(cache_key)
        while len(_WEATHER_CACHE) > _WEATHER_CACHE_MAX_ENTRIES:
            _WEATHER_CACHE.popitem(last=False)


def clean_json(raw_text):
    text = (raw_text or "").replace("```json", "").replace("```", "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return json.loads(text[start:end + 1])
    return json.loads(text)


# ============================================================
# WEATHER DATA SERVICES
# ============================================================

# Outbound HTTP hardening: never hang a worker waiting on a third-party API.
_REQUEST_TIMEOUT = (int(os.getenv("HTTP_CONNECT_TIMEOUT", "5")), int(os.getenv("HTTP_READ_TIMEOUT", "15")))


def _condition_text_to_wmo(text):
    t = (text or "").lower()
    if "thunder" in t:
        return 95
    if "snow" in t or "sleet" in t or "ice pellet" in t or "blizzard" in t:
        return 73
    if "freezing" in t and "rain" in t:
        return 66
    if "drizzle" in t:
        return 51
    if "rain" in t or "shower" in t:
        return 63
    if "mist" in t or "fog" in t:
        return 45
    if "overcast" in t:
        return 3
    if "cloud" in t:
        return 2 if "partly" in t else 3
    if "clear" in t or "sunny" in t:
        return 0
    return 2


def _fetch_air_quality(latitude, longitude):
    try:
        r = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "pm10,pm2_5,us_aqi,european_aqi",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("current", {})
    except Exception as e:
        print(f"[air-quality] fetch failed: {e}")
    return {"pm2_5": 8.0, "pm10": 12.0, "us_aqi": 35, "european_aqi": 20}


def _fetch_open_meteo(latitude, longitude, forecast_days):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,rain,weather_code,wind_speed_10m,wind_direction_10m,"
            "wind_gusts_10m,surface_pressure,dew_point_2m,is_day"
        ),
        "hourly": (
            "temperature_2m,relative_humidity_2m,precipitation_probability,"
            "precipitation,weather_code,uv_index,wind_speed_10m"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,precipitation_probability_max,"
            "uv_index_max,wind_speed_10m_max"
        ),
        "forecast_days": max(1, min(int(forecast_days), 7)),
        "timezone": "auto",
    }

    max_attempts = 3
    backoff_seconds = 1.5
    last_error = None

    for attempt in range(1, max_attempts + 1):
        response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=_REQUEST_TIMEOUT)

        if response.status_code == 429:
            last_error = requests.HTTPError(response=response)
            if attempt < max_attempts:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff_seconds * attempt
                time.sleep(wait)
                continue
            break

        response.raise_for_status()
        data = response.json()

        # Augment with Air Quality telemetry
        aqi_data = _fetch_air_quality(latitude, longitude)
        data["air_quality"] = aqi_data
        return data

    raise last_error


def _fetch_weatherapi(latitude, longitude, forecast_days):
    if not WEATHERAPI_KEY:
        raise RuntimeError("No WEATHERAPI_KEY configured, cannot fall back")

    days = max(1, min(int(forecast_days), 3))
    response = requests.get(
        "https://api.weatherapi.com/v1/forecast.json",
        params={
            "key": WEATHERAPI_KEY,
            "q": f"{latitude},{longitude}",
            "days": days,
            "aqi": "yes",
            "alerts": "no",
        },
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    raw = response.json()

    current = raw.get("current", {})
    forecast_days_list = raw.get("forecast", {}).get("forecastday", [])
    first_day_hours = forecast_days_list[0].get("hour", []) if forecast_days_list else []

    hourly_times = [h.get("time") for h in first_day_hours]
    hourly_temps = [h.get("temp_c") for h in first_day_hours]
    hourly_humidity = [h.get("humidity") for h in first_day_hours]
    hourly_rain_prob = [h.get("chance_of_rain") for h in first_day_hours]
    hourly_weather_codes = [_condition_text_to_wmo(h.get("condition", {}).get("text")) for h in first_day_hours]
    hourly_uv = [h.get("uv") for h in first_day_hours]
    hourly_wind = [h.get("wind_kph") for h in first_day_hours]

    aqi_obj = current.get("air_quality", {})
    pm2_5 = aqi_obj.get("pm2_5", 8.0)
    pm10 = aqi_obj.get("pm10", 12.0)
    us_epa_index = aqi_obj.get("us-epa-index", 1)
    approx_us_aqi = min(300, max(10, int(us_epa_index * 25)))

    normalized = {
        "current": {
            "temperature_2m": current.get("temp_c"),
            "relative_humidity_2m": current.get("humidity"),
            "apparent_temperature": current.get("feelslike_c"),
            "precipitation": current.get("precip_mm"),
            "rain": current.get("precip_mm"),
            "weather_code": _condition_text_to_wmo(current.get("condition", {}).get("text")),
            "wind_speed_10m": current.get("wind_kph"),
            "wind_direction_10m": current.get("wind_degree", 180),
            "wind_gusts_10m": current.get("gust_kph", current.get("wind_kph", 0) * 1.3),
            "surface_pressure": current.get("pressure_mb", 1013.25),
            "dew_point_2m": current.get("dewpoint_c", (current.get("temp_c", 20) - 4)),
            "is_day": current.get("is_day", 1),
        },
        "hourly": {
            "time": hourly_times,
            "temperature_2m": hourly_temps,
            "relative_humidity_2m": hourly_humidity,
            "precipitation_probability": hourly_rain_prob,
            "weather_code": hourly_weather_codes,
            "uv_index": hourly_uv,
            "wind_speed_10m": hourly_wind,
        },
        "daily": {
            "time": [d.get("date") for d in forecast_days_list],
            "weather_code": [_condition_text_to_wmo(d.get("day", {}).get("condition", {}).get("text")) for d in forecast_days_list],
            "temperature_2m_max": [d.get("day", {}).get("maxtemp_c") for d in forecast_days_list],
            "temperature_2m_min": [d.get("day", {}).get("mintemp_c") for d in forecast_days_list],
            "precipitation_sum": [d.get("day", {}).get("totalprecip_mm") for d in forecast_days_list],
            "precipitation_probability_max": [d.get("day", {}).get("daily_chance_of_rain") for d in forecast_days_list],
            "uv_index_max": [d.get("day", {}).get("uv") for d in forecast_days_list],
            "wind_speed_10m_max": [d.get("day", {}).get("maxwind_kph") for d in forecast_days_list],
        },
        "air_quality": {
            "pm2_5": pm2_5,
            "pm10": pm10,
            "us_aqi": approx_us_aqi,
            "european_aqi": min(100, int(approx_us_aqi * 0.6)),
        },
    }
    return normalized


def fetch_weather(latitude, longitude, forecast_days=7):
    try:
        forecast_days = int(forecast_days)
    except (TypeError, ValueError):
        raise ValueError("forecast_days must be an integer between 1 and 7")
    forecast_days = max(1, min(forecast_days, 7))
    cache_key = (round(float(latitude), 2), round(float(longitude), 2), forecast_days)

    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        data = _fetch_open_meteo(latitude, longitude, forecast_days)
        print(f"[weather] served by Open-Meteo for {cache_key}")
        _cache_set(cache_key, data)
        return data
    except requests.HTTPError as open_meteo_error:
        is_429 = open_meteo_error.response is not None and open_meteo_error.response.status_code == 429
        if not is_429 or not WEATHERAPI_KEY:
            raise

        print(f"[weather] Open-Meteo 429 rate limit, falling back to WeatherAPI for {cache_key}")
        data = _fetch_weatherapi(latitude, longitude, forecast_days)
        _cache_set(cache_key, data)
        return data


def build_alerts(weather_data):
    alerts = []
    current = weather_data.get("current", {})
    daily = weather_data.get("daily", {})

    temperature = current.get("temperature_2m")
    wind_speed = current.get("wind_speed_10m")
    gusts = current.get("wind_gusts_10m")

    if temperature is not None and temperature >= 40:
        alerts.append({
            "severity": "Warning",
            "type": "Extreme Heat Alert",
            "text": "Extreme heat alert: Ambient temperature is 40°C or higher. Avoid prolonged sun exposure.",
            "protocol": ["Stay indoors in air-conditioned environments", "Drink plenty of water and electrolytes"],
        })
    elif temperature is not None and temperature >= 35:
        alerts.append({
            "severity": "Advisory",
            "type": "High Temperature Advisory",
            "text": "High temperature advisory: Temperature exceeds 35°C. Hydrate frequently.",
            "protocol": ["Limit strenuous outdoor activity between 12 PM and 4 PM"],
        })
    elif temperature is not None and temperature <= 0:
        alerts.append({
            "severity": "Warning",
            "type": "Freeze Warning",
            "text": "Sub-zero temperatures detected. Frost and black ice risks on elevated surfaces.",
            "protocol": ["Protect sensitive vegetation and exposed plumbing", "Drive with caution"],
        })

    if (wind_speed is not None and wind_speed >= 50) or (gusts is not None and gusts >= 65):
        alerts.append({
            "severity": "Advisory",
            "type": "Gale / Strong Wind Advisory",
            "text": f"Strong winds detected: Sustained {round(wind_speed or 0)} km/h with gusts topping {round(gusts or wind_speed or 0)} km/h.",
            "protocol": ["Secure loose outdoor furniture and architectural elements", "Exercise caution on open highways"],
        })

    for i, p in enumerate(daily.get("precipitation_probability_max", [])):
        day_name = "Today" if i == 0 else f"Day {i + 1}"
        if p is not None and p >= 80:
            alerts.append({
                "severity": "Advisory",
                "type": "High Precipitation Probability",
                "text": f"Heavy rain probability on {day_name}: {p}% likelihood of rain.",
                "protocol": ["Carry high-durability waterproof gear", "Check local drainage updates"],
            })
            break

    for i, u in enumerate(daily.get("uv_index_max", [])):
        if u is not None and u >= 8:
            alerts.append({
                "severity": "Advisory",
                "type": "Extreme UV Index",
                "text": f"Very high solar radiation: UV index peaking at {round(u, 1)}.",
                "protocol": ["Apply broad-spectrum SPF 50+ sunscreen", "Wear UV400 sunglasses and protective headwear"],
            })
            break

    return alerts


# ============================================================
# SMART RAIN ALERT + RAIN TIMELINE
# Deterministic, transparent logic over the SAME hourly Open-Meteo
# payload already powering the app. Every emitted alert/text is
# auditable against the `rain_snapshot` echoed in the payload.
# ============================================================

# Hourly precipitation-probability (in %) considered "likely rain".
RAIN_ALERT_PROB_THRESHOLD = 60
# Consecutive hours at/above the threshold needed to call it an event.
RAIN_ALERT_MIN_DURATION_H = 2
# How far ahead (hours) the alert scans the hourly forecast.
RAIN_ALERT_HORIZON_H = 18

_RAIN_TEXT = {
    "event_in": {
        "en": "Rain likely from about {start} for about {dur}h — peak chance {peak}%. Carry an umbrella.",
        "hi": "लगभग {start} से लगभग {dur} घंटे बारिश संभावित — अधिकतम संभावना {peak}%। छाता साथ रखें।",
        "te": "సుమారు {start} నుండి {dur} గంటలు వర్షం సాధ్యమే — గరిష్ఠ అవకాశం {peak}%। గొడుగు తీసుకోండి।",
    },
    "event_now": {
        "en": "Rain likely now for about {dur}h — peak chance {peak}%. Carry an umbrella.",
        "hi": "अभी लगभग {dur} घंटे बारिश संभावित — अधिकतम संभावना {peak}%। छाता साथ रखें।",
        "te": "ఇప్పుడే {dur} గంటలు వర్షం సాధ్యమే — గరిష్ఠ అవకాశం {peak}%। గొడుగు తీసుకోండి।",
    },
}


def _parse_iso_hour(value):
    """Parse an Open-Meteo hourly ISO timestamp (no timezone suffix)."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None


def build_rain_timeline(weather_data, max_hours=RAIN_ALERT_HORIZON_H, now_local=None):
    """Next-rain-window summary from the real hourly forecast.

    Scans the next `max_hours` hourly precipitation-probability values and
    returns {has_event, starts_in_h, duration_h, start_label, end_label,
    peak_probability, total_mm, start_iso, end_iso} or a "no rain" shape.
    Times are echoed in the location's own timezone (Open-Meteo returns
    local times for timezone=auto); offsets are derived from the location
    header so the clock of the viewing device does not skew "starts in".
    `now_local` is injectable for deterministic tests.
    """
    hourly = weather_data.get("hourly", {}) or {}
    times = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []
    precip_mm = hourly.get("precipitation") or []

    # Location-local wall clock: Open-Meteo hourly times are already local
    # when timezone=auto, so "now" must be computed with the same offset.
    offset_seconds = weather_data.get("utc_offset_seconds", 0)
    try:
        offset_seconds = int(offset_seconds)
    except (TypeError, ValueError):
        offset_seconds = 0
    if now_local is None:
        now_local = datetime.utcnow() + timedelta(seconds=offset_seconds)

    # First hour not fully in the past (like the frontend hourly scroller).
    start_idx = 0
    for i, t in enumerate(times):
        parsed = _parse_iso_hour(t)
        if parsed and parsed + timedelta(hours=1) > now_local:
            start_idx = i
            break

    end_idx = min(len(times), start_idx + max(1, max_hours))

    # Longest consecutive run at/above threshold inside the horizon.
    best_start, best_len = None, 0
    run_start, run_len = None, 0
    for i in range(start_idx, end_idx):
        p = probs[i] if i < len(probs) else None
        if isinstance(p, (int, float)) and p >= RAIN_ALERT_PROB_THRESHOLD:
            if run_start is None:
                run_start = i
            run_len += 1
        else:
            if run_len > best_len:
                best_start, best_len = run_start, run_len
            run_start, run_len = None, 0
    if run_len > best_len:
        best_start, best_len = run_start, run_len

    base = {
        "has_event": False,
        "starts_in_h": None,
        "duration_h": 0,
        "start_label": None,
        "end_label": None,
        "peak_probability": None,
        "total_mm": None,
        "start_iso": None,
        "end_iso": None,
        "horizon_h": max(1, max_hours),
    }

    if best_start is None or best_len < RAIN_ALERT_MIN_DURATION_H:
        return base

    end_idx_excl = best_start + best_len
    window_probs = [p for p in probs[best_start:end_idx_excl] if isinstance(p, (int, float))]
    window_mm = [m for m in precip_mm[best_start:end_idx_excl] if isinstance(m, (int, float))]
    start_parsed = _parse_iso_hour(times[best_start])
    end_parsed = _parse_iso_hour(times[end_idx_excl - 1])

    base.update({
        "has_event": True,
        "starts_in_h": max(0, best_start - start_idx),
        "duration_h": best_len,
        "start_label": start_parsed.strftime("%H:%M") if start_parsed else None,
        "end_label": end_parsed.strftime("%H:%M") if end_parsed else None,
        "peak_probability": round(max(window_probs)) if window_probs else None,
        "total_mm": round(sum(window_mm), 1) if window_mm else 0.0,
        "start_iso": times[best_start],
        "end_iso": times[end_idx_excl - 1],
    })
    return base


def build_smart_rain_alerts(timeline, language="en"):
    """Localized, evidence-backed "Smart Rain Alert" from a rain timeline.

    Only fires when the timeline found a real upcoming rain window; the
    snapshot values echo the hourly payload so the alert is fully auditable.
    """
    if not timeline or not timeline.get("has_event"):
        return []

    lang = language if language in _RAIN_TEXT["event_in"] else "en"
    starts_in = timeline.get("starts_in_h") or 0
    dur = timeline.get("duration_h") or 0
    peak = timeline.get("peak_probability")
    snapshot = {
        "threshold_probability": RAIN_ALERT_PROB_THRESHOLD,
        "min_duration_h": RAIN_ALERT_MIN_DURATION_H,
        "horizon_h": timeline.get("horizon_h"),
        "starts_in_h": starts_in,
        "duration_h": dur,
        "start_iso": timeline.get("start_iso"),
        "end_iso": timeline.get("end_iso"),
        "peak_probability": peak,
        "total_mm": timeline.get("total_mm"),
    }

    if starts_in <= 1:
        template = _RAIN_TEXT["event_now"][lang]
        text = template.format(dur=dur, peak=peak)
    else:
        template = _RAIN_TEXT["event_in"][lang]
        text = template.format(start=timeline.get("start_label") or "", dur=dur, peak=peak)

    return [{
        "severity": "Advisory",
        "type": "Smart Rain Alert",
        "text": text,
        "protocol": [
            "Carry an umbrella or waterproof layer before heading out",
            "Allow extra travel time during the rain window",
        ],
        "snapshot": snapshot,
    }]


def guess_suffix(uploaded_file):
    name = (uploaded_file.filename or "").lower()
    mimetype = (uploaded_file.mimetype or "").lower()

    if "ogg" in mimetype or name.endswith(".ogg"):
        return ".ogg"
    if "wav" in mimetype or name.endswith(".wav"):
        return ".wav"
    if "mp4" in mimetype or name.endswith((".m4a", ".mp4")):
        return ".m4a"
    return ".webm"


def normalize_language(raw_value, fallback_code):
    value = (raw_value or "").strip().lower()
    if value in LANG_CODE_TO_NAME:
        code = value
    elif value in LANG_NAME_TO_CODE:
        code = LANG_NAME_TO_CODE[value]
    else:
        code = fallback_code if fallback_code in LANG_CODE_TO_NAME else "en"
    return code, LANG_CODE_TO_NAME.get(code, "English")


def transcribe_audio(audio_path, preferred_language="auto"):
    if not groq_client:
        raise RuntimeError("Groq client not configured (GROQ_API_KEY missing)")

    language_hint = preferred_language if preferred_language in LANG_CODE_TO_NAME else None

    with open(audio_path, "rb") as f:
        kwargs = {
            "file": (os.path.basename(audio_path), f.read()),
            "model": GROQ_WHISPER_MODEL,
            "response_format": "verbose_json",
            "temperature": 0.0,
        }
        if language_hint:
            kwargs["language"] = language_hint

        result = groq_client.audio.transcriptions.create(**kwargs)

    transcript = (getattr(result, "text", "") or "").strip()
    code, name = normalize_language(getattr(result, "language", None), language_hint)
    return {"transcript": transcript, "language_code": code, "language": name}


# ============================================================
# API ROUTES
# ============================================================

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "groq_whisper": bool(groq_client),
        "groq_model": GROQ_WHISPER_MODEL,
        "ollama": bool(ollama_client),
        "ollama_model": OLLAMA_MODEL,
        "weatherapi_fallback": bool(WEATHERAPI_KEY),
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/config")
def api_config():
    """Public, client-safe configuration (no secrets here — only keys that
    are designed to be embedded in browsers, e.g. CARTO basemaps)."""
    return jsonify({"carto_api_key": CARTO_API_KEY})


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    uploaded_file = request.files.get("audio")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Audio file is required"}), 400

    preferred_language = request.form.get("preferred_language", "auto").strip().lower()
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=guess_suffix(uploaded_file)) as tmp:
            temp_path = tmp.name
            uploaded_file.save(temp_path)

        result = transcribe_audio(temp_path, preferred_language)
        if not result["transcript"]:
            return jsonify({"error": "No speech detected. Please speak closer to microphone."}), 422

        return jsonify(result)
    except Exception as error:
        app.logger.warning("[transcribe] Error: %s", type(error).__name__)
        return jsonify({"error": "Voice transcription failed. Please try again."}), 502
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    query = (request.get_json(silent=True) or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    if ollama_client:
        try:
            response = ollama_client.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": ANALYZE_PROMPT},
                    {"role": "user", "content": query},
                ],
                options={"temperature": 0},
            )
            return jsonify(clean_json(response.message.content))
        except Exception as error:
            print("[analyze] Ollama Error:", error)

    if groq_client:
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": ANALYZE_PROMPT},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return jsonify(clean_json(content))
        except Exception as error:
            print("[analyze] Groq Error:", error)

    # Heuristic Fallback
    q_low = query.lower()
    focus = "general"
    if any(k in q_low for k in ["rain", "umbrella", "shower", "बारिश", "వర్షం"]):
        focus = "rain"
    elif any(k in q_low for k in ["bike", "cycling", "ride", "साइकिल", "సైకిల్"]):
        focus = "wind"
    elif any(k in q_low for k in ["wear", "cloth", "dress", "कपड़े", "దుస్తులు"]):
        focus = "clothing"
    elif any(k in q_low for k in ["temp", "heat", "cold", "तापमान", "ఉష్ణోగ్రత"]):
        focus = "temperature"

    days = 1
    if any(k in q_low for k in ["tomorrow", "कल", "రేపు"]):
        days = 2
    elif any(k in q_low for k in ["week", "7 days", "हफ्ते", "వారం"]):
        days = 7

    lang = "English"
    if any(k in q_low for k in ["कल", "बारिश", "मौसम", "क्या"]):
        lang = "Hindi"
    elif any(k in q_low for k in ["ఈ రోజు", "రేపు", "వర్షం", "ఎలా"]):
        lang = "Telugu"

    return jsonify({
        "location": None,
        "language": lang,
        "forecast_days": days,
        "weather_focus": focus,
        "time_reference": "current" if days == 1 else "future"
    })


@app.route("/api/geocode")
def api_geocode():
    location_name = (request.args.get("location") or "").strip()
    if not location_name:
        return jsonify({"error": "location is required"}), 400

    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location_name, "count": 1, "language": "en", "format": "json"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("results"):
            return jsonify({"error": f"Location '{location_name}' could not be found."}), 404

        place = data["results"][0]
        return jsonify({
            "name": place.get("name"),
            "admin1": place.get("admin1"),
            "country": place.get("country"),
            "country_code": place.get("country_code"),
            "latitude": place.get("latitude"),
            "longitude": place.get("longitude"),
            "timezone": place.get("timezone", "UTC"),
        })
    except requests.RequestException as error:
        app.logger.warning("[geocode] upstream error: %s", error)
        return jsonify({"error": "Geocoding service temporarily unavailable"}), 502


# Small TTL cache for reverse-geocode lookups: GPS fixes cluster around the
# same spot, so repeated UI refreshes should not re-hit upstream providers.
_REV_GEO_CACHE = OrderedDict()  # (lat, lon) -> {name, admin1, country, ...}
_REV_GEO_CACHE_TTL = 600  # seconds


def _rev_geo_cache_get(latitude, longitude):
    key = (round(latitude, 4), round(longitude, 4))  # ~11 m precision
    entry = _REV_GEO_CACHE.get(key)
    if entry and time.time() - entry["ts"] < _REV_GEO_CACHE_TTL:
        _REV_GEO_CACHE.move_to_end(key)
        return {k: v for k, v in entry.items() if k != "ts"}
    if entry:
        _REV_GEO_CACHE.pop(key, None)
    return None


def _rev_geo_cache_set(latitude, longitude, payload):
    key = (round(latitude, 4), round(longitude, 4))
    _REV_GEO_CACHE[key] = {**payload, "ts": time.time()}
    _REV_GEO_CACHE.move_to_end(key)
    while len(_REV_GEO_CACHE) > 128:
        _REV_GEO_CACHE.popitem(last=False)


def _reverse_geocode_nominatim(latitude, longitude):
    """Fallback provider: OSM Nominatim public reverse endpoint.
    Returns a payload dict or raises — never a placeholder name."""
    r = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "format": "jsonv2",
            "lat": latitude,
            "lon": longitude,
            "zoom": 14,
            "addressdetails": 1,
            "accept-language": "en",
        },
        headers={
            "User-Agent": "WeatherGPT/1.0 (weather dashboard; personal project)",
            "Accept": "application/json",
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"nominatim HTTP {r.status_code}")
    data = r.json()
    addr = data.get("address") or {}
    name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or addr.get("suburb") or addr.get("county") or addr.get("state")
    if not name:
        return None
    return {
        "name": name,
        "admin1": addr.get("state") or data.get("name") or "",
        "country": addr.get("country") or "",
        "latitude": latitude,
        "longitude": longitude,
    }


@app.route("/api/reverse-geocode")
def api_reverse_geocode():
    try:
        latitude = float(request.args["latitude"])
        longitude = float(request.args["longitude"])
    except (KeyError, ValueError):
        return jsonify({"error": "valid latitude and longitude required"}), 400

    if not _valid_latlon(latitude, longitude):
        return jsonify({"error": "latitude must be in [-90, 90] and longitude in [-180, 180]"}), 400

    cached = _rev_geo_cache_get(latitude, longitude)
    if cached:
        return jsonify(cached)

    # Provider 1: BigDataCloud client API.
    try:
        r = requests.get(
            "https://api.bigdatacloud.net/data/reverse-geocode-client",
            params={"latitude": latitude, "longitude": longitude, "localityLanguage": "en"},
            timeout=_REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            city = data.get("locality") or data.get("city") or data.get("principalSubdivision") or ""
            if city:
                payload = {
                    "name": city,
                    "admin1": data.get("principalSubdivision") or "",
                    "country": data.get("countryName") or data.get("countryCode") or "",
                    "latitude": latitude,
                    "longitude": longitude,
                }
                _rev_geo_cache_set(latitude, longitude, payload)
                return jsonify(payload)
    except Exception as e:
        print("[reverse-geocode] BigDataCloud failed:", e)

    # Provider 2 (fallback): OSM Nominatim — a real name or nothing.
    try:
        payload = _reverse_geocode_nominatim(latitude, longitude)
        if payload:
            _rev_geo_cache_set(latitude, longitude, payload)
            return jsonify(payload)
    except Exception as e:
        print("[reverse-geocode] Nominatim failed:", e)

    # Both providers unavailable: coordinates-only shape — never a fake
    # "Current Location" name that could overwrite a good cached label.
    return jsonify({
        "name": "",
        "admin1": "",
        "country": "",
        "latitude": latitude,
        "longitude": longitude,
    })


def _valid_latlon(latitude, longitude):
    return (
        isinstance(latitude, (int, float))
        and isinstance(longitude, (int, float))
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


@app.route("/api/weather")
def api_weather():
    try:
        latitude = float(request.args["latitude"])
        longitude = float(request.args["longitude"])
    except (KeyError, ValueError):
        return jsonify({"error": "valid latitude and longitude are required"}), 400

    if not _valid_latlon(latitude, longitude):
        return jsonify({"error": "latitude must be in [-90, 90] and longitude in [-180, 180]"}), 400

    try:
        forecast_days = int(request.args.get("forecast_days", 7))
    except ValueError:
        return jsonify({"error": "forecast_days must be an integer between 1 and 7"}), 400

    try:
        weather_data = fetch_weather(latitude, longitude, forecast_days)
        alerts = build_alerts(weather_data)
        rain_timeline = build_rain_timeline(weather_data)
        alerts = build_smart_rain_alerts(rain_timeline) + alerts
        air_quality = weather_data.get("air_quality", {})
        return jsonify({
            "weather": weather_data,
            "alerts": alerts,
            "air_quality": air_quality,
            "rain_timeline": rain_timeline,
        })
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 429:
            message = "Weather API rate-limited. Please try again in a few moments."
            return jsonify({"error": message}), 503
        app.logger.warning("[weather] upstream error: %s", error)
        return jsonify({"error": "Weather service temporarily unavailable"}), 502
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        app.logger.exception("[weather] Error: %s", error)
        return jsonify({"error": "Weather service temporarily unavailable"}), 502


def _extract_location_name(location):
    if isinstance(location, dict):
        name = (location.get("name") or "").strip() if isinstance(location.get("name"), str) else ""
        return name or "Local Area"
    if isinstance(location, str) and location.strip():
        return location.strip()
    return "Local Area"


def generate_synoptic_report_fallback(body, language="English"):
    loc = _extract_location_name(body.get("location"))
    w = body.get("weather_data", {})
    curr = w.get("current", {})
    daily = w.get("daily", {})
    temp = round(curr.get("temperature_2m", 22))
    wind = round(curr.get("wind_speed_10m", 10))
    rain_prob = round(daily.get("precipitation_probability_max", [0])[0] if daily.get("precipitation_probability_max") else 0)
    uv = round(daily.get("uv_index_max", [4.0])[0] if daily.get("uv_index_max") else 4.0, 1)

    is_hindi = (language or "").lower() in ["hi", "hindi"]
    is_telugu = (language or "").lower() in ["te", "telugu"]

    if is_hindi:
        return f"""- **Atmospheric Summary**: {loc} में वर्तमान तापमान {temp}°C है। वायुमंडलीय स्थिति सामान्य और स्थिर बनी हुई है।
- **Key Meteorological Factors**: वर्षा की संभावना {rain_prob}%, हवा की गति {wind} किमी/घंटा और यूवी इंडेक्स {uv} है।
- **Recommendations**: दोपहर के समय पर्याप्त मात्रा में पानी पिएं और धूप से बचाव के लिए उपयुक्त चश्मा या टोपी का प्रयोग करें।"""
    elif is_telugu:
        return f"""- **Atmospheric Summary**: {loc}లో ప్రస్తుత ఉష్ణోగ్రత {temp}°C గా నమోదైంది. వాతావరణం నిలకడగా ఉంది.
- **Key Meteorological Factors**: వర్షం పడే అవకాశం {rain_prob}%, గాలి వేగం {wind} km/h మరియు UV ఇండెక్స్ {uv}.
- **Recommendations**: సాధారణ సమయాల్లో బయటకు వెళ్లడం అనుకూలం, ఎండ వేళల్లో తగిన జాగ్రత్తలు తీసుకోండి."""
    else:
        return f"""- **Atmospheric Summary**: Current ambient temperature in {loc} is {temp}°C with balanced isobaric density.
- **Key Meteorological Factors**: Precipitation risk at {rain_prob}%, wind velocities averaging {wind} km/h, and peak UV index of {uv}.
- **Recommendations**: Favorable conditions for outdoor activity. Keep protective eyewear on hand during peak solar hours."""


def generate_synoptic_assistant_fallback(query, location_name, weather_summary, language="English"):
    q = (query or "").lower().strip()
    is_hindi = (language or "").strip().lower() in ["hi", "hindi"]
    is_telugu = (language or "").strip().lower() in ["te", "telugu"]

    is_bike = any(w in q for w in ["bike", "cycling", "cycle", "ride", "साइकेल", "साइकिल", "సైకిల్"])
    is_rain = any(w in q for w in ["rain", "commute", "umbrella", "shower", "wet", "बारिश", "वर्षा", "వర్షం", "వాన"])
    is_clothes = any(w in q for w in ["wear", "cloth", "dress", "jacket", "coat", "apparel", "कपड़े", "पहने", "దుస్తులు"])
    is_tomorrow = any(w in q for w in ["tomorrow", "next day", "कल", "రేపు"])

    if is_bike:
        if is_hindi:
            answer = f"{location_name} में वर्तमान मौसम: {weather_summary}। दोपहर में साइकिल चलाने के लिए वायुमंडलीय स्थिति अनुकूल है। तेज धूप और हवा के झोंकों से बचने के लिए दोपहर 2:30 से 5:00 बजे के बीच का समय सबसे सुरक्षित और आरामदायक रहेगा।"
        elif is_telugu:
            answer = f"{location_name}లో ప్రస్తుత వాతావరణం: {weather_summary}। మధ్యాహ్నం సైక్లింగ్ చేయడానికి పరిస్థితులు అనుకూలంగా ఉన్నాయి. ఎండ తీవ్రత తగ్గాక మధ్యాహ్నం 2:30 నుండి 5:00 గంటల మధ్య బయటకు వెళ్లడం మంచిది."
        else:
            answer = f"Conditions in {location_name} currently show {weather_summary}. Biking is favorable this afternoon with manageable winds and good atmospheric visibility. Plan for an afternoon departure between 2:00 PM and 4:45 PM for the best thermal comfort."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Optimal Cycling Window",
                "time_range": "2:15 PM – 4:45 PM",
                "reliability": "93% High Confidence",
                "favorable_note": "Favorable tailwinds & pavement dry",
                "caution_note": "UV protection advised around midday",
            },
            "route_progression": [
                {"label": "Origin", "place": location_name, "temp": "Current", "condition": "Favorable road grade"},
                {"label": "Midway", "place": "Parkway Transect", "temp": "Nominal", "condition": "Crosswind < 14 km/h"},
                {"label": "Destination", "place": "Outer Loop", "temp": "Comfortable", "condition": "Optimal visibility"},
            ],
            "attire_guidance": {
                "headline": "Recommended Cycling Attire",
                "layers": ["Breathable athletic base layer", "Lightweight wind vest"],
                "accessories": ["UV400 cycling shades", "Hydration bottle"],
                "thermal_rating": "Comfortable",
            },
        }

    elif is_rain:
        if is_hindi:
            answer = f"{location_name} में वर्तमान मौसम स्थिति: {weather_summary}। शाम के आवागमन (commute) के दौरान हल्की या मध्यम बारिश की संभावना बनी रह सकती है। सुरक्षित यात्रा के लिए छाता या वाटरप्रूफ जैकेट साथ रखना उचित रहेगा।"
        elif is_telugu:
            answer = f"{location_name}లో ప్రస్తుత వాతావరణ పరిస్థితి: {weather_summary}। సాయంత్రం ప్రయాణ సమయంలో తేలికపాటి వర్షం కురిసే అవకాశం ఉంది. మీ వెంట గొడుగు లేదా రెయిన్‌కోట్ ఉంచుకోవడం శ్రేయస్కరం."
        else:
            answer = f"Atmospheric scans for {location_name} indicate {weather_summary}. There is a moderate potential for isolated showers during evening peak transit. We advise having rain gear or an umbrella on hand for your commute."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Commute Departure Window",
                "time_range": "4:30 PM – 6:00 PM",
                "reliability": "89% Reliability",
                "favorable_note": "Pre-shower departure window",
                "caution_note": "Localized damp roads post-6:00 PM",
            },
            "route_progression": [
                {"label": "Origin", "place": location_name, "temp": "Current", "condition": "Dry pavements"},
                {"label": "Midway", "place": "Transit Corridor", "temp": "Cooling", "condition": "Overcast gradient"},
                {"label": "Destination", "place": "Target Zone", "temp": "Humid", "condition": "Showers possible"},
            ],
            "attire_guidance": {
                "headline": "Commuter Rain Protection",
                "layers": ["Water-resistant outer shell", "Comfortable commuter apparel"],
                "accessories": ["Compact durable umbrella", "Waterproof footwear"],
                "thermal_rating": "Mild / Damp",
            },
        }

    elif is_clothes:
        if is_hindi:
            answer = f"{location_name} में {weather_summary} के आधार पर, आज हल्के और सांस लेने योग्य सूती कपड़े पहनने की सलाह दी जाती है। यदि आप शाम को बाहर जा रहे हैं, तो तापमान में गिरावट के लिए एक हल्का कार्डिगन या विंडब्रेकर साथ रखें।"
        elif is_telugu:
            answer = f"{location_name}లో {weather_summary} నమోదైంది. ఈ రోజు సౌకర్యవంతమైన కాటన్ దుస్తులు అనుకూలం. సాయంత్రం వేళల్లో ఉష్ణోగ్రత కాస్త తగ్గితే తేలికపాటి జాకెట్ లేదా శాలువా ఉపయోగపడుతుంది."
        else:
            answer = f"Given {location_name}'s current telemetry of {weather_summary}, we suggest versatile, breathable layers. Cotton or technical blend fabric will keep you comfortable during peak daylight hours, with a light over-layer for air-conditioned interiors or evening cooling."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Attire Thermal Comfort",
                "time_range": "All Day Balance",
                "reliability": "95% Reliability",
                "favorable_note": "Mild thermal comfort range",
                "caution_note": "Diurnal cooling after sunset",
            },
            "route_progression": [
                {"label": "Morning", "place": location_name, "temp": "Crisp", "condition": "Light layer recommended"},
                {"label": "Midday", "place": "Solar Peak", "temp": "Warm", "condition": "Breathable base layer"},
                {"label": "Evening", "place": "Dusk Transect", "temp": "Mild", "condition": "Comfortable breeze"},
            ],
            "attire_guidance": {
                "headline": "Smart Daily Layering",
                "layers": ["Moisture-wicking breathable base", "Light cardigan or windbreaker shell"],
                "accessories": ["UV sunglasses", "Comfortable walking shoes"],
                "thermal_rating": "Comfortable",
            },
        }

    elif is_tomorrow:
        if is_hindi:
            answer = f"{location_name} में कल का मौसम लगभग वर्तमान पैटर्न ({weather_summary}) के समान ही स्थिर रहने का अनुमान है। दिन में सामान्य तापमान और अच्छी दृश्यता रहेगी।"
        elif is_telugu:
            answer = f"{location_name}లో రేపటి వాతావరణం దాదాపు ప్రస్తుత పరిస్థితి ({weather_summary}) లాగే కొనసాగుతుంది. సాధారణ ఉష్ణోగ్రతలతో వాతావరణం అనుకూలంగా ఉంటుంది."
        else:
            answer = f"Tomorrow's synoptic forecast for {location_name} projects steady atmospheric continuity aligned with {weather_summary}. Barometric stability will support clear morning travel and outdoor schedules."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Tomorrow's Peak Window",
                "time_range": "8:00 AM – 11:30 AM",
                "reliability": "91% Reliability",
                "favorable_note": "Crisp morning breeze, clear horizon",
                "caution_note": "Standard diurnal heat increase midday",
            },
            "route_progression": [
                {"label": "Morning", "place": location_name, "temp": "Cool", "condition": "Stable atmosphere"},
                {"label": "Midday", "place": "Urban Metro", "temp": "Warm", "condition": "Solar exposure"},
                {"label": "Evening", "place": "Surrounding Hills", "temp": "Mild", "condition": "Steady breezes"},
            ],
            "attire_guidance": {
                "headline": "Next-Day Casual Travel",
                "layers": ["Everyday breathable cottons", "Lightweight wind layer"],
                "accessories": ["Sun protection hat", "Refillable hydration flask"],
                "thermal_rating": "Mild",
            },
        }

    else:
        if is_hindi:
            answer = f"{location_name} के लिए वायुमंडलीय विश्लेषण: वर्तमान में {weather_summary} रिकॉर्ड किया गया है। वायुमंडलीय दबाव और हवा की स्थिति सामान्य सीमा में है।"
        elif is_telugu:
            answer = f"{location_name} వాతావరణ సమాచారం: ప్రస్తుతం {weather_summary} నమోదైంది. వాతావరణ పీడనం మరియు గాలి వేగం సాధారణ పరిమితుల్లో ఉన్నాయి."
        else:
            answer = f"Atmospheric telemetry for {location_name} reports {weather_summary}. Local sensor nodes show stable pressure gradients and standard ambient conditions across the urban transect."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Optimal Activity Window",
                "time_range": "1:30 PM – 4:45 PM",
                "reliability": "92% Reliability",
                "favorable_note": "Stable synoptic pressure",
                "caution_note": "Check local radar updates periodically",
            },
            "route_progression": [
                {"label": "Origin", "place": location_name, "temp": "Current", "condition": "Stable telemetry"},
                {"label": "Midway", "place": "En Route", "temp": "Nominal", "condition": "Light breezes"},
                {"label": "Destination", "place": "Target Zone", "temp": "Expected", "condition": "Normal conditions"},
            ],
            "attire_guidance": {
                "headline": "Weather-Appropriate Guidance",
                "layers": ["Everyday comfortable attire", "Optional light layer"],
                "accessories": ["Sunglasses or weather-appropriate gear"],
                "thermal_rating": "Comfortable",
            },
        }


@app.route("/api/report", methods=["POST"])
def api_report():
    body = request.get_json(silent=True) or {}
    language = body.get("language", "English")

    if ollama_client:
        try:
            response = ollama_client.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": REPORT_PROMPT.format(language=language)},
                    {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
                ],
                options={"temperature": 0.2},
            )
            return jsonify({"report": response.message.content})
        except Exception as error:
            print("[report] Ollama Error:", error)

    if groq_client:
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": REPORT_PROMPT.format(language=language)},
                    {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
            return jsonify({"report": response.choices[0].message.content})
        except Exception as error:
            print("[report] Groq Error:", error)

    # Synoptic Meteorological Fallback
    fallback_report = generate_synoptic_report_fallback(body, language=language)
    return jsonify({"report": fallback_report})


@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    body = request.get_json(silent=True) or {}
    query = body.get("query", "").strip()
    location_name = body.get("location_name", "Local Area")
    weather_summary = body.get("weather_summary", "Partly cloudy, 22°C")
    language = body.get("language", "English")

    if not query:
        return jsonify({"error": "query is required"}), 400

    prompt = ASSISTANT_PROMPT.format(
        query=query,
        location_name=location_name,
        weather_summary=weather_summary,
        language=language,
    )

    if ollama_client:
        try:
            response = ollama_client.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": "You are WeatherGPT. Output strictly JSON."},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.2},
            )
            structured = clean_json(response.message.content)
            return jsonify(structured)
        except Exception as error:
            print("[assistant] Ollama primary error:", error)
            try:
                simple_resp = ollama_client.chat(
                    model=OLLAMA_MODEL,
                    messages=[
                        {"role": "system", "content": f"You are WeatherGPT. Respond in {language}."},
                        {"role": "user", "content": f"Location: {location_name}. Conditions: {weather_summary}. Question: {query}"},
                    ],
                )
                return jsonify({
                    "answer": simple_resp.message.content,
                    "optimal_window": {
                        "title": "Optimal Activity Window",
                        "time_range": "Check hourly forecast",
                        "reliability": "90%",
                        "favorable_note": "Stable pressure profile",
                        "caution_note": "Dress in comfortable layers",
                    },
                    "route_progression": [],
                    "attire_guidance": {
                        "headline": "Attire Recommendation",
                        "layers": ["Breathable everyday wear"],
                        "accessories": ["Weather appropriate accessories"],
                        "thermal_rating": "Mild",
                    },
                })
            except Exception as err2:
                print("[assistant] Ollama simple fallback error:", err2)

    if groq_client:
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": "You are WeatherGPT, an advanced Atmospheric Intelligence Assistant. Output strictly JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            structured = clean_json(content)
            return jsonify(structured)
        except Exception as error:
            print("[assistant] Groq Error:", error)

    # Smart Synoptic Atmospheric Intelligence Fallback
    fallback_result = generate_synoptic_assistant_fallback(
        query=query,
        location_name=location_name,
        weather_summary=weather_summary,
        language=language,
    )
    return jsonify(fallback_result)


@app.route("/api/farm-advice", methods=["GET"])
def api_farm_advice():
    """Deterministic farm advisory from real forecast data.

    Query params: crop, stage, latitude, longitude, language (en|hi|te),
    optional forecast_days (1-7, default 7).
    """
    crop = request.args.get("crop", "")
    stage = request.args.get("stage", "")
    lang = (request.args.get("language") or "en").strip().lower()
    lang = {"en": "en", "en-us": "en", "english": "en", "hi": "hi", "hindi": "hi",
            "te": "te", "telugu": "te"}.get(lang, "en")

    crop_n, stage_n, _, error = validate_params(crop, stage, lang)
    if error:
        return jsonify(error[0]), error[1]

    try:
        latitude = float(request.args["latitude"])
        longitude = float(request.args["longitude"])
    except (KeyError, ValueError):
        return jsonify({"error": "valid latitude and longitude are required"}), 400

    if not _valid_latlon(latitude, longitude):
        return jsonify({"error": "latitude must be in [-90, 90] and longitude in [-180, 180]"}), 400

    try:
        forecast_days = int(request.args.get("forecast_days", 7))
    except ValueError:
        return jsonify({"error": "forecast_days must be an integer between 1 and 7"}), 400

    try:
        weather_data = fetch_weather(latitude, longitude, forecast_days)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 429:
            return jsonify({"error": "Weather API rate-limited. Please try again in a few moments."}), 503
        app.logger.warning("[farm-advice] upstream error: %s", error)
        return jsonify({"error": "Weather service temporarily unavailable"}), 502
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        app.logger.exception("[farm-advice] Error: %s", error)
        return jsonify({"error": "Weather service temporarily unavailable"}), 502

    advice = build_farm_advice(weather_data, crop_n, stage_n, lang)
    return jsonify(advice)


@app.route("/api/crops")
def api_crops():
    """Crop + stage catalogue for the frontend selectors."""
    return jsonify({
        "crops": [
            {"id": cid, "names": p["names"], "emoji": p["emoji"]}
            for cid, p in CROPS.items()
        ],
        "stages": [
            {"id": sid, "names": s["names"], "emoji": s["emoji"]}
            for sid, s in STAGES.items()
        ],
    })


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    # Interactive debugger MUST stay off unless explicitly opted in —
    # it exposes a remote code-execution console on any 500 error.
    debug = os.getenv("FLASK_DEBUG") == "1"
    print(f"WeatherGPT Atmospheric Intel running at http://0.0.0.0:{port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
