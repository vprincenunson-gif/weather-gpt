"""Smart Farm Weather Advisor — deterministic, multilingual rules engine.

Design principles:
- Uses ONLY the real weather payload already fetched by the backend
  (Open-Meteo current/hourly/daily + computed alerts). No invented data:
  every number shown is read from the payload and echoed in
  `weather_snapshot` so the advice is fully auditable.
- Threshold rules are transparent and testable (`build_farm_advice` is a
  pure function over the weather dict).
- Advice is translated at the data level (each rule carries en/hi/te
  strings) so the frontend never needs to translate programmatically.
- No pesticide/fertilizer dosage guidance is generated. Spraying-related
  rules only reference wind/drainage safety windows, never chemicals or
  quantities.
"""

# ---------------------------------------------------------------
# Crops: agronomically standard, stage-aware sensitivity profiles.
# KRW = crop water requirement proxy (mm/day, rough FAO-56 typical
# seasonal mid-range) used ONLY to phrase irrigation need relative to
# forecast rainfall — never as a dosage or absolute schedule.
# ---------------------------------------------------------------

CROPS = {
    "rice": {
        "names": {"en": "Rice (Paddy)", "hi": "धान (चावल)", "te": "వరి"},
        "emoji": "🌾",
        "krw_mm_day": 6.0,
        "heat_stress_c": 35.0,
        "wind_sensitive_stages": {"flowering"},
        "notes": {
            "en": "Standing water crop: keep fields flooded during growing; drain before harvest.",
            "hi": "तालाबी फसल: बढ़ती अवस्था में खेत में पानी बनाए रखें; कटाई से पहले पानी निकालें।",
            "te": "నీటి పంట: పెరుగుదల దశలో పొలంలో నీరు నిలిచి ఉంచండి; కోతకు ముందు నీరు తీసేయండి.",
        },
    },
    "cotton": {
        "names": {"en": "Cotton", "hi": "कपास", "te": "పత్తి"},
        "emoji": "🪴",
        "krw_mm_day": 5.0,
        "heat_stress_c": 38.0,
        "wind_sensitive_stages": {"flowering"},
        "notes": {
            "en": "Avoid waterlogging; pick only dry bolls to keep lint quality.",
            "hi": "जल भराव से बचें; कपास की गुणवत्ता के लिए सूखी बॉल्स ही तुड़ें।",
            "te": "నీరు నిలిచే దానికి దూరంగా ఉండండి; పొడి బొల్లి మాత్రమే కోయండి.",
        },
    },
    "maize": {
        "names": {"en": "Maize", "hi": "मक्का", "te": "మొక్కజొన్న"},
        "emoji": "🌽",
        "krw_mm_day": 5.5,
        "heat_stress_c": 35.0,
        "wind_sensitive_stages": {"flowering", "growing"},
        "notes": {
            "en": "Tasseling/silking is the most moisture-sensitive window; strong wind can lodge tall plants.",
            "hi": "टैसेलिंग/सिल्किंग सबसे नमी-संवेदनशील समय है; तेज हवा लंबे पौधों को गिरा सकती है।",
            "te": "పుష్పించే సమయంలో తేమ లోపం తీవ్ర ప్రభావం చూపుతుంది; బలమైన గాలి మొక్కలను కూల్చేస్తుంది.",
        },
    },
    "groundnut": {
        "names": {"en": "Groundnut", "hi": "मूंगफली", "te": "వేరుశనగ"},
        "emoji": "🥜",
        "krw_mm_day": 4.5,
        "heat_stress_c": 36.0,
        "wind_sensitive_stages": set(),
        "notes": {
            "en": "Harvest when 70–80% of pods mature; dry pods on tarpaulin if rain threatens.",
            "hi": "70–80% फली पकने पर कटाई करें; बारिश की आशंका पर तिरपाल पर सुखाएं।",
            "te": "70–80% గింజలు పండినప్పుడు కోత చేయండి; వర్షం ఉంటే టార్పాలిన్‌పై ఆరబెట్టండి.",
        },
    },
    "wheat": {
        "names": {"en": "Wheat", "hi": "गेहूँ", "te": "గోధుమ"},
        "emoji": "🌾",
        "krw_mm_day": 4.5,
        "heat_stress_c": 32.0,
        "wind_sensitive_stages": {"flowering"},
        "notes": {
            "en": "Terminal heat during grain filling cuts yield; irrigate lightly on hot spells.",
            "hi": "दाना भरने के समय तेज गर्मी उपज घटाती है; गर्मी में हल्की सिंचाई करें।",
            "te": "ధాన్యం నింపుకునే సమయంలో వేడి దిగుబడిని తగ్గిస్తుంది; వేడి రోజుల్లో కొద్దిగా నీరు పెట్టండి.",
        },
    },
    "sugarcane": {
        "names": {"en": "Sugarcane", "hi": "गन्ना", "te": "చెరకు"},
        "emoji": "🎍",
        "krw_mm_day": 7.0,
        "heat_stress_c": 38.0,
        "wind_sensitive_stages": {"growing"},
        "notes": {
            "en": "Long-duration crop: maintain even moisture; lodge-prone in cyclonic wind.",
            "hi": "लंबी अवधि की फसल: समान नमी बनाए रखें; आंधी में गिरने का खतरा।",
            "te": "సుదీర్ఘ కాల పంట: సమాన తేమ అవసరం; తుఫాను గాలుల్లో పడిపోతుంది.",
        },
    },
}

STAGES = {
    "sowing": {
        "names": {"en": "Sowing", "hi": "बुवाई", "te": "విత్తులు వేయడం"},
        "emoji": "🌱",
        "germination_rain_mm": 5.0,
    },
    "growing": {
        "names": {"en": "Growing", "hi": "बढ़ती अवस्था", "te": "పెరుగుదల దశ"},
        "emoji": "🌿",
        "germination_rain_mm": 0.0,
    },
    "flowering": {
        "names": {"en": "Flowering", "hi": "फूल आना", "te": "పూల దశ"},
        "emoji": "🌼",
        "germination_rain_mm": 0.0,
    },
    "harvesting": {
        "names": {"en": "Harvesting", "hi": "कटाई", "te": "కోత"},
        "emoji": "🚜",
        "germination_rain_mm": 0.0,
    },
}

VALID_CROPS = set(CROPS)
VALID_STAGES = set(STAGES)

# Advisory severities used by the UI
SEV_INFO, SEV_CAUTION, SEV_ACTION = "info", "caution", "action"

_W = {
    "irrigation": {"en": "Irrigation", "hi": "सिंचाई", "te": "నీటి పారుదల"},
    "rain": {"en": "Rainfall", "hi": "वर्षा", "te": "వర్షం"},
    "harvest": {"en": "Harvesting", "hi": "कटाई", "te": "కోత"},
    "wind": {"en": "Strong Wind", "hi": "तेज हवा", "te": "బలమైన గాలి"},
    "heat": {"en": "Heat", "hi": "गर्मी", "te": "వేడి"},
    "field": {"en": "Field Operations", "hi": "खेत के काम", "te": "పొల పనులు"},
}

_T = {
    "title": {
        "en": "What should I do today?",
        "hi": "आज मुझे क्या करना चाहिए?",
        "te": "ఈరోజు నేను ఏమి చేయాలి?",
    },
    "advisory": {
        "en": "Farm Advisory",
        "hi": "कृषि सलाह",
        "te": "వ్యవసాయ సలహా",
    },
    "irrigation_plenty": {
        "en": "Rain expected — skip irrigation. {rain} mm forecast over the next 3 days is enough; drain excess water to avoid waterlogging.",
        "hi": "बारिश की संभावना — सिंचाई टालें। अगले 3 दिनों में {rain} मिमी बारिश पर्याप्त है; जल भराव से बचने के लिए अतिरिक्त पानी निकालें।",
        "te": "వర్ష అవకాశం — నీటి పారుదల ఆపండి. ముందు 3 రోజుల్లో {rain} మి.మీ వర్షం సరిపోతుంది; నీరు నిలిచే ముప్పు లేకుండా అదనపు నీటిని తీసేయండి.",
    },
    "irrigation_ok": {
        "en": "Light rain possible ({rain} mm over 3 days). Irrigate only if the topsoil dries; morning hours are best to cut evaporation loss.",
        "hi": "हल्की बारिश संभव (3 दिनों में {rain} मिमी)। ऊपरी मिट्टी सूखने पर ही सिंचाई करें; भापीकरण कम करने के लिए सुबह का समय सबसे अच्छा है।",
        "te": "కొద్దిగా వర్షం సాధ్యం (3 రోజుల్లో {rain} మి.మీ). పై మట్టి ఆరితే మాత్రమే నీరు పెట్టండి; ఆవిరి తగ్గించేందుకు ఉదయం సమయం మెరుగు.",
    },
    "irrigation_needed": {
        "en": "Dry spell — only {rain} mm rain over the next 3 days vs typical crop need of ~{need} mm/day. Plan irrigation; morning application reduces evaporation.",
        "hi": "सूखा समय — अगले 3 दिनों में केवल {rain} मिमी बारिश बनाम ~{need} मिमी/दिन की सामान्य फसल जरूरत। सिंचाई की योजना बनाएं; सुबह की सिंचाई से भापीकरण कम होता है।",
        "te": "పొడి కాలం — ముందు 3 రోజుల్లో {rain} మి.మీ మాత్రమే, పంటకు సగటు ~{need} మి.మీ/రోజు అవసరం. నీటి పారుదల ప్లాన్ చేయండి; ఉదయం సమయంలో చేస్తే ఆవిరి తక్కువ.",
    },
    "rain_sowing_delay": {
        "en": "{rain} mm rain forecast in the next 3 days — good for moisture, but wait for the shower to pass before sowing so seeds don't wash out or crust over.",
        "hi": "अगले 3 दिनों में {rain} मिमी बारिश — नमी के लिए अच्छी, पर बीज बहने/पपड़ी जमने से बचने के लिए बौवाई में बौछार रुकने दें।",
        "te": "ముందు 3 రోజుల్లో {rain} మి.మీ వర్షం — తేమకు మంచిది, కానీ విత్తనాలు కొట్టుకుపోకుండా వర్షం ఆగే వరకు వేచి ఉండండి.",
    },
    "rain_sowing_good": {
        "en": "Only {rain} mm rain expected in 3 days and soil is workable — a suitable sowing window. Sow in the morning and water lightly after.",
        "hi": "3 दिनों में केवल {rain} मिमी बारिश और मिट्टी कामयाब — बुवाई के लिए उपयुक्त समय। सुबह बुवाई करें और हल्का पानी दें।",
        "te": "3 రోజుల్లో {rain} మి.మీ మాత్రమే, మట్టి పనికొస్తుంది — విత్తులకు అనుకూల సమయం. ఉదయం వేసి, తర్వాత కొద్దిగా నీరు పెట్టండి.",
    },
    "rain_harvest_stop": {
        "en": "PAUSE harvest: {rain} mm rain forecast over the next 3 days. Complete cutting before the rain day; move harvested produce under cover and spread it to dry.",
        "hi": "कटाई रोकें: अगले 3 दिनों में {rain} मिमी बारिश। बारिश से पहले कटाई पूरी करें; कटी फसल छाया/छत के नीचे फैलाकर सुखाएं।",
        "te": "కోత ఆపండి: ముందు 3 రోజుల్లో {rain} మి.మీ వర్షం. వర్షానికి ముందు కోత పూర్తి చేయండి; కోసిన పంటను ఆశ్రయంలో ఆరబెట్టండి.",
    },
    "rain_harvest_watch": {
        "en": "Light rain possible ({rain} mm in 3 days) — keep harvested produce covered overnight and dry it on tarpaulin.",
        "hi": "हल्की बारिश संभव (3 दिनों में {rain} मिमी) — कटी फसल रात में ढकें और तिरपाल पर सुखाएं।",
        "te": "కొద్దిగా వర్షం సాధ్యం (3 రోజుల్లో {rain} మి.మీ) — కోసిన పంటను రాత్రి కప్పి, టార్పాలిన్‌పై ఆరబెట్టండి.",
    },
    "rain_harvest_go": {
        "en": "Dry window ahead — {rain} mm rain in 3 days is minimal. Good conditions for harvest and sun-drying.",
        "hi": "सूखी खिड़की — 3 दिनों में केवल {rain} मिमी बारिश। कटाई और धूप में सुखाने के लिए अच्छा समय।",
        "te": "పొడి సమయం — 3 రోజుల్లో {rain} మి.మీ మాత్రమే. కోత మరియు ఎండలో ఆరబెట్టడానికి మంచి రోజులు.",
    },
    "wind_no_spray": {
        "en": "Strong wind today ({speed} km/h, gusts {gust} km/h) — do NOT spray (drift risk) and stake/brace tall crops; flowering stage is most exposed.",
        "hi": "आज तेज हवा ({speed} किमी/घंटा, झोंके {gust} किमी/घंटा) — छिड़काव न करें (बहाव जोखिम) और लंबी फसलों को सहारा दें; फूल अवस्था सबसे संवेदनशील है।",
        "te": "ఈరోజు బలమైన గాలి ({speed} కి.మీ/గం, దెబ్బలు {gust} కి.మీ/గం) — చల్లడం చేయవద్దు (దూరంగా పోతుంది); పొడవాటి పంటలకు ఆధారం వేయండి; పూల దశ ఎక్కువ ప్రభావితం.",
    },
    "wind_caution": {
        "en": "Moderate wind ({speed} km/h) — spray only in early morning when wind is lowest; check young plants for lodging.",
        "hi": "मध्यम हवा ({speed} किमी/घंटा) — सुबह जब हवा कम हो तभी छिड़काव करें; युवा पौधों की जांच करें।",
        "te": "మధ్యస్థ గాలి ({speed} కి.మీ/గం) — గాలి తక్కువగా ఉండే ఉదయం మాత్రమే చల్లడం చేయండి; నారు మొక్కలు పరిశోధించండి.",
    },
    "heat_shifting": {
        "en": "Hot day ({temp}°C, feels {feels}°C) — shift field work to early morning/evening; ensure drinking water for workers and livestock.",
        "hi": "गर्म दिन ({temp}°C, महसूस {feels}°C) — खेत का काम सुबह/शाम को करें; मजदूरों और पशुओं को पीने का पानी दें।",
        "te": "వేడి రోజు ({temp}°C, {feels}°C అనిపించును) — పొల పనులు ఉదయం/సాయంత్రం చేయండి; కార్మికులకు, పశువులకు నీళ్లు సరిపంచండి.",
    },
    "heat_stress_crop": {
        "en": "{temp}°C is above the {crop} heat-stress threshold ({thresh}°C) during {stage} — irrigate lightly in the evening to cool the root zone and mulch to hold moisture.",
        "hi": "{temp}°C, {stage} के दौरान {crop} की गर्मी-तनाव सीमा ({thresh}°C) से अधिक है — जड़ क्षेत्र को ठंडा रखने के लिए शाम को हल्की सिंचाई करें और मल्चिंग करें।",
        "te": "{temp}°C, {stage} సమయంలో {crop} వేడి-ఒత్తిడి పరిమితి ({thresh}°C) కంటే ఎక్కువ — వేర్ల ప్రాంతాన్ని చల్లగా ఉంచేందుకు సాయంత్రం కొద్దిగా నీరు పెట్టండి, మల్చింగ్ చేయండి.",
    },
    "field_go": {
        "en": "Field operations (ploughing, weeding, fertilizer application): weather is suitable — {speed} km/h wind and {rain} mm rain expected over 3 days.",
        "hi": "खेत के काम (जुताई, निराई, खाद डालना): मौसम उपयुक्त — {speed} किमी/घंटा हवा और 3 दिनों में {rain} मिमी बारिश।",
        "te": "పొల పనులు (దున్నడం, కలుపు తీయడం, ఎరువు): వాతావరణం అనుకూలం — {speed} కి.మీ/గం గాలి, 3 రోజుల్లో {rain} మి.మీ వర్షం.",
    },
    "field_wait": {
        "en": "Hold field operations: {rain} mm rain expected over the next 3 days — wet soil compacts and inputs wash away. Resume after it dries.",
        "hi": "खेत के काम टालें: अगले 3 दिनों में {rain} मिमी बारिश — गीली मिट्टी सघन होती है और खाद बह जाती है। सूखने के बाद शुरू करें।",
        "te": "పొల పనులు వాయిదా: ముందు 3 రోజుల్లో {rain} మి.మీ వర్షం — తడి మట్టి మృదువుగా మారుతుంది, ఎరువు కొట్టుకుపోతుంది. ఆరిన తర్వాత ప్రారంభించండి.",
    },
    "frost_note": {
        "en": "Near-freezing night ({min}°C) — light evening irrigation can protect against frost; cover seedlings where practical.",
        "hi": "पाला के करीब रात ({min}°C) — शाम को हल्की सिंचाई पाले से बचा सकती है; संभव हो तो पौधों को ढकें।",
        "te": "మంచుకు దగ్గరగా రాత్రి ({min}°C) — సాయంత్రం కొద్దిగా నీరు పెట్టడం మంచు నుండి కాపాడుతుంది; వీలైతే మొక్కలను కప్పండి.",
    },
    "disclaimer": {
        "en": "Advice is generated from local forecast data and standard agronomy thresholds. Verify locally before major field decisions.",
        "hi": "सलाह स्थानीय पूर्वानुमान और मानक कृषि-विज्ञान सीमाओं पर आधारित है। बड़े निर्णयों से पहले स्थानीय सत्यापन करें।",
        "te": "సలహా స్థానిక అంచనా డేటా మరియు ప్రామాణిక వ్యవసాయ ప్రమాణాల ఆధారంగా. పెద్ద నిర్ణయాలకు ముందు స్థానికంగా సరిచూసుకోండి.",
    },
    "crop_note_label": {"en": "Crop note", "hi": "फसल टिप्पणी", "te": "పంట గమనిక"},
}


def _t(key, lang, **fmt):
    text = _T[key].get(lang) or _T[key]["en"]
    return text.format(**fmt) if fmt else text


def _w(key, lang):
    return _W[key].get(lang) or _W[key]["en"]


def validate_params(crop, stage, lang):
    """Return (normalized_crop, normalized_stage, lang, error_response).
    error_response is a (jsonify-able dict, status) tuple when invalid."""
    crop = (crop or "").strip().lower()
    stage = (stage or "").strip().lower()
    if crop not in VALID_CROPS:
        return None, None, None, ({"error": f"Unknown crop '{crop}'. Valid crops: {sorted(VALID_CROPS)}"}, 400)
    if stage not in VALID_STAGES:
        return None, None, None, ({"error": f"Unknown stage '{stage}'. Valid stages: {sorted(VALID_STAGES)}"}, 400)
    return crop, stage, lang, None


def build_farm_advice(weather_data, crop, stage, lang="en"):
    """Pure function: real weather payload + crop/stage -> structured advice.

    lang: "en" | "hi" | "te" (anything else falls back to English text).
    """
    lang = lang if lang in ("en", "hi", "te") else "en"
    current = weather_data.get("current", {}) or {}
    daily = weather_data.get("daily", {}) or {}
    hourly = weather_data.get("hourly", {}) or {}

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    wind = current.get("wind_speed_10m")
    gust = current.get("wind_gusts_10m") or wind
    rain_3d = sum(
        (v or 0) for v in (daily.get("precipitation_sum") or [])[:3]
    )
    rain_3d = round(rain_3d, 1)
    min_temp_3d = min((v for v in (daily.get("temperature_2m_min") or [])[:3] if v is not None), default=None)
    rain_probs = (daily.get("precipitation_probability_max") or [])[:3]
    max_rain_prob = max((p or 0) for p in rain_probs) if rain_probs else 0

    profile = CROPS[crop]
    stage_info = STAGES[stage]
    advice = []
    weather_snapshot = {
        "temperature_c": temp,
        "feels_like_c": feels,
        "wind_kph": wind,
        "gust_kph": gust,
        "rain_next_3_days_mm": rain_3d,
        "max_daily_rain_probability": max_rain_prob,
        "min_temp_next_3_days_c": min_temp_3d,
    }

    def add(topic, severity, text_key, fmt):
        advice.append({
            "topic": topic,
            "topic_label": _w(topic, lang),
            "severity": severity,
            "text": _t(text_key, lang, **fmt),
        })

    # ---- IRRIGATION (skip for rice at sowing; paddy managed by flooding) ----
    if crop == "rice" and stage == "sowing":
        pass  # nursery stage handled by rain advice below
    elif rain_3d >= 25 or (crop != "rice" and max_rain_prob >= 80 and rain_3d >= 15):
        add("irrigation", SEV_INFO, "irrigation_plenty", {"rain": rain_3d})
    elif rain_3d >= 10:
        add("irrigation", SEV_INFO, "irrigation_ok", {"rain": rain_3d})
    else:
        add("irrigation", SEV_CAUTION, "irrigation_needed", {"rain": rain_3d, "need": profile["krw_mm_day"]})

    # ---- RAINFALL / SOWING ----
    if stage == "sowing":
        if rain_3d >= 20:
            add("rain", SEV_CAUTION, "rain_sowing_delay", {"rain": rain_3d})
        else:
            add("rain", SEV_INFO, "rain_sowing_good", {"rain": rain_3d})

    # ---- HARVESTING ----
    if stage == "harvesting":
        if rain_3d >= 10 or max_rain_prob >= 70:
            add("harvest", SEV_ACTION, "rain_harvest_stop", {"rain": rain_3d})
        elif rain_3d >= 3:
            add("harvest", SEV_CAUTION, "rain_harvest_watch", {"rain": rain_3d})
        else:
            add("harvest", SEV_INFO, "rain_harvest_go", {"rain": rain_3d})

    # ---- WIND (spraying + lodging) ----
    wind_val = wind or 0
    gust_val = gust or 0
    if wind_val >= 30 or gust_val >= 45:
        add("wind", SEV_ACTION, "wind_no_spray", {"speed": round(wind_val), "gust": round(gust_val)})
    elif wind_val >= 20:
        add("wind", SEV_CAUTION, "wind_caution", {"speed": round(wind_val)})

    # ---- HEAT ----
    if temp is not None and temp >= profile["heat_stress_c"]:
        crop_label = profile["names"][lang]
        stage_label = stage_info["names"][lang]
        add("heat", SEV_ACTION, "heat_stress_crop", {
            "temp": round(temp), "crop": crop_label, "thresh": profile["heat_stress_c"], "stage": stage_label,
        })
    elif (temp is not None and temp >= 33) or (feels is not None and feels >= 38):
        add("heat", SEV_CAUTION, "heat_shifting", {
            "temp": round(temp or 0), "feels": round(feels or temp or 0),
        })

    # ---- FROST PROTECTION (real min temp) ----
    if min_temp_3d is not None and min_temp_3d <= 3:
        add("field", SEV_ACTION, "frost_note", {"min": min_temp_3d})

    # ---- FIELD OPERATIONS ----
    if rain_3d >= 15 or max_rain_prob >= 70:
        add("field", SEV_CAUTION, "field_wait", {"rain": rain_3d})
    else:
        add("field", SEV_INFO, "field_go", {
            "rain": rain_3d, "speed": round(wind_val or 0),
        })

    return {
        "crop": crop,
        "stage": stage,
        "language": lang,
        "crop_name": profile["names"][lang],
        "crop_emoji": profile["emoji"],
        "stage_name": stage_info["names"][lang],
        "stage_emoji": stage_info["emoji"],
        "headline": _t("title", lang),
        "advisory_label": _t("advisory", lang),
        "advice": advice,
        "crop_note": f"{profile['notes'][lang]}",
        "crop_note_label": _t("crop_note_label", lang),
        "disclaimer": _t("disclaimer", lang),
        "weather_snapshot": weather_snapshot,
    }
