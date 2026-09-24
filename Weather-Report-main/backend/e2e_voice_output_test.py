"""E2E checks for user-controlled voice output (Web Speech API).

Requires the Flask server running on 127.0.0.1:5000.

Contract under test (post-rework):
  - NOTHING is spoken automatically. Voice-origin AND typed answers are
    only remembered; the user must press the stateful Speak button.
  - One button drives every phase:
      idle -> "Speak answer" (fresh answer) -> "Stop speaking" (while
      talking) -> "Replay answer" (already played). No Auto Speak toggle.
  - Natural voice selection per language (EN/HI/TE): natural/Google/
    premium voices preferred; graceful fallback to language match, then
    engine default.
  - Softer delivery: rate < 1, volume < 1, and a natural pause between
    sentence chunks.
  - Speech text cleaning: markdown, URLs, emoji/weather glyphs, units
    and UI symbols never reach the engine.
  - Unsupported browsers: UI controls stay hidden, all calls no-op.

Boundaries are mocked (same style as test_app.py / e2e_rain_feature_test.py)
so every path is deterministic:
  - speechSynthesis + SpeechSynthesisUtterance are replaced with a
    controllable stub BEFORE page scripts run (add_init_script), so the
    REAL voice-output.js module logic executes.
  - /api/assistant and /api/transcribe are route-mocked.
  - The real voice-input pipeline is exercised by stubbing only
    getUserMedia/MediaRecorder and driving the production handler.

Evidence: prints PASS/FAIL per check; exits non-zero on any FAIL.
Covers mobile (390x844) and desktop (1440x900) viewports.
"""
import json
import sys

from playwright.sync_api import sync_playwright

# Windows consoles default to cp1252 — Hindi/Telugu evidence text would
# crash printing. Force UTF-8 with replacement.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------
# Stub Web Speech API injected BEFORE any page script runs.
# Records every utterance handed to synth.speak (with speak-time
# timestamps so inter-chunk pauses can be asserted) so the real
# voice-output.js logic can be asserted deterministically.
# ---------------------------------------------------------------
INIT_SCRIPT = r"""
(() => {
  const recorded = [];
  // ---- Backend TTS boundary stubs (fetch + Audio) ----
  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  let ttsAvailable = false; // what a REAL /api/health would report
  window.__vtSetTtsAvailable = (v) => { ttsAvailable = !!v; };
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.includes("/api/health")) {
      return Promise.resolve(new Response(JSON.stringify({ status: "ok", tts: ttsAvailable }),
        { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    if (url.includes("/api/tts")) {
      if (!ttsAvailable) {
        return Promise.resolve(new Response(JSON.stringify({ error: "TTS is not configured" }),
          { status: 503, headers: { "Content-Type": "application/json" } }));
      }
      if (window.__vtTtsFails) {
        return Promise.resolve(new Response(JSON.stringify({ error: "TTS upstream error 502" }),
          { status: 502, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(new Blob(["FAKEMP3"], { type: "audio/mpeg" }),
        { status: 200, headers: { "Content-Type": "audio/mpeg" } }));
    }
    return realFetch ? realFetch(input, init) : Promise.reject(new Error("no fetch"));
  };
  class StubAudio {
    constructor(src) {
      this.src = src;
      this.paused = true;
      this.ended = false;
      this.onplay = null;
      this.onended = null;
      this.onerror = null;
      window.__vtAudio = this;
    }
    play() {
      this.paused = false;
      if (typeof this.onplay === "function") this.onplay();
      return Promise.resolve();
    }
    pause() { this.paused = true; }
  }
  window.Audio = StubAudio;
  // Configurable fake voice catalogue (name drives quality scoring).
  const voices = [
    { name: "Microsoft Heera - English (India)", lang: "en-IN", localService: true, default: true },
    { name: "Google हिन्दी", lang: "hi-IN", localService: false, default: false },
    { name: "Microsoft Swara - Telugu (India)", lang: "te-IN", localService: true, default: false },
  ];
  const voiceListeners = [];
  class StubUtterance {
    constructor(text) {
      this.text = text;
      this.lang = "";
      this.voice = null;
      this.rate = 1;
      this.pitch = 1;
      this.volume = 1;
      this.onstart = null;
      this.onend = null;
      this.onerror = null;
    }
  }
  const synth = {
    speaking: false,
    pending: false,
    getVoices: () => voices,
    addEventListener: (type, cb) => { if (type === "voiceschanged") voiceListeners.push(cb); },
    cancel: () => {
      synth.speaking = false;
      synth.pending = false;
      const u = synth._current;
      synth._current = null;
      if (u) {
        u.__cancelled = true;
        if (typeof u.onerror === "function") u.onerror({ error: "canceled" });
      }
    },
    speak: (u) => {
      synth._current = u;
      recorded.push(u); // keep the utterance object (carries __cancelled)
      u.__at = performance.now();
      synth.speaking = true;
      if (typeof u.onstart === "function") u.onstart();
      // Real engines END asynchronously; mimic a ~150 ms utterance so the
      // module's queue machinery (advance-on-end, pause, drain) runs like
      // it does against a real speech engine.
      setTimeout(() => {
        if (!u.__cancelled && typeof u.onend === "function") u.onend();
      }, 150);
    },
    _recorded: recorded,
    _voices: voices,
  };
  // speechSynthesis is a getter-only accessor on Window.prototype, so a
  // plain assignment would silently fail — define an own property instead.
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true, writable: true });
  window.SpeechSynthesisUtterance = StubUtterance;
  window.__vt = {
    ready: () => true,
    reset: () => { recorded.length = 0; },
    state: () => {
      const V = window.VoiceOutput;
      const st = V._state;
      return {
        speaking: st.speaking,
        lang: st.lang,
        lastSpoken: V.lastSpoken,
        phase: V.phase,
        mode: V.mode,
        queue: st.queue.map((q) => q.text),
        spoken: recorded.map((r) => ({
          text: r.text, lang: r.lang, voice: r.voice ? r.voice.name : null,
          rate: r.rate, pitch: r.pitch, volume: r.volume, at: r.__at,
          cancelled: !!r.__cancelled,
        })),
        cancelled: recorded.filter((r) => r.__cancelled).length,
      };
    },
    ui: () => {
      const bar = document.getElementById("voice-output-controls");
      const btn = document.getElementById("voice-speak-btn");
      return {
        barDisplay: bar ? getComputedStyle(bar).display : "absent",
        btnPresent: !!btn,
        btnPhase: btn?.dataset.phase,
        btnLabel: document.getElementById("voice-speak-label")?.textContent,
        btnIcon: document.getElementById("voice-speak-icon")?.textContent,
        btnDisabled: btn?.disabled,
        btnAria: btn?.getAttribute("aria-label"),
        autoSpeakGone: !document.getElementById("auto-speak-toggle"),
        hint: document.getElementById("voice-output-hint")?.textContent,
        liveRegion: document.getElementById("voice-speech-status")?.textContent,
        mode: window.VoiceOutput ? window.VoiceOutput.mode : null,
        ttsChecked: window.VoiceOutput ? window.VoiceOutput.tts.checked : null,
        ttsAvailable: window.VoiceOutput ? window.VoiceOutput.tts.available : null,
        lang: window.VoiceOutput ? window.VoiceOutput.language : null,
        legacyKey: (() => { try { return localStorage.getItem("weathergpt-autospeak"); } catch (e) { return "unavailable"; } })(),
      };
    },
    // Drive the REAL typed-query path through the UI form.
    typed: (q) => {
      const input = document.getElementById("assistant-input-text");
      input.value = q;
      document.getElementById("assistant-chat-form")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      return true;
    },
    // Press the real Speak button.
    press: () => { document.getElementById("voice-speak-btn").click(); return true; },
    // Fire the PRODUCTION voice flow: seed the recorder state the real
    // handler expects, then call handleVoiceRecordingFinished() against a
    // route-mocked /api/transcribe. `state` is a top-level const in
    // script.js, so it is reachable from global-scope evaluation.
    simulateVoice: () => {
      state.recordedChunks = ["stub-audio-chunk"];
      state.isRecording = false;
      state.mediaRecorder = { mimeType: "audio/webm" };
      handleVoiceRecordingFinished();
      return true;
    },
    // Replace the fake catalogue and fire voiceschanged so the module
    // re-resolves its preferences (used by the strict hi/te section).
    setVoices: (list) => {
      voices.length = 0;
      for (const v of list) voices.push(v);
      voiceListeners.forEach((cb) => { try { cb(); } catch (e) {} });
    },
    // Per-language voice resolution probe ("ok" | "blocked" | "engine-default").
    vstat: (lang) => window.VoiceOutput.voiceStatus(lang),
    // Flip what a REAL /api/health would report (backend TTS configured?).
    setTts: (v) => { window.__vtSetTtsAvailable(v); },
    // Make the mocked /api/tts endpoint fail (tests the browser fallback).
    failTts: (v) => { window.__vtTtsFails = !!v; },
    // Fire the audio element's natural end (replay-phase transition).
    finishAudio: () => {
      const a = window.__vtAudio;
      if (a && typeof a.onended === "function") { a.ended = true; a.onended(); return true; }
      return false;
    },
    // Overlap probe: starting a new speak() must cancel any current one.
    overlap: () => {
      const st = window.VoiceOutput._state;
      window.VoiceOutput.speak("First answer one. First answer two.", "en");
      window.VoiceOutput.speak("Second answer one.", "en");
      return {
        queue: st.queue.map((q) => q.text),
        cancelled: recorded.filter((r) => r.__cancelled).length,
        speaking: st.speaking,
      };
    },
    // Stale-callback race: a canceled utterance's onend must NOT resume
    // or mutate the queue of the generation that canceled it.
    race: () => {
      const st = window.VoiceOutput._state;
      window.VoiceOutput.speak("one two three four five.", "en");
      const stale = synth._current;
      window.VoiceOutput.stop();
      if (stale && stale.onend) stale.onend(); // late end event after cancel
      return {
        queue: st.queue.map((q) => q.text),
        speaking: st.speaking,
        cancelled: recorded.filter((r) => r.__cancelled).length,
      };
    },
  };
})();
"""

HELPER = "(() => window.__vt)()"

ANSWERS = {
    "en": "Mostly sunny today with a gentle breeze across the city. It is a great day for outdoor plans and an evening walk. Stay hydrated through the afternoon heat. Winds stay light until evening, then pick up slightly along the coast later at night.",
    "hi": "आज शहर में हल्की धूप और ठंडी हवा रहेगी। दोपहर बाद बादल बढ़ेंगे और शाम को हल्की बूंदाबांदी की संभावना है। बाहर निकलें तो छाता साथ रखें और पानी पीते रहें। रात का तापमान आरामदायक रहेगा और हवाएँ शांत रहेंगी। सुबह के समय कुहासा हो सकता है, इसलिए देर से निकलें।",
    "te": "ఈ రోజు నగరంలో తేలికపాటి ఎండ మరియు చల్లని గాలులు ఉంటాయి। మధ్యాహ్నం తర్వాత మేఘాలు పెరుగుతాయి మరియు సాయంత్రం చినుకులు కురిసే అవకాశం ఉంది. బయటికి వెళ్లేటప్పుడు గొడుగు తీసుకెళ్లండి మరియు నీళ్లు తాగుతూ ఉండండి. రాత్రి ఉష్ణోగ్రత ఆహ్లాదకరంగా ఉంటుంది మరియు గాలులు ప్రశాంతంగా ఉంటాయి.",
}
BCP47 = {"en": "en-IN", "hi": "hi-IN", "te": "te-IN"}
EXPECTED_VOICE = {
    "en": "Microsoft Heera - English (India)",  # sweet female en-IN voice
    "hi": "Google हिन्दी",  # Google voice wins quality scoring
    "te": "Microsoft Swara - Telugu (India)",  # sweet female te-IN voice
}
VOICE_ANSWER = "अभी हल्की बारिश हो रही है। दो घंटे में तेज़ बारिश की संभावना है। छाता ले जाएँ।"
TRANSCRIPT = "क्या आज बारिश होगी?"


def route_json(payload):
    def handler(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    return handler


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # =========================================================
    # MOBILE (390x844) — primary functional pass
    # =========================================================
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script(INIT_SCRIPT)
    page = ctx.new_page()

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("window.__vt && window.VoiceOutput", timeout=15000)

    # The voice-output controls live in the WeatherGPT view (like the chat
    # they belong to). Open it the way a user would before asserting.
    page.click('.nav-tab[data-view="view-weathergpt"]')
    page.wait_for_timeout(300)

    # ---------- Initial UI state ----------
    s = page.evaluate(f"{HELPER}.ui()")
    check("init: controls bar visible when speechSynthesis present", s["barDisplay"] == "flex", s["barDisplay"])
    check("init: single stateful Speak button present", s["btnPresent"] is True)
    check("init: button starts in idle phase, disabled", s["btnPhase"] == "idle" and s["btnDisabled"] is True, f"{s['btnPhase']} disabled={s['btnDisabled']}")
    check("init: Auto Speak toggle removed from the UI", s["autoSpeakGone"] is True)
    check("init: hint says answers speak only on press", "press" in (s["hint"] or "").lower(), s["hint"])
    check("init: legacy auto-speak localStorage key removed", s["legacyKey"] in (None, "", "unavailable"), s["legacyKey"])

    # ---------- Per-language: remember-only, then button-driven playback ----------
    for lang, answer in ANSWERS.items():
        page.click(f'.lang-btn[data-lang="{lang}"]')
        page.wait_for_timeout(200)
        page.evaluate(f"{HELPER}.reset()")  # utterance log is per-iteration
        lang_state = page.evaluate(f"{HELPER}.state()")
        check(f"{lang}: language sync (VoiceOutput.language)", lang_state["lang"] == lang, lang_state["lang"])

        # Expected Speak-button wording comes from the live i18n bundle —
        # the UI localizes these labels per selected language.
        expect = page.evaluate(
            """(lang) => {
              const d = window.CHROME_I18N[lang];
              return { ready: d.speakAnswer, stop: d.speakStop, replay: d.speakReplay,
                       stopAria: d.speakStop, replayAria: d.speakReplayA11y };
            }""",
            lang,
        )

        page.route("**/api/assistant", route_json({"answer": answer}))

        # Typed query -> NEVER spoken, only remembered (button arms).
        page.evaluate(f"{HELPER}.typed('typed question in {lang}')")
        page.wait_for_timeout(150)
        st = page.evaluate(f"{HELPER}.state()")
        ui = page.evaluate(f"{HELPER}.ui()")
        check(f"{lang}: typed answer is NOT spoken automatically",
              len(st["spoken"]) == 0 and st["speaking"] is False, f"spoken={len(st['spoken'])}")
        check(f"{lang}: typed answer remembered -> button phase 'ready'",
              st["phase"] == "ready" and ui["btnLabel"] == expect["ready"] and ui["btnDisabled"] is False,
              f"phase={st['phase']} label={ui['btnLabel']}")

        # Press the real Speak button -> speaks the cleaned answer.
        page.evaluate(f"{HELPER}.press()")
        page.wait_for_function("window.__vt.state().speaking", timeout=5000)
        ui = page.evaluate(f"{HELPER}.ui()")
        check(f"{lang}: pressing Speak starts playback", ui["btnPhase"] == "speaking" and ui["btnLabel"] == expect["stop"], f"{ui['btnPhase']} :: {ui['btnLabel']}")
        check(f"{lang}: Stop phase exposes stop aria-label", ui["btnAria"] == expect["stopAria"], ui["btnAria"])

        # Press again mid-speech -> stop.
        page.evaluate(f"{HELPER}.press()")
        page.wait_for_timeout(100)
        st = page.evaluate(f"{HELPER}.state()")
        ui = page.evaluate(f"{HELPER}.ui()")
        check(f"{lang}: pressing again stops playback", st["speaking"] is False and len(st["queue"]) == 0, st["queue"])
        check(f"{lang}: after stop, phase is 'replay' (Replay answer)", ui["btnPhase"] == "replay" and ui["btnLabel"] == expect["replay"], f"{ui['btnPhase']} :: {ui['btnLabel']}")
        check(f"{lang}: stop fired canceled error once", st["cancelled"] >= 1, st["cancelled"])

        # Replay re-speaks the FULL answer from memory; drain the stub
        # engine and verify chunked reconstruction end-to-end.
        page.evaluate(f"{HELPER}.reset()")
        page.evaluate(f"{HELPER}.press()")
        page.wait_for_timeout(80)
        st = page.evaluate(f"{HELPER}.state()")
        check(f"{lang}: Replay speaks again after Stop", st["speaking"] is True and len(st["spoken"]) >= 1, len(st["spoken"]))
        page.wait_for_function(
            "!window.__vt.state().speaking && window.__vt.state().queue.length === 0", timeout=30000
        )
        st = page.evaluate(f"{HELPER}.state()")
        check(f"{lang}: long answer split into sentence chunks", len(st["spoken"]) >= 2, f"chunks={len(st['spoken'])}")
        joined = "".join(u["text"] for u in st["spoken"])
        check(f"{lang}: answer spoken verbatim (order kept)", joined == answer, joined[:40])
        check(f"{lang}: utterance lang is {BCP47[lang]}",
              all(u["lang"] == BCP47[lang] for u in st["spoken"]), st["spoken"][:1])
        check(f"{lang}: natural voice selected ({EXPECTED_VOICE[lang]})",
              all(u["voice"] == EXPECTED_VOICE[lang] for u in st["spoken"]), st["spoken"][:1])
        check(f"{lang}: softer delivery applied (rate<1, volume<1, pitch=1)",
              all(u["rate"] < 1 and u["volume"] < 1 and u["pitch"] == 1 for u in st["spoken"]),
              st["spoken"][:1])
        danda = "." if lang == "en" else "।"
        check(f"{lang}: sentence punctuation preserved across chunks", danda in joined, danda)
        gaps = [st["spoken"][i + 1]["at"] - st["spoken"][i]["at"] for i in range(len(st["spoken"]) - 1)]
        check(f"{lang}: natural pause between sentences (>=300ms)", all(g >= 300 for g in gaps), f"gaps={[round(g) for g in gaps]}")
        check(f"{lang}: chat bubble still shows written answer", answer[:20] in page.evaluate(
            "document.getElementById('chat-stream')?.innerText || ''"))

        # New typed answer re-arms the button (fresh answer -> ready, not replay).
        page.route("**/api/assistant", route_json({"answer": "A fresh answer for the button."}))
        page.evaluate(f"{HELPER}.reset()")  # only count utterances from THIS query
        page.evaluate(f"{HELPER}.typed('another question in {lang}')")
        page.wait_for_timeout(150)
        st = page.evaluate(f"{HELPER}.state()")
        check(f"{lang}: NEW answer re-arms button to 'ready' (never auto-spoken)",
              st["phase"] == "ready" and len(st["spoken"]) == 0, f"phase={st['phase']} spoken={len(st['spoken'])}")
        page.unroute("**/api/assistant")

    # ---------- Voice-origin query: remembered, NOT spoken ----------
    page.evaluate(f"{HELPER}.reset()")
    page.click('.lang-btn[data-lang="hi"]')
    page.wait_for_timeout(200)
    page.route("**/api/transcribe", route_json({"transcript": TRANSCRIPT, "language_code": "hi", "language": "Hindi"}))
    page.route("**/api/assistant", route_json({"answer": VOICE_ANSWER}))
    page.evaluate(f"{HELPER}.simulateVoice()")
    page.wait_for_function("window.__vt.state().lastSpoken.length > 0", timeout=10000)
    page.wait_for_timeout(150)
    st = page.evaluate(f"{HELPER}.state()")
    check("voice: spoken query answer is NOT auto-spoken (user-controlled only)",
          len(st["spoken"]) == 0 and st["speaking"] is False, f"spoken={len(st['spoken'])}")
    check("voice: answer remembered for the Speak button", st["lastSpoken"] == VOICE_ANSWER, st["lastSpoken"][:30])
    check("voice: button armed to 'ready' after voice query", st["phase"] == "ready", st["phase"])

    # User presses Speak -> Hindi answer spoken with the Google voice.
    page.evaluate(f"{HELPER}.press()")
    page.wait_for_function("!window.__vt.state().speaking && window.__vt.state().queue.length === 0", timeout=30000)
    st = page.evaluate(f"{HELPER}.state()")
    check("voice: press speaks Hindi answer verbatim", "".join(u["text"] for u in st["spoken"]) == VOICE_ANSWER)
    check("voice: utterance lang hi-IN with Google voice",
          all(u["lang"] == "hi-IN" and u["voice"] == "Google हिन्दी" for u in st["spoken"]), st["spoken"][:1])
    check("voice: transcript + answer rendered in chat",
          TRANSCRIPT in page.evaluate("document.getElementById('chat-stream')?.innerText || ''")
          and "छाता" in page.evaluate("document.getElementById('chat-stream')?.innerText || ''"))
    page.unroute("**/api/transcribe")
    page.unroute("**/api/assistant")

    # ---------- Speech cleaning: symbols/UI text never spoken ----------
    dirty = "**Sunny** 28°C, wind 12 km/h (85%) [details](https://x.y) • ⛅ & 🌧️ tomorrow — $5 & 50%"
    clean = page.evaluate(f"window.VoiceOutput.cleanForSpeech({json.dumps(dirty)}, 'en-IN')")
    check("clean: markdown/emoji/units/URLs cleaned for speech",
          "https" not in clean and "°" not in clean and "km/h" not in clean and "⛅" not in clean
          and "degrees Celsius" in clean and "kilometers per hour" in clean
          and "percent" in clean and "Sunny" in clean and "and" in clean and "dollars" in clean, clean)
    clean_hi = page.evaluate(f"window.VoiceOutput.cleanForSpeech({json.dumps('मौसम & तापमान 30°C')}, 'hi-IN')")
    check("clean: ampersand localized per language (hi -> और)", "और" in clean_hi and "degrees Celsius" in clean_hi, clean_hi)

    # ---------- STRICT hi/te: no male/robotic voice is ever used ----------
    # Simulate a machine with ONLY a male hi voice and a robotic te voice:
    # the module must refuse them, report the missing voice (aria-live +
    # visible hint, localized) and speak NOTHING.
    page.route("**/api/assistant", route_json({"answer": ANSWERS["hi"]}))
    page.evaluate(
        f"""{HELPER}.setVoices([
          {{ name: 'Microsoft Hemant - Hindi (India)', lang: 'hi-IN', localService: true, default: false }},
          {{ name: 'Microsoft Compact Telugu', lang: 'te-IN', localService: true, default: false }}
        ])"""
    )
    page.evaluate(f"{HELPER}.reset()")
    page.click('.lang-btn[data-lang="te"]')
    page.wait_for_timeout(200)
    vs = page.evaluate(f"{HELPER}.vstat('te')")
    check("strict: te with robotic-only catalogue reports 'blocked'", vs["state"] == "blocked" and vs["voice"] is None, vs)
    page.evaluate(f"{HELPER}.typed('strict mode question')")
    page.wait_for_timeout(150)
    page.evaluate(f"{HELPER}.press()")
    page.wait_for_timeout(250)
    st = page.evaluate(f"{HELPER}.state()")
    ui = page.evaluate(f"{HELPER}.ui()")
    check("strict: te speaks NOTHING when only robotic voice exists", len(st["spoken"]) == 0 and st["speaking"] is False, f"spoken={len(st['spoken'])}")
    check("strict: missing-voice report localized to Telugu", "స్త్రీ వాణి" in (ui["liveRegion"] or "") and "స్త్రీ వాణి" in (ui["hint"] or ""), (ui["liveRegion"], ui["hint"]))
    check("strict: button stays armed (replay/ready) so the user can retry", st["phase"] in ("ready", "replay") and ui["btnDisabled"] is False, f"phase={st['phase']} disabled={ui['btnDisabled']}")
    check("strict: chat still shows the written answer", ANSWERS["hi"][:20] in page.evaluate("document.getElementById('chat-stream')?.innerText || ''"))

    # A female te voice appearing later (voiceschanged) un-blocks te.
    page.evaluate(
        f"""{HELPER}.setVoices([
          {{ name: 'Microsoft Hemant - Hindi (India)', lang: 'hi-IN', localService: true, default: false }},
          {{ name: 'Microsoft Swara - Telugu (India)', lang: 'te-IN', localService: true, default: false }}
        ])"""
    )
    page.wait_for_timeout(150)
    vs2 = page.evaluate(f"{HELPER}.vstat('te')")
    check("strict: female te voice arriving later un-blocks te", vs2["state"] == "ok" and vs2["voice"] == "Microsoft Swara - Telugu (India)", vs2)

    # Unknown-gender branded voices (Google हिन्दी) still qualify under the
    # strict rule; robotic ones never do.
    page.evaluate(
        f"""{HELPER}.setVoices([
          {{ name: 'Google हिन्दी', lang: 'hi-IN', localService: false, default: false }}
        ])"""
    )
    page.wait_for_timeout(150)
    vs3 = page.evaluate(f"{HELPER}.vstat('hi')")
    check("strict: unknown-gender branded hi voice accepted (Google हिन्दी)", vs3["state"] == "ok" and vs3["voice"] == "Google हिन्दी", vs3)
    vs4 = page.evaluate(f"{HELPER}.vstat('te')")
    check("strict: te with NO voice at all falls to engine-default (graceful)", vs4["state"] == "engine-default", vs4)

    # Restore the original catalogue for any later sections.
    page.evaluate(
        f"""{HELPER}.setVoices([
          {{ name: 'Microsoft Heera - English (India)', lang: 'en-IN', localService: true, default: true }},
          {{ name: 'Google हिन्दी', lang: 'hi-IN', localService: false, default: false }},
          {{ name: 'Microsoft Swara - Telugu (India)', lang: 'te-IN', localService: true, default: false }}
        ])"""
    )
    page.unroute("**/api/assistant")

    # =========================================================
    # BACKEND ELEVENLABS TTS (mocked /api/tts): key never reaches the
    # frontend; success plays audio; failure falls back to the browser.
    # =========================================================
    # A. TTS configured + success -> whole answer via backend audio,
    #    browser synth untouched.
    page.evaluate("window.VoiceOutput._reprobeTts()")
    page.evaluate(f"{HELPER}.setTts(true)")
    page.evaluate("window.VoiceOutput._reprobeTts()")
    page.wait_for_function("window.VoiceOutput.tts.checked && window.VoiceOutput.tts.available", timeout=5000)
    page.evaluate("window.__vtAudio = null")
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("Backend speaks this answer sweetly. Second sentence here.", "en")')
    page.wait_for_function("window.__vtAudio && !window.__vtAudio.paused", timeout=5000)
    st = page.evaluate(f"{HELPER}.state()")
    check("tts: backend audio plays (mode elevenlabs)", st["mode"] == "elevenlabs" and st["speaking"] is True, st["mode"])
    check("tts: browser synth NOT used for backend audio", len(st["spoken"]) == 0, len(st["spoken"]))
    check("tts: audio src is a blob URL from /api/tts", page.evaluate("window.__vtAudio.src.includes('blob:')") is True)
    page.evaluate(f"{HELPER}.finishAudio()")
    page.wait_for_timeout(100)
    st = page.evaluate(f"{HELPER}.state()")
    ui = page.evaluate(f"{HELPER}.ui()")
    check("tts: audio end -> replay phase (Stop/Replay contract intact)", st["phase"] == "replay" and st["speaking"] is False, st["phase"])

    # Stop DURING backend audio silences it immediately.
    page.evaluate("window.__vtAudio = null")
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("Stopping mid audio works too.", "en")')
    page.wait_for_function("window.__vtAudio && !window.__vtAudio.paused", timeout=5000)
    page.evaluate("window.VoiceOutput.stop()")
    page.wait_for_timeout(100)
    st = page.evaluate(f"{HELPER}.state()")
    audio_paused = page.evaluate("window.__vtAudio ? window.__vtAudio.paused : 'gone'")
    check("tts: Stop pauses backend audio immediately", st["speaking"] is False and audio_paused in (True, "gone"), f"speaking={st['speaking']} paused={audio_paused}")

    # B. TTS failure (upstream 502) -> graceful browser-voice fallback.
    page.evaluate(f"{HELPER}.failTts(true)")
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("Falls back to the browser voice. Second sentence.", "en")')
    page.wait_for_function("window.__vt.state().spoken.length > 0", timeout=8000)
    st = page.evaluate(f"{HELPER}.state()")
    check("tts: upstream failure falls back to browser voices", st["mode"] == "browser" and len(st["spoken"]) >= 1, f"mode={st['mode']} spoken={len(st['spoken'])}")
    check("tts: fallback joined text matches the answer", "".join(u["text"] for u in st["spoken"]) == "Falls back to the browser voice. Second sentence.")

    # Cooldown: after a failure the next speak goes straight to the browser
    # (no second upstream hammering) for a minute.
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("Cooldown uses the browser.", "en")')
    page.wait_for_function("window.__vt.state().spoken.length > 0", timeout=8000)
    st = page.evaluate(f"{HELPER}.state()")
    check("tts: cooldown prefers browser after failure", st["mode"] == "browser" and len(st["spoken"]) >= 1, st["mode"])
    page.evaluate(f"{HELPER}.failTts(false)")

    # C. Health says TTS unconfigured -> pure browser path.
    page.evaluate(f"{HELPER}.setTts(false)")
    page.evaluate("window.VoiceOutput._reprobeTts()")
    page.wait_for_function("window.VoiceOutput.tts.checked && !window.VoiceOutput.tts.available", timeout=5000)
    page.evaluate("window.__vtAudio = null")
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("Browser only now.", "en")')
    page.wait_for_function("window.__vt.state().spoken.length > 0", timeout=8000)
    st = page.evaluate(f"{HELPER}.state()")
    check("tts: unconfigured backend -> browser engine speaks", st["mode"] == "browser" and len(st["spoken"]) >= 1, st["mode"])

    # Strict + TTS-configured: backend IS an acceptable sweet female voice
    # for hi/te, so it speaks; TTS failure then reports (never harsh).
    page.evaluate(f"{HELPER}.setTts(true)")
    page.evaluate("window.VoiceOutput._reprobeTts()")
    page.wait_for_function("window.VoiceOutput.tts.available", timeout=5000)
    page.evaluate(
        f"""{HELPER}.setVoices([
          {{ name: 'Microsoft Hemant - Hindi (India)', lang: 'hi-IN', localService: true, default: false }}
        ])"""
    )
    page.click('.lang-btn[data-lang="hi"]')
    page.wait_for_timeout(200)
    page.evaluate("window.__vtAudio = null")
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("हेलो, यह उत्तर है।", "hi")')
    page.wait_for_function("window.__vtAudio && !window.__vtAudio.paused", timeout=5000)
    st = page.evaluate(f"{HELPER}.state()")
    check("tts: strict hi speaks via backend (no male browser voice)", st["mode"] == "elevenlabs" and len(st["spoken"]) == 0, st["mode"])
    page.evaluate(f"{HELPER}.failTts(true)")
    page.evaluate(f"{HELPER}.reset()")
    page.evaluate('window.VoiceOutput.speak("हेलो, यह उत्तर है।", "hi")')
    page.wait_for_timeout(600)
    st = page.evaluate(f"{HELPER}.state()")
    ui = page.evaluate(f"{HELPER}.ui()")
    check("tts: strict hi + backend failure reports missing voice (never male)",
          len(st["spoken"]) == 0 and st["speaking"] is False and "महिला" in (ui["liveRegion"] or ""),
          f"spoken={len(st['spoken'])} live={ui['liveRegion']}")
    page.evaluate(f"{HELPER}.failTts(false)")
    page.evaluate(f"{HELPER}.setTts(false)")
    page.evaluate("window.VoiceOutput._reprobeTts()")
    page.evaluate(
        f"""{HELPER}.setVoices([
          {{ name: 'Microsoft Heera - English (India)', lang: 'en-IN', localService: true, default: true }},
          {{ name: 'Google हिन्दी', lang: 'hi-IN', localService: false, default: false }},
          {{ name: 'Microsoft Swara - Telugu (India)', lang: 'te-IN', localService: true, default: false }}
        ])"""
    )
    page.wait_for_timeout(150)

    # ---------- Speaking pulse visible when motion allowed ----------
    page.evaluate("document.getElementById('voice-speak-btn').dataset.phase = 'speaking'")
    anim = page.evaluate("getComputedStyle(document.getElementById('voice-speak-btn')).animationName")
    check("a11y: speaking pulse animates when motion allowed", anim == "voiceSpeakingPulse", anim)
    page.evaluate("document.getElementById('voice-speak-btn').dataset.phase = 'idle'")

    # ---------- pagehide safety ----------
    page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    page.wait_for_timeout(100)
    st = page.evaluate(f"{HELPER}.state()")
    check("lifecycle: pagehide stops speech", not st["speaking"] and len(st["queue"]) == 0)

    check("mobile: no unexpected JS errors", not console_errors, str(console_errors[:3]))
    ctx.close()

    # =========================================================
    # DESKTOP (1440x900, prefers-reduced-motion: reduce)
    # =========================================================
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    ctx2.add_init_script(INIT_SCRIPT)
    page2 = ctx2.new_page()
    desktop_errors = []
    page2.on("pageerror", lambda e: desktop_errors.append(f"PAGEERROR: {e}"))
    page2.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page2.wait_for_function("window.__vt && window.VoiceOutput", timeout=15000)

    s2 = page2.evaluate(f"{HELPER}.ui()")
    check("desktop: controls bar visible", s2["barDisplay"] == "flex", s2["barDisplay"])
    check("desktop: Auto Speak toggle removed", s2["autoSpeakGone"] is True)
    page2.evaluate("document.getElementById('voice-speak-btn').dataset.phase = 'speaking'")
    anim2 = page2.evaluate("getComputedStyle(document.getElementById('voice-speak-btn')).animationName")
    check("a11y: prefers-reduced-motion disables speaking pulse", anim2 == "none", anim2)

    race = page2.evaluate(f"{HELPER}.race()")
    check("overlap: stale onend after Stop does not resurrect queue",
          race["queue"] == [] and race["speaking"] is False, race)
    check("overlap: stale utterance was canceled exactly once", race["cancelled"] == 1, race["cancelled"])

    page2.evaluate(f"{HELPER}.reset()")  # overlap probe needs a clean log
    ov = page2.evaluate(f"{HELPER}.overlap()")
    check("overlap: second speak cancels first (no overlapping audio)",
          ov["queue"] == ["Second answer one."] and ov["cancelled"] == 1 and ov["speaking"] is True, ov)

    check("desktop: no unexpected JS errors", not desktop_errors, str(desktop_errors[:3]))
    ctx2.close()

    # =========================================================
    # DESKTOP, UNSUPPORTED BROWSER (no speechSynthesis at all)
    # Headless Chromium actually ships window.speechSynthesis, so it is
    # deleted before scripts run to simulate a genuinely unsupported
    # browser (old Safari, some WebViews).
    # =========================================================
    ctx3 = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx3.add_init_script(
        "Object.defineProperty(window, 'speechSynthesis', { value: undefined, configurable: true, writable: true });"
        "Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: undefined, configurable: true, writable: true });"
    )
    page3 = ctx3.new_page()
    u_errors = []
    page3.on("pageerror", lambda e: u_errors.append(f"PAGEERROR: {e}"))
    page3.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page3.wait_for_timeout(1200)

    s3 = page3.evaluate("""(() => {
        const bar = document.getElementById('voice-output-controls');
        const btn = document.getElementById('voice-speak-btn');
        const V = window.VoiceOutput;
        return {
            barDisplay: bar ? getComputedStyle(bar).display : 'absent',
            supported: V ? V.supported() : null,
            phase: V ? V.phase : null,
            speakOk: V ? V.speak('hello') : null,
            speakLastOk: V ? V.speakLast() : null,
            stopOk: V ? (V.stop(), true) : null,
            btnVisible: !!btn && btn.offsetParent !== null,
        };
    })()""")
    check("unsupported: speechSynthesis absent -> module reports unsupported",
          s3["supported"] is False, s3["supported"])
    check("unsupported: phase is 'unsupported'", s3["phase"] == "unsupported", s3["phase"])
    check("unsupported: controls bar hidden entirely", s3["barDisplay"] in ("none", "absent"), s3["barDisplay"])
    check("unsupported: Speak button hidden entirely with its bar", s3["btnVisible"] is False)
    check("unsupported: speak/speakLast are safe no-ops (no throw)",
          s3["speakOk"] in (False, None) and s3["speakLastOk"] in (False, None), s3)
    check("unsupported: no JS errors on load or use", not u_errors, str(u_errors[:3]))
    ctx3.close()

    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(failed)}/{len(results)} voice-output E2E checks passed =====")
sys.exit(1 if failed else 0)
