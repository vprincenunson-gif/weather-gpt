"""Unit tests for the server-side ElevenLabs TTS endpoint (/api/tts).

The ELEVENLABS_API_KEY must NEVER leave the backend: these tests verify
the endpoint contract (auth shape, model, sweet-female voice settings,
error mapping) with the upstream HTTP call mocked — no network, no key.
"""
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")
import app as wgpt  # noqa: E402


def _fake_response(status=200, content=b"MP3BYTES", json_body=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.content = content
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


class TestTtsEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = wgpt.app.test_client()
        self._orig_key = wgpt.ELEVENLABS_API_KEY

    def tearDown(self):
        wgpt.ELEVENLABS_API_KEY = self._orig_key

    def test_503_without_key(self):
        wgpt.ELEVENLABS_API_KEY = None
        r = self.client.post("/api/tts", json={"text": "hello"})
        self.assertEqual(r.status_code, 503)
        self.assertIn(b"not configured", r.data)

    def test_400_without_text(self):
        wgpt.ELEVENLABS_API_KEY = "k"
        r = self.client.post("/api/tts", json={})
        self.assertEqual(r.status_code, 400)

    def test_413_text_too_long(self):
        wgpt.ELEVENLABS_API_KEY = "k"
        r = self.client.post("/api/tts", json={"text": "a" * 1300})
        self.assertEqual(r.status_code, 413)

    @mock.patch.object(wgpt.requests, "post")
    def test_success_calls_upstream_with_server_key(self, post):
        wgpt.ELEVENLABS_API_KEY = "SECRET-KEY"
        post.return_value = _fake_response(200, b"MP3BYTES")
        r = self.client.post("/api/tts", json={"text": "Namaste", "lang": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "audio/mpeg")
        self.assertEqual(r.data, b"MP3BYTES")
        args, kwargs = post.call_args
        # Key only in the server->upstream header, never in a response body.
        self.assertEqual(kwargs["headers"]["xi-api-key"], "SECRET-KEY")
        self.assertIn("/text-to-speech/", args[0])
        self.assertIn(wgpt.ELEVENLABS_VOICE_ID, args[0])
        body = kwargs["json"]
        self.assertEqual(body["model_id"], "eleven_multilingual_v2")
        # Soft, sweet delivery settings.
        self.assertLess(body["voice_settings"]["speed"], 1.0)
        self.assertEqual(body["voice_settings"]["stability"], 0.55)

    @mock.patch.object(wgpt.requests, "post")
    def test_unknown_language_normalizes(self, post):
        wgpt.ELEVENLABS_API_KEY = "k"
        post.return_value = _fake_response(200, b"A")
        r = self.client.post("/api/tts", json={"text": "Bonjour", "lang": "fr"})
        self.assertEqual(r.status_code, 200)  # treated as default language

    @mock.patch.object(wgpt.requests, "post")
    def test_upstream_auth_error_maps_to_502_json(self, post):
        wgpt.ELEVENLABS_API_KEY = "k"
        post.return_value = _fake_response(401, b'{"detail": "bad key"}')
        r = self.client.post("/api/tts", json={"text": "hello"})
        self.assertEqual(r.status_code, 502)
        self.assertIn(b"TTS upstream error", r.data)  # JSON error, not audio

    @mock.patch.object(wgpt.requests, "post", side_effect=wgpt.requests.RequestException("down"))
    def test_unreachable_upstream_maps_to_502(self, post):
        wgpt.ELEVENLABS_API_KEY = "k"
        r = self.client.post("/api/tts", json={"text": "hello"})
        self.assertEqual(r.status_code, 502)
        self.assertIn(b"unreachable", r.data)

    def test_health_reports_tts_flag(self):
        wgpt.ELEVENLABS_API_KEY = "k"
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["tts"])
        wgpt.ELEVENLABS_API_KEY = None
        r = self.client.get("/api/health")
        self.assertFalse(r.get_json()["tts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
