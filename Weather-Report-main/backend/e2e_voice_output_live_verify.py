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

ANSWERS = {"en": (EN_ANSWER, "en-US"), "hi": (HI_ANSWER, "hi-IN"), "te": (TE_ANSWER, "te-IN")}

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
    log.push({ text: u.text, lang: u.lang, cancelled: false });
    realSpeak(u);
  };
  S.cancel = () => {
    for (let i = log.length - 1; i >= 0; i--) {
      if (!log[i].cancelled) { log[i].cancelled = true; break; }
    }
    realCancel();
  };
  window.__vp = {
    log,
    reset: () => { log.length = 0; },
    state: () => {
      const V = window.VoiceOutput;
      const spoken = log.filter((e) => !e.cancelled);
      const last = spoken[spoken.length - 1] || {};
      return {
        supported: V.supported(),
        speaking: V.speaking,
        phase: V.phase,
        lastSpoken: V.lastSpoken,
        lang: V.language,
        engineSpeaking: S.speaking,
        enginePending: S.pending,
        spokenCount: spoken.length,
        cancelledCount: log.length - spoken.length,
        spokenJoined: spoken.map((e) => e.text).join(""),
        spokenLangs: spoken.map((e) => e.lang),
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

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, channel="chrome")

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
    page.wait_for_function(DRAIN, timeout=30000)
    st = page.evaluate("window.__vp.state()")
    check("speak: answer SPOKEN ALOUD by the real engine, verbatim",
          st["spokenCount"] >= 1 and st["spokenJoined"] == EN_ANSWER, f"chunks={st['spokenCount']} :: {st['spokenJoined'][:60]}")
    check("speak: spoken with en-US tag", all(l == "en-US" for l in st["spokenLangs"]), st["spokenLangs"])
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
    page.wait_for_function(DRAIN, timeout=30000)
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

        page.evaluate("window.__vp.press()")
        page.wait_for_function(DRAIN, timeout=30000)
        st = page.evaluate("window.__vp.state()")
        check(f"{lang}: Speak button speaks the answer aloud in {tag}, verbatim",
              st["spokenCount"] >= 1 and st["spokenJoined"] == answer and all(l == tag for l in st["spokenLangs"]),
              f"langs={set(st['spokenLangs'])} :: {st['spokenJoined'][:30]}")
        check(f"{lang}: chat shows the written answer",
              answer[:12] in page.evaluate("window.__vp.chatText()"))
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
    page2.wait_for_function(DRAIN, timeout=30000)
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
