"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  config: { max_file_mb: 10, max_side: 2000, scales: [2, 4], retention_hours: 24 },
  file: null,
  dims: null,
  jobId: null,
  pollTimer: null,
};

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];

const STATUS_TEXT = {
  uploading: "Загрузка файла…",
  queued: "В очереди",
  processing: "Обработка",
};

const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* приватный режим */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* приватный режим */ } },
};

function formatBytes(n) {
  return n < 1024 * 1024 ? `${Math.round(n / 1024)} КБ` : `${(n / 1024 / 1024).toFixed(1)} МБ`;
}

function selectedScale() {
  return Number(document.querySelector("input[name=scale]:checked").value);
}

function show(section) {
  for (const id of ["upload-card", "progress-card", "result-card"]) $(id).hidden = id !== section;
}

function showError(message) {
  $("upload-error").textContent = message;
  $("upload-error").hidden = !message;
}

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.detail || `Ошибка сервера (${res.status})`);
  return body;
}

// --- выбор файла (FR-001) ---

function updateOutSize() {
  if (!state.dims) { $("out-size").textContent = ""; return; }
  const k = selectedScale();
  $("out-size").textContent = `Результат: ${state.dims.w * k}×${state.dims.h * k} px`;
}

function resetFile() {
  state.file = null;
  state.dims = null;
  $("file-input").value = "";
  $("drop-empty").hidden = false;
  $("drop-file").hidden = true;
  $("start-btn").disabled = true;
  updateOutSize();
}

function pickFile(file) {
  showError("");
  if (!file) return;
  const { max_file_mb, max_side } = state.config;
  if (!ALLOWED_TYPES.includes(file.type)) {
    showError("Неподдерживаемый формат. Допустимы JPG, PNG и WEBP.");
    return;
  }
  if (file.size > max_file_mb * 1024 * 1024) {
    showError(`Файл больше ${max_file_mb} МБ.`);
    return;
  }
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    const w = img.naturalWidth, h = img.naturalHeight;
    if (w > max_side || h > max_side) {
      URL.revokeObjectURL(url);
      showError(`Разрешение ${w}×${h} px больше допустимого ${max_side}×${max_side} px.`);
      return;
    }
    state.file = file;
    state.dims = { w, h };
    $("thumb").src = url;
    $("file-name").textContent = file.name;
    $("file-meta").textContent = `${w}×${h} px · ${formatBytes(file.size)}`;
    $("drop-empty").hidden = true;
    $("drop-file").hidden = false;
    $("start-btn").disabled = false;
    updateOutSize();
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    showError("Файл повреждён или не является изображением.");
  };
  img.src = url;
}

function initUpload() {
  const zone = $("dropzone");
  const input = $("file-input");
  input.addEventListener("change", () => pickFile(input.files[0]));
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  $("change-file").addEventListener("click", (e) => { e.preventDefault(); input.click(); });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add("is-over"); });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.remove("is-over"); });
  }
  zone.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));
  for (const radio of document.querySelectorAll("input[name=scale]")) {
    radio.addEventListener("change", updateOutSize);
  }
  $("start-btn").addEventListener("click", startJob);
}

// --- задача и прогресс (FR-004, FR-005) ---

function setProgress(title, fraction, note) {
  const bar = $("progress-bar");
  const waiting = fraction === null;
  $("progress-title").textContent = title;
  $("progress-value").textContent = waiting ? "" : `${Math.round(fraction * 100)}%`;
  bar.parentElement.classList.toggle("is-waiting", waiting);
  bar.parentElement.setAttribute("aria-valuenow", waiting ? 0 : Math.round(fraction * 100));
  bar.style.width = waiting ? "" : `${fraction * 100}%`;
  if (note) $("progress-note").textContent = note;
}

async function startJob() {
  if (!state.file) return;
  $("start-btn").disabled = true;
  showError("");
  show("progress-card");
  setProgress(STATUS_TEXT.uploading, null);
  const form = new FormData();
  form.append("file", state.file);
  form.append("scale", String(selectedScale()));
  try {
    const { id } = await api("/api/jobs", { method: "POST", body: form });
    track(id);
  } catch (err) {
    show("upload-card");
    $("start-btn").disabled = false;
    showError(err.message);
  }
}

function track(jobId, { restoring = false } = {}) {
  state.jobId = jobId;
  storage.set("lastJob", jobId);
  clearTimeout(state.pollTimer);
  poll(restoring);
}

async function poll(restoring = false) {
  let job;
  try {
    job = await api(`/api/jobs/${state.jobId}`);
  } catch (err) {
    storage.remove("lastJob");
    show("upload-card");
    $("start-btn").disabled = !state.file;
    // При восстановлении после перезагрузки задача могла быть уже удалена — это не ошибка
    if (!restoring) showError(err.message);
    return;
  }
  if (job.status === "queued") {
    const pos = job.position ? `: позиция ${job.position}` : "";
    setProgress(`${STATUS_TEXT.queued}${pos}`, null,
      "Задачи обрабатываются по одной. Страницу можно не закрывать — результат появится здесь.");
  } else if (job.status === "processing") {
    setProgress(STATUS_TEXT.processing, job.progress,
      "Модель обрабатывает изображение по фрагментам.");
  } else if (job.status === "done") {
    renderResult(job);
    return;
  } else {
    storage.remove("lastJob");
    show("upload-card");
    $("start-btn").disabled = !state.file;
    showError(job.error || "Не удалось обработать изображение.");
    return;
  }
  show("progress-card");
  state.pollTimer = setTimeout(poll, 1000);
}

// --- результат (FR-004, FR-006, FR-009) ---

function stat(label, value) {
  const div = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = label;
  dd.textContent = value;
  div.append(dt, dd);
  return div;
}

function renderResult(job) {
  const res = job.result;
  $("img-before").src = job.files.before;
  $("img-after").src = job.files.after;
  $("img-diff").src = job.files.diff;
  $("download-btn").href = job.files.download;
  $("face-flag").hidden = !res.flag_face_zone;

  $("stats").replaceChildren(
    stat("Размер", `${res.width}×${res.height} → ${res.out_width}×${res.out_height}`),
    stat("Увеличение", `×${job.scale}`),
    stat("Время обработки", `${res.total_seconds.toFixed(1)} с`),
    stat("Индекс достройки", `${(res.detail_index * 100).toFixed(1)}%`),
    stat("BRISQUE (меньше — лучше)", res.brisque.toFixed(1)),
    stat("Лиц обнаружено", String(res.faces)),
  );

  const notices = [...res.warnings].map((text) => ({ text, cls: "" }));
  notices.push({
    cls: "info",
    text: "Результат сгенерирован нейросетью и может содержать детали, которых не было в оригинале. " +
      "Модель хуже работает с мелким текстом, документами, сканами и медицинскими снимками.",
  });
  $("notices").replaceChildren(...notices.map(({ text, cls }) => {
    const li = document.createElement("li");
    li.textContent = text;
    if (cls) li.className = cls;
    return li;
  }));

  const expires = new Date(job.expires_at * 1000);
  $("expires").textContent = `Файлы будут удалены автоматически ${expires.toLocaleString("ru-RU")}.`;
  setView("compare");
  setCompare(50);
  show("result-card");
}

function setView(view) {
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.view === view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  $("view-compare").hidden = view !== "compare";
  $("view-diff").hidden = view !== "diff";
}

function setCompare(pos) {
  $("compare").style.setProperty("--pos", `${pos}%`);
  $("compare-range").value = pos;
}

function newImage() {
  clearTimeout(state.pollTimer);
  state.jobId = null;
  storage.remove("lastJob");
  resetFile();
  showError("");
  show("upload-card");
}

async function deleteJob() {
  if (!state.jobId || !confirm("Удалить исходное изображение и результат с сервера?")) return;
  try {
    await api(`/api/jobs/${state.jobId}`, { method: "DELETE" });
    newImage();
  } catch (err) {
    alert(err.message);
  }
}

function initResult() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  }
  $("compare-range").addEventListener("input", (e) => setCompare(Number(e.target.value)));
  $("new-btn").addEventListener("click", newImage);
  $("delete-btn").addEventListener("click", deleteJob);
}

// --- запуск ---

async function init() {
  initUpload();
  initResult();
  try {
    state.config = await api("/api/config");
    const { formats, max_file_mb, max_side, retention_hours } = state.config;
    $("limits-hint").textContent = `${formats.join(", ")} · до ${max_file_mb} МБ · до ${max_side}×${max_side} px`;
    for (const el of document.querySelectorAll("[data-retention]")) el.textContent = String(retention_hours);
  } catch { /* остаются значения по умолчанию */ }

  // Восстановление после перезагрузки страницы: активная задача или последний результат
  try {
    const { active_job } = await api("/api/session");
    const jobId = active_job || storage.get("lastJob");
    if (jobId) track(jobId, { restoring: true });
  } catch { /* сервис недоступен — останется форма загрузки */ }
}

init();
