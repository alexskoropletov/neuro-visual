const state = {
  title: "",
  character_bible: "",
  /** @type {{id:string,label:string,text:string}[]} */
  characters: [],
  world_bible: "",
  scenes: [],
  splitBusy: false,
  /** ComfyUI generate / stitch */
  videoBusy: false,
  /** Leonardo refs */
  leoBusy: false,
  /** scene id currently in video gen (optional lock for that scene's leo) */
  videoSceneId: null,
  wanDefaultsApplied: false,
  projectId: null,
  templates: [],
  /** @type {{id:string,name:string}[]} */
  leoModels: [],
  /** @type {Record<string, boolean>} scene id -> open */
  sceneOpen: {},
  /** last processed leonardo ref key: sceneId:ref:t */
  lastRefKey: "",
  /** last processed video clip key */
  lastClipKey: "",
};

const DRAFT_KEY = "videomake.draft";
let draftTimer = null;

/** @type {Record<string, any>} */
let wanOptions = null;

const WAN_OPTIONS_FALLBACK = {
  samplers: ["uni_pc", "uni_pc_bh2", "euler", "dpmpp_2m", "dpmpp_2m_sde", "ddim"],
  schedulers: ["simple", "normal", "karras", "exponential", "sgm_uniform"],
  weight_dtypes: ["default", "fp8_e4m3fn", "fp8_e5m2", "fp16", "bf16"],
  unets: ["wan2.2_ti2v_5B_fp16.safetensors"],
  clips: ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
  vaes: ["wan2.2_vae.safetensors"],
  steps: [20, 30, 40, 50, 55, 60, 70, 80],
  cfg: [2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 7, 8],
  shift: [3, 5, 6, 7, 8, 10, 12],
  length: [33, 49, 65, 81, 97, 113, 121],
  denoise: [0.7, 0.8, 0.85, 0.9, 0.95, 1],
  fps: [16, 24, 30],
  resolutions: [
    { value: "640x360", label: "640×360 (быстрее)" },
    { value: "832x480", label: "832×480 (TI2V-5B default)" },
    { value: "1024x576", label: "1024×576" },
    { value: "1280x704", label: "1280×704 (качество)" },
  ],
  source: "fallback",
  defaults: {
    width: 832,
    height: 480,
    steps: 40,
    cfg: 5,
    length: 81,
    shift: 8,
    seed: -1,
    sampler: "uni_pc",
    scheduler: "simple",
    denoise: 1,
    fps: 24,
    unet: "wan2.2_ti2v_5B_fp16.safetensors",
    clip: "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    vae: "wan2.2_vae.safetensors",
    weight_dtype: "default",
  },
};

const CAMERA_OPTS = [
  ["", "— ракурс —"],
  ["eye-level medium shot", "Eye-level"],
  ["low angle looking up", "Low angle"],
  ["high angle looking down", "High angle"],
  ["dutch angle", "Dutch angle"],
  ["over-the-shoulder", "Over-shoulder (solo-safe)"],
  ["POV first person", "POV"],
  ["tracking side shot", "Tracking"],
];

/** Explicit phrasing for Leonardo (short UI values → strong camera language). */
const CAMERA_PROMPT = {
  "eye-level medium shot":
    "eye-level medium shot, camera at subject eye height, straight-on framing",
  "low angle looking up":
    "low-angle shot looking up at the subject, heroic foreshortening, camera near the ground",
  "high angle looking down":
    "high-angle shot looking down on the subject, camera elevated above eye level",
  "dutch angle":
    "dutch angle / canted frame, horizon tilted, uneasy cinematic composition",
  "over-the-shoulder":
    "rear three-quarter from behind the sole subject looking past their shoulder into the scene — only one person, no dialogue partner",
  "POV first person":
    "first-person POV shot, camera as the character's eyes, immersive viewpoint",
  "tracking side shot":
    "side tracking shot, camera moving laterally beside the subject",
};

const TARGET_OPTS = [
  ["comfyui", "ComfyUI — видео (Wan I2V)"],
  ["leonardo", "Leonardo — кадр (image API)"],
];

const ENV_OPTS = [
  ["", "— локация / фон —"],
  ["dense tropical jungle, thick canopy, humid mist, muddy ground", "Jungle / rainforest"],
  ["lush green forest, tall trees, undergrowth", "Forest"],
  ["arid desert dunes, sand, sparse scrub", "Desert dunes"],
  ["rocky canyon, red stone cliffs", "Canyon"],
  ["tribal village clearing in the jungle, huts, torches", "Jungle village"],
  ["mountain road, cliffside asphalt, misty valleys", "Mountain road"],
  ["night city street, wet asphalt, neon practicals", "Night city"],
  ["interior hut, wood walls, firelight", "Hut interior"],
];

const TIME_OPTS = [
  ["", "— время суток —"],
  ["golden hour warm side light", "Golden hour"],
  ["harsh noon sunlight", "Noon"],
  ["overcast soft daylight", "Overcast day"],
  ["blue hour twilight", "Blue hour"],
  ["deep night, moonlight", "Night / moon"],
  ["pre-dawn grey light", "Pre-dawn"],
];

const WEATHER_OPTS = [
  ["", "— погода —"],
  ["clear dry air", "Clear"],
  ["humid haze, sticky air", "Humid haze"],
  ["light rain, wet surfaces", "Light rain"],
  ["heavy rain, storm", "Storm"],
  ["dust in the air", "Dusty"],
  ["fog / mist between trees", "Fog / mist"],
];

const PALETTE_OPTS = [
  ["", "— палитра —"],
  ["deep greens and warm browns, wet foliage", "Jungle greens"],
  ["desaturated earth tones", "Earth tones"],
  ["high contrast noir shadows", "Noir contrast"],
  ["warm amber firelight palette", "Firelight amber"],
  ["cool cyan moonlight", "Moon cyan"],
  ["golden warm highlights, soft shadows", "Golden warm"],
];

function sceneTarget(scene) {
  const t = String(scene?.target || "comfyui").toLowerCase();
  return t === "leonardo" || t === "leo" || t === "api" ? "leonardo" : "comfyui";
}

function leoModelOptionsHtml(selected) {
  const models = state.leoModels.length ? state.leoModels : LEO_MODEL_FALLBACK;
  const cur = selected || "";
  const opts = [
    ["", "— как в шапке —"],
    ...models.map((m) => [m.id, m.name || m.id]),
  ];
  return optionsHtml(opts, cur);
}

const LEO_MODEL_FALLBACK = [
  { id: "aa77f04e-3eec-4034-9c07-d0f619684628", name: "Leonardo Kino XL (cinema)" },
  { id: "de7d3faf-762f-48e0-b3b7-9d0ac3a3fcf3", name: "Phoenix 1.0" },
  { id: "5c232a9e-9061-4777-980a-ddc8e65647c6", name: "Leonardo Vision XL" },
  { id: "1e60896f-3c26-4296-8ecc-53e2afecc132", name: "Leonardo Diffusion XL" },
  { id: "b24e16ff-06e3-43eb-8d33-4416c2d75876", name: "Leonardo Lightning XL" },
  { id: "1dd50843-d653-4516-a8e3-f0238ee453ff", name: "Flux Schnell (fast)" },
  { id: "b2614463-296c-462a-9586-aafdb8f00e36", name: "Flux Dev" },
  { id: "7b592283-e8a7-4c5a-9ba6-d18c31f258b9", name: "Lucid Origin" },
  { id: "05ce0082-2d80-4a2d-8653-4d1c85e2418e", name: "Lucid Realism" },
  { id: "e71a1c2f-4f80-4800-934f-2c68979d8cc8", name: "Leonardo Anime XL" },
];

const LIGHTING_OPTS = [
  ["", "— свет —"],
  ["soft daylight through window", "Soft daylight"],
  ["hard noon sun, sharp shadows", "Hard noon"],
  ["golden hour warm side light", "Golden hour"],
  ["cool moonlight", "Moonlight"],
  ["neon practicals, night interior", "Neon night"],
  ["overcast soft ambient", "Overcast"],
  ["single practical lamp, chiaroscuro", "Practical lamp"],
];

const FOV_OPTS = [
  ["", "— FOV / lens —"],
  ["24mm wide angle", "24mm wide"],
  ["35mm cinematic", "35mm"],
  ["50mm normal", "50mm"],
  ["85mm portrait compression", "85mm"],
  ["135mm telephoto", "135mm tele"],
];

const THEME_OPTS = [
  ["cinematic film look", "Cinematic"],
  ["documentary realism", "Documentary"],
  ["noir high contrast", "Noir"],
  ["warm intimate", "Warm intimate"],
  ["cold clinical", "Cold clinical"],
  ["handheld urgency", "Handheld"],
];

const $ = (id) => document.getElementById(id);

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
}

function fillSelect(el, values, selected, { labels } = {}) {
  if (!el) return;
  const prev = selected != null && selected !== "" ? String(selected) : el.value;
  const list = Array.isArray(values) ? values.slice() : [];
  const labelMap = labels && typeof labels === "object" ? labels : null;
  // Ensure current value stays selectable even if not in curated list
  if (prev && !list.some((v) => String(v) === prev)) {
    list.unshift(prev);
  }
  el.innerHTML = list
    .map((v) => {
      const val = String(v);
      const lab = labelMap?.[val] != null ? labelMap[val] : val;
      return `<option value="${escapeHtml(val)}">${escapeHtml(String(lab))}</option>`;
    })
    .join("");
  if (prev && Array.from(el.options).some((o) => o.value === prev)) {
    el.value = prev;
  } else if (list.length) {
    el.value = String(list[0]);
  }
}

function fillWanOptionSelects(opts, preferred) {
  const o = opts || wanOptions || WAN_OPTIONS_FALLBACK;
  wanOptions = o;
  const pref = preferred || o.defaults || {};

  const preset = $("wanPreset");
  if (preset && Array.isArray(o.resolutions) && o.resolutions.length) {
    const cur = preset.value;
    preset.innerHTML =
      o.resolutions
        .map((r) => `<option value="${escapeHtml(r.value)}">${escapeHtml(r.label || r.value)}</option>`)
        .join("") + `<option value="custom">Своё…</option>`;
    if (cur && Array.from(preset.options).some((x) => x.value === cur)) preset.value = cur;
  }

  fillSelect($("wanSteps"), o.steps, pref.steps ?? 40);
  fillSelect($("wanCfg"), o.cfg, pref.cfg ?? 5);
  fillSelect($("wanLength"), o.length, pref.length ?? 81, {
    labels: Object.fromEntries(
      (o.length || []).map((n) => [String(n), `${n} (~${(n / 24).toFixed(1)}s @24fps)`])
    ),
  });
  fillSelect($("wanShift"), o.shift, pref.shift ?? 8);
  fillSelect($("wanSampler"), o.samplers, pref.sampler || "uni_pc");
  fillSelect($("wanScheduler"), o.schedulers, pref.scheduler || "simple");
  fillSelect($("wanDenoise"), o.denoise, pref.denoise ?? 1);
  fillSelect($("wanFps"), o.fps, pref.fps ?? 24);
  fillSelect($("wanUnet"), o.unets, pref.unet);
  fillSelect($("wanClip"), o.clips, pref.clip);
  fillSelect($("wanVae"), o.vaes, pref.vae);
  fillSelect($("wanDtype"), o.weight_dtypes, pref.weight_dtype || "default");

  const hint = $("wanOptionsHint");
  if (hint) {
    hint.textContent =
      o.source === "comfyui"
        ? "Списки моделей/sampler подтянуты из ComfyUI"
        : "ComfyUI недоступен — показаны локальные значения по умолчанию";
  }
  syncCustomResolutionVisibility();
}

function syncCustomResolutionVisibility() {
  const custom = $("wanPreset")?.value === "custom";
  const w = $("wanWidthWrap");
  const h = $("wanHeightWrap");
  if (w) w.hidden = !custom;
  if (h) h.hidden = !custom;
}

function collectWanSettings() {
  const preset = $("wanPreset")?.value || "832x480";
  let width = Number($("wanWidth")?.value) || 832;
  let height = Number($("wanHeight")?.value) || 480;
  if (preset !== "custom" && /^\d+x\d+$/.test(preset)) {
    const [w, h] = preset.split("x").map(Number);
    width = w;
    height = h;
  }
  const seedRaw = $("wanSeed")?.value;
  const seed = seedRaw === "" || seedRaw == null ? -1 : Number(seedRaw);
  return {
    width,
    height,
    steps: Number($("wanSteps")?.value) || 40,
    cfg: Number($("wanCfg")?.value) || 5,
    length: Number($("wanLength")?.value) || 81,
    shift: Number($("wanShift")?.value) || 8,
    seed: Number.isFinite(seed) ? seed : -1,
    sampler: $("wanSampler")?.value || "uni_pc",
    scheduler: $("wanScheduler")?.value || "simple",
    denoise: Number($("wanDenoise")?.value) || 1,
    fps: Number($("wanFps")?.value) || 24,
    unet: $("wanUnet")?.value || "",
    clip: $("wanClip")?.value || "",
    vae: $("wanVae")?.value || "",
    weight_dtype: $("wanDtype")?.value || "default",
  };
}

function saveDraftNow() {
  const draft = {
    script: $("script")?.value ?? "",
    scenes: Number($("sceneCount")?.value || 5),
    leoModel: $("leoModel")?.value ?? "",
    wan: collectWanSettings(),
    wanPreset: $("wanPreset")?.value || "832x480",
  };
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* ignore */
  }
}

function scheduleSaveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraftNow, 300);
}

function applyWanToForm(wan, preset) {
  if (!wan) return;
  fillWanOptionSelects(wanOptions || WAN_OPTIONS_FALLBACK, wan);
  if (wan.width) $("wanWidth").value = wan.width;
  if (wan.height) $("wanHeight").value = wan.height;
  if (wan.seed != null && $("wanSeed")) $("wanSeed").value = wan.seed;
  const key = `${wan.width}x${wan.height}`;
  const select = $("wanPreset");
  const match = Array.from(select.options).some((o) => o.value === key);
  select.value = preset || (match ? key : "custom");
  syncCustomResolutionVisibility();
}

function applyDraft(draft) {
  if (!draft) return;
  if (typeof draft.script === "string") $("script").value = draft.script;
  if (draft.scenes >= 4 && draft.scenes <= 8) $("sceneCount").value = String(draft.scenes);
  if (draft.wan) {
    applyWanToForm(draft.wan, draft.wanPreset);
    state.wanDefaultsApplied = true;
  }
}

function syncPresetFromWh() {
  const key = `${$("wanWidth").value}x${$("wanHeight").value}`;
  const select = $("wanPreset");
  const match = Array.from(select.options).some((o) => o.value === key);
  select.value = match ? key : "custom";
  syncCustomResolutionVisibility();
}

function onWanPresetChange() {
  const v = $("wanPreset").value;
  syncCustomResolutionVisibility();
  if (v === "custom") {
    saveDraftNow();
    return;
  }
  const [w, h] = v.split("x").map(Number);
  if (w && h) {
    $("wanWidth").value = w;
    $("wanHeight").value = h;
  }
  saveDraftNow();
}

async function getJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function setStatus(text, isError, busy) {
  const el = $("status");
  const textEl = $("statusText");
  if (!text) {
    el.hidden = true;
    el.classList.remove("busy", "error");
    textEl.textContent = "";
    return;
  }
  el.hidden = false;
  textEl.textContent = text;
  el.classList.toggle("error", Boolean(isError));
  el.classList.toggle("busy", Boolean(busy) && !isError);
}

function setSplitBusy(busy) {
  const btn = $("splitBtn");
  btn.disabled = busy;
  btn.classList.toggle("busy", busy);
  $("script").disabled = busy;
  $("sceneCount").disabled = busy;
}

function renderOllamaPipeline(health) {
  const hint = $("modelHint");
  const line = $("modelPipeline");
  const expand = health.ollama_expand_model || health.ollama_model || "qwen3.8:27b";
  const split = health.ollama_split_model || "dolphin-llama3";
  const models = Array.isArray(health.ollama_models) ? health.ollama_models : [];
  const expandOk = !models.length || models.includes(expand);
  const splitOk = !models.length || models.includes(split);
  if (hint) {
    hint.textContent = health.ollama
      ? `1) ${expand} — RU→EN + детали · 2) ${split} — нарезка сцен`
      : `Ollama недоступен (${health.ollama_host || "?"})`;
    hint.classList.toggle("bad", !health.ollama || !expandOk || !splitOk);
    hint.classList.toggle("ok", Boolean(health.ollama && expandOk && splitOk));
  }
  if (line) {
    const missing = [];
    if (models.length && !expandOk) missing.push(expand);
    if (models.length && !splitOk) missing.push(split);
    line.textContent = missing.length
      ? `Нет на хосте: ${missing.join(", ")} — ollama pull …`
      : `${models.length} model(s) @ ${health.ollama_host || "Ollama"}`;
  }
}

function applyHealthWanDefaults(health) {
  if (health.wan_options) {
    wanOptions = health.wan_options;
  }
  const opts = wanOptions || health.wan_options || WAN_OPTIONS_FALLBACK;
  if (state.wanDefaultsApplied) {
    // Refresh option lists from Comfy without clobbering current form
    fillWanOptionSelects(opts, collectWanSettings());
    return;
  }
  const draft = loadDraft();
  if (draft?.wan) {
    applyWanToForm(draft.wan, draft.wanPreset);
    state.wanDefaultsApplied = true;
    return;
  }
  fillWanOptionSelects(opts, health.wan || opts.defaults);
  if (health.wan) applyWanToForm(health.wan);
  state.wanDefaultsApplied = true;
}

function renderLeoModels(health) {
  const select = $("leoModel");
  const hint = $("leoModelHint");
  if (!select) return;
  const fromApi = Array.isArray(health.leonardo_models) ? health.leonardo_models : [];
  const models = fromApi.length ? fromApi : health.leonardo ? LEO_MODEL_FALLBACK : [];
  state.leoModels = models;
  const draft = loadDraft();
  const preferred = (draft && draft.leoModel) || health.leonardo_model_id || "";
  const current = select.value || preferred;
  select.innerHTML = "";
  if (!models.length) {
    state.leoModels = LEO_MODEL_FALLBACK;
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Leonardo не настроен";
    select.appendChild(opt);
    if (hint) {
      hint.textContent = "Добавьте LEONARDO_API_KEY в .env и перезапустите UI";
      hint.classList.add("bad");
      hint.classList.remove("ok");
    }
    return;
  }
  models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.name || m.id;
    select.appendChild(opt);
  });
  if (current && models.some((m) => m.id === current)) select.value = current;
  else select.value = models[0].id;
  if (hint) {
    hint.textContent = fromApi.length
      ? `${models.length} моделей · влияет на референсы`
      : `${models.length} моделей (локальный список) · перезапустите UI для синхронизации`;
    hint.classList.add("ok");
    hint.classList.remove("bad");
  }
}

function renderHealth(health) {
  for (const li of document.querySelectorAll("#health li")) {
    const ok = health[li.dataset.key];
    li.classList.toggle("ok", ok === true);
    li.classList.toggle("bad", ok === false);
  }
  renderOllamaPipeline(health);
  renderLeoModels(health);
  applyHealthWanDefaults(health);
}

function renderProjects(projects, current) {
  const select = $("projectSelect");
  const list = Array.isArray(projects) ? projects : [];
  state.projectId = current || null;
  select.innerHTML = list
    .map((p) => {
      const label = `${p.id}${p.title ? " · " + p.title : ""}${p.has_final ? " · final" : ""}`;
      const sel = p.id === current ? " selected" : "";
      return `<option value="${escapeHtml(p.id)}"${sel}>${escapeHtml(label)}</option>`;
    })
    .join("");
  $("projectHint").textContent = current
    ? `Папка: output/projects/${current}/`
    : "Нет активного проекта";
}

function renderTemplates(templates) {
  state.templates = Array.isArray(templates) ? templates : [];
  const select = $("templateSelect");
  const cur = select.value;
  select.innerHTML =
    '<option value="">— выбрать —</option>' +
    state.templates
      .map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`)
      .join("");
  if (cur && state.templates.some((t) => t.name === cur)) select.value = cur;
}

function optionsHtml(pairs, selected) {
  return pairs
    .map(([value, label]) => {
      const sel = value === selected ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${sel}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function themesHtml(selected) {
  const set = new Set(Array.isArray(selected) ? selected : []);
  return THEME_OPTS.map(
    ([value, label]) => `
      <label>
        <input type="checkbox" class="theme" value="${escapeHtml(value)}"${set.has(value) ? " checked" : ""} />
        ${escapeHtml(label)}
      </label>`
  ).join("");
}

function readShot(card) {
  if (!card) {
    return {
      camera: "",
      lighting: "",
      fov: "",
      environment: "",
      time_of_day: "",
      weather: "",
      palette: "",
      ambience: "",
      extras: "",
      themes: [],
    };
  }
  return {
    camera: card.querySelector(".shot-camera")?.value || "",
    lighting: card.querySelector(".shot-lighting")?.value || "",
    fov: card.querySelector(".shot-fov")?.value || "",
    environment: card.querySelector(".shot-environment")?.value || "",
    time_of_day: card.querySelector(".shot-time")?.value || "",
    weather: card.querySelector(".shot-weather")?.value || "",
    palette: card.querySelector(".shot-palette")?.value || "",
    ambience: card.querySelector(".shot-ambience")?.value?.trim() || "",
    extras: card.querySelector(".shot-extras")?.value?.trim() || "",
    themes: Array.from(card.querySelectorAll(".theme:checked")).map((el) => el.value),
  };
}

function readSceneTarget(card) {
  const raw = card?.querySelector(".shot-target")?.value || "comfyui";
  return raw === "leonardo" ? "leonardo" : "comfyui";
}

function collectScenes() {
  return state.scenes.map((scene, index) => {
    const card = document.querySelector(`[data-scene-id="${scene.id}"]`);
    const visual = card?.querySelector(".visual")?.value ?? scene.visual_prompt;
    const negative = card?.querySelector(".negative")?.value ?? scene.negative_prompt;
    return {
      ...scene,
      id: scene.id || index + 1,
      visual_prompt: visual,
      negative_prompt: negative,
      shot: readShot(card),
      target: readSceneTarget(card),
      leonardo_model: card?.querySelector(".shot-leo-model")?.value || scene.leonardo_model || "",
      ref: scene.ref || null,
      clip: scene.clip || null,
      clips: scene.clips || [],
      selected_clip: scene.selected_clip || null,
    };
  });
}

function applyShotToAll(shot, extras = {}) {
  state.scenes = collectScenes().map((scene) => ({
    ...scene,
    shot: {
      camera: shot.camera || "",
      lighting: shot.lighting || "",
      fov: shot.fov || "",
      environment: shot.environment || "",
      time_of_day: shot.time_of_day || "",
      weather: shot.weather || "",
      palette: shot.palette || "",
      ambience: shot.ambience || "",
      extras: shot.extras || "",
      themes: Array.isArray(shot.themes) ? [...shot.themes] : [],
    },
    target: extras.target || scene.target || "comfyui",
    leonardo_model:
      extras.leonardo_model !== undefined ? extras.leonardo_model : scene.leonardo_model || "",
  }));
  renderScenes();
}

function newCharId() {
  return `char-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

function collectCharacterBible() {
  const list = $("bibleList");
  if (!list) {
    return { characters: state.characters.slice(), character_bible: state.character_bible || "" };
  }
  const characters = Array.from(list.querySelectorAll(".bible-card")).map((card, index) => {
    const label = card.querySelector(".char-label")?.value?.trim() || `character ${index + 1}`;
    const text = card.querySelector(".char-text")?.value?.trim() || "";
    return {
      id: card.dataset.charId || newCharId(),
      label,
      text,
    };
  }).filter((c) => c.label || c.text);
  const character_bible = characters
    .map((c) => {
      let text = (c.text || "").trim();
      if (!text) return `The ${c.label}.`;
      const re = new RegExp(`^(?:The\\s+)?${c.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i");
      if (!re.test(text) && c.label) {
        const body = text[0] && text[0] === text[0].toUpperCase() ? text[0].toLowerCase() + text.slice(1) : text;
        text = /^(?:is|are|has|have|wears|wear|consists)\b/i.test(body)
          ? `The ${c.label} ${body}`
          : `The ${c.label} is ${body}`;
      }
      return text.replace(/[.]*$/, "") + ".";
    })
    .join(" ");
  state.characters = characters;
  state.character_bible = character_bible;
  return { characters, character_bible };
}

function renderBibleEditor(characters) {
  const wrap = $("bibleWrap");
  const list = $("bibleList");
  if (!wrap || !list) return;
  const cards = Array.isArray(characters) ? characters : state.characters;
  state.characters = cards.map((c, i) => ({
    id: c.id || `char-${i + 1}`,
    label: c.label || `character ${i + 1}`,
    text: c.text || "",
  }));
  if (!state.characters.length && !state.character_bible) {
    wrap.hidden = true;
    list.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  if (!state.characters.length && state.character_bible) {
    state.characters = [
      { id: newCharId(), label: "cast", text: state.character_bible },
    ];
  }
  list.innerHTML = state.characters
    .map(
      (c) => `
    <article class="bible-card" data-char-id="${escapeHtml(c.id)}">
      <div class="bible-card-top">
        <label>Имя / роль
          <input type="text" class="char-label" value="${escapeHtml(c.label)}" placeholder="protagonist, female tribe…" />
        </label>
        <button type="button" class="ghost" data-char-del="${escapeHtml(c.id)}">Удалить</button>
      </div>
      <label>Описание
        <textarea class="char-text" rows="3" placeholder="Внешность, одежда, возраст, отличительные черты…">${escapeHtml(c.text)}</textarea>
      </label>
    </article>`
    )
    .join("");

  list.querySelectorAll("[data-char-del]").forEach((btn) => {
    btn.addEventListener("click", () => {
      collectCharacterBible();
      state.characters = state.characters.filter((c) => c.id !== btn.dataset.charDel);
      if (!state.characters.length) state.character_bible = "";
      else collectCharacterBible();
      renderBibleEditor(state.characters);
    });
  });
  list.querySelectorAll(".char-label, .char-text").forEach((el) => {
    el.addEventListener("input", () => {
      collectCharacterBible();
    });
  });
}

async function loadCharacterBible(text, characters) {
  const rawText = (text || "").trim();
  if (Array.isArray(characters) && characters.length) {
    state.character_bible = rawText || state.character_bible;
    renderBibleEditor(characters);
    collectCharacterBible();
    return;
  }
  if (!rawText) {
    state.character_bible = "";
    state.characters = [];
    renderBibleEditor([]);
    return;
  }
  try {
    const data = await getJson("/api/bible/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: rawText }),
    });
    state.character_bible = data.character_bible || rawText;
    renderBibleEditor(data.characters || []);
  } catch {
    state.character_bible = rawText;
    renderBibleEditor([{ id: newCharId(), label: "cast", text: rawText }]);
  }
}

async function saveCharacterBible() {
  const { characters, character_bible } = collectCharacterBible();
  const world_bible = ($("worldBible")?.value || "").trim();
  state.world_bible = world_bible;
  try {
    const data = await getJson("/api/project/bible", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ characters, character_bible, world_bible }),
    });
    state.character_bible = data.character_bible || character_bible;
    state.world_bible = data.world_bible || world_bible;
    renderBibleEditor(data.characters || characters);
    setStatus("Character / world bible сохранены");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderScenes() {
  const wrap = $("bibleWrap");
  if (wrap) wrap.hidden = !(state.characters.length || state.character_bible);

  const root = $("scenes");
  if (!state.scenes.length) {
    root.innerHTML = '<p class="empty">Сначала разрежьте сценарий или откройте проект.</p>';
    $("genAllBtn").disabled = true;
    if ($("genAllX5Btn")) $("genAllX5Btn").disabled = true;
    if ($("genAllX10Btn")) $("genAllX10Btn").disabled = true;
    $("leoAllBtn").disabled = true;
    return;
  }

  $("genAllBtn").disabled = !state.scenes.length || (state.videoBusy && state.leoBusy);
  if ($("genAllX5Btn")) $("genAllX5Btn").disabled = !state.scenes.length || state.videoBusy;
  if ($("genAllX10Btn")) $("genAllX10Btn").disabled = !state.scenes.length || state.videoBusy;
  $("leoAllBtn").disabled = state.leoBusy;
  root.innerHTML = state.scenes
    .map((scene) => {
      const shot = scene.shot || {};
      const sid = String(scene.id);
      const fn = scene.filename || `scene-${String(scene.id).padStart(2, "0")}`;
      const isOpen = state.sceneOpen[sid] !== false;
      const refUrl = scene.ref ? refMediaUrl(scene.ref, scene.refBust) : "";
      const clipName = scene.clip || null;
      const clipUrl = clipName ? clipMediaUrl(clipName, scene.clipBust) : "";
      const target = sceneTarget(scene);
      const variants = Array.isArray(scene.clips) ? scene.clips : [];
      const refBlock = scene.ref
        ? `<img class="scene-ref-img media-zoom" src="${refUrl}" alt="Leonardo ref" title="Открыть полностью" data-lightbox="image" data-lightbox-src="${escapeHtml(refUrl)}" />`
        : `<div class="scene-placeholder scene-edit-only">Нет референса Leonardo</div>`;
      const clipBlock = clipName
        ? `<video class="scene-clip-video media-zoom" src="${clipUrl}" preload="metadata" title="Открыть полностью" data-lightbox="video" data-lightbox-src="${escapeHtml(clipUrl)}"></video>
           <div class="clip-actions scene-edit-only">
             <a class="download" href="${clipUrl}" download="${escapeHtml(clipName)}">Скачать</a>
             <button type="button" class="ghost" data-lightbox="video" data-lightbox-src="${escapeHtml(clipUrl)}">На весь экран</button>
           </div>`
        : `<div class="scene-placeholder scene-edit-only">Видео ещё не сгенерировано</div>`;
      const variantsBlock =
        target === "comfyui" && variants.length
          ? `<div class="clip-variants scene-edit-only" data-clip-variants="${scene.id}">
              ${variants
                .map((v) => {
                  const name = typeof v === "string" ? v : v.name;
                  if (!name) return "";
                  const selected = Boolean(v.selected) || name === scene.selected_clip;
                  const vUrl = clipMediaUrl(name, scene.clipBust);
                  const take = String(name).match(/__v(\d+)/i);
                  const label = take ? `take ${take[1]}` : "clip";
                  return `<div class="clip-variant ${selected ? "is-selected" : ""}">
                    <video src="${vUrl}" preload="metadata" muted playsinline></video>
                    <div class="clip-variant-actions">
                      <span class="clip-variant-label">${label}${selected ? " · выбран" : ""}</span>
                      <button type="button" class="ghost" data-clip-select="${escapeHtml(name)}" ${selected ? "disabled" : ""}>Выбрать</button>
                      <button type="button" class="ghost danger" data-clip-del="${escapeHtml(name)}">Удалить</button>
                    </div>
                  </div>`;
                })
                .join("")}
            </div>`
          : "";
      const genDisabled =
        target === "leonardo"
          ? state.leoBusy
          : state.videoBusy || !scene.ref;
      const genLabel = target === "leonardo" ? "Сгенерировать кадр" : "Сгенерировать видео";
      const outLabel = target === "leonardo" ? "Кадр Leonardo" : "Видео ComfyUI";
      const genXButtons =
        target === "comfyui"
          ? `<button type="button" class="ghost" data-gen-x5="${scene.id}" ${genDisabled ? "disabled" : ""} title="5 takes подряд, все сохраняются">×5</button>
             <button type="button" class="ghost" data-gen-x10="${scene.id}" ${genDisabled ? "disabled" : ""} title="10 takes подряд, все сохраняются">×10</button>`
          : "";
      const mediaClass = [
        "scene-media",
        scene.ref ? "has-ref" : "",
        clipName ? "has-clip" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const targetBadge = target === "leonardo" ? " · Leo" : " · Comfy";
      return `
      <article class="scene ${isOpen ? "is-open" : "is-collapsed"}" data-scene-id="${scene.id}">
        <header class="scene-summary">
          <button type="button" class="scene-toggle" data-scene-toggle="${scene.id}" aria-expanded="${isOpen}">
            <span class="scene-chevron" aria-hidden="true"></span>
            <span class="scene-summary-title">${escapeHtml(fn)}</span>
            <span class="scene-summary-meta">${scene.ref ? "ref" : "—"}${clipName ? " · video" : ""}${variants.length > 1 ? ` · ${variants.length} takes` : ""}${targetBadge}</span>
          </button>
        </header>
        <div class="${mediaClass}">
          <div class="scene-media-col scene-ref-col ${scene.ref ? "" : "is-empty"}">
            <p class="shot-label scene-edit-only">Референс Leonardo</p>
            <div class="scene-ref-frame" data-ref-wrap="${scene.id}">${refBlock}</div>
            <div class="row gap scene-media-actions scene-edit-only">
              <button type="button" class="ghost" data-leo-ref="${scene.id}" ${state.leoBusy ? "disabled" : ""}>Перегенерить</button>
              <label class="ghost file-btn">Загрузить…
                <input type="file" accept="image/*" data-ref-upload="${scene.id}" hidden />
              </label>
              ${
                scene.ref
                  ? `<button type="button" class="ghost" data-ref-del="${scene.id}">Удалить</button>`
                  : ""
              }
            </div>
          </div>
          <div class="scene-media-col scene-clip-col ${clipName ? "" : "is-empty"}">
            <p class="shot-label scene-edit-only" data-out-label>${outLabel}</p>
            <div class="scene-clip-frame" data-clip-wrap="${scene.id}">${clipBlock}</div>
            ${variantsBlock}
            <div class="row gap scene-media-actions scene-edit-only">
              <button type="button" class="primary" data-gen="${scene.id}" ${genDisabled ? "disabled" : ""}>${genLabel}</button>
              ${genXButtons}
            </div>
          </div>
        </div>
        <div class="scene-edit">
          <div class="row gap scene-edit-toolbar">
            <button type="button" class="ghost" data-apply-all="${scene.id}">Применить ко всем</button>
          </div>
          <label>Visual prompt
            <textarea class="visual" rows="4">${escapeHtml(scene.visual_prompt || "")}</textarea>
          </label>
          <label>Negative
            <textarea class="negative" rows="2">${escapeHtml(scene.negative_prompt || "")}</textarea>
          </label>
          <p class="shot-label">Цель / кадр / мир</p>
          <div class="shot-grid">
            <label>Цель генерации
              <select class="shot-target">${optionsHtml(TARGET_OPTS, target)}</select>
            </label>
            <label class="shot-leo-model-wrap" ${target === "leonardo" ? "" : "hidden"}>
              Модель Leonardo (image)
              <select class="shot-leo-model">${leoModelOptionsHtml(scene.leonardo_model || "")}</select>
            </label>
            <label>Локация / фон
              <select class="shot-environment">${optionsHtml(ENV_OPTS, shot.environment || "")}</select>
            </label>
            <label>Время суток
              <select class="shot-time">${optionsHtml(TIME_OPTS, shot.time_of_day || "")}</select>
            </label>
            <label>Погода
              <select class="shot-weather">${optionsHtml(WEATHER_OPTS, shot.weather || "")}</select>
            </label>
            <label>Палитра
              <select class="shot-palette">${optionsHtml(PALETTE_OPTS, shot.palette || "")}</select>
            </label>
            <label>Ракурс
              <select class="shot-camera">${optionsHtml(CAMERA_OPTS, shot.camera || "")}</select>
            </label>
            <label>Освещение
              <select class="shot-lighting">${optionsHtml(LIGHTING_OPTS, shot.lighting || "")}</select>
            </label>
            <label>FOV / объектив
              <select class="shot-fov">${optionsHtml(FOV_OPTS, shot.fov || "")}</select>
            </label>
          </div>
          <label>Атмосфера (свободно)
            <input type="text" class="shot-ambience" value="${escapeHtml(shot.ambience || "")}" placeholder="humid, oppressive, ritual tension…" />
          </label>
          <label>Доп. описание кадра
            <textarea class="shot-extras" rows="2" placeholder="Детали фона, props, свет practicals…">${escapeHtml(shot.extras || "")}</textarea>
          </label>
          <div class="theme-row">${themesHtml(shot.themes || [])}</div>
        </div>
      </article>`;
    })
    .join("");

  root.querySelectorAll("[data-scene-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = String(btn.dataset.sceneToggle);
      const open = state.sceneOpen[id] !== false;
      state.sceneOpen[id] = !open;
      const card = document.querySelector(`[data-scene-id="${id}"]`);
      if (card) {
        card.classList.toggle("is-open", !open);
        card.classList.toggle("is-collapsed", open);
        btn.setAttribute("aria-expanded", String(!open));
      }
    });
  });
  root.querySelectorAll("[data-gen]").forEach((btn) => {
    btn.addEventListener("click", () => generate(Number(btn.dataset.gen)));
  });
  root.querySelectorAll("[data-gen-x5]").forEach((btn) => {
    btn.addEventListener("click", () => generate(Number(btn.dataset.genX5), 5));
  });
  root.querySelectorAll("[data-gen-x10]").forEach((btn) => {
    btn.addEventListener("click", () => generate(Number(btn.dataset.genX10), 10));
  });
  root.querySelectorAll("[data-clip-select]").forEach((btn) => {
    btn.addEventListener("click", () => selectClip(btn.dataset.clipSelect));
  });
  root.querySelectorAll("[data-clip-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteClip(btn.dataset.clipDel));
  });
  root.querySelectorAll("[data-leo-ref]").forEach((btn) => {
    btn.addEventListener("click", () => generateLeonardoRefs(Number(btn.dataset.leoRef)));
  });
  root.querySelectorAll("[data-apply-all]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = document.querySelector(`[data-scene-id="${btn.dataset.applyAll}"]`);
      applyShotToAll(readShot(card), {
        target: readSceneTarget(card),
        leonardo_model: card?.querySelector(".shot-leo-model")?.value || "",
      });
      setStatus("Настройки (цель, модель, кадр) скопированы на все сцены");
    });
  });
  root.querySelectorAll(".shot-target").forEach((sel) => {
    sel.addEventListener("change", () => {
      const card = sel.closest("[data-scene-id]");
      if (!card) return;
      const target = sel.value === "leonardo" ? "leonardo" : "comfyui";
      const wrap = card.querySelector(".shot-leo-model-wrap");
      if (wrap) wrap.hidden = target !== "leonardo";
      const out = card.querySelector("[data-out-label]");
      if (out) out.textContent = target === "leonardo" ? "Кадр Leonardo" : "Видео ComfyUI";
      const genBtn = card.querySelector("[data-gen]");
      if (genBtn) {
        const id = Number(genBtn.dataset.gen);
        const scene = state.scenes.find((s) => Number(s.id) === id) || {};
        genBtn.textContent = target === "leonardo" ? "Сгенерировать кадр" : "Сгенерировать видео";
        genBtn.disabled =
          target === "leonardo" ? state.leoBusy : state.videoBusy || !scene.ref;
      }
      const x5 = card.querySelector("[data-gen-x5]");
      if (x5) {
        const id = Number(x5.dataset.genX5);
        const scene = state.scenes.find((s) => Number(s.id) === id) || {};
        x5.hidden = target !== "comfyui";
        x5.disabled = state.videoBusy || !scene.ref;
      }
      const x10 = card.querySelector("[data-gen-x10]");
      if (x10) {
        const id = Number(x10.dataset.genX10);
        const scene = state.scenes.find((s) => Number(s.id) === id) || {};
        x10.hidden = target !== "comfyui";
        x10.disabled = state.videoBusy || !scene.ref;
      }
      const meta = card.querySelector(".scene-summary-meta");
      if (meta) {
        const hasRef = Boolean(sceneRefFromCard(card));
        const hasClip = Boolean(card.querySelector(".scene-clip-video"));
        meta.textContent = `${hasRef ? "ref" : "—"}${hasClip ? " · video" : ""} · ${
          target === "leonardo" ? "Leo" : "Comfy"
        }`;
      }
    });
  });
  root.querySelectorAll("[data-ref-upload]").forEach((input) => {
    input.addEventListener("change", () => uploadRef(Number(input.dataset.refUpload), input.files?.[0]));
  });
  root.querySelectorAll("[data-ref-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteRef(Number(btn.dataset.refDel)));
  });
}

function sceneRefFromCard(card) {
  return Boolean(card?.querySelector(".scene-ref-img"));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function uploadRef(sceneId, file) {
  if (!file) return;
  const scene = state.scenes.find((s) => Number(s.id) === sceneId);
  if (!scene) return;
  const fn = scene.filename || `scene-${String(sceneId).padStart(2, "0")}`;
  const ext = (file.name.split(".").pop() || "png").toLowerCase();
  setStatus(`Загрузка референса для ${fn}…`, false, true);
  try {
    const dataUrl = await fileToBase64(file);
    const res = await getJson("/api/project/ref", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene: fn, ext: `.${ext}`, data_base64: dataUrl }),
    });
    scene.ref = res.ref;
    renderScenes();
    setStatus(`Референс сохранён: ${res.ref}`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteRef(sceneId) {
  const scene = state.scenes.find((s) => Number(s.id) === sceneId);
  if (!scene?.ref) return;
  const fn = scene.filename || `scene-${String(sceneId).padStart(2, "0")}`;
  try {
    await fetch("/api/project/ref", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene: fn }),
    }).then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    });
    scene.ref = null;
    renderScenes();
    setStatus("Референс удалён");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function refMediaUrl(refName, bust) {
  const t = bust || Date.now();
  return `/api/media/ref/${encodeURIComponent(refName)}?t=${t}`;
}

function clipMediaUrl(clipName, bust) {
  const t = bust || Date.now();
  return `/api/media/scene/${encodeURIComponent(clipName)}?t=${t}`;
}

function applyRefToDom(scene) {
  if (!scene?.ref) return;
  const wrapRef = document.querySelector(`[data-ref-wrap="${scene.id}"]`);
  if (!wrapRef) return;
  const src = refMediaUrl(scene.ref, scene.refBust);
  let img = wrapRef.querySelector("img.scene-ref-img");
  if (!img) {
    wrapRef.innerHTML = `<img class="scene-ref-img media-zoom" src="${src}" alt="Leonardo ref" title="Открыть полностью" data-lightbox="image" data-lightbox-src="${escapeHtml(src)}" />`;
  } else {
    img.src = src;
    img.dataset.lightboxSrc = src;
    img.classList.add("media-zoom");
    img.dataset.lightbox = "image";
    img.title = "Открыть полностью";
  }
  wrapRef.closest(".scene-media-col")?.classList.remove("is-empty");
  wrapRef.closest(".scene-media")?.classList.add("has-ref");
  const genBtn = document.querySelector(`[data-gen="${scene.id}"]`);
  if (genBtn) genBtn.disabled = state.videoBusy || !scene.ref;
}

function syncSceneMediaFromJob(job) {
  const clips = job.clips || [];
  const byScene = {};
  clips.forEach((c) => {
    const name = String(c.name || "");
    const stem = sceneStemFromClip(name);
    if (!stem) return;
    if (!byScene[stem]) byScene[stem] = [];
    byScene[stem].push(name);
  });

  // refs_ready: ["scene-01.jpg"] or [{name, t}]
  const refsReady = Array.isArray(job.refs_ready) ? job.refs_ready : [];
  const refByStem = {};
  refsReady.forEach((item) => {
    const name = typeof item === "string" ? item : item?.name;
    const bust = typeof item === "object" && item?.t ? item.t : null;
    if (!name) return;
    const stem = String(name).replace(/\.(png|jpe?g|webp|gif)$/i, "");
    if (stem) refByStem[stem] = { name, t: bust };
  });
  if (job.last_ref?.ref) {
    const stem = String(job.last_ref.filename || job.last_ref.ref).replace(
      /\.(png|jpe?g|webp|gif)$/i,
      ""
    );
    const bust = job.last_ref.t || null;
    refByStem[stem || stemOf(job.last_ref.ref)] = {
      name: job.last_ref.ref,
      t: bust,
    };
  }

  let needFullRender = false;
  const refreshed = [];

  state.scenes.forEach((scene) => {
    const fn = (scene.filename || `scene-${String(scene.id).padStart(2, "0")}`).toLowerCase();
    const sceneClips = byScene[fn] || [];
    const primary = sceneClips.find((n) => /^scene-\d+\.(webm|mp4|mkv|mov)$/i.test(n)) || null;
    const takes = sceneClips.filter((n) => /__v\d+\./i.test(n)).sort();
    const fromJob =
      job.last_clip?.scene_id != null &&
      Number(job.last_clip.scene_id) === Number(scene.id) &&
      Array.isArray(job.last_clip.variants)
        ? job.last_clip.variants
        : null;
    const nextTakes = fromJob
      ? fromJob.map((v) => (typeof v === "string" ? v : v.name)).filter(Boolean)
      : takes;
    const uniqueTakes = [...new Set(nextTakes.length ? nextTakes : takes)];
    const nextClip = primary || uniqueTakes[uniqueTakes.length - 1] || null;
    const prevKey = JSON.stringify((scene.clips || []).map((c) => (typeof c === "string" ? c : c.name)));
    const nextKey = JSON.stringify(uniqueTakes);
    const takeSet = new Set(uniqueTakes);
    const fromJobSelected = fromJob && fromJob.find((v) => v.selected)?.name;
    const lastClipName =
      job.last_clip?.scene_id != null &&
      Number(job.last_clip.scene_id) === Number(scene.id) &&
      job.last_clip?.clip &&
      takeSet.has(job.last_clip.clip)
        ? job.last_clip.clip
        : null;
    // Prefer persisted selection; only use last_clip while that take still exists
    const selectedName =
      (scene.selected_clip && takeSet.has(scene.selected_clip) ? scene.selected_clip : null) ||
      (fromJobSelected && takeSet.has(fromJobSelected) ? fromJobSelected : null) ||
      lastClipName ||
      (uniqueTakes.length ? uniqueTakes[uniqueTakes.length - 1] : null);

    if (scene.clip !== nextClip || prevKey !== nextKey || scene.selected_clip !== selectedName) {
      scene.clip = nextClip;
      scene.clipBust = Date.now();
      scene.selected_clip = selectedName;
      if (uniqueTakes.length) {
        scene.clips = uniqueTakes.map((name) => ({
          name,
          selected: name === selectedName,
        }));
      } else if (primary) {
        scene.clips = [{ name: primary, selected: true }];
        scene.selected_clip = primary;
      } else {
        scene.clips = [];
      }
      needFullRender = true;
    }
    if (
      job.last_clip?.scene_id != null &&
      Number(job.last_clip.scene_id) === Number(scene.id) &&
      job.last_clip?.clip &&
      takeSet.has(job.last_clip.clip)
    ) {
      const key = `${job.last_clip.clip}:${job.last_clip.t || ""}:${uniqueTakes.length}`;
      if (key !== state.lastClipKey) {
        state.lastClipKey = key;
        scene.clip = job.last_clip.primary || primary || job.last_clip.clip;
        scene.selected_clip = job.last_clip.clip;
        scene.clipBust = job.last_clip.t || Date.now();
        scene.clips = uniqueTakes.map((name) => ({
          name,
          selected: name === job.last_clip.clip,
        }));
        needFullRender = true;
      }
    }
    const nextRef = refByStem[fn] || refByStem[scene.filename] || null;
    if (nextRef) {
      const sameName = scene.ref === nextRef.name;
      const sameBust = nextRef.t != null && scene.refBust === nextRef.t;
      // If server sent mtime/bust — refresh when it changes. If no bust, rely on last_ref key below.
      if (nextRef.t != null && (!sameName || !sameBust)) {
        scene.ref = nextRef.name;
        scene.refBust = nextRef.t;
        refreshed.push(scene);
        if (!sameName) needFullRender = true;
      } else if (!scene.ref && nextRef.name) {
        scene.ref = nextRef.name;
        scene.refBust = nextRef.t || Date.now();
        refreshed.push(scene);
        needFullRender = true;
      }
    }
  });

  // Force-refresh once per completed Leonardo still (same filename, new bytes)
  if (job.last_ref?.scene_id != null && job.last_ref.ref) {
    const key = `${job.last_ref.scene_id}:${job.last_ref.ref}:${job.last_ref.t || job.message || ""}`;
    if (key !== state.lastRefKey) {
      state.lastRefKey = key;
      const scene = state.scenes.find((s) => Number(s.id) === Number(job.last_ref.scene_id));
      if (scene) {
        scene.ref = job.last_ref.ref;
        scene.refBust = job.last_ref.t || Date.now();
        if (!refreshed.includes(scene)) refreshed.push(scene);
      }
    }
  }

  const editing = Boolean(document.activeElement?.closest?.(".scene textarea, .scene select"));
  if (needFullRender && !editing) {
    renderScenes();
    return;
  }
  refreshed.forEach((scene) => applyRefToDom(scene));
  if (needFullRender && editing) {
    state.scenes.forEach((scene) => {
      if (scene.clip) {
        const wrapClip = document.querySelector(`[data-clip-wrap="${scene.id}"]`);
        if (!wrapClip) return;
        const src = clipMediaUrl(scene.clip, scene.clipBust);
        let video = wrapClip.querySelector("video");
        if (!video) {
          wrapClip.innerHTML = `<video class="scene-clip-video media-zoom" src="${src}" preload="metadata" title="Открыть полностью" data-lightbox="video" data-lightbox-src="${escapeHtml(src)}"></video>
            <div class="clip-actions scene-edit-only"><a class="download" href="${src}" download="${escapeHtml(scene.clip)}">Скачать</a>
            <button type="button" class="ghost" data-lightbox="video" data-lightbox-src="${escapeHtml(src)}">На весь экран</button></div>`;
        } else {
          video.src = src;
          video.dataset.lightboxSrc = src;
          video.classList.add("media-zoom");
          video.dataset.lightbox = "video";
        }
      }
      applyRefToDom(scene);
    });
  }
}

function sceneStemFromClip(name) {
  const stem = stemOf(name);
  const m = String(stem).match(/^(scene-\d+)/i);
  return m ? m[1].toLowerCase() : stem.toLowerCase();
}

function stemOf(name) {
  return String(name || "").replace(/\.(png|jpe?g|webp|gif|webm|mp4|mkv|mov)$/i, "");
}

function updateFinal(job) {
  const box = $("finalBox");
  const empty = $("finalEmpty");
  const player = $("player");
  const dl = $("finalDownload");
  const hasClips = (job.clips || []).length > 0;
  $("stitchBtn").disabled = !hasClips || state.videoBusy;
  if (job.final) {
    box.hidden = false;
    if (empty) empty.hidden = true;
    const next = `/api/media/final.mp4?t=${Date.now()}`;
    if (job.message === "Склейка готова." || !player.src.includes("final.mp4")) {
      player.src = next;
    } else if (!player.getAttribute("src")) {
      player.src = next;
    }
    dl.href = next;
    player.title = "Используйте «На весь экран» для попапа";
    let expand = box.querySelector("[data-final-expand]");
    if (!expand) {
      expand = document.createElement("button");
      expand.type = "button";
      expand.className = "ghost";
      expand.dataset.finalExpand = "1";
      expand.textContent = "На весь экран";
      expand.addEventListener("click", () => openLightbox("video", player.currentSrc || player.src));
      dl.insertAdjacentElement("afterend", expand);
    }
  } else {
    box.hidden = true;
    if (empty) empty.hidden = false;
  }
}

function openLightbox(type, src) {
  const root = $("lightbox");
  const img = $("lightboxImg");
  const video = $("lightboxVideo");
  if (!root || !src) return;
  img.hidden = true;
  video.hidden = true;
  video.pause();
  video.removeAttribute("src");
  video.load();
  if (type === "video") {
    video.src = src;
    video.hidden = false;
    video.play().catch(() => {});
  } else {
    img.src = src;
    img.hidden = false;
  }
  root.hidden = false;
  document.body.classList.add("lightbox-open");
}

function closeLightbox() {
  const root = $("lightbox");
  const img = $("lightboxImg");
  const video = $("lightboxVideo");
  if (!root || root.hidden) return;
  video?.pause();
  if (video) {
    video.removeAttribute("src");
    video.load();
    video.hidden = true;
  }
  if (img) {
    img.removeAttribute("src");
    img.hidden = true;
  }
  root.hidden = true;
  document.body.classList.remove("lightbox-open");
}

function onLightboxTriggerClick(ev) {
  const el = ev.target.closest("[data-lightbox]");
  if (!el) return;
  // Allow download links / real controls without hijack
  if (ev.target.closest("a.download, .clip-actions a")) return;
  const type = el.dataset.lightbox;
  const src = el.dataset.lightboxSrc || el.currentSrc || el.src;
  if (!type || !src) return;
  ev.preventDefault();
  openLightbox(type, src);
}

function setGenerateBusy({ videoBusy = state.videoBusy, leoBusy = state.leoBusy, videoSceneId = state.videoSceneId } = {}) {
  state.videoBusy = Boolean(videoBusy);
  state.leoBusy = Boolean(leoBusy);
  state.videoSceneId = videoSceneId != null && videoSceneId !== "" ? Number(videoSceneId) : null;
  const hasScenes = state.scenes.length > 0;
  const anyBusy = state.videoBusy || state.leoBusy;
  $("genAllBtn").disabled = !hasScenes || (state.videoBusy && state.leoBusy);
  if ($("genAllX5Btn")) $("genAllX5Btn").disabled = !hasScenes || state.videoBusy;
  if ($("genAllX10Btn")) $("genAllX10Btn").disabled = !hasScenes || state.videoBusy;
  $("leoAllBtn").disabled = state.leoBusy || !hasScenes;
  $("cancelGenBtn").disabled = !anyBusy;
  document.querySelectorAll("[data-gen]").forEach((btn) => {
    const id = Number(btn.dataset.gen);
    const scene = state.scenes.find((s) => Number(s.id) === id);
    const card = document.querySelector(`[data-scene-id="${id}"]`);
    const target = card ? readSceneTarget(card) : sceneTarget(scene || {});
    if (target === "leonardo") {
      btn.disabled = state.leoBusy;
      btn.textContent = "Сгенерировать кадр";
    } else {
      btn.disabled = state.videoBusy || !scene?.ref;
      btn.textContent = "Сгенерировать видео";
    }
  });
  document.querySelectorAll("[data-gen-x5], [data-gen-x10]").forEach((btn) => {
    const id = Number(btn.dataset.genX5 || btn.dataset.genX10);
    const scene = state.scenes.find((s) => Number(s.id) === id);
    btn.disabled = state.videoBusy || !scene?.ref;
  });
  document.querySelectorAll("[data-leo-ref]").forEach((btn) => {
    btn.disabled = state.leoBusy;
  });
}

async function refreshJob() {
  const job = await getJson("/api/job");
  updateFinal(job);
  syncSceneMediaFromJob(job);
  if (job.project_id) {
    state.projectId = job.project_id;
    $("projectHint").textContent = `Папка: output/projects/${job.project_id}/`;
  }
  const videoBusy = Boolean(job.video_busy ?? job.jobs?.video?.status === "running");
  const leoBusy = Boolean(job.leo_busy ?? job.jobs?.leonardo?.status === "running");
  const videoSceneId = job.jobs?.video?.scene_id ?? (videoBusy ? job.scene_id : null);
  setGenerateBusy({ videoBusy, leoBusy, videoSceneId });
  if (state.splitBusy) return job;
  if (job.status === "running") setStatus(job.message || "Работает…", false, true);
  else if (job.status === "error") setStatus(job.error || job.message || "Ошибка", true);
  else if (job.message) setStatus(job.message);
  return job;
}

async function newProject() {
  saveDraftNow();
  const data = await getJson("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script: $("script").value, title: "" }),
  });
  renderProjects(data.projects, data.current);
  state.scenes = [];
  state.character_bible = "";
  state.characters = [];
  state.world_bible = "";
  state.title = "";
  if ($("worldBible")) $("worldBible").value = "";
  renderBibleEditor([]);
  renderScenes();
  updateFinal({ clips: [], final: false });
  setStatus(`Новый проект ${data.current}`);
}

async function selectProject() {
  const id = $("projectSelect").value;
  if (!id) return;
  await getJson("/api/projects/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  await loadState();
  setStatus(`Открыт проект ${id}`);
}

async function saveTemplateFromFirst() {
  const name = $("templateName").value.trim();
  if (!name) {
    setStatus("Укажите имя шаблона", true);
    return;
  }
  const scenes = collectScenes();
  if (!scenes.length) {
    setStatus("Нет сцен для шаблона", true);
    return;
  }
  const data = await getJson("/api/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, shot: scenes[0].shot || {} }),
  });
  renderTemplates(data.templates);
  $("templateSelect").value = data.name;
  setStatus(`Шаблон сохранён: ${data.name}`);
}

async function applySelectedTemplate() {
  const name = $("templateSelect").value;
  if (!name) {
    setStatus("Выберите шаблон", true);
    return;
  }
  const tpl = state.templates.find((t) => t.name === name);
  if (!tpl?.shot) {
    setStatus("Шаблон пуст", true);
    return;
  }
  applyShotToAll(tpl.shot);
  setStatus(`Шаблон «${name}» применён ко всем сценам`);
}

async function deleteSelectedTemplate() {
  const name = $("templateSelect").value;
  if (!name) return;
  const res = await fetch(`/api/templates/${encodeURIComponent(name)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.error || res.statusText, true);
    return;
  }
  const list = await getJson("/api/templates");
  renderTemplates(list.templates);
  setStatus(`Шаблон удалён: ${name}`);
}

async function splitScript() {
  saveDraftNow();
  state.splitBusy = true;
  setStatus("Ollama: qwen переводит и расширяет → dolphin режет на сцены…", false, true);
  setSplitBusy(true);
  try {
    const data = await getJson("/api/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script: $("script").value,
        scenes: Number($("sceneCount").value),
      }),
    });
    if (data.project_id) {
      state.projectId = data.project_id;
      $("projectHint").textContent = `Папка: output/projects/${data.project_id}/`;
    }
    state.title = data.title || "";
    state.character_bible = data.character_bible || "";
    state.world_bible = data.world_bible || "";
    if ($("worldBible")) $("worldBible").value = state.world_bible;
    await loadCharacterBible(data.character_bible || "", data.characters);
    state.scenes = (data.scenes || []).map((scene) => ({
      ...scene,
      shot: scene.shot || { camera: "", lighting: "", fov: "", themes: [] },
      target: sceneTarget(scene),
      leonardo_model: scene.leonardo_model || "",
      ref: scene.ref || null,
      clip: scene.clip || null,
      clips: scene.clips || [],
      selected_clip: scene.selected_clip || null,
    }));
    renderScenes();
    const projects = await getJson("/api/projects");
    renderProjects(projects.projects, projects.current);
    const pipeline = [data.expand_model, data.split_model].filter(Boolean).join(" → ");
    setStatus(
      `Готово: ${state.scenes.length} сцен${pipeline ? ` (${pipeline})` : ""} — рисую референсы Leonardo…`,
      false,
      true
    );
    await generateLeonardoRefs(null);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    state.splitBusy = false;
    setSplitBusy(false);
  }
}

async function generateLeonardoRefs(sceneId) {
  const scenes = collectScenes().map((scene, index) => {
    const sid = Number(scene.id) || index + 1;
    return {
      ...scene,
      id: sid,
      filename: scene.filename || `scene-${String(sid).padStart(2, "0")}`,
    };
  });
  state.scenes = scenes;
  saveDraftNow();
  const wan = collectWanSettings();
  const scene = sceneId != null ? scenes.find((s) => Number(s.id) === Number(sceneId)) : null;
  const model = (scene && scene.leonardo_model) || $("leoModel")?.value || "";
  setGenerateBusy({ leoBusy: true });
  setStatus(
    sceneId
      ? `Leonardo рисует сцену ${sceneId}…`
      : `Leonardo рисует референсы (${scenes.length} сцен)…`,
    false,
    true
  );
  try {
    await getJson("/api/leonardo/refs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenes,
          scene_id: sceneId ?? null,
        character_bible: collectCharacterBible().character_bible || "",
        characters: state.characters,
        world_bible: ($("worldBible")?.value || state.world_bible || "").trim(),
        title: state.title || "",
        wan,
        model,
      }),
    });
  } catch (err) {
    setGenerateBusy({ leoBusy: false });
    setStatus(err.message, true);
  }
}

async function generate(sceneId, repeats = 1) {
  const scenes = collectScenes();
  state.scenes = scenes;
  saveDraftNow();

  const batch =
    sceneId != null ? scenes.filter((s) => Number(s.id) === Number(sceneId)) : scenes;
  if (!batch.length) {
    setStatus("Нет сцен для генерации", true);
    return;
  }

  const comfy = batch.filter((s) => sceneTarget(s) === "comfyui");
  const leo = batch.filter((s) => sceneTarget(s) === "leonardo");

  if (comfy.length) {
    const missing = comfy.filter((s) => !s.ref);
    if (missing.length) {
      setStatus(
        "Для ComfyUI нужен референс. Сначала «Все референсы» / «Перегенерить», либо смените цель на Leonardo.",
        true
      );
      return;
    }
  }

  const wan = collectWanSettings();
  const jobs = [];
  const takeCount = Math.max(1, Math.min(Number(repeats) || 1, 20));

  if (comfy.length) {
    setGenerateBusy({ videoBusy: true, videoSceneId: sceneId ?? null });
    setStatus(
      takeCount > 1
        ? sceneId != null
          ? `ComfyUI: сцена ${comfy[0].id} — ${takeCount} takes подряд…`
          : `ComfyUI: ${comfy.length} сцен × ${takeCount} takes…`
        : comfy.length === 1
          ? `ComfyUI: видео сцены ${comfy[0].id}…`
          : `ComfyUI: ${comfy.length} сцен… (${wan.width}×${wan.height})`,
      false,
      true
    );
    jobs.push(
      getJson("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenes,
          scene_id: sceneId != null && comfy.length === 1 ? comfy[0].id : null,
          repeats: takeCount,
          character_bible: collectCharacterBible().character_bible || "",
          characters: state.characters,
          world_bible: ($("worldBible")?.value || state.world_bible || "").trim(),
          title: state.title || "",
          wan,
        }),
      }).catch((err) => {
        setGenerateBusy({ videoBusy: false, videoSceneId: null });
        throw err;
      })
    );
  }

  if (leo.length) {
    setGenerateBusy({ leoBusy: true });
    const model =
      (leo.length === 1 && leo[0].leonardo_model) || $("leoModel")?.value || "";
    setStatus(
      leo.length === 1
        ? `Leonardo API: кадр сцены ${leo[0].id}…`
        : `Leonardo API: ${leo.length} кадров…`,
      false,
      true
    );
    jobs.push(
      getJson("/api/leonardo/refs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenes,
          scene_id: sceneId != null && leo.length === 1 ? leo[0].id : null,
          only_ids: sceneId == null ? leo.map((s) => Number(s.id)) : undefined,
          character_bible: collectCharacterBible().character_bible || "",
          characters: state.characters,
          world_bible: ($("worldBible")?.value || state.world_bible || "").trim(),
          title: state.title || "",
          wan,
          model,
        }),
      }).catch((err) => {
        setGenerateBusy({ leoBusy: false });
        throw err;
      })
    );
  }

  try {
    await Promise.all(jobs);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function selectClip(name) {
  if (!name) return;
  try {
    const data = await getJson("/api/project/clip/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const stem = sceneStemFromClip(name);
    const scene = state.scenes.find(
      (s) => (s.filename || `scene-${String(s.id).padStart(2, "0")}`).toLowerCase() === stem
    );
    if (scene) {
      scene.clips = data.variants || [];
      scene.selected_clip = data.selected || name;
      scene.clip = data.primary || name;
      scene.clipBust = Date.now();
      renderScenes();
    }
    setStatus(`Выбран клип ${name}`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteClip(name) {
  if (!name) return;
  if (!confirm(`Удалить видео ${name}?`)) return;
  try {
    const data = await getJson("/api/project/clip", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const stem = sceneStemFromClip(name);
    const scene = state.scenes.find(
      (s) => (s.filename || `scene-${String(s.id).padStart(2, "0")}`).toLowerCase() === stem
    );
    if (scene) {
      scene.clips = data.variants || [];
      const selected = (scene.clips || []).find((c) => c.selected);
      scene.selected_clip = selected?.name || null;
      scene.clip = scene.clips.length ? `${stem}.webm` : null;
      if (scene.clips.length && !selected) {
        scene.selected_clip = scene.clips[scene.clips.length - 1].name;
      }
      scene.clipBust = Date.now();
      renderScenes();
    }
    setStatus((data.variants || []).length ? `Удалён ${name}` : `Удалён ${name}, клипов больше нет`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function cancelGenerate() {
  setStatus("Прерывание…", false, true);
  try {
    await getJson("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: "all" }),
    });
  } catch (err) {
    // Old server without /api/cancel — try force reset, then unlock UI locally.
    try {
      await getJson("/api/job/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch {
      setGenerateBusy({ videoBusy: false, leoBusy: false, videoSceneId: null });
      setStatus(err.message + " — перезапустите UI-сервер (python ui/server.py)", true);
      return;
    }
  }
}

async function stitch() {
  setGenerateBusy({ videoBusy: true, videoSceneId: null });
  setStatus("Склейка…", false, true);
  try {
    await getJson("/api/stitch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch (err) {
    setGenerateBusy({ videoBusy: false });
    setStatus(err.message, true);
  }
}

async function loadState() {
  const snap = await getJson("/api/state");
  renderProjects(snap.projects || [], snap.project_id || snap.meta?.id || null);
  renderTemplates(snap.templates || []);
  if (snap.meta?.script && !$("script").value) {
    $("script").value = snap.meta.script;
  }
  if (snap.project?.scenes) {
    state.title = snap.project.title || snap.meta?.title || "";
    state.character_bible = snap.project.character_bible || "";
    state.world_bible = snap.project.world_bible || "";
    if ($("worldBible")) $("worldBible").value = state.world_bible;
    await loadCharacterBible(snap.project.character_bible || "", snap.project.characters);
    state.scenes = snap.project.scenes.map((scene) => ({
      ...scene,
      shot: scene.shot || { camera: "", lighting: "", fov: "", themes: [] },
      target: sceneTarget(scene),
      leonardo_model: scene.leonardo_model || "",
      ref: scene.ref || null,
      clip: scene.clip || null,
      clips: scene.clips || [],
      selected_clip: scene.selected_clip || null,
    }));
    if (snap.project.wan) {
      applyWanToForm(snap.project.wan);
      state.wanDefaultsApplied = true;
    }
    renderScenes();
  } else {
    state.scenes = [];
    state.characters = [];
    state.character_bible = "";
    state.world_bible = "";
    if ($("worldBible")) $("worldBible").value = "";
    renderBibleEditor([]);
    renderScenes();
  }
  updateFinal(snap);
}

async function boot() {
  applyDraft(loadDraft());
  try {
    renderHealth(await getJson("/api/health"));
  } catch {
    renderHealth({ ollama: false, comfyui: false, ffmpeg: false, ollama_models: [] });
  }
  try {
    await loadState();
  } catch {
    renderScenes();
  }
}

$("script").addEventListener("input", scheduleSaveDraft);
$("sceneCount").addEventListener("change", saveDraftNow);
$("wanPreset").addEventListener("change", onWanPresetChange);
$("worldBible")?.addEventListener("input", () => {
  state.world_bible = $("worldBible").value;
});
$("bibleAddBtn")?.addEventListener("click", () => {
  collectCharacterBible();
  state.characters.push({
    id: newCharId(),
    label: `character ${state.characters.length + 1}`,
    text: "",
  });
  renderBibleEditor(state.characters);
  const wrap = $("bibleWrap");
  if (wrap) wrap.hidden = false;
});
$("bibleSaveBtn")?.addEventListener("click", () => {
  saveCharacterBible().catch((e) => setStatus(e.message, true));
});
["wanWidth", "wanHeight", "wanSteps", "wanCfg", "wanLength", "wanShift", "wanSampler", "wanScheduler", "wanDenoise", "wanFps", "wanSeed", "wanUnet", "wanClip", "wanVae", "wanDtype"].forEach((id) => {
  const el = $(id);
  if (!el) return;
  el.addEventListener("change", () => {
    if (id === "wanWidth" || id === "wanHeight") syncPresetFromWh();
    saveDraftNow();
  });
});

// Initial select population before health returns
fillWanOptionSelects(WAN_OPTIONS_FALLBACK, WAN_OPTIONS_FALLBACK.defaults);

$("newProjectBtn").addEventListener("click", () => newProject().catch((e) => setStatus(e.message, true)));
$("projectSelect").addEventListener("change", () => selectProject().catch((e) => setStatus(e.message, true)));
$("saveTemplateBtn").addEventListener("click", () => saveTemplateFromFirst().catch((e) => setStatus(e.message, true)));
$("applyTemplateBtn").addEventListener("click", () => applySelectedTemplate().catch((e) => setStatus(e.message, true)));
$("deleteTemplateBtn").addEventListener("click", () => deleteSelectedTemplate().catch((e) => setStatus(e.message, true)));

$("splitBtn").addEventListener("click", splitScript);
$("leoAllBtn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  generateLeonardoRefs(null);
});
$("genAllBtn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  generate(null);
});
$("genAllX5Btn")?.addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  generate(null, 5);
});
$("genAllX10Btn")?.addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  generate(null, 10);
});
$("cancelGenBtn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  cancelGenerate();
});
$("stitchBtn").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  stitch();
});
$("scenesSummaryActions")?.addEventListener("click", (ev) => ev.stopPropagation());
$("leoModel")?.addEventListener("change", saveDraftNow);

document.addEventListener("click", onLightboxTriggerClick);
$("lightboxClose")?.addEventListener("click", closeLightbox);
document.querySelector(".lightbox-backdrop")?.addEventListener("click", closeLightbox);
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeLightbox();
});

boot();
setInterval(async () => {
  try {
    renderHealth(await getJson("/api/health"));
    await refreshJob();
  } catch {
    /* keep last UI */
  }
}, 2000);
