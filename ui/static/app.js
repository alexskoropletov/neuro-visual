const state = {
  title: "",
  character_bible: "",
  scenes: [],
};

const $ = (id) => document.getElementById(id);

async function getJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || res.statusText);
  }
  return data;
}

function setStatus(text, isError) {
  const el = $("status");
  if (!text) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("error", Boolean(isError));
}

function renderHealth(health) {
  for (const li of document.querySelectorAll("#health li")) {
    const ok = health[li.dataset.key];
    li.classList.toggle("ok", ok === true);
    li.classList.toggle("bad", ok === false);
  }
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
    };
  });
}

function renderScenes() {
  const bible = $("bible");
  if (state.character_bible) {
    bible.hidden = false;
    bible.textContent = state.character_bible;
  } else {
    bible.hidden = true;
  }

  const root = $("scenes");
  if (!state.scenes.length) {
    root.innerHTML = '<p class="empty">Сначала разрежьте сценарий. Можно править промпты перед генерацией.</p>';
    $("genAllBtn").disabled = true;
    return;
  }

  $("genAllBtn").disabled = false;
  root.innerHTML = state.scenes
    .map(
      (scene) => `
      <article class="scene" data-scene-id="${scene.id}">
        <header>
          <h3>${scene.filename || "scene-" + String(scene.id).padStart(2, "0")}</h3>
          <button type="button" class="ghost" data-gen="${scene.id}">Только эту</button>
        </header>
        <label>Visual prompt
          <textarea class="visual" rows="4">${escapeHtml(scene.visual_prompt || "")}</textarea>
        </label>
        <label>Negative
          <textarea class="negative" rows="2">${escapeHtml(scene.negative_prompt || "")}</textarea>
        </label>
      </article>`
    )
    .join("");

  root.querySelectorAll("[data-gen]").forEach((btn) => {
    btn.addEventListener("click", () => generate(Number(btn.dataset.gen)));
  });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderClips(job) {
  const root = $("clips");
  const clips = job.clips || [];
  $("stitchBtn").disabled = clips.length === 0;
  if (!clips.length) {
    root.innerHTML = '<p class="empty">Клипов пока нет. Нужны Ollama и ComfyUI.</p>';
  } else {
    root.innerHTML = clips
      .map((clip) => `<a href="/api/media/scene/${encodeURIComponent(clip.name)}" target="_blank">${clip.name}</a>`)
      .join("");
  }
  const player = $("player");
  if (job.final) {
    player.hidden = false;
    player.src = `/api/media/final.mp4?t=${Date.now()}`;
  }
}

async function refreshJob() {
  const job = await getJson("/api/job");
  renderClips(job);
  if (job.status === "running") {
    setStatus(job.message || "Работает…");
  } else if (job.status === "error") {
    setStatus(job.error || job.message || "Ошибка", true);
  } else if (job.message) {
    setStatus(job.message);
  }
  return job;
}

async function splitScript() {
  setStatus("Нарезка через Ollama…");
  $("splitBtn").disabled = true;
  try {
    const data = await getJson("/api/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script: $("script").value,
        scenes: Number($("sceneCount").value),
        model: $("model").value.trim() || "dolphin-llama3",
      }),
    });
    state.title = data.title || "";
    state.character_bible = data.character_bible || "";
    state.scenes = data.scenes || [];
    renderScenes();
    setStatus(`Готово: ${state.scenes.length} сцен${state.title ? " · " + state.title : ""}`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    $("splitBtn").disabled = false;
  }
}

async function generate(sceneId) {
  const scenes = collectScenes();
  state.scenes = scenes;
  setStatus(sceneId ? `Очередь сцены ${sceneId}…` : "Очередь всех сцен…");
  try {
    await getJson("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenes, scene_id: sceneId ?? null }),
    });
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function stitch() {
  setStatus("Склейка…");
  try {
    await getJson("/api/stitch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function boot() {
  try {
    renderHealth(await getJson("/api/health"));
  } catch {
    renderHealth({ ollama: false, comfyui: false, ffmpeg: false });
  }
  try {
    const snap = await getJson("/api/state");
    if (snap.project?.scenes) {
      state.title = snap.project.title || "";
      state.character_bible = snap.project.character_bible || "";
      state.scenes = snap.project.scenes;
      renderScenes();
    }
    renderClips(snap);
  } catch {
    renderScenes();
  }
}

$("splitBtn").addEventListener("click", splitScript);
$("genAllBtn").addEventListener("click", () => generate(null));
$("stitchBtn").addEventListener("click", stitch);

boot();
setInterval(async () => {
  try {
    renderHealth(await getJson("/api/health"));
    await refreshJob();
  } catch {
    /* keep last UI */
  }
}, 2000);
