"""Unit + API tests for the WeatherGPT backend.

Run:  cd backend && python test_app.py

Covers alert thresholds, location-name extraction, input validation,
cache eviction, and the live API contract. The live-API section needs
network access; everything else runs offline.
"""

import sys
import unittest

sys.path.insert(0, ".")


def _network_available():
    import socket

    try:
        socket.create_connection(("api.open-meteo.com", 443), timeout=3)
        return True
    except OSError:
        return False

import app as wgpt  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
