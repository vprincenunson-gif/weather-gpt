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
//   - Sweetest female voice per language (EN en-IN / HI hi-IN /
//     TE te-IN): female, natural/Google/premium and soft-branded
//     voices are preferred; male and compact/espeak-style robotic
//     voices are penalised. Falls back gracefully to any language
//     match, then the engine default — never harsh by intent.
//   - Hindi/Telugu are STRICT: if the browser offers no acceptable
//     female voice for them (only male/robotic ones), nothing harsh is
//     spoken — the missing voice is clearly reported instead.
//   - Backend ElevenLabs TTS (Eleven Multilingual v2, natural sweet
//     female voice) is preferred when the server reports it configured:
//     the API key NEVER reaches the frontend — the browser posts text +
//     language to /api/tts and receives audio bytes. Any failure (no
//     key, network, upstream error) falls back gracefully to the
//     built-in browser voices above.
//   - Switching the app language switches the spoken voice/language
//     immediately (current speech is stopped; next Speak uses it).
//   - Softer conversational delivery: slightly slower rate, neutral
//     pitch, reduced volume, and a natural pause between sentences.
//   - One transient engine error never kills the answer: the chunk is
//     retried once, then skipped; Stop/Replay phases stay intact.
//   - Answers are cleaned for speech: markdown, links, weather
//     symbols/emoji and UI glyphs are never spoken awkwardly.
//   - Unsupported browsers: UI controls stay hidden, all calls no-op.
//
// NOTE: some mobile browsers require a user gesture before the first
// utterance — the Speak button IS a direct gesture, which satisfies
// that requirement by design (auto-speak would not).

(function () {
  "use strict";

  // Build tag — bumped whenever this module's behaviour changes. The live
  // E2E harness gates on it so a service-worker-served stale copy can never
  // silently invalidate a verification run.
  const BUILD = "voice-output-9";

  // Legacy key from the removed Auto Speak setting — cleaned up on load.
  const LEGACY_AUTOSPEAK_KEY = "weathergpt-autospeak";

  // Max characters per utterance — long single utterances get cut off on
  // several mobile TTS engines, so answers are spoken as sentence chunks.
  const MAX_CHUNK = 180;

  // Softer, conversational delivery (user-tuned defaults). Non-English
  // engines (hi-IN/te-IN) tend to read noticeably faster, so they get a
  // slightly slower, more comfortable rate.
  const TUNE = {
    rate: { "en-IN": 0.95, "hi-IN": 0.92, "te-IN": 0.92 },
    rateDefault: 0.95, // slightly slower than the brisk engine default
    pitch: 1.0, // neutral; raised pitch sounds unnatural on most engines
    volume: 0.9, // a touch below max removes the harsh "loudspeaker" edge
    sentencePauseMs: 380, // natural breath between spoken sentences
    errorRetryMs: 250, // brief backoff before retrying a hiccuped chunk
  };

  // App language selector value -> BCP-47 tag. English targets en-IN:
  // Indian-accented English matches the app's audience and pairs with the
  // same sweet female voice family used for Hindi (e.g. Windows' Heera,
  // Google's Indian English voice). "auto" is resolved from the answer's
  // Unicode script at speak time (see detectScriptLang) — never from a
  // hardcoded default.
  const BCP47 = { auto: "en-IN", en: "en-IN", hi: "hi-IN", te: "te-IN" };

  // Resolve the "auto" selector to the actual language of the text about
  // to be spoken: Devanagari blocks are Hindi, Telugu blocks are Telugu,
  // everything else (Latin, digits, punctuation) is English. Speaking a
  // Hindi/Telugu answer under the en-IN tag/voice is exactly what made
  // Auto mode pronounce them with an English accent. Deterministic — an
  // answer is written in exactly one of the three scripts.
  function detectScriptLang(text) {
    const s = String(text || "");
    if (/[\u0900-\u097F]/.test(s)) return "hi"; // Devanagari
    if (/[\u0C00-\u0C7F]/.test(s)) return "te"; // Telugu
    return "en";
  }

  // Languages that must NEVER fall back to a male or robotic voice: if the
  // browser only offers harsh options for them, the missing voice is
  // clearly reported instead of reading the answer badly (see pickVoice /
  // reportMissingVoice). English keeps the graceful engine fallback.
  const STRICT_FEMALE_LANGS = { "hi-IN": true, "te-IN": true };

  // Spoken replacement for "&" per language ("and" reads oddly inside
  // Hindi/Telugu sentences).
  const AND_WORD = { "en-IN": "and", "hi-IN": "और", "te-IN": "మరియు" };

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
    spokeAny: false, // at least one chunk of this playback truly sounded
    mode: "browser", // current playback engine: "browser" | "elevenlabs"
    tts: { checked: false, available: false }, // /api/health probe result
    ttsCooldownUntil: 0, // after a TTS failure, prefer browser for 60 s
    ttsAbort: null, // AbortController for the in-flight /api/tts request
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
        "No natural female voice is installed for this language. The written answer is shown above.": "speakNoVoice",
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
    if (!lang || !BCP47[lang] || state.lang === lang) return;
    state.lang = lang;
    // A pending/replayed answer now belongs to the NEW language: the next
    // Speak must use the newly selected voice/locale — never the language
    // the answer was originally asked in.
    state.lastLang = lang;
    // Silence stale speech in the old voice immediately; the user can
    // press Speak to hear the answer in the newly selected language.
    stop();
  }

  function loadVoices() {
    if (!synth) return;
    try {
      state.voices = synth.getVoices() || [];
    } catch (err) {
      state.voices = [];
    }
    voiceCache = {}; // voice list changed -> re-resolve preferences
    for (const k in reportedMissingVoices) delete reportedMissingVoices[k];
  }
  let voiceCache = {};
  const reportedMissingVoices = {}; // langTag -> true; reset whenever the voice list changes
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

  // Gender detection from engine voice names (there is no portable API):
  // common female and male voice names across Windows / Google / Apple,
  // including the Hindi/Telugu/Indian-English voice families.
  const FEMALE_RE = /female|heera|kalpana|swara|neerja|aditi|isha|priya|geeta|veena|sangeeta|lekha|shruti|kajal|shweta|vaishali|bhuvaneshwari|sita|usha|swecha|aria|jenny|emma|ava|allison|susan|zira|samantha|karen|moira|tessa|fiona|serena|rebecca|sonia|libby|natasha|salli|joanna|raveena|heami/i;
  const MALE_RE = /male|\bdavid\b|\bmark\b|daniel|george|james|ryan|guy|william|\balex\b|fred|\btom\b|rishi|hemant|madhur|prabhat|vijay|arjun|rahul|suresh|ravi|mohan|anil|kishore|balamurugan|alexander|arthur|oliver|conrad/i;

  // Sweet-voice scoring: the sweetest, softest FEMALE voice wins.
  //   - Female names strongly outrank male ones (never harsh by intent).
  //   - Neural / Google / premium engines sound human; compact and
  //     espeak-style voices are robotic and are penalised hard.
  //   - Sweet/soft-branded voices get a gentle extra nudge.
  function scoreVoice(voice) {
    const name = (voice.name || "").toLowerCase();
    let score = 0;
    const female = FEMALE_RE.test(name);
    const male = MALE_RE.test(name);
    if (female && !male) score += 55; // sweet female delivery is the goal
    else if (male && !female) score -= 40; // avoid harsh/male unless nothing else exists
    if (/\bnatural\b|neural/.test(name)) score += 60; // Edge/Windows natural + neural packs
    if (/google/.test(name)) score += 45; // Chrome's Google voices
    if (/premium|enhanced|elite|siri/.test(name)) score += 30;
    if (/sweet|soft|gentle|warm/.test(name)) score += 18;
    if (/microsoft/.test(name)) score += 12;
    if (/\bcompact\b|espeak|pico|robotic|festival/.test(name)) score -= 50; // known robotic
    if (voice.localService === false) score += 8; // cloud voices usually sound better
    if (voice.default) score += 2;
    return score;
  }

  // Sentinel cached when a language HAS voices but none acceptable
  // (male/robotic only) under the strict-female rule — never spoken.
  const BLOCKED = { blocked: true };

  // Best available voice for a BCP-47 tag: exact regional match first,
  // then any dialect of the language. Sweet female voices outrank
  // male/robotic ones. For STRICT languages (hi/te) male and robotic
  // candidates are skipped entirely: if that leaves nothing, the cache
  // holds the BLOCKED sentinel and the caller reports the missing voice
  // instead of speaking harshly. Non-strict languages (en) fall back to
  // any language match, then null (engine default — the utterance keeps
  // its lang tag either way, so tag-aware engines still pronounce
  // correctly). Never throws when voices are unavailable.
  function pickVoice(tag) {
    if (voiceCache[tag] !== undefined) return voiceCache[tag];
    const strict = STRICT_FEMALE_LANGS[tag];
    let best = null;
    let bestScore = -Infinity;
    let sawAny = false;
    let sawEligible = false;
    for (const v of state.voices) {
      if (!voiceLangMatches(v, tag)) continue;
      sawAny = true;
      const name = (v.name || "").toLowerCase();
      const male = MALE_RE.test(name);
      const robotic = /\bcompact\b|espeak|pico|robotic|festival/.test(name);
      if (strict && (male || robotic)) continue; // unacceptable: would sound harsh
      sawEligible = true;
      let s = scoreVoice(v);
      if ((v.lang || "").replace("_", "-").toLowerCase() === tag.toLowerCase()) s += 20; // exact regional match
      if (s > bestScore) {
        bestScore = s;
        best = v;
      }
    }
    // Voices existed but every one was male/robotic under the strict rule.
    voiceCache[tag] = sawAny && !sawEligible ? BLOCKED : best;
    return voiceCache[tag];
  }

  // ----------------------------------------------------------
  // Missing-voice report: when a strict language has no acceptable
  // female voice, say so clearly instead of using a male/robotic one.
  // Announced once per language per voice-list version via the aria-live
  // region and mirrored on the visible hint line.
  // ----------------------------------------------------------
  function reportMissingVoice(langTag) {
    if (reportedMissingVoices[langTag]) return;
    reportedMissingVoices[langTag] = true;
    const message = "No natural female voice is installed for this language. The written answer is shown above.";
    announce(message);
    const hint = document.getElementById("voice-output-hint");
    if (hint) hint.textContent = localizeVoiceText(message);
  }

  // Test/diagnostic seam: what would this language use right now?
  //   state: "ok" (female/natural voice found) | "blocked" (only
  //   male/robotic voices exist — strict report path) | "engine-default"
  //   (no matching voice; the engine's language routing decides).
  function voiceStatus(lang) {
    const langKey = lang || state.lang || "auto";
    const tag = BCP47[langKey] || "en-IN";
    if (!supported()) return { supported: false, tag: tag, state: "unsupported", voice: null };
    const picked = pickVoice(tag);
    if (picked && picked.blocked) return { supported: true, tag: tag, state: "blocked", voice: null };
    return {
      supported: true,
      tag: tag,
      state: picked ? "ok" : "engine-default",
      voice: picked ? picked.name : null,
    };
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

    // Units and symbols -> speakable words. English is the default;
    // Telugu gets Telugu words so English unit normalization never
    // interrupts Telugu sentences (en/hi behavior is unchanged — hi
    // intentionally keeps the English unit words it always had).
    const teUnits = langTag === "te-IN";
    t = t.replace(/°\s*C\b/gi, teUnits ? " డిగ్రీల సెల్సియస్" : " degrees Celsius");
    t = t.replace(/°\s*F\b/gi, teUnits ? " డిగ్రీల ఫారెన్‌హీట్" : " degrees Fahrenheit");
    t = t.replace(/°/g, teUnits ? " డిగ్రీలు" : " degrees");
    t = t.replace(/\bkm\/?h\b/gi, teUnits ? " కిలోమీటర్లు గంటకు" : " kilometers per hour");
    t = t.replace(/\bkph\b/gi, teUnits ? " కిలోమీటర్లు గంటకు" : " kilometers per hour");
    t = t.replace(/\bmph\b/gi, teUnits ? " మైళ్లు గంటకు" : " miles per hour");
    t = t.replace(/\bm\/s\b/gi, teUnits ? " మీటర్లు సెకనుకు" : " meters per second");
    t = t.replace(/%/g, teUnits ? " శాతం" : " percent");
    t = t.replace(/\$\s?([\d.]+)/g, teUnits ? "$1 డాలర్లు" : "$1 dollars");

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

  // Watchdog timer for the current chunk (cleared by stop() and by every
  // onend/onerror): some engines (notably Windows SAPI) occasionally never
  // fire onend/onerror for an utterance — without a watchdog the queue
  // would hang mid-answer forever and every later sentence is lost.
  let watchdogTimer = null;
  function clearWatchdog() {
    if (watchdogTimer) {
      clearTimeout(watchdogTimer);
      watchdogTimer = null;
    }
  }

  // ------------------------------------------------------------
  // ElevenLabs TTS (server-proxied). The API key lives ONLY in the
  // backend: this module posts text + language to /api/tts and plays the
  // returned audio. The server's /api/health probe tells us whether TTS
  // is configured; any failure falls back to the browser engine.
  // ------------------------------------------------------------
  const TTS_RETRY_MS = 60000; // after a failure, prefer browser for a minute

  function ttsConfigured() {
    return state.tts.checked && state.tts.available && Date.now() >= state.ttsCooldownUntil;
  }

  // One-shot probe so the FIRST Speak press knows whether the backend
  // offers ElevenLabs. Never blocks longer than the fetch itself.
  // force=true re-probes (test seam — the server config can change).
  function probeTts(force) {
    if (state.tts.checked && !force) return;
    state.tts.checked = true;
    try {
      fetch("/api/health", { method: "GET", cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          state.tts.available = !!(data && data.tts);
          // Server healthy again (or freshly configured): clear any
          // post-failure cooldown so the sweet backend voice is used.
          if (state.tts.available) state.ttsCooldownUntil = 0;
        })
        .catch(() => {
          state.tts.available = false;
        });
    } catch (err) {
      state.tts.available = false;
    }
  }

  // Fetch backend-synthesized audio for the WHOLE answer (ElevenLabs
  // handles pacing, pauses and the sweet female voice; language is
  // auto-detected by Eleven Multilingual v2 from the text itself).
  // Resolves with an object URL, or null on any failure (caller falls
  // back to the browser engine).
  function fetchTtsAudio(text, lang) {
    return new Promise((resolve) => {
      const controller = new AbortController();
      state.ttsAbort = controller;
      const timer = setTimeout(() => controller.abort(), 20000);
      fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, lang: lang }),
        signal: controller.signal,
      })
        .then((res) => {
          clearTimeout(timer);
          if (!res.ok) throw new Error("tts-http-" + res.status);
          return res.blob();
        })
        .then((blob) => {
          if (!blob || blob.type === "application/json" || blob.size === 0) {
            throw new Error("tts-empty");
          }
          resolve(URL.createObjectURL(blob));
        })
        .catch(() => {
          clearTimeout(timer);
          state.ttsCooldownUntil = Date.now() + TTS_RETRY_MS; // stop hammering a failing service
          resolve(null); // graceful fallback to browser voices
        });
    });
  }

  // Play a fetched audio blob through an <audio> element. Phases mirror
  // the browser engine: speaking on play, Stop via pause, phase 'replay'
  // via onended. The element is kept referenced (Chrome GC rule).
  function playAudioBlob(url, gen, lang) {
    const audio = new Audio(url);
    state.audio = audio;
    audio.onplay = () => {
      if (gen !== state.gen) return;
      state.spokeAny = true;
      state.speaking = true;
      emitState();
    };
    audio.onended = () => {
      state.audio = null;
      try { URL.revokeObjectURL(url); } catch (err) { /* already gone */ }
      if (gen !== state.gen) return;
      state.speaking = false;
      emitState();
    };
    audio.onerror = () => {
      state.audio = null;
      try { URL.revokeObjectURL(url); } catch (err) { /* already gone */ }
      if (gen !== state.gen) return;
      // Audio element failed (decode/support): fall back to the browser
      // engine for this answer instead of failing silently.
      speakWithBrowser(state.lastText, lang);
    };
    const p = audio.play();
    if (p && typeof p.catch === "function") {
      p.catch(() => {
        state.audio = null;
        if (gen !== state.gen) return;
        speakWithBrowser(state.lastText, lang);
      });
    }
  }

  function speakWithBrowser(text, lang) {
    const langTag = BCP47[lang] || "en-IN";
    const clean = cleanForSpeech(text, langTag);
    if (!clean) {
      state.speaking = false;
      emitState();
      return false;
    }
    state.mode = "browser";
    state.queue = chunkText(clean).map((t) => ({ text: t, lang: langTag }));
    speakNext();
    return true;
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
    utterance.rate = TUNE.rate[chunk.lang] || TUNE.rateDefault;
    utterance.pitch = TUNE.pitch;
    utterance.volume = TUNE.volume;
    const picked = pickVoice(chunk.lang);
    if (picked && picked.blocked) {
      // Strict language lost its acceptable voice mid-answer (the voice
      // list changed underneath us): never switch to a harsh voice —
      // report the missing voice and halt gracefully.
      reportMissingVoice(chunk.lang);
      state.queue = [];
      state.speaking = false;
      emitState();
      return;
    }
    const voice = picked;
    if (voice) {
      utterance.voice = voice;
      // Keep utterance.lang in agreement with the chosen voice's own
      // locale: engines (notably Windows SAPI) can stall or mispronounce
      // when the tag and the voice's region disagree (e.g. an en-IN tag
      // spoken by an en-US voice). null -> engine default (graceful).
      utterance.lang = (voice.lang || chunk.lang).replace("_", "-");
    }
    const gen = state.gen; // this playback generation

    activeUtterances.add(utterance);
    utterance.onstart = () => {
      if (gen !== state.gen) return; // canceled before it began
      state.spokeAny = true; // at least one chunk truly sounded
      state.speaking = true;
      emitState();
    };
    utterance.onend = () => {
      activeUtterances.delete(utterance);
      // Stale/duplicate events (real engines — notably Windows SAPI — fire
      // occasional late or duplicate onend) must return BEFORE touching the
      // watchdog: a duplicate from a finished chunk would otherwise disarm
      // the CURRENT chunk's hang protection and the queue freezes forever.
      if (utterance.__finished) return;
      utterance.__finished = true;
      clearWatchdog();
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
      if (utterance.__finished) return; // stale duplicate — keep current watchdog armed
      utterance.__finished = true;
      clearWatchdog();
      const reason = (event && event.error) || "unknown";
      // User-initiated cancels are not failures — stay quiet for those.
      if (reason === "interrupted" || reason === "canceled") return;
      // A transient engine hiccup on one chunk must never drop the rest of
      // the answer: retry this chunk once, then skip to the next sentence.
      // Real engines occasionally error a mid-answer utterance; without
      // this the user silently loses every sentence after it.
      clearPauseTimer();
      clearWatchdog();
      chunk.__retries = (chunk.__retries || 0) + 1;
      if (chunk.__retries <= 1) {
        state.queue.unshift(chunk); // same chunk gets one more chance
        pauseTimer = setTimeout(() => {
          pauseTimer = null;
          if (gen === state.gen) speakNext();
        }, TUNE.errorRetryMs);
        return;
      }
      state.queue.shift(); // skip the failed chunk, keep the answer going
      if (state.queue.length) {
        pauseTimer = setTimeout(() => {
          pauseTimer = null;
          if (gen === state.gen) speakNext();
        }, TUNE.sentencePauseMs);
        return;
      }
      // Nothing recoverable remains: surface the failure honestly.
      state.speaking = false;
      if (!state.spokeAny) {
        announce("Speech output failed. The written answer is shown above.");
      }
      emitState();
    };
    // Generous per-chunk time budget (~3× a comfortable reading pace —
    // real SAPI voices can read slower than nominal while still healthy).
    // Fires only when the engine goes silent WITHOUT onend/onerror; the
    // stuck utterance is cancelled and the answer continues gracefully.
    clearWatchdog();
    const rate = TUNE.rate[chunk.lang] || TUNE.rateDefault;
    const budgetMs = Math.min(90000, Math.max(12000, (chunk.text.length * 95 * 3) / rate));
    watchdogTimer = setTimeout(() => {
      watchdogTimer = null;
      if (gen !== state.gen || !state.speaking) return;
      try {
        synth.cancel(); // silence the stuck engine; its canceled onerror is ignored
      } catch (err) {
        /* engine may already be dead — advance regardless */
      }
      state.queue.shift(); // treat this chunk as finished
      if (state.queue.length) {
        clearPauseTimer();
        pauseTimer = setTimeout(() => {
          pauseTimer = null;
          if (gen === state.gen) speakNext();
        }, TUNE.sentencePauseMs);
      } else {
        state.speaking = false;
        emitState();
      }
    }, budgetMs);
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
      state.spokenOnce = false; // a fresh answer starts unspoken
    }
    // Always re-sync the language: even an unchanged answer re-armed after
    // a language switch must replay in the CURRENTLY selected language.
    if (clean) state.lastLang = state.lang;
  }

  function speak(text, langOverride) {
    if (!supported()) return false;
    // "auto" is resolved from the text itself (script detection) so the
    // utterance gets the voice/locale of the language actually being
    // spoken; explicit en/hi/te selections pass through untouched.
    const selected = langOverride || state.lang;
    const lang = selected === "auto" ? detectScriptLang(text) : selected;
    const langTag = BCP47[lang] || "en-IN";
    const clean = cleanForSpeech(text, langTag);
    if (!clean) return false;

    stop(); // never overlap: any new utterance flushes the current one
    const playbackGen = state.gen; // this playback's generation token

    state.lastText = clean;
    state.lastLang = lang;
    state.spokenOnce = true; // user initiated playback of this answer
    state.spokeAny = false; // nothing has actually sounded yet this generation
    // The button must show "Stop speaking" immediately, not at the engine's
    // async onstart — a fast second click must STOP, never restart.
    state.speaking = true;

    announce("Speaking the answer.");

    // Strict languages must never use a male/robotic BROWSER voice — but
    // ElevenLabs (when configured) IS an acceptable sweet female voice for
    // them, so TTS is tried first and the blocked-voice report only fires
    // on the browser fallback path.
    if (ttsConfigured()) {
      state.mode = "elevenlabs";
      fetchTtsAudio(clean, lang).then((url) => {
        if (state.gen !== playbackGen) return; // stopped/overridden meanwhile
        if (url) {
          playAudioBlob(url, state.gen, lang);
        } else if (STRICT_FEMALE_LANGS[langTag] && pickVoice(langTag)?.blocked) {
          // No server voice and no acceptable browser voice: report.
          reportMissingVoice(langTag);
          state.speaking = false;
          emitState();
        } else {
          speakWithBrowser(clean, lang); // graceful fallback
        }
      });
      return true;
    }

    // Browser-engine path: strict languages with only harsh voices are
    // reported, never spoken badly.
    if (STRICT_FEMALE_LANGS[langTag]) {
      const picked = pickVoice(langTag);
      if (picked && picked.blocked) {
        reportMissingVoice(langTag);
        state.speaking = false;
        emitState();
        return true;
      }
    }
    speakWithBrowser(clean, lang);
    return true;
  }

  function stop() {
    state.gen += 1; // invalidate any in-flight utterance callbacks
    clearPauseTimer();
    clearWatchdog();
    state.queue = [];
    state.speaking = false;
    // Abort any in-flight backend TTS request (Stop must feel immediate).
    if (state.ttsAbort) {
      try { state.ttsAbort.abort(); } catch (err) { /* already aborted */ }
      state.ttsAbort = null;
    }
    // Stop/pause the ElevenLabs <audio> element, if one is playing.
    if (state.audio) {
      const el = state.audio;
      state.audio = null;
      try { el.pause(); } catch (err) { /* already stopped */ }
    }
    if (synth && (synth.speaking || synth.pending)) {
      try {
        synth.cancel();
      } catch (err) {
        /* some engines throw on cancel of an empty queue */
      }
    }
    emitState();
  }

  // Replay the last answer (Speak button in its replay phase). Always
  // speaks with the CURRENTLY selected language so a language switch is
  // honored immediately — replaying a Hindi answer after switching to
  // Telugu uses the Telugu voice/locale, never the old one.
  function speakLast() {
    if (!state.lastText) return false;
    return speak(state.lastText, state.lang);
  }
  const replay = speakLast; // backwards-compatible alias

  // Stop speech when the page is hidden/navigated away — no ghost audio.
  window.addEventListener("pagehide", stop);

  // Learn once per page load whether the backend offers ElevenLabs TTS.
  probeTts();

  // UI phase for the stateful Speak button: idle (nothing to say),
  // ready (fresh answer), replay (answer played before), speaking.
  function phase() {
    if (!supported()) return "unsupported";
    if (state.speaking) return "speaking";
    if (state.lastText) return state.spokenOnce ? "replay" : "ready";
    return "idle";
  }

  window.VoiceOutput = {
    version: BUILD,
    supported,
    voiceStatus,
    speak,
    speakLast,
    stop,
    replay, // alias of speakLast
    remember,
    setLanguage,
    cleanForSpeech, // exported for tests
    // Backend TTS availability ("checked" flips once the probe resolves).
    get tts() {
      return { checked: state.tts.checked, available: state.tts.available };
    },
    get mode() {
      return state.mode; // "browser" | "elevenlabs"
    },
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
    _reprobeTts: () => probeTts(true), // test seam: server config changed
  };
})();
