// ============================================================
// VOICE OUTPUT — user-controlled spoken answers (Web Speech API)
// ============================================================
// Speaks WeatherGPT's answers aloud ONLY when the user presses
// the Speak button. Zero dependencies; loaded BEFORE script.js
// and exposed as window.VoiceOutput.
//
// Behaviour contract (see e2e_voice_output_test.py):
//   - NOTHING is spoken automatically: answers (voice-origin or
//     typed) are only remembered; the user presses Speak.
//   - One stateful button drives every phase:
//       Speak answer -> Stop (while speaking) -> Replay answer.
//   - Natural voice selection per language (EN/HI/TE): neural /
//     Google / premium voices are preferred over compact or
//     espeak-style robotic ones; falls back gracefully to any
//     language match, then the engine default.
//   - Softer conversational delivery: slightly slower rate, neutral
//     pitch, reduced volume, and a natural pause between sentences.
//   - Answers are cleaned for speech: markdown, links, weather
//     symbols/emoji and UI glyphs are never spoken awkwardly.
//   - Unsupported browsers: UI controls stay hidden, all calls no-op.
//
// NOTE: some mobile browsers require a user gesture before the first
// utterance — the Speak button IS a direct gesture, which satisfies
// that requirement by design (auto-speak would not).

(function () {
  "use strict";

  // Legacy key from the removed Auto Speak setting — cleaned up on load.
  const LEGACY_AUTOSPEAK_KEY = "weathergpt-autospeak";

  // Max characters per utterance — long single utterances get cut off on
  // several mobile TTS engines, so answers are spoken as sentence chunks.
  const MAX_CHUNK = 180;

  // Softer, conversational delivery (user-tuned defaults).
  const TUNE = {
    rate: 0.95, // slightly slower than the brisk engine default
    pitch: 1.0, // neutral; raised pitch sounds unnatural on most engines
    volume: 0.9, // a touch below max removes the harsh "loudspeaker" edge
    sentencePauseMs: 380, // natural breath between spoken sentences
  };

  // App language selector value -> BCP-47 tag ("auto" follows the default).
  const BCP47 = { auto: "en-US", en: "en-US", hi: "hi-IN", te: "te-IN" };

  // Spoken replacement for "&" per language ("and" reads oddly inside
  // Hindi/Telugu sentences).
  const AND_WORD = { "en-US": "and", "hi-IN": "और", "te-IN": "మరియు" };

  const synth = window.speechSynthesis;
  const SpeechUtterance = window.SpeechSynthesisUtterance;

  // Chrome keeps long utterances alive only while referenced; without this
  // registry playback can silently die mid-sentence.
  const activeUtterances = new Set();

  const state = {
    lang: "auto",
    lastText: "",
    lastLang: "auto",
    spokenOnce: false, // the current answer has been played at least once
    queue: [], // pending chunks: [{ text, lang }]
    speaking: false,
    voices: [],
    gen: 0, // generation token: stale onend/onstart from canceled
    // utterances must not mutate the CURRENT queue (overlap safety)
  };

  try {
    localStorage.removeItem(LEGACY_AUTOSPEAK_KEY); // setting removed
  } catch (err) {
    /* storage unavailable — nothing to clean */
  }

  function supported() {
    return !!synth && typeof SpeechUtterance === "function";
  }

  // Screen-reader announcements (visually hidden aria-live region).
  // Text is localized at call time via the CHROME_I18N bundle (loaded
  // before this script) — English fallback keeps tests and edge cases safe.
  function announce(message) {
    const region = document.getElementById("voice-speech-status");
    if (region) region.textContent = localizeVoiceText(message);
  }

  function localizeVoiceText(message) {
    const dicts = window.CHROME_I18N;
    const lang = (window.WeatherState && window.WeatherState.voiceLang) ||
      (document.documentElement.lang || "en").slice(0, 2);
    const dict = (dicts && dicts[lang]) || (dicts && dicts.en);
    if (dict) {
      const map = {
        "Speaking the answer.": "speakAnswer",
        "Speech output failed. The written answer is shown above.": "speakFailed",
      };
      const key = map[message];
      if (key && dict[key]) return dict[key];
      if (key && dicts && dicts.en && dicts.en[key]) return dicts.en[key];
    }
    return message;
  }

  // Let script.js (and tests) react to state changes without coupling.
  function emitState() {
    document.dispatchEvent(
      new CustomEvent("voice-output-state", {
        detail: { speaking: state.speaking, supported: supported() },
      })
    );
  }

  function setLanguage(lang) {
    if (lang && BCP47[lang]) state.lang = lang;
  }

  function loadVoices() {
    if (!synth) return;
    try {
      state.voices = synth.getVoices() || [];
    } catch (err) {
      state.voices = [];
    }
    voiceCache = {}; // voice list changed -> re-resolve preferences
  }
  let voiceCache = {};
  loadVoices();
  if (synth && typeof synth.addEventListener === "function") {
    synth.addEventListener("voiceschanged", loadVoices);
  } else if (synth) {
    synth.onvoiceschanged = loadVoices;
  }

  // Does this voice speak the requested language? Accepts "en-US" and
  // legacy underscore forms ("en_US").
  function voiceLangMatches(voice, tag) {
    const vl = (voice.lang || "").replace("_", "-").toLowerCase();
    return vl === tag.toLowerCase() || vl.startsWith(tag.split("-")[0].toLowerCase() + "-") || vl === tag.split("-")[0].toLowerCase();
  }

  // Quality score: neural / branded engines sound human; compact and
  // espeak-style voices are robotic. Higher is better.
  function scoreVoice(voice) {
    const name = (voice.name || "").toLowerCase();
    let score = 0;
    if (/\bnatural\b|neural/.test(name)) score += 60; // Edge/Windows natural + neural packs
    if (/google/.test(name)) score += 45; // Chrome's Google voices
    if (/premium|enhanced|elite|siri/.test(name)) score += 30;
    if (/microsoft/.test(name)) score += 12;
    if (/\bcompact\b|espeak|pico|robotic|festival/.test(name)) score -= 50; // known robotic
    if (voice.localService === false) score += 8; // cloud voices usually sound better
    if (voice.default) score += 2;
    return score;
  }

  // Best available voice for a BCP-47 tag: exact regional match first,
  // then any dialect of the language, then null (engine default — the
  // utterance keeps its lang tag either way, so tag-aware engines still
  // pronounce correctly). Never throws when voices are unavailable.
  function pickVoice(tag) {
    if (voiceCache[tag] !== undefined) return voiceCache[tag];
    let best = null;
    let bestScore = -Infinity;
    for (const v of state.voices) {
      if (!voiceLangMatches(v, tag)) continue;
      let s = scoreVoice(v);
      if ((v.lang || "").replace("_", "-").toLowerCase() === tag.toLowerCase()) s += 20; // exact regional match
      if (s > bestScore) {
        bestScore = s;
        best = v;
      }
    }
    voiceCache[tag] = best; // may be null — that IS the graceful fallback
    return best;
  }

  // Split into sentence chunks (Hindi danda / Telugu danda included);
  // overlong sentences are hard-split at word boundaries. Chunk pieces
  // keep their trailing boundary space so the concatenated chunks
  // reconstruct the answer verbatim (engines pause between chunks).
  function chunkText(text) {
    const sentences = text.split(/(?<=[.!?।॥…])\s+/).filter(Boolean);
    const pieces = [];
    let current = "";
    for (const sentence of sentences) {
      if ((current + " " + sentence).trim().length <= MAX_CHUNK) {
        current = (current + " " + sentence).trim();
        continue;
      }
      if (current) pieces.push(current);
      let rest = sentence;
      while (rest.length > MAX_CHUNK) {
        let cut = rest.lastIndexOf(" ", MAX_CHUNK);
        if (cut < MAX_CHUNK * 0.5) cut = MAX_CHUNK;
        pieces.push(rest.slice(0, cut).trim());
        rest = rest.slice(cut).trim();
      }
      if (rest) current = rest;
    }
    if (current) pieces.push(current);
    return pieces.map((p, i) => (i < pieces.length - 1 ? p + " " : p));
  }

  // ------------------------------------------------------------
  // Speech text cleaning — symbols/UI text must never be spoken.
  // Returns prose that reads naturally aloud in any language.
  // ------------------------------------------------------------
  function cleanForSpeech(text, langTag) {
    let t = String(text || "");
    const andWord = AND_WORD[langTag] || "and";

    t = t.replace(/```[\s\S]*?```/g, " "); // fenced code blocks: drop
    t = t.replace(/`([^`]+)`/g, "$1"); // inline code: keep the words
    t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1"); // [words](url) -> words
    t = t.replace(/\bhttps?:\/\/\S+/gi, " "); // bare URLs: drop
    t = t.replace(/(\*\*|__)(.*?)\1/g, "$2"); // **bold** / __bold__
    t = t.replace(/(\*|_)([^\s*][^*_]*?)\1/g, "$2"); // *em* / _em_
    t = t.replace(/(^|\n)\s*#{1,6}\s+/g, "$1"); // markdown headers
    t = t.replace(/^\s*[-*•‣▪◦]\s+/gm, ""); // bullet markers
    t = t.replace(/(^|\n)\s*>\s?/g, "$1"); // blockquotes

    // Emoji, weather glyphs, dingbats, arrows, variation selectors, ZWJ.
    t = t.replace(
      /[\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u{1F000}-\u{1FAFF}\u{1F900}-\u{1F9FF}]/gu,
      " "
    );
    // Ornament bullets/pips live in General Punctuation & Geometric Shapes
    // and would be read aloud as "bullet" by several engines.
    t = t.replace(/[\u00B7\u2022\u2023\u2043\u2219\u25AA\u25AB\u25CF\u25E6]/g, " ");

    // Units and symbols -> speakable words.
    t = t.replace(/°\s*C\b/gi, " degrees Celsius");
    t = t.replace(/°\s*F\b/gi, " degrees Fahrenheit");
    t = t.replace(/°/g, " degrees");
    t = t.replace(/\bkm\/?h\b/gi, " kilometers per hour");
    t = t.replace(/\bkph\b/gi, " kilometers per hour");
    t = t.replace(/\bmph\b/gi, " miles per hour");
    t = t.replace(/\bm\/s\b/gi, " meters per second");
    t = t.replace(/%/g, " percent");
    t = t.replace(/\$\s?([\d.]+)/g, "$1 dollars");

    // HTML entities and bare ampersands.
    t = t.replace(/&[a-z]+;/gi, " ");
    t = t.replace(/&/g, ` ${andWord} `);

    // Collapse whitespace (newlines become the sentence pause's job).
    t = t.replace(/\s*\n+\s*/g, " ").replace(/\s{2,}/g, " ").trim();
    return t;
  }

  // Pending inter-chunk pause timer (cleared by stop()).
  let pauseTimer = null;
  function clearPauseTimer() {
    if (pauseTimer) {
      clearTimeout(pauseTimer);
      pauseTimer = null;
    }
  }

  function speakNext() {
    clearPauseTimer();
    if (!state.queue.length) {
      state.speaking = false;
      emitState();
      return;
    }
    const chunk = state.queue[0];
    const utterance = new SpeechUtterance(chunk.text);
    utterance.lang = chunk.lang;
    utterance.rate = TUNE.rate;
    utterance.pitch = TUNE.pitch;
    utterance.volume = TUNE.volume;
    const voice = pickVoice(chunk.lang);
    if (voice) utterance.voice = voice; // null -> engine default (graceful)
    const gen = state.gen; // this playback generation

    activeUtterances.add(utterance);
    utterance.onstart = () => {
      if (gen !== state.gen) return; // canceled before it began
      state.speaking = true;
      emitState();
    };
    utterance.onend = () => {
      activeUtterances.delete(utterance);
      if (gen !== state.gen) return; // stale end from a canceled utterance
      state.queue.shift();
      if (state.queue.length) {
        // Natural breath between sentences instead of machine-gun chunks.
        clearPauseTimer();
        pauseTimer = setTimeout(() => {
          pauseTimer = null;
          if (gen === state.gen) speakNext();
        }, TUNE.sentencePauseMs);
      } else {
        speakNext(); // drains and clears the speaking flag
      }
    };
    utterance.onerror = (event) => {
      activeUtterances.delete(utterance);
      if (gen !== state.gen) return; // the cancel that caused this was intentional
      clearPauseTimer();
      state.queue = [];
      state.speaking = false;
      const reason = (event && event.error) || "unknown";
      // User-initiated cancels are not failures — stay quiet for those.
      if (reason !== "interrupted" && reason !== "canceled") {
        announce("Speech output failed. The written answer is shown above.");
      }
      emitState();
    };
    try {
      synth.speak(utterance);
    } catch (err) {
      // A misbehaving engine must never break the chat flow — the written
      // answer is always on screen.
      activeUtterances.delete(utterance);
      if (gen === state.gen) {
        clearPauseTimer();
        state.queue = [];
        state.speaking = false;
        announce("Speech output failed. The written answer is shown above.");
        emitState();
      }
    }
  }

  // Remember an answer for the Speak button WITHOUT speaking it.
  // Speaking happens only through the user's explicit button press.
  function remember(text) {
    const clean = (text || "").replace(/\s+/g, " ").trim();
    if (clean && clean !== state.lastText) {
      state.lastText = clean;
      state.lastLang = state.lang;
      state.spokenOnce = false; // a fresh answer starts unspoken
    }
  }

  function speak(text, langOverride) {
    if (!supported()) return false;
    const lang = langOverride || state.lang;
    const langTag = BCP47[lang] || "en-US";
    const clean = cleanForSpeech(text, langTag);
    if (!clean) return false;

    stop(); // never overlap: any new utterance flushes the current one

    state.lastText = clean;
    state.lastLang = lang;
    state.spokenOnce = true; // user initiated playback of this answer
    state.queue = chunkText(clean).map((t) => ({ text: t, lang: langTag }));
    // Speaking begins the moment playback is INITIATED, not at the engine's
    // async onstart (~100ms later on real engines). The button must show
    // "Stop speaking" immediately, or a fast second click would restart
    // playback instead of stopping it.
    state.speaking = true;

    announce("Speaking the answer.");
    speakNext();
    return true;
  }

  function stop() {
    state.gen += 1; // invalidate any in-flight utterance callbacks
    clearPauseTimer();
    state.queue = [];
    state.speaking = false;
    if (synth && (synth.speaking || synth.pending)) {
      try {
        synth.cancel();
      } catch (err) {
        /* some engines throw on cancel of an empty queue */
      }
    }
    emitState();
  }

  // Replay the last answer (Speak button in its replay phase).
  function speakLast() {
    if (!state.lastText) return false;
    return speak(state.lastText, state.lastLang);
  }
  const replay = speakLast; // backwards-compatible alias

  // Stop speech when the page is hidden/navigated away — no ghost audio.
  window.addEventListener("pagehide", stop);

  // UI phase for the stateful Speak button: idle (nothing to say),
  // ready (fresh answer), replay (answer played before), speaking.
  function phase() {
    if (!supported()) return "unsupported";
    if (state.speaking) return "speaking";
    if (state.lastText) return state.spokenOnce ? "replay" : "ready";
    return "idle";
  }

  window.VoiceOutput = {
    supported,
    speak,
    speakLast,
    stop,
    replay, // alias of speakLast
    remember,
    setLanguage,
    cleanForSpeech, // exported for tests
    get phase() {
      return phase();
    },
    get speaking() {
      return state.speaking;
    },
    get lastSpoken() {
      return state.lastText;
    },
    get language() {
      return state.lang;
    },
    _state: state, // test seam
  };
})();
