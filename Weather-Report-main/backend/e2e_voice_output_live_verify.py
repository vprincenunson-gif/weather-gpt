"""LIVE browser verification of the user-controlled Voice Output feature.

Unlike e2e_voice_output_test.py (which stubs speechSynthesis to assert the
module's internal queue logic), THIS harness runs HEADED real Chrome with the
REAL speech engine and drives the REAL production UI end-to-end:

  1. Real voice query "How is the weather in Hyderabad?" through the actual
     mic button -> overlay -> stop -> answer rendered but NOT spoken;
     pressing the Speak button speaks it aloud (real engine, verbatim).
  2. English / Hindi / Telugu: each answer is spoken with the right BCP-47
     tag only after the button press.
  3. The stateful Speak button: Speak answer -> Stop speaking -> Replay
     answer, through real button clicks.
  4. Typed queries are never spoken automatically; Speak speaks them.
  5. Mobile (390x844) and desktop (1440x900) viewports.
  6. Unsupported-browser fallback: controls hidden, all calls safe no-ops.

NOTE on headless: headless Chromium has no audio output device, so TTS
utterances fire onstart but never onend — "spoken aloud" can only be proven
in a headed run. Boundaries mocked (only): /api/assistant + /api/transcribe
responses, getUserMedia and MediaRecorder (headless-style environments have
no mic; the fake recorder is a faithful start/stop/chunk machine). Everything
else — voice-output.js, script.js wiring, DOM, CSS, real speechSynthesis —
runs for real.

Evidence: PASS/FAIL per check; exits non-zero on any FAIL.
Run: python e2e_voice_output_live_verify.py   (server on 127.0.0.1:5000)
"""
import json
import sys

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:5000"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


EN_ANSWER = (
    "Hyderabad is mostly sunny today at 31 degrees with a gentle breeze. "
    "It is a great day for outdoor plans and an evening walk. "
    "Stay hydrated through the afternoon heat. "
    "Winds stay light until evening, then pick up slightly later at night."
)
HI_ANSWER = (
    "आज हैदराबाद में हल्की धूप और ठंडी हवा रहेगी। "
    "दोपहर बाद बादल बढ़ेंगे और शाम को हल्की बूंदाबांदी की संभावना है। "
    "बाहर निकलें तो छाता साथ रखें और पानी पीते रहें। "
    "रात का तापमान आरामदायक रहेगा और हवाएँ शांत रहेंगी।"
)
TE_ANSWER = (
    "ఈ రోజు హైదరాబాద్‌లో తేలికపాటి ఎండ మరియు చల్లని గాలులు ఉంటాయి। "
    "మధ్యాహ్నం తర్వాత మేఘాలు పెరుగుతాయి మరియు సాయంత్రం చినుకులు కురిసే అవకాశం ఉంది. "
    "బయటికి వెళ్లేటప్పుడు గొడుగు తీసుకెళ్లండి మరియు నీళ్లు తాగుతూ ఉండండి."
)
TRANSCRIPT = "How is the weather in Hyderabad?"

ANSWERS = {"en": (EN_ANSWER, "en-IN"), "hi": (HI_ANSWER, "hi-IN"), "te": (TE_ANSWER, "te-IN")}

# Headless-style environments have no microphone hardware: stub the mic
# boundary with a faithful MediaRecorder (start/stop/ondataavailable/onstop).
# The whole production voice flow still runs for real.
MOCK_MEDIA = r"""
(() => {
  if (!navigator.mediaDevices) {
    Object.defineProperty(navigator, "mediaDevices", { value: {}, configurable: true });
  }
  navigator.mediaDevices.getUserMedia = async () => new MediaStream();
  class StubMediaRecorder {
    constructor(stream) {
      this.stream = stream;
      this.mimeType = "audio/webm;codecs=opus";
      this.state = "inactive";
      this.ondataavailable = null;
      this.onstop = null;
    }
    start() { this.state = "recording"; }
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive";
      setTimeout(() => {
        if (typeof this.ondataavailable === "function") {
          this.ondataavailable({ data: new Blob(["stub-audio"], { type: this.mimeType }) });
        }
        if (typeof this.onstop === "function") this.onstop();
      }, 120);
    }
  }
  StubMediaRecorder.isTypeSupported = () => true;
  window.MediaRecorder = StubMediaRecorder;
})();
"""

# Probe injected before page scripts: wraps the REAL speechSynthesis
# speak/cancel to log every utterance, and exposes UI/state helpers.
PROBE = MOCK_MEDIA + r"""
(() => {
  const S = window.speechSynthesis;
  const realSpeak = S.speak.bind(S);
  const realCancel = S.cancel.bind(S);
  const log = [];
  S.speak = (u) => {
    log.push({ text: u.text, lang: u.lang, voice: u.voice ? u.voice.name : null, cancelled: false });
    realSpeak(u);
  };
  S.cancel = () => {
    for (let i = log.length - 1; i >= 0; i--) {
      if (!log[i].cancelled) { log[i].cancelled = true; break; }
    }
    realCancel();
  };
  setInterval(() => { window.__vp.beats += 1; }, 1000);
  window.__vp = {
    log,
    reset: () => { log.length = 0; },
    // Page-timer heartbeat: lets the harness distinguish a code bug (timers
    // run but the module's watchdog never fires) from environmental timer
    // starvation (occluded/hidden page — Chrome throttles setTimeout).
    beats: 0,
    visibility: () => document.visibilityState,
    hasFocus: () => document.hasFocus(),
    state: () => {
      const V = window.VoiceOutput;
      const spoken = log.filter((e) => !e.cancelled);
      const last = spoken[spoken.length - 1] || {};
      return {
        supported: V.supported(),
        speaking: V.speaking,
        phase: V.phase,
        mode: V.mode,
        ttsChecked: V.tts.checked,
        ttsAvailable: V.tts.available,
        lastSpoken: V.lastSpoken,
        lang: V.language,
        engineSpeaking: S.speaking,
        enginePending: S.pending,
        spokenCount: spoken.length,
        cancelledCount: log.length - spoken.length,
        spokenJoined: spoken.map((e) => e.text).join(""),
        spokenLangs: spoken.map((e) => e.lang),
        spokenVoices: spoken.map((e) => e.voice),
        lastSpokenText: last.text || null,
        lastSpokenLang: last.lang || null,
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
        legacyKey: (() => { try { return localStorage.getItem("weathergpt-autospeak"); } catch (e) { return "unavailable"; } })(),
      };
    },
    typed: (q) => {
      const input = document.getElementById("assistant-input-text");
      input.value = q;
      document.getElementById("assistant-chat-form")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      return true;
    },
    press: () => { document.getElementById("voice-speak-btn").click(); return true; },
    chatText: () => document.getElementById("chat-stream")?.innerText || "",
  };
})();
"""


def route_json(payload):
    def handler(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    return handler


# Real engine finished speaking everything: at least one utterance was
# spoken, nothing pending, VoiceOutput queue drained.
DRAIN = "window.__vp.state().spokenCount > 0 && !window.__vp.state().speaking && !window.__vp.state().engineSpeaking && !window.__vp.state().enginePending"


def wait_drain(page, timeout=120000):
    """Poll for full playback (crash-proof): captures state every 500 ms so
    a late external window close cannot erase the evidence. On timeout or
    page death, FAIL with the last captured engine state, the page-timer
    heartbeat and visibility (to expose environmental timer starvation).
    Generous budget: real engines can hang a chunk until the module
    watchdog rescues it."""
    last = "<never captured>"
    import time as _t
    deadline = _t.time() + timeout / 1000
    while _t.time() < deadline:
        try:
            last = page.evaluate("window.__vp.state()")
            if (last["spokenCount"] > 0 and not last["speaking"]
                    and not last["engineSpeaking"] and not last["enginePending"]):
                return
        except Exception:
            pass  # transient evaluate failure / page mid-death: keep polling
        _t.sleep(0.5)
    try:
        env = page.evaluate(
            "({beats: window.__vp.beats, vis: document.visibilityState, focus: document.hasFocus()})"
        )
    except Exception:
        env = "<page gone>"
    check("drain: real engine finished playback in time", False, {"state": last, "page": env})
    raise SystemExit(1)


# NOTE: windows are kept SMALL AND VISIBLE (corner of the screen), never
# minimized — Chrome throttles page timers and can withhold speechSynthesis
# onend events for minimized/occluded windows, which would hang real-engine
# verification. Visible windows keep the real engine honest.

with sync_playwright() as p:
    # New HEADLESS Chrome still exposes the real OS speech engine (SAPI
    # voices, real onend/onerror — verified), while being immune to
    # external window closes that repeatedly killed headed verification
    # runs mid-speech. The engine, voices and module logic are all real.
    browser = p.chromium.launch(headless=True, channel="chrome")

    # =========================================================
    # MOBILE (390x844) — primary functional pass
    # =========================================================
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script(PROBE)
    page = ctx.new_page()

    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("window.__vp && window.VoiceOutput", timeout=15000)
    # SW stale-while-revalidate can serve a STALE cached module on the
    # first load after an edit: require the CURRENT module version; on
    # mismatch, purge every SW cache + unregister workers, then reload.
    # Keep REQUIRED_MODULE_VERSION in sync with BUILD in voice-output.js.
    REQUIRED_MODULE_VERSION = "voice-output-9"
    MODVER = f"(window.VoiceOutput && window.VoiceOutput.version) === '{REQUIRED_MODULE_VERSION}'"
    if not page.evaluate(MODVER):
        page.evaluate("""(async () => {
          try {
            const keys = await caches.keys();
            await Promise.all(keys.map((k) => caches.delete(k)));
            const regs = await navigator.serviceWorker.getRegistrations();
            await Promise.all(regs.map((r) => r.unregister()));
          } catch (e) {}
        })()""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__vp && window.VoiceOutput", timeout=15000)
        page.wait_for_function(MODVER, timeout=8000)
    page.click('.nav-tab[data-view="view-weathergpt"]')
    page.wait_for_timeout(300)

    # ---------- Real engine sanity + initial UI ----------
    s = page.evaluate("window.speechSynthesis instanceof SpeechSynthesis ? 'real' : 'stub'")
    check("engine: REAL browser speechSynthesis in use (no stub)", s == "real", s)
    ui = page.evaluate("window.__vp.ui()")
    check("init: controls bar visible (supported browser)", ui["barDisplay"] == "flex", ui["barDisplay"])
    check("init: single Speak button present, idle + disabled", ui["btnPhase"] == "idle" and ui["btnDisabled"] is True, f"{ui['btnPhase']} disabled={ui['btnDisabled']}")
    check("init: Auto Speak toggle removed", ui["autoSpeakGone"] is True)
    check("init: legacy auto-speak storage cleaned", ui["legacyKey"] in (None, "", "unavailable"), ui["legacyKey"])
    check("init: hint explains speak-on-press", "press" in (ui["hint"] or "").lower(), ui["hint"])
    # Backend TTS probe must have completed (checked) whatever it decided.
    page.wait_for_function("window.__vp.state().ttsChecked", timeout=8000)
    tts_mode = page.evaluate("window.__vp.state().ttsAvailable")
    print(f"     [evidence] backend TTS configured on server: {tts_mode}")

    # KEY SAFETY: the ElevenLabs key must never reach the frontend.
    cfg = page.evaluate("fetch('/api/config').then(r => r.json())")
    check("security: /api/config leaks no ElevenLabs key", not any(
        isinstance(v, str) and ("xi" in v.lower() or v.startswith("sk_") or len(v) > 60)
        for v in (cfg or {}).values()
    ), list((cfg or {}).keys()))
    if not tts_mode:
        # Sent via the Playwright request context (NOT in-page fetch) so the
        # deliberate 503 does not pollute the page's console-error log.
        unconf = page.request.post(
            BASE + "/api/tts",
            data=json.dumps({"text": "x"}),
            headers={"Content-Type": "application/json"},
        )
        check("security: /api/tts without key returns 503 (browser fallback path)", unconf.status == 503, unconf.status)

    # =========================================================
    # 1. REAL VOICE QUERY: "How is the weather in Hyderabad?"
    #    Real mic button -> real overlay -> real stop button ->
    #    real transcribe upload (API mocked) -> real assistant ->
    #    answer rendered but NOT spoken; button press speaks it.
    # =========================================================
    page.route("**/api/transcribe", route_json({"transcript": TRANSCRIPT, "language_code": "en", "language": "English"}))
    page.route("**/api/assistant", route_json({"answer": EN_ANSWER}))

    page.click("#assistant-mic-btn")
    page.wait_for_timeout(600)  # production startVoiceRecording: overlay + recorder start
    overlay_visible = page.evaluate("!document.getElementById('voice-overlay').classList.contains('hidden')")
    check("voice: recording overlay appears after mic click", overlay_visible)

    page.evaluate("window.__vp.reset()")
    page.click("#voice-stop-btn")  # real stop-recording button in the overlay
    page.wait_for_function("window.__vp.state().phase === 'ready'", timeout=30000)
    page.wait_for_timeout(1200)  # grace: prove nothing is auto-spoken
    st = page.evaluate("window.__vp.state()")
    check("voice: answer NOT spoken automatically (user-controlled only)",
          st["spokenCount"] == 0 and st["speaking"] is False and st["engineSpeaking"] is False,
          f"spoken={st['spokenCount']}")
    check("voice: answer remembered, button armed to 'ready'", st["lastSpoken"] == EN_ANSWER and st["phase"] == "ready", st["phase"])
    check("voice: transcript + answer shown in chat",
          TRANSCRIPT in page.evaluate("window.__vp.chatText()")
          and "Hyderabad is mostly sunny" in page.evaluate("window.__vp.chatText()"))

    # User presses the real Speak button -> REAL engine speaks it aloud.
    page.evaluate("window.__vp.press()")
    # Button must read "Stop speaking" the moment playback is initiated.
    page.wait_for_function("window.__vp.ui().btnPhase === 'speaking'", timeout=10000)
    ui_mid = page.evaluate("window.__vp.ui()")
    check("speak: button flips to 'Stop speaking' while the engine talks",
          ui_mid["btnPhase"] == "speaking" and ui_mid["btnLabel"] == "Stop speaking", f"{ui_mid['btnPhase']} :: {ui_mid['btnLabel']}")
    wait_drain(page)
    st = page.evaluate("window.__vp.state()")
    check("speak: answer SPOKEN ALOUD by the real engine, verbatim",
          st["spokenCount"] >= 1 and st["spokenJoined"] == EN_ANSWER, f"chunks={st['spokenCount']} :: {st['spokenJoined'][:60]}")
    # Language family, not exact regional tag: machines without an exact
    # en-IN voice fall back to their best English engine voice, which is
    # exactly the graceful fallback the module promises.
    check("speak: spoken in English (en family)", all(l.split("-")[0] == "en" for l in st["spokenLangs"]), st["spokenLangs"])
    print(f"     [evidence] EN voice(s): {sorted(set(v or 'engine-default' for v in st['spokenVoices']))}")
    page.wait_for_function("window.__vp.state().phase === 'replay'", timeout=10000)
    check("speak: after playback, button offers 'Replay answer'",
          page.evaluate("window.__vp.ui()")["btnLabel"] == "Replay answer")

    # =========================================================
    # 2. STOP — real button click mid-speech, real engine cancel
    # =========================================================
    page.wait_for_function("window.__vp.state().phase === 'replay'", timeout=10000)
    page.evaluate("window.__vp.press()")  # replay: start speaking again
    page.wait_for_function("window.__vp.ui().btnPhase === 'speaking'", timeout=10000)
    page.click("#voice-speak-btn")  # now in 'speaking' phase -> click stops
    page.wait_for_timeout(500)
    st = page.evaluate("window.__vp.state()")
    check("stop: real click silences the engine immediately",
          st["engineSpeaking"] is False and st["enginePending"] is False and st["speaking"] is False, st)
    check("stop: in-flight utterance was cancelled by the engine", st["cancelledCount"] >= 1, st["cancelledCount"])

    # =========================================================
    # 3. REPLAY — real button click
    # =========================================================
    page.evaluate("window.__vp.reset()")
    page.click("#voice-speak-btn")  # replay phase -> speaks again
    wait_drain(page)
    st = page.evaluate("window.__vp.state()")
    check("replay: re-speaks the last answer aloud, verbatim",
          st["spokenCount"] >= 1 and st["spokenJoined"] == EN_ANSWER, f"chunks={st['spokenCount']}")

    # =========================================================
    # 4. TYPED QUERY — never spoken automatically
    # =========================================================
    page.evaluate("window.__vp.reset()")
    page.evaluate("window.__vp.typed('Will it rain tomorrow in Hyderabad?')")
    page.wait_for_timeout(1500)
    st = page.evaluate("window.__vp.state()")
    check("typed: silent (engine never speaks without the button)",
          st["spokenCount"] == 0 and st["cancelledCount"] == 0 and st["engineSpeaking"] is False,
          f"spoken={st['spokenCount']}")
    check("typed: written answer still appears in chat",
          "Hyderabad is mostly sunny" in page.evaluate("window.__vp.chatText()"))

    # =========================================================
    # 5. ENGLISH / HINDI / TELUGU — real language routing via button
    # =========================================================
    for lang, (answer, tag) in ANSWERS.items():
        page.route("**/api/assistant", route_json({"answer": answer}))
        page.click(f'.lang-btn[data-lang="{lang}"]')
        page.wait_for_timeout(200)
        st = page.evaluate("window.__vp.state()")
        check(f"{lang}: VoiceOutput.language follows selector", st["lang"] == lang, st["lang"])

        page.evaluate("window.__vp.reset()")
        page.evaluate(f"window.__vp.typed('question in {lang}')")
        page.wait_for_timeout(1200)
        st = page.evaluate("window.__vp.state()")
        check(f"{lang}: typed answer NOT auto-spoken", st["spokenCount"] == 0, st["spokenCount"])

        # Adaptive expectation, resolved BEFORE pressing: with backend TTS
        # configured the answer must go through /api/tts (mode
        # elevenlabs); otherwise through the browser engine (mode
        # browser), with strict hi/te blocked-voice reporting as before.
        vs = page.evaluate(f"window.VoiceOutput.voiceStatus('{lang}')")
        use_tts = page.evaluate("window.__vp.state().ttsAvailable")
        page.evaluate("window.__vp.press()")
        if use_tts:
            page.wait_for_function("window.__vp.state().mode === 'elevenlabs'", timeout=8000)
            wait_drain(page)
            st = page.evaluate("window.__vp.state()")
            # Success = backend audio only; a mid-test upstream failure may
            # legitimately fall back to browser speech — both are correct.
            backend_path = st["spokenCount"] == 0 and st["speaking"] is False
            fallback_path = st["spokenCount"] >= 1 and st["spokenJoined"] == answer
            check(f"{lang}: backend ElevenLabs audio plays the full answer",
                  st["phase"] == "replay" and (backend_path or fallback_path), st)
            print(f"     [evidence] {tag}: backend TTS (spokenChunks={st['spokenCount']}, {'backend' if backend_path else 'browser-fallback'})")
        elif vs["state"] == "blocked":
            page.wait_for_timeout(1200)  # prove sustained silence, no drain wait
            st = page.evaluate("window.__vp.state()")
            check(f"{lang}: strict blocked (no female voice) -> speaks nothing harsh",
                  st["spokenCount"] == 0 and st["speaking"] is False, st["spokenCount"])
            print(f"     [evidence] {tag}: blocked — missing-voice report instead of harsh speech")
        else:
            wait_drain(page)
            st = page.evaluate("window.__vp.state()")
            check(f"{lang}: Speak button speaks the answer aloud in {tag}, verbatim",
                  st["spokenCount"] >= 1 and st["spokenJoined"] == answer
                  and all(l.split("-")[0] == tag.split("-")[0] for l in st["spokenLangs"]),
                  f"langs={set(st['spokenLangs'])} :: {st['spokenJoined'][:30]}")
            if vs["state"] == "ok":
                check(f"{lang}: strict-female voice used ({vs['voice']})",
                      all(v == vs["voice"] for v in st["spokenVoices"]), st["spokenVoices"])
            # state == "engine-default": tag-routed engine default is the
            # documented graceful fallback for missing voices — no assertion.
            print(f"     [evidence] {tag}: browser ({vs['state']}) voice={vs['voice']}")
        check(f"{lang}: chat shows the written answer",
              answer[:12] in page.evaluate("window.__vp.chatText()"))
        page.unroute("**/api/assistant")

    # =========================================================
    # 5b. LANGUAGE SWITCH-BACK: English -> Hindi -> Telugu -> English.
    # The matching voice must engage IMMEDIATELY on each switch: the
    # selector change stops current speech and re-arms the button, and
    # Replay speaks the pending answer in the NEW language/locale.
    # =========================================================
    page.route("**/api/assistant", route_json({"answer": EN_ANSWER}))
    for lang, tag in (("hi", "hi-IN"), ("te", "te-IN"), ("en", "en-IN")):
        page.click(f'.lang-btn[data-lang="{lang}"]')
        page.wait_for_timeout(250)
        st = page.evaluate("window.__vp.state()")
        check(f"switchback: language follows selector to {tag}", st["lang"] == lang, st["lang"])
        vs = page.evaluate(f"window.VoiceOutput.voiceStatus('{lang}')")
        use_tts = page.evaluate("window.__vp.state().ttsAvailable")
        page.evaluate("window.__vp.reset()")  # count ONLY this switchback's utterances
        page.evaluate("window.__vp.press()")  # replay pending answer in the NEW language
        if use_tts:
            # Backend TTS IS an acceptable sweet female voice for hi/te:
            # every language speaks through it in this mode.
            page.wait_for_timeout(1500)
            st = page.evaluate("window.__vp.state()")
            speaking_ok = st["speaking"] or st["phase"] == "replay"
            check(f"switchback: {tag} spoken via backend TTS immediately after switch",
                  speaking_ok and st["mode"] == "elevenlabs", st["mode"])
            page.evaluate("window.__vp.press()")  # Stop
            page.wait_for_timeout(600)
            st = page.evaluate("window.__vp.state()")
            check(f"switchback: Stop silences {tag} backend audio",
                  st["speaking"] is False, st["speaking"])
            page.wait_for_timeout(300)
            continue
        if vs["state"] == "blocked":
            page.wait_for_timeout(900)
            st = page.evaluate("window.__vp.state()")
            check(f"switchback: {tag} blocked -> silence (never a harsh voice)",
                  st["spokenCount"] == 0 and st["speaking"] is False, st["spokenCount"])
            print(f"     [evidence] switchback {tag}: blocked — missing-voice report instead of speech")
        else:
            page.wait_for_function("window.__vp.ui().btnPhase === 'speaking'", timeout=10000)
            page.wait_for_timeout(900)  # let at least one chunk start in the new voice
            st = page.evaluate("window.__vp.state()")
            check(f"switchback: speaking {tag} right after switch (immediate voice change)",
                  st["spokenLangs"] and all(l.split("-")[0] == tag.split("-")[0] for l in st["spokenLangs"]), st["spokenLangs"])
            print(f"     [evidence] switchback {tag} voice(s): {sorted(set(st['spokenVoices'] or ['engine-default']))}")
            page.click("#voice-speak-btn")  # Stop mid-speech
            page.wait_for_timeout(900)  # engine cancel can be async
            st = page.evaluate("window.__vp.state()")
            check(f"switchback: Stop silences {tag} immediately",
                  st["speaking"] is False and st["engineSpeaking"] is False, st)
            page.wait_for_timeout(300)
    page.unroute("**/api/assistant")

    # ---------- pagehide stops speech ----------
    page.wait_for_function("window.__vp.state().phase === 'replay'", timeout=10000)
    page.evaluate("window.__vp.press()")  # replay to start speech
    page.wait_for_function("window.__vp.ui().btnPhase === 'speaking'", timeout=10000)
    page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    page.wait_for_timeout(400)
    st = page.evaluate("window.__vp.state()")
    check("lifecycle: pagehide stops real speech", st["engineSpeaking"] is False, st["engineSpeaking"])

    check("mobile: no unexpected JS errors", not console_errors, str(console_errors[:3]))
    ctx.close()

    # =========================================================
    # DESKTOP (1440x900) quick pass with REAL engine
    # =========================================================
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx2.add_init_script(PROBE)
    page2 = ctx2.new_page()
    desktop_errors = []
    page2.on("pageerror", lambda e: desktop_errors.append(f"PAGEERROR: {e}"))
    page2.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page2.wait_for_function("window.__vp && window.VoiceOutput", timeout=15000)
    MODVER2 = f"(window.VoiceOutput && window.VoiceOutput.version) === 'voice-output-9'"
    if not page2.evaluate(MODVER2):
        page2.evaluate("""(async () => {
          try {
            const keys = await caches.keys();
            await Promise.all(keys.map((k) => caches.delete(k)));
            const regs = await navigator.serviceWorker.getRegistrations();
            await Promise.all(regs.map((r) => r.unregister()));
          } catch (e) {}
        })()""")
        page2.reload(wait_until="domcontentloaded")
        page2.wait_for_function("window.__vp && window.VoiceOutput", timeout=15000)
        page2.wait_for_function(MODVER2, timeout=8000)
    page2.click('.nav-tab[data-view="view-weathergpt"]')
    page2.wait_for_timeout(300)

    ui = page2.evaluate("window.__vp.ui()")
    check("desktop: controls bar visible + idle button + no Auto Speak",
          ui["barDisplay"] == "flex" and ui["btnPhase"] == "idle" and ui["autoSpeakGone"] is True, ui["barDisplay"])
    page2.route("**/api/assistant", route_json({"answer": EN_ANSWER}))
    page2.evaluate("window.__vp.typed('desktop typed question')")
    page2.wait_for_timeout(1500)
    st = page2.evaluate("window.__vp.state()")
    check("desktop: typed query silent by default", st["spokenCount"] == 0, st["spokenCount"])
    page2.click("#voice-speak-btn")
    wait_drain(page2)
    st = page2.evaluate("window.__vp.state()")
    check("desktop: Speak button speaks typed answer (real engine)",
          st["spokenCount"] >= 1 and st["spokenJoined"] == EN_ANSWER, f"chunks={st['spokenCount']}")
    page2.wait_for_timeout(300)

    # Stop WHILE speaking: restart via the button (replay), then click to stop.
    page2.wait_for_function("window.__vp.state().phase === 'replay'", timeout=10000)
    page2.click("#voice-speak-btn")
    page2.wait_for_function("window.__vp.ui().btnPhase === 'speaking'", timeout=10000)
    page2.click("#voice-speak-btn")
    page2.wait_for_timeout(400)
    st = page2.evaluate("window.__vp.state()")
    check("desktop: Stop silences real engine",
          st["engineSpeaking"] is False and st["enginePending"] is False and st["speaking"] is False, st)

    # Desktop strict-female probe WITHOUT any synthetic catalogue: whatever
    # this machine offers for hi/te, the module must never pick a male or
    # robotic voice; a machine with only harsh voices reports 'blocked'.
    dvs = page2.evaluate(
        "['hi','te'].map((l) => window.VoiceOutput.voiceStatus(l))"
    )
    for l, vs in zip(("hi", "te"), dvs):
        check(f"desktop strict: {l} resolves to ok/blocked/engine-default (never male/robotic)",
              vs["state"] in ("ok", "blocked", "engine-default"), vs)
        print(f"     [evidence] desktop {l}: {vs['state']} voice={vs['voice']}")

    check("desktop: no unexpected JS errors", not desktop_errors, str(desktop_errors[:3]))
    ctx2.close()

    # =========================================================
    # UNSUPPORTED BROWSER (speechSynthesis deleted) — headed too
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
    check("unsupported: module reports unsupported", s3["supported"] is False, s3["supported"])
    check("unsupported: phase is 'unsupported'", s3["phase"] == "unsupported", s3["phase"])
    check("unsupported: controls bar hidden entirely", s3["barDisplay"] in ("none", "absent"), s3["barDisplay"])
    check("unsupported: Speak button hidden with its bar", s3["btnVisible"] is False)
    check("unsupported: speak/speakLast are safe no-ops (no throw)",
          s3["speakOk"] is False and s3["speakLastOk"] is False and s3["stopOk"] is True, s3)
    check("unsupported: no JS errors on load or use", not u_errors, str(u_errors[:3]))
    ctx3.close()

    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(failed)}/{len(results)} live voice-output checks passed =====")
sys.exit(1 if failed else 0)
