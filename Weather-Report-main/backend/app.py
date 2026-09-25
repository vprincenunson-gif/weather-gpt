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
# ElevenLabs TTS (server-side ONLY — the key never reaches the browser).
# Used by /api/tts to give WeatherGPT's Speak Answer a natural sweet female
# voice; the frontend falls back to the built-in browser voices when unset
# or on any failure.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
# Telugu is NOT in Eleven Multilingual v2's 29-language list — it needs
# Eleven v3 (74 languages). EN/HI stay on the most life-like multilingual
# model; TE is routed to v3. Overridable via env for future model changes.
ELEVENLABS_TTS_MODEL_TE = os.getenv("ELEVENLABS_TTS_MODEL_TE", "eleven_v3")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
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

REPORT_PROMPT = """You are WeatherGPT, a friendly weather assistant. Using the provided request, location, Open-Meteo weather data and alerts, answer accurately.
Respond ONLY in {language}.
Write for an ordinary person — short, warm, plain language. No jargon, no filler.
Format your response using concise bullet points and bold section labels:
- **What's happening**: What the sky and temperature are doing right now.
- **Worth knowing**: Rain chance, wind, humidity, UV — only the numbers that matter today.
- **What you can do**: Practical, specific advice for travel, outdoor plans, or clothing.
Use Celsius, km/h and mm. Be concise and practical."""

ASSISTANT_PROMPT = """You are WeatherGPT, a friendly, practical weather assistant.
The user is asking: "{query}".
Current Location: {location_name}
Current Weather Data: {weather_summary}
Language: Respond in {language}.

Return a valid JSON object with the following keys:
{{
  "answer": "A clear, natural-language response addressing the user query in {language}. Write like a knowledgeable neighbour — plain words, no jargon. Explain what the conditions mean practically for their activity or question.",
  "optimal_window": {{
    "title": "Best time",
    "time_range": "e.g., 1:30 PM – 4:15 PM or Morning Hours",
    "reliability": "e.g., High confidence",
    "favorable_note": "e.g., Light winds, comfortable humidity",
    "caution_note": "e.g., Gusts pick up or it cools down after 5:00 PM"
  }},
  "route_progression": [
    {{"label": "Start", "place": "{location_name}", "temp": "Current temp", "condition": "Short condition note"}},
    {{"label": "Midway", "place": "En route", "temp": "Midway temp", "condition": "Wind or exposure notes"}},
    {{"label": "Arrival", "place": "Destination", "temp": "Arrival temp", "condition": "Arrival conditions"}}
  ],
  "attire_guidance": {{
    "headline": "What to wear",
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


# ============================================================
# MAP FIELD DATA (Micro-Temp / Wind Vectors / AQI Plume layers)
# ============================================================
# The radar map's data layers sample real gridded model data around the
# viewed location: temperature and wind come from Open-Meteo's forecast
# API (one request per grid point, same endpoints the app already uses)
# and AQI from Open-Meteo's Air-Quality API. Grid geometry is tuned per
# layer: a 1°, 9x9 lattice resolves temperature/wind gradients, while a
# coarser 2°, 7x7 lattice is enough for the smoother AQI plume.

_FIELD_METRICS = {
    "temp": {"grid_step": 1.0, "grid_size": 9},
    "wind": {"grid_step": 1.0, "grid_size": 9},
    "aqi": {"grid_step": 2.0, "grid_size": 7},
}

_FIELD_CACHE = OrderedDict()  # (metric, min_lat, min_lon) -> (ts, data); LRU
_FIELD_CACHE_TTL_SECONDS = 600  # model fields refresh on a ~10-15 min cadence
_FIELD_CACHE_MAX_ENTRIES = 200
_FIELD_CACHE_LOCK = threading.Lock()


def _field_cache_get(cache_key):
    with _FIELD_CACHE_LOCK:
        cached = _FIELD_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _FIELD_CACHE_TTL_SECONDS:
            _FIELD_CACHE.move_to_end(cache_key)
            return cached[1]
        if cache_key in _FIELD_CACHE:
            del _FIELD_CACHE[cache_key]  # expired
    return None


def _field_cache_set(cache_key, data):
    with _FIELD_CACHE_LOCK:
        _FIELD_CACHE[cache_key] = (time.time(), data)
        _FIELD_CACHE.move_to_end(cache_key)
        while len(_FIELD_CACHE) > _FIELD_CACHE_MAX_ENTRIES:
            _FIELD_CACHE.popitem(last=False)


def _field_metric_params(metric):
    """Upstream endpoint + `current` fields for each field metric."""
    if metric == "temp":
        return "https://api.open-meteo.com/v1/forecast", "temperature_2m"
    if metric == "wind":
        return "https://api.open-meteo.com/v1/forecast", "wind_speed_10m,wind_direction_10m"
    return "https://air-quality-api.open-meteo.com/v1/air-quality", "us_aqi"


def _field_point_value(metric, current):
    """Parse one upstream `current` object into a grid value (or None).

    A wind vector needs BOTH components — half a reading is no reading.
    Values the model reports as unavailable stay None (never fabricated).
    """
    if metric == "wind":
        speed = current.get("wind_speed_10m")
        direction = current.get("wind_direction_10m")
        if speed is None or direction is None:
            return None
        return {"speed": round(float(speed), 1), "direction": round(float(direction), 1)}
    field = "temperature_2m" if metric == "temp" else "us_aqi"
    value = current.get(field)
    return None if value is None else round(float(value), 1)


def _fetch_field_batch(metric, points):
    """Sample all lattice points in ONE upstream request.

    Open-Meteo accepts comma-separated coordinate lists and answers with
    one JSON object per point, in request order — 81 or 49 model lookups
    for the price of a single HTTP call.
    """
    url, current_fields = _field_metric_params(metric)
    response = requests.get(
        url,
        params={
            "latitude": ",".join(str(p[0]) for p in points),
            "longitude": ",".join(str(p[1]) for p in points),
            "current": current_fields,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    results = data if isinstance(data, list) else [data]
    if len(results) != len(points):
        raise ValueError(f"upstream returned {len(results)} points for {len(points)} requested")
    return [_field_point_value(metric, r.get("current", {})) for r in results]


def _fetch_field_points(metric, points):
    """Batched sampling with a per-point fallback, so one bad coordinate
    (or a batch-level failure) degrades to missing points instead of an
    empty layer."""
    try:
        return _fetch_field_batch(metric, points)
    except Exception as batch_error:
        print(f"[field:{metric}] batch failed ({batch_error}); falling back to per-point sampling")

    url, current_fields = _field_metric_params(metric)
    values = []
    for point_lat, point_lon in points:
        try:
            response = requests.get(
                url,
                params={"latitude": point_lat, "longitude": point_lon, "current": current_fields},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            values.append(_field_point_value(metric, response.json().get("current", {})))
        except Exception as point_error:
            # One failed point must not sink the layer: mark it missing.
            print(f"[field:{metric}] point ({point_lat},{point_lon}) failed: {point_error}")
            values.append(None)
    return values


def build_field_grid(metric, latitude, longitude):
    """Sample a real lat/lon lattice of `metric` around a center point.

    Returns {"metric", "unit", "grid_size", "grid_step_deg", "min_lat",
    "min_lon", "values"} where values is a row-major size x size matrix
    (row 0 = min_lat) of rounded floats, or None for points where the
    upstream model reports no data (never fabricated).
    """
    spec = _FIELD_METRICS.get(metric)
    if not spec:
        raise ValueError(f"unknown field metric: {metric}")
    latitude = float(latitude)
    longitude = float(longitude)
    if not _valid_latlon(latitude, longitude):
        raise ValueError("latitude must be in [-90, 90] and longitude in [-180, 180]")

    size = spec["grid_size"]
    step = spec["grid_step"]
    half = (size - 1) / 2.0
    min_lat = max(-90.0, latitude - half * step)
    min_lon = max(-180.0, longitude - half * step)

    cache_key = (metric, round(min_lat, 3), round(min_lon, 3))
    cached = _field_cache_get(cache_key)
    if cached:
        return cached

    points = [
        (round(min_lat + row * step, 4), round(min_lon + col * step, 4))
        for row in range(size)
        for col in range(size)
    ]
    flat_values = _fetch_field_points(metric, points)

    values = [
        flat_values[row * size : (row + 1) * size]
        for row in range(size)
    ]

    # Layer is only usable if at least one real reading came back.
    if not any(v is not None for v in flat_values):
        return None

    unit = "°C" if metric == "temp" else ("km/h" if metric == "wind" else "AQI")
    data = {
        "metric": metric,
        "unit": unit,
        "grid_size": size,
        "grid_step_deg": step,
        "min_lat": round(min_lat, 4),
        "min_lon": round(min_lon, 4),
        "values": values,
    }
    _field_cache_set(cache_key, data)
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
            "text": "Extreme heat: it's 40°C or hotter. Stay out of the sun as much as you can.",
            "protocol": ["Stay indoors where it's cool if possible", "Drink plenty of water — don't wait until you're thirsty"],
        })
    elif temperature is not None and temperature >= 35:
        alerts.append({
            "severity": "Advisory",
            "type": "High Temperature Advisory",
            "text": "It's a hot day — above 35°C. Drink water often and take breaks in the shade.",
            "protocol": ["Avoid heavy outdoor work between 12 PM and 4 PM"],
        })
    elif temperature is not None and temperature <= 0:
        alerts.append({
            "severity": "Warning",
            "type": "Freeze Warning",
            "text": "It's below freezing. Watch for frost and slippery patches, especially early morning.",
            "protocol": ["Protect plants and exposed pipes from the cold", "Drive carefully — bridges freeze first"],
        })

    if (wind_speed is not None and wind_speed >= 50) or (gusts is not None and gusts >= 65):
        alerts.append({
            "severity": "Advisory",
            "type": "Gale / Strong Wind Advisory",
            "text": f"Strong winds today — steady around {round(wind_speed or 0)} km/h, gusting to {round(gusts or wind_speed or 0)} km/h.",
            "protocol": ["Bring in or tie down loose outdoor items", "Take extra care on open roads"],
        })

    for i, p in enumerate(daily.get("precipitation_probability_max", [])):
        day_name = "Today" if i == 0 else f"Day {i + 1}"
        if p is not None and p >= 80:
            alerts.append({
                "severity": "Advisory",
                "type": "High Precipitation Probability",
                "text": f"Rain is very likely on {day_name}: about a {p}% chance. Keep an umbrella handy.",
                "protocol": ["Carry an umbrella or raincoat", "Expect wet roads — allow extra travel time"],
            })
            break

    for i, u in enumerate(daily.get("uv_index_max", [])):
        if u is not None and u >= 8:
            alerts.append({
                "severity": "Advisory",
                "type": "Extreme UV Index",
                "text": f"The sun will be intense — UV index peaks at {round(u, 1)}.",
                "protocol": ["Use sunscreen (SPF 30 or higher) and reapply through the day", "Wear sunglasses and a hat outdoors"],
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
        "tts": bool(ELEVENLABS_API_KEY),
        "tts_model": ELEVENLABS_TTS_MODEL,
        "tts_model_te": ELEVENLABS_TTS_MODEL_TE,
        "weatherapi_fallback": bool(WEATHERAPI_KEY),
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/config")
def api_config():
    """Public, client-safe configuration (no secrets here — only keys that
    are designed to be embedded in browsers, e.g. CARTO basemaps)."""
    return jsonify({"carto_api_key": CARTO_API_KEY})


def _detect_tts_lang(text):
    """Resolve an unknown/auto language label from the text's Unicode script:
    Devanagari blocks are Hindi, Telugu blocks are Telugu, everything else
    is English. Keeps the TE -> eleven_v3 routing (Telugu is NOT in Eleven
    Multilingual v2's language set) working when the frontend sends
    lang:"auto" — without this, Auto-mode Telugu was approximated by v2
    and pronounced with an English accent."""
    if any("\u0900" <= ch <= "\u097F" for ch in text):
        return "hi"
    if any("\u0C00" <= ch <= "\u0C7F" for ch in text):
        return "te"
    return "en"


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """Server-side ElevenLabs text-to-speech for the Speak Answer button.

    The ELEVENLABS_API_KEY stays in the backend — the frontend only sends
    text + language and receives audio bytes. Uses a natural sweet female
    voice; EN/HI are synthesized with Eleven Multilingual v2 (auto-detects
    the language from the text) while TE routes to Eleven v3, the only
    model with Telugu support (v2 covers 29 languages, Telugu isn't one).
    Soft, gentle delivery via voice_settings. Any failure returns a JSON
    error (never partial audio) so the frontend can fall back to browser
    TTS.
    """
    if not ELEVENLABS_API_KEY:
        return jsonify({"error": "TTS is not configured (ELEVENLABS_API_KEY missing)."}), 503
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    lang = str(payload.get("lang") or "en").strip().lower()
    if not text:
        return jsonify({"error": "text is required"}), 400
    if len(text) > 1200:
        return jsonify({"error": "text too long"}), 413
    if lang not in ("en", "hi", "te"):
        # "auto" (or any unknown label) is resolved from the text's script
        # so model routing below matches the language actually spoken;
        # explicit en/hi/te selections pass through untouched.
        lang = _detect_tts_lang(text)

    # Softer, slower, sweet delivery: low-ish stability for warmth, high
    # similarity for a natural voice, gentle speed below 1. Used for both
    # models (v3 accepts stability; its other documented control).
    sweet_settings = {
        "stability": 0.55,
        "similarity_boost": 0.8,
        "style": 0.25,
        "use_speaker_boost": True,
        "speed": 0.93,
    }

    def _call_upstream(model_id):
        body = {"text": text, "model_id": model_id, "voice_settings": sweet_settings}
        # Enforce Telugu on the v3 call (the only model that supports it):
        # without this the accent of the English reference voice bleeds into
        # the Telugu phonemes. Not sent on the multilingual-v2 retry — v2
        # does not support language_code, and its payload stays as before.
        if model_id == ELEVENLABS_TTS_MODEL_TE:
            body["language_code"] = "te"
        return requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )

    try:
        resp = _call_upstream(ELEVENLABS_TTS_MODEL if lang != "te" else ELEVENLABS_TTS_MODEL_TE)
        # Eleven v3 (the only model with Telugu) occasionally rejects
        # payloads v2 accepts; retry the same voice through v2 so TE
        # still speaks instead of failing (v2 may approximate it).
        if resp.status_code != 200 and lang == "te":
            print(f"[tts] eleven_v3 TE failed ({resp.status_code}); retrying with {ELEVENLABS_TTS_MODEL}")
            resp = _call_upstream(ELEVENLABS_TTS_MODEL)
    except requests.RequestException as error:
        return jsonify({"error": f"TTS upstream unreachable: {error}"}), 502
    if resp.status_code != 200:
        # Surface the upstream failure as JSON so the frontend falls back.
        return jsonify({"error": f"TTS upstream error {resp.status_code}"}), 502
    audio = resp.content
    if not audio:
        return jsonify({"error": "TTS upstream returned empty audio"}), 502
    return audio, 200, {"Content-Type": "audio/mpeg", "Cache-Control": "no-store"}


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


@app.route("/api/field")
def api_field():
    """Gridded model field for the radar map's Micro-Temp / Wind / AQI layers.

    All values are real model readings (Open-Meteo forecast / air-quality
    grids); points the model reports as unavailable are returned as null
    rather than interpolated or fabricated. Cached 10 minutes per grid.
    """
    try:
        latitude = float(request.args["latitude"])
        longitude = float(request.args["longitude"])
    except (KeyError, ValueError):
        return jsonify({"error": "valid latitude and longitude are required"}), 400

    if not _valid_latlon(latitude, longitude):
        return jsonify({"error": "latitude must be in [-90, 90] and longitude in [-180, 180]"}), 400

    metric = request.args.get("metric", "").strip().lower()
    if metric not in _FIELD_METRICS:
        return jsonify({"error": "metric must be one of: temp, wind, aqi"}), 400

    try:
        grid = build_field_grid(metric, latitude, longitude)
    except Exception as error:
        app.logger.warning("[field] %s grid failed: %s", metric, error)
        return jsonify({"error": "Field data temporarily unavailable"}), 502

    if grid is None:
        return jsonify({"error": "No field data available for this region"}), 404
    return jsonify(grid)


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
        return f"""- **What's happening**: {loc} में अभी तापमान {temp}°C है। मौसम शांत और स्थिर है।
- **Worth knowing**: बारिश की संभावना {rain_prob}%, हवा {wind} किमी/घंटा, UV इंडेक्स {uv}।
- **What you can do**: दिन में पानी पीते रहें और तेज़ धूप में टोपी या चश्मा रखें।"""
    elif is_telugu:
        return f"""- **What's happening**: {loc}లో ప్రస్తుత ఉష్ణోగ్రత {temp}°C. వాతావరణం ప్రశాంతంగా ఉంది.
- **Worth knowing**: వర్షం అవకాశం {rain_prob}%, గాలి {wind} km/h, UV ఇండెక్స్ {uv}.
- **What you can do**: పగటిపూట నీరు తగినంత తాగండి, ఎండ వేళల్లో టోపీ లేదా కళ్లద్దాలు వాడండి."""
    else:
        return f"""- **What's happening**: The temperature in {loc} is {temp}°C with calm, settled weather.
- **Worth knowing**: Rain chance {rain_prob}%, wind around {wind} km/h, UV index {uv}.
- **What you can do**: A good day to be outdoors — drink water through the day and keep a hat or sunglasses handy in the sun."""


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
            answer = f"It's {weather_summary} in {location_name} — a good afternoon for a ride, with light winds and clear air. Leaving between 2:00 PM and 4:45 PM keeps you ahead of the heat and any evening breeze."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Best time to ride",
                "time_range": "2:15 PM – 4:45 PM",
                "reliability": "High confidence",
                "favorable_note": "Dry roads, light wind",
                "caution_note": "Strong sun around midday — carry water",
            },
            "route_progression": [
                {"label": "Start", "place": location_name, "temp": "Current", "condition": "Roads dry"},
                {"label": "Midway", "place": "En route", "temp": "Warm", "condition": "Light crosswind"},
                {"label": "Arrival", "place": "Destination", "temp": "Comfortable", "condition": "Clear visibility"},
            ],
            "attire_guidance": {
                "headline": "What to wear",
                "layers": ["Breathable t-shirt or jersey", "Light wind vest if it gets breezy"],
                "accessories": ["Sunglasses", "Water bottle"],
                "thermal_rating": "Comfortable",
            },
        }

    elif is_rain:
        if is_hindi:
            answer = f"{location_name} में वर्तमान मौसम स्थिति: {weather_summary}। शाम के आवागमन (commute) के दौरान हल्की या मध्यम बारिश की संभावना बनी रह सकती है। सुरक्षित यात्रा के लिए छाता या वाटरप्रूफ जैकेट साथ रखना उचित रहेगा।"
        elif is_telugu:
            answer = f"{location_name}లో ప్రస్తుత వాతావరణ పరిస్థితి: {weather_summary}। సాయంత్రం ప్రయాణ సమయంలో తేలికపాటి వర్షం కురిసే అవకాశం ఉంది. మీ వెంట గొడుగు లేదా రెయిన్‌కోట్ ఉంచుకోవడం శ్రేయస్కరం."
        else:
            answer = f"It's {weather_summary} in {location_name}. Light showers are possible during the evening commute, so carry an umbrella or rain jacket just in case."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Leave before the showers",
                "time_range": "4:30 PM – 6:00 PM",
                "reliability": "High confidence",
                "favorable_note": "Roads mostly dry if you leave early",
                "caution_note": "Wet roads possible after 6:00 PM",
            },
            "route_progression": [
                {"label": "Start", "place": location_name, "temp": "Current", "condition": "Pavements dry"},
                {"label": "Midway", "place": "En route", "temp": "Cooling", "condition": "Clouds building"},
                {"label": "Arrival", "place": "Destination", "temp": "Muggy", "condition": "Showers possible"},
            ],
            "attire_guidance": {
                "headline": "What to wear",
                "layers": ["Water-resistant jacket on top", "Comfortable everyday clothes"],
                "accessories": ["Compact umbrella", "Shoes that can get wet"],
                "thermal_rating": "Mild / Damp",
            },
        }

    elif is_clothes:
        if is_hindi:
            answer = f"{location_name} में {weather_summary} के आधार पर, आज हल्के और सांस लेने योग्य सूती कपड़े पहनने की सलाह दी जाती है। यदि आप शाम को बाहर जा रहे हैं, तो तापमान में गिरावट के लिए एक हल्का कार्डिगन या विंडब्रेकर साथ रखें।"
        elif is_telugu:
            answer = f"{location_name}లో {weather_summary} నమోదైంది. ఈ రోజు సౌకర్యవంతమైన కాటన్ దుస్తులు అనుకూలం. సాయంత్రం వేళల్లో ఉష్ణోగ్రత కాస్త తగ్గితే తేలికపాటి జాకెట్ లేదా శాలువా ఉపయోగపడుతుంది."
        else:
            answer = f"With {weather_summary} in {location_name}, light and breathable clothes are your friend today. Keep a light layer handy for air-conditioned rooms or once the evening cools down."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Comfortable hours",
                "time_range": "All day, mildest in the morning",
                "reliability": "High confidence",
                "favorable_note": "Pleasant through midday",
                "caution_note": "Cools down after sunset",
            },
            "route_progression": [
                {"label": "Morning", "place": location_name, "temp": "Cool", "condition": "A light layer helps"},
                {"label": "Midday", "place": "Out and about", "temp": "Warm", "condition": "Light, breathable clothes"},
                {"label": "Evening", "place": "After sunset", "temp": "Mild", "condition": "Comfortable with a breeze"},
            ],
            "attire_guidance": {
                "headline": "What to wear",
                "layers": ["Light, breathable base", "Cardigan or windbreaker for later"],
                "accessories": ["Sunglasses", "Comfortable walking shoes"],
                "thermal_rating": "Comfortable",
            },
        }

    elif is_tomorrow:
        if is_hindi:
            answer = f"{location_name} में कल का मौसम लगभग वर्तमान पैटर्न ({weather_summary}) के समान ही स्थिर रहने का अनुमान है। दिन में सामान्य तापमान और अच्छी दृश्यता रहेगी।"
        elif is_telugu:
            answer = f"{location_name}లో రేపటి వాతావరణం దాదాపు ప్రస్తుత పరిస్థితి ({weather_summary}) లాగే కొనసాగుతుంది. సాధారణ ఉష్ణోగ్రతలతో వాతావరణం అనుకూలంగా ఉంటుంది."
        else:
            answer = f"Tomorrow looks much like today in {location_name} ({weather_summary}). A steady morning works well for travel and outdoor plans."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Best time tomorrow",
                "time_range": "8:00 AM – 11:30 AM",
                "reliability": "High confidence",
                "favorable_note": "Fresh morning air, clear skies",
                "caution_note": "Warms up around midday",
            },
            "route_progression": [
                {"label": "Morning", "place": location_name, "temp": "Cool", "condition": "Calm and clear"},
                {"label": "Midday", "place": "Around town", "temp": "Warm", "condition": "Full sun"},
                {"label": "Evening", "place": "After sunset", "temp": "Mild", "condition": "Light breeze"},
            ],
            "attire_guidance": {
                "headline": "What to wear tomorrow",
                "layers": ["Everyday light clothes", "A light layer for the evening"],
                "accessories": ["Sun hat", "Water bottle"],
                "thermal_rating": "Mild",
            },
        }

    else:
        if is_hindi:
            answer = f"{location_name} के लिए वायुमंडलीय विश्लेषण: वर्तमान में {weather_summary} रिकॉर्ड किया गया है। वायुमंडलीय दबाव और हवा की स्थिति सामान्य सीमा में है।"
        elif is_telugu:
            answer = f"{location_name} వాతావరణ సమాచారం: ప్రస్తుతం {weather_summary} నమోదైంది. వాతావరణ పీడనం మరియు గాలి వేగం సాధారణ పరిమితుల్లో ఉన్నాయి."
        else:
            answer = f"Right now in {location_name} it's {weather_summary}. Pressure and wind are sitting in their normal range — a steady, unremarkable weather day."

        return {
            "answer": answer,
            "optimal_window": {
                "title": "Good time for plans",
                "time_range": "1:30 PM – 4:45 PM",
                "reliability": "High confidence",
                "favorable_note": "Steady weather through the afternoon",
                "caution_note": "Worth a quick radar check before you head out",
            },
            "route_progression": [
                {"label": "Start", "place": location_name, "temp": "Current", "condition": "Settled conditions"},
                {"label": "Midway", "place": "En route", "temp": "Normal", "condition": "Light breeze"},
                {"label": "Arrival", "place": "Destination", "temp": "As expected", "condition": "No surprises"},
            ],
            "attire_guidance": {
                "headline": "What to wear",
                "layers": ["Everyday comfortable clothes", "An optional light layer"],
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
                    {"role": "system", "content": "You are WeatherGPT, a friendly, practical weather assistant. Output strictly JSON."},
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
