"""Unit + API tests for the WeatherGPT backend.

Run:  cd backend && python test_app.py

Covers alert thresholds, location-name extraction, input validation,
cache eviction, and the live API contract. The live-API section needs
network access; everything else runs offline.
"""

import json
import sys
import unittest
import unittest.mock
from datetime import datetime

import requests

sys.path.insert(0, ".")


def _network_available():
    import socket

    try:
        socket.create_connection(("api.open-meteo.com", 443), timeout=3)
        return True
    except OSError:
        return False

import app as wgpt  # noqa: E402
import farm_advisor  # noqa: E402


class TestLocationExtraction(unittest.TestCase):
    def test_dict_name(self):
        self.assertEqual(wgpt._extract_location_name({"name": "Hyderabad"}), "Hyderabad")

    def test_plain_string(self):
        self.assertEqual(wgpt._extract_location_name("Bengaluru"), "Bengaluru")

    def test_none_and_empty(self):
        self.assertEqual(wgpt._extract_location_name(None), "Local Area")
        self.assertEqual(wgpt._extract_location_name({"name": ""}), "Local Area")
        self.assertEqual(wgpt._extract_location_name({"name": "   "}), "Local Area")

    def test_fallback_report_accepts_string_location(self):
        # Regression: this shape previously raised AttributeError -> 500.
        report = wgpt.generate_synoptic_report_fallback(
            {"location": "Hyderabad", "weather_data": {"current": {"temperature_2m": 31}, "daily": {}}},
            "English",
        )
        self.assertIn("Hyderabad", report)
        self.assertIn("31", report)


class TestLatLonValidation(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(wgpt._valid_latlon(17.38, 78.48))
        self.assertTrue(wgpt._valid_latlon(-90, -180))
        self.assertTrue(wgpt._valid_latlon(90, 180))

    def test_invalid(self):
        self.assertFalse(wgpt._valid_latlon(999, 999))
        self.assertFalse(wgpt._valid_latlon(-91, 0))
        self.assertFalse(wgpt._valid_latlon(0, 181))
        self.assertFalse(wgpt._valid_latlon("17", 78))


class TestForecastDaysValidation(unittest.TestCase):
    def test_non_integer_raises_valueerror(self):
        with self.assertRaises(ValueError):
            wgpt.fetch_weather(17.38, 78.48, "abc")

    def test_out_of_range_clamped(self):
        params_seen = {}

        class FakeResp:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {}

        def fake_get(url, params=None, timeout=None):
            params_seen.update(params)
            return FakeResp()

        original_get = wgpt.requests.get
        wgpt.requests.get = fake_get
        try:
            wgpt._fetch_open_meteo(17.38, 78.48, 99)
            self.assertEqual(params_seen["forecast_days"], 7)
        finally:
            wgpt.requests.get = original_get


class TestAlertThresholds(unittest.TestCase):
    def test_all_alerts_fire(self):
        data = {
            "current": {"temperature_2m": 41, "wind_speed_10m": 55, "wind_gusts_10m": 70},
            "daily": {"precipitation_probability_max": [85], "uv_index_max": [9]},
        }
        types = {a["type"] for a in wgpt.build_alerts(data)}
        self.assertLessEqual(
            {"Extreme Heat Alert", "Gale / Strong Wind Advisory", "High Precipitation Probability", "Extreme UV Index"},
            types,
        )

    def test_no_alerts_in_mild_weather(self):
        data = {
            "current": {"temperature_2m": 22, "wind_speed_10m": 10, "wind_gusts_10m": 15},
            "daily": {"precipitation_probability_max": [10], "uv_index_max": [3]},
        }
        self.assertEqual(wgpt.build_alerts(data), [])

    def test_boundary_values(self):
        self.assertEqual(
            [a["type"] for a in wgpt.build_alerts({"current": {"temperature_2m": 40}, "daily": {}})],
            ["Extreme Heat Alert"],
        )
        self.assertEqual(
            [a["type"] for a in wgpt.build_alerts({"current": {"temperature_2m": 0}, "daily": {}})],
            ["Freeze Warning"],
        )
        self.assertEqual(
            [a["type"] for a in wgpt.build_alerts({"current": {"temperature_2m": 35}, "daily": {}})],
            ["High Temperature Advisory"],
        )

    def test_missing_fields_do_not_crash(self):
        self.assertEqual(wgpt.build_alerts({"current": {}, "daily": {}}), [])


class TestConditionTextToWmo(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(wgpt._condition_text_to_wmo("Thunderstorm"), 95)
        self.assertEqual(wgpt._condition_text_to_wmo("Heavy snow"), 73)
        self.assertEqual(wgpt._condition_text_to_wmo("Light drizzle"), 51)
        self.assertEqual(wgpt._condition_text_to_wmo("Partly cloudy"), 2)
        self.assertEqual(wgpt._condition_text_to_wmo("Overcast"), 3)
        self.assertEqual(wgpt._condition_text_to_wmo("Clear"), 0)
        self.assertEqual(wgpt._condition_text_to_wmo(None), 2)


class TestCacheEviction(unittest.TestCase):
    def setUp(self):
        wgpt._WEATHER_CACHE.clear()

    def tearDown(self):
        wgpt._WEATHER_CACHE.clear()

    def test_lru_eviction_caps_entries(self):
        for i in range(wgpt._WEATHER_CACHE_MAX_ENTRIES + 10):
            wgpt._cache_set((i, 0, 7), {"i": i})
        self.assertEqual(len(wgpt._WEATHER_CACHE), wgpt._WEATHER_CACHE_MAX_ENTRIES)
        self.assertIsNone(wgpt._cache_get((0, 0, 7)))  # oldest evicted
        self.assertEqual(
            wgpt._cache_get((wgpt._WEATHER_CACHE_MAX_ENTRIES + 9, 0, 7)),
            {"i": wgpt._WEATHER_CACHE_MAX_ENTRIES + 9},
        )

    def test_ttl_expiry(self):
        wgpt._WEATHER_CACHE[(1, 1, 7)] = (0, {"stale": True})  # timestamp 0 = ancient
        self.assertIsNone(wgpt._cache_get((1, 1, 7)))


class TestLanguageNormalization(unittest.TestCase):
    def test_codes_and_names(self):
        self.assertEqual(wgpt.normalize_language("en", "auto"), ("en", "English"))
        self.assertEqual(wgpt.normalize_language("Hindi", "en"), ("hi", "Hindi"))
        self.assertEqual(wgpt.normalize_language("TELUGU", "en"), ("te", "Telugu"))
        self.assertEqual(wgpt.normalize_language("fr", "hi"), ("hi", "Hindi"))
class TestFarmAdvisorEngine(unittest.TestCase):
    """Deterministic rules engine: real payload in -> auditable advice out."""

    def _payload(self, **over):
        base = {
            "current": {"temperature_2m": 28.0, "apparent_temperature": 30.0,
                        "wind_speed_10m": 12.0, "wind_gusts_10m": 18.0},
            "daily": {"precipitation_sum": [0.0, 0.0, 0.0, 5.0],
                      "precipitation_probability_max": [10, 15, 20, 25],
                      "temperature_2m_min": [20.0, 21.0, 22.0],
                      "temperature_2m_max": [31.0, 32.0, 33.0]},
        }
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
        return base

    def test_validation_rejects_unknown_crop_stage(self):
        _, _, _, err = farm_advisor.validate_params("mango", "sowing", "en")
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 400)
        _, _, _, err = farm_advisor.validate_params("rice", "blooming", "en")
        self.assertIsNotNone(err)

    def test_validation_accepts_valid(self):
        c, s, lang, err = farm_advisor.validate_params("  Rice ", "Sowing", "hi")
        self.assertIsNone(err)
        self.assertEqual((c, s, lang), ("rice", "sowing", "hi"))

    def test_snapshot_echoes_real_values_only(self):
        p = self._payload()
        out = farm_advisor.build_farm_advice(p, "cotton", "growing", "en")
        snap = out["weather_snapshot"]
        self.assertEqual(snap["temperature_c"], 28.0)
        self.assertEqual(snap["rain_next_3_days_mm"], 0.0)
        self.assertEqual(snap["wind_kph"], 12.0)

    def test_rain_3d_sums_daily_precip(self):
        p = self._payload(daily={"precipitation_sum": [2.0, 3.5, 4.5]})
        out = farm_advisor.build_farm_advice(p, "maize", "growing", "en")
        self.assertEqual(out["weather_snapshot"]["rain_next_3_days_mm"], 10.0)

    def test_irrigation_skip_when_heavy_rain(self):
        p = self._payload(daily={"precipitation_sum": [15.0, 12.0, 8.0]})
        out = farm_advisor.build_farm_advice(p, "cotton", "growing", "en")
        irr = next(a for a in out["advice"] if a["topic"] == "irrigation")
        self.assertEqual(irr["severity"], "info")
        self.assertIn("35", irr["text"])  # 15+12+8

    def test_irrigation_caution_when_dry(self):
        p = self._payload()
        out = farm_advisor.build_farm_advice(p, "groundnut", "growing", "en")
        irr = next(a for a in out["advice"] if a["topic"] == "irrigation")
        self.assertEqual(irr["severity"], "caution")
        self.assertIn("Dry spell", irr["text"])

    def test_harvest_pause_on_rain(self):
        p = self._payload(daily={"precipitation_sum": [12.0, 0.0, 0.0]})
        out = farm_advisor.build_farm_advice(p, "rice", "harvesting", "en")
        h = next(a for a in out["advice"] if a["topic"] == "harvest")
        self.assertEqual(h["severity"], "action")
        self.assertIn("PAUSE", h["text"])

    def test_harvest_go_when_dry(self):
        p = self._payload()
        out = farm_advisor.build_farm_advice(p, "wheat", "harvesting", "en")
        h = next(a for a in out["advice"] if a["topic"] == "harvest")
        self.assertEqual(h["severity"], "info")
        self.assertIn("Dry window", h["text"])

    def test_wind_no_spray_threshold(self):
        p = self._payload(current={"wind_speed_10m": 32.0, "wind_gusts_10m": 50.0})
        out = farm_advisor.build_farm_advice(p, "maize", "flowering", "en")
        w = next(a for a in out["advice"] if a["topic"] == "wind")
        self.assertEqual(w["severity"], "action")
        self.assertIn("do NOT spray", w["text"])

    def test_heat_stress_uses_crop_threshold(self):
        # wheat threshold is 32; 34 must trigger crop-specific advice
        p = self._payload(current={"temperature_2m": 34.0, "apparent_temperature": 34.0})
        out = farm_advisor.build_farm_advice(p, "wheat", "flowering", "en")
        h = next(a for a in out["advice"] if a["topic"] == "heat")
        self.assertIn("heat-stress threshold", h["text"])
        self.assertIn("Wheat", h["text"])

    def test_heat_shift_advice_between_33_and_threshold(self):
        # cotton threshold is 38; 35 -> generic heat-shift advice
        p = self._payload(current={"temperature_2m": 35.0, "apparent_temperature": 36.0})
        out = farm_advisor.build_farm_advice(p, "cotton", "growing", "en")
        h = next(a for a in out["advice"] if a["topic"] == "heat")
        self.assertIn("shift field work", h["text"])

    def test_frost_note_on_cold_min(self):
        p = self._payload(daily={"temperature_2m_min": [2.0, 4.0, 5.0]})
        out = farm_advisor.build_farm_advice(p, "wheat", "growing", "en")
        topics = {a["topic"] for a in out["advice"]}
        self.assertIn("field", topics)
        frost = [a for a in out["advice"] if "frost" in a["text"].lower()]
        self.assertTrue(frost)

    def test_multilingual_output(self):
        p = self._payload()
        for lang, expected in (("hi", "सिंचाई"), ("te", "నీటి పారుదల")):
            out = farm_advisor.build_farm_advice(p, "rice", "growing", lang)
            self.assertEqual(out["crop_name"], farm_advisor.CROPS["rice"]["names"][lang])
            irr = next(a for a in out["advice"] if a["topic"] == "irrigation")
            self.assertIn(expected, irr["topic_label"])
            self.assertTrue(any("\u0900" <= ch <= "\u097f" or "\u0c00" <= ch <= "\u0c7f" for ch in irr["text"]),
                            f"advice text should be in {lang}")

    def test_unknown_lang_falls_back_to_english(self):
        p = self._payload()
        out = farm_advisor.build_farm_advice(p, "rice", "growing", "fr")
        self.assertIn("Irrigation", next(a for a in out["advice"] if a["topic"] == "irrigation")["topic_label"])

    def test_no_pesticide_or_dosage_terms(self):
        p = self._payload()
        for crop in farm_advisor.CROPS:
            for stage in farm_advisor.STAGES:
                out = farm_advisor.build_farm_advice(p, crop, stage, "en")
                blob = " ".join(a["text"] for a in out["advice"]).lower()
                for bad in ("ml/acre", "kg/acre", "grams per", "dose", "dosage", "ppm"):
                    self.assertNotIn(bad, blob)


class TestFarmAdvisorEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wgpt.app.config["TESTING"] = True
        cls.client = wgpt.app.test_client()

    def test_requires_crop_and_stage(self):
        r = self.client.get("/api/farm-advice?latitude=17.38&longitude=78.48")
        self.assertEqual(r.status_code, 400)

    def test_rejects_unknown_crop(self):
        r = self.client.get("/api/farm-advice?crop=mango&stage=sowing&latitude=17.38&longitude=78.48")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Valid crops", r.get_json()["error"])

    def test_rejects_unknown_stage(self):
        r = self.client.get("/api/farm-advice?crop=rice&stage=blooming&latitude=17.38&longitude=78.48")
        self.assertEqual(r.status_code, 400)

    def test_rejects_bad_latlon(self):
        r = self.client.get("/api/farm-advice?crop=rice&stage=sowing&latitude=999&longitude=999")
        self.assertEqual(r.status_code, 400)

    def test_rejects_bad_forecast_days(self):
        r = self.client.get("/api/farm-advice?crop=rice&stage=sowing&latitude=17.38&longitude=78.48&forecast_days=abc")
        self.assertEqual(r.status_code, 400)

    @unittest.skipUnless(_network_available(), "network unavailable")
    def test_live_advice_en_hi_te(self):
        for lang, marker in (("en", "advisory"), ("hi", "सलाह"), ("te", "సలహా")):
            r = self.client.get(f"/api/farm-advice?crop=rice&stage=growing&latitude=17.38&longitude=78.48&language={lang}")
            self.assertEqual(r.status_code, 200, f"lang={lang}")
            body = r.get_json()
            self.assertEqual(body["crop"], "rice")
            self.assertEqual(body["language"], lang)
            self.assertTrue(body["advice"])
            self.assertIn(marker, json.dumps(body, ensure_ascii=False))

    @unittest.skipUnless(_network_available(), "network unavailable")
    def test_live_all_crops_all_stages(self):
        for crop in farm_advisor.CROPS:
            for stage in farm_advisor.STAGES:
                r = self.client.get(
                    f"/api/farm-advice?crop={crop}&stage={stage}&latitude=17.38&longitude=78.48")
                self.assertEqual(r.status_code, 200, f"{crop}/{stage}")
                body = r.get_json()
                self.assertTrue(body["advice"])
                self.assertNotIn("error", body)

    def test_crops_catalogue(self):
        r = self.client.get("/api/crops")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        ids = {c["id"] for c in body["crops"]}
        self.assertGreaterEqual(len(ids), 6)
        self.assertTrue({"rice", "cotton", "maize", "groundnut"} <= ids)
        self.assertEqual({s["id"] for s in body["stages"]}, {"sowing", "growing", "flowering", "harvesting"})


@unittest.skipUnless(SystemExit is not None, "always runs")
class TestLiveApiContract(unittest.TestCase):
    """Boots the real app and exercises endpoints. Needs network for the
    weather/geocode happy paths; validation paths work offline."""

    @classmethod
    def setUpClass(cls):
        wgpt.app.config["TESTING"] = True
        cls.client = wgpt.app.test_client()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")

    def test_config_exposes_only_public_key(self):
        r = self.client.get("/api/config")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("carto_api_key", body)
        self.assertNotIn("OLLAMA_API_KEY", str(body))

    def test_weather_validation(self):
        r = self.client.get("/api/weather")
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/weather?latitude=999&longitude=999")
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/weather?latitude=17.38&longitude=78.48&forecast_days=abc")
        self.assertEqual(r.status_code, 400)

    def test_weather_no_error_leak(self):
        r = self.client.get("/api/weather?latitude=999&longitude=999")
        self.assertNotIn("open-meteo.com", r.get_data(as_text=True))

    def test_report_accepts_string_location(self):
        r = self.client.post(
            "/api/report",
            json={"location": "Hyderabad", "weather_data": {"current": {"temperature_2m": 31}}},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("report", r.get_json())

    def test_assistant_requires_query(self):
        r = self.client.post("/api/assistant", json={"query": ""})
        self.assertEqual(r.status_code, 400)

    def test_geocode_requires_location(self):
        r = self.client.get("/api/geocode")
        self.assertEqual(r.status_code, 400)

    def test_unknown_api_path_returns_json_404(self):
        r = self.client.get("/api/nonexistent")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    @unittest.skipUnless(_network_available(), "network unavailable")
    def test_weather_live(self):
        r = self.client.get("/api/weather?latitude=17.38&longitude=78.48")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("current", body["weather"])
        self.assertIn("alerts", body)
        self.assertIn("air_quality", body)

    @unittest.skipUnless(_network_available(), "network unavailable")
    def test_geocode_live(self):
        r = self.client.get("/api/geocode?location=Tokyo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["name"], "Tokyo")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class TestReverseGeocode(unittest.TestCase):
    """GPS coordinates -> real locality name, including provider fallback.
    Upstream HTTP is mocked so these run offline and deterministically."""

    @classmethod
    def setUpClass(cls):
        wgpt.app.config["TESTING"] = True
        cls.client = wgpt.app.test_client()

    def setUp(self):
        wgpt._REV_GEO_CACHE.clear()

    def test_primary_resolves_locality(self):
        with unittest.mock.patch.object(
            wgpt.requests, "get",
            return_value=_FakeResponse(payload={
                "locality": "Suginami-ku", "principalSubdivision": "Tokyo",
                "countryName": "Japan"}),
        ):
            r = self.client.get("/api/reverse-geocode?latitude=35.70&longitude=139.65")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["name"], "Suginami-ku")
        self.assertEqual(body["country"], "Japan")
        self.assertNotEqual(body["name"], "Current Location")

    def test_primary_missing_fields_falls_back_to_nominatim(self):
        def fake_get(url, **kwargs):
            if "bigdatacloud" in url:
                return _FakeResponse(payload={})  # 200 but no usable fields
            return _FakeResponse(payload={
                "address": {"city": "São Paulo", "state": "São Paulo", "country": "Brazil"}})
        with unittest.mock.patch.object(wgpt.requests, "get", side_effect=fake_get):
            r = self.client.get("/api/reverse-geocode?latitude=-23.55&longitude=-46.63")
        body = r.get_json()
        self.assertEqual(body["name"], "São Paulo")
        self.assertEqual(body["country"], "Brazil")

    def test_primary_error_falls_back_to_nominatim(self):
        def fake_get(url, **kwargs):
            if "bigdatacloud" in url:
                raise requests.exceptions.ConnectionError("upstream down")
            return _FakeResponse(payload={
                "address": {"town": "Reading", "county": "Berkshire", "country": "United Kingdom"}})
        with unittest.mock.patch.object(wgpt.requests, "get", side_effect=fake_get):
            r = self.client.get("/api/reverse-geocode?latitude=51.45&longitude=-0.97")
        body = r.get_json()
        self.assertEqual(body["name"], "Reading")
        self.assertEqual(body["country"], "United Kingdom")

    def test_both_providers_down_never_returns_placeholder_name(self):
        with unittest.mock.patch.object(
            wgpt.requests, "get", side_effect=requests.exceptions.ConnectionError("offline"),
        ):
            r = self.client.get("/api/reverse-geocode?latitude=10.0&longitude=20.0")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["name"], "")  # coordinates-only shape
        self.assertNotEqual(body["name"], "Current Location")
        self.assertEqual(body["latitude"], 10.0)
        self.assertEqual(body["longitude"], 20.0)

    def test_cache_hit_skips_upstream(self):
        calls = []
        def fake_get(url, **kwargs):
            calls.append(url)
            return _FakeResponse(payload={
                "locality": "Suginami-ku", "principalSubdivision": "Tokyo",
                "countryName": "Japan"})
        with unittest.mock.patch.object(wgpt.requests, "get", side_effect=fake_get):
            self.client.get("/api/reverse-geocode?latitude=35.70&longitude=139.65")
            # GPS jitter below the cache's ~11 m rounding reuses the same key.
            self.client.get("/api/reverse-geocode?latitude=35.700004&longitude=139.649998")
        self.assertEqual(len(calls), 1)  # second call served from TTL cache

    def test_invalid_coordinates_rejected(self):
        r = self.client.get("/api/reverse-geocode?latitude=999&longitude=999")
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/reverse-geocode")
        self.assertEqual(r.status_code, 400)


class TestRainTimelineAndSmartRainAlerts(unittest.TestCase):
    """Rain Timeline + Smart Rain Alert: deterministic logic over the real
    hourly payload. Runs fully offline (no upstream HTTP involved)."""

    @classmethod
    def setUpClass(cls):
        wgpt.app.config["TESTING"] = True
        cls.client = wgpt.app.test_client()

    @staticmethod
    def _payload(probs):
        # Hourly series starts at 12:00 local; "now" is frozen just before it
        # so every provided hour counts as upcoming — keeps tests wall-clock-proof.
        times = [f"2026-09-15T{h:02d}:00" for h in range(12, 12 + len(probs))]
        data = {"hourly": {"time": times, "precipitation_probability": probs}, "utc_offset_seconds": 0}
        return wgpt.build_rain_timeline(data, now_local=datetime(2026, 9, 15, 11, 30))

    def test_no_rain_below_threshold(self):
        tl = self._payload([10, 20, 30, 40, 50, 55, 59, 59, 59, 59, 59, 59])
        self.assertFalse(tl["has_event"])
        self.assertIsNone(tl["start_label"])

    def test_event_below_min_duration_ignored(self):
        # 60% for a single hour does not meet the 2-hour minimum.
        tl = self._payload([10, 65, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10])
        self.assertFalse(tl["has_event"])

    def test_event_detected_with_labels(self):
        tl = self._payload([5, 5, 70, 80, 65, 10, 10, 10, 10, 10, 10, 10])
        self.assertTrue(tl["has_event"])
        self.assertEqual(tl["starts_in_h"], 2)
        self.assertEqual(tl["duration_h"], 3)
        self.assertEqual(tl["start_label"], "14:00")
        self.assertEqual(tl["end_label"], "16:00")
        self.assertEqual(tl["peak_probability"], 80)

    def test_longest_run_wins_over_two_shorter(self):
        # 3-hour run (19:00-21:00) must beat two 2-hour runs earlier on.
        tl = self._payload([0, 60, 60, 0, 60, 60, 0, 80, 80, 80, 0, 0])
        self.assertEqual(tl["start_label"], "19:00")
        self.assertEqual(tl["duration_h"], 3)

    def test_alert_fires_with_snapshot_and_localization(self):
        tl = self._payload([5, 5, 70, 80, 65, 10, 10, 10, 10, 10, 10, 10])
        en = wgpt.build_smart_rain_alerts(tl, "en")[0]
        self.assertEqual(en["severity"], "Advisory")
        self.assertIn("14:00", en["text"])
        self.assertIn("80%", en["text"])
        self.assertEqual(en["snapshot"]["peak_probability"], 80)
        self.assertEqual(en["snapshot"]["starts_in_h"], 2)

        hi = wgpt.build_smart_rain_alerts(tl, "hi")[0]["text"]
        self.assertIn("छाता", hi)
        te = wgpt.build_smart_rain_alerts(tl, "te")[0]["text"]
        self.assertIn("గొడుగు", te)
        # Unknown language codes fall back to English, never crash.
        self.assertEqual(wgpt.build_smart_rain_alerts(tl, "xx")[0], en)

    def test_no_alert_when_no_event(self):
        self.assertEqual(wgpt.build_smart_rain_alerts({"has_event": False}), [])

    def test_weather_payload_includes_rain_timeline(self):
        wgpt._WEATHER_CACHE.clear()
        with unittest.mock.patch.object(
            wgpt.requests, "get", return_value=_FakeResponse(payload={
                "hourly": {
                    "time": [f"2026-09-15T{h:02d}:00" for h in range(12, 24)],
                    "precipitation_probability": [5, 5, 70, 80, 65, 10, 10, 10, 10, 10, 10, 10],
                },
                "utc_offset_seconds": 0,
            }),
        ):
            # Unusual coords keep this mocked entry out of the shared
            # cache slot used by the live-API tests (2-decimal key).
            r = self.client.get("/api/weather?latitude=12.34&longitude=56.78")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("rain_timeline", body)
        # The mocked series starts at 12:00, so the real "now" falls inside
        # it — the timeline must still find the 14:00-16:00 event regardless
        # of the wall clock this test runs on.
        self.assertTrue(body["rain_timeline"]["has_event"])
        self.assertEqual(body["rain_timeline"]["peak_probability"], 80)
        smart = [a for a in body["alerts"] if a["type"] == "Smart Rain Alert"]
        self.assertEqual(len(smart), 1)
        self.assertEqual(smart[0]["snapshot"]["peak_probability"], 80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
