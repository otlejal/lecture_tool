const $ = (id) => document.getElementById(id);

const state = {
  lectureId: null,
  recordingId: null,
  recorder: null,
  stream: null,
  timer: null,
  seconds: 0,
  chain: Promise.resolve(),
  settings: null,
  settingsLoaded: false,
  sumIsHtml: false,
  slidesIsHtml: false,
  quizIsHtml: false,
};

// ---------- общие помощники ----------

function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (node.className = "toast"), 4000);
}

async function api(path, options = {}) {
  const resp = await fetch("/api" + path, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || resp.statusText));
  }
  return data;
}

const postJson = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const putJson = (path, body) =>
  api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function fileUrl(name) {
  // Параметр t сбрасывает кэш браузера, чтобы после перегенерации показывалась новая версия
  return `/api/lectures/${state.lectureId}/files/${name}?t=${Date.now()}`;
}

function setLink(id, exists, name) {
  const link = $(id);
  link.hidden = !exists;
  link.href = exists ? fileUrl(name) : "#";
}

async function waitJob(jobId, statusEl) {
  for (;;) {
    const job = await api(`/jobs/${jobId}`);
    statusEl.textContent = job.message;
    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error);
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function runJob(button, statusEl, startFn, onDone) {
  button.disabled = true;
  statusEl.textContent = "Запуск...";
  try {
    const { job_id } = await startFn();
    await waitJob(job_id, statusEl);
    toast("Готово");
    if (onDone) await onDone();
  } catch (err) {
    statusEl.textContent = err.message;
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

// ---------- вкладки ----------

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + btn.dataset.tab));
    if (btn.dataset.tab === "settings") loadSettings().catch((e) => toast(e.message, true));
  });
});

// ---------- лекции ----------

function describeAsr() {
  const t = state.settings && state.settings.transcription;
  if (!t) return "-";
  return t.backend === "local"
    ? `локально, whisper «${t.local.model}», ${t.local.device}`
    : `удалённо, ${t.remote.base_url}`;
}

function lectureLabel(l) {
  const topic = l.title && l.title !== l.id ? l.title : l.id;
  return `${l.group || "Без группы"} / ${l.subject || "Без предмета"} — ${topic}`;
}

async function loadLectures(preferId) {
  const list = await api("/lectures");
  const select = $("lecture-select");
  select.innerHTML = "";
  const groups = new Set();
  const subjects = new Set();
  for (const lecture of list) {
    const option = document.createElement("option");
    option.value = lecture.id;
    option.textContent = lectureLabel(lecture);
    select.append(option);
    if (lecture.group) groups.add(lecture.group);
    if (lecture.subject) subjects.add(lecture.subject);
  }
  // Подсказки для полей Группа/Предмет из уже используемых значений
  for (const [listId, values] of [["group-list", groups], ["subject-list", subjects]]) {
    const datalist = $(listId);
    datalist.innerHTML = "";
    for (const value of values) {
      const option = document.createElement("option");
      option.value = value;
      datalist.append(option);
    }
  }
  const wanted = [preferId, localStorage.getItem("lectureId")].find((id) => id && list.some((l) => l.id === id));
  if (wanted) select.value = wanted;
  state.lectureId = select.value || null;
  if (state.lectureId) localStorage.setItem("lectureId", state.lectureId);
  await refreshLecture();
}

async function refreshLecture() {
  const hasLecture = Boolean(state.lectureId);
  for (const id of ["rec-start", "upload-audio", "save-transcript", "sum-run", "sum-rerender",
                    "sum-tpl-save", "slides-run", "slides-rerender", "slides-tpl-save",
                    "quiz-run", "quiz-rerender", "delete-lecture"]) {
    $(id).disabled = !hasLecture;
  }
  $("asr-mode").textContent = describeAsr();
  if (!hasLecture) {
    $("transcribe").disabled = true;
    $("transcript").value = "";
    $("sum-preview").src = "about:blank";
    $("slides-preview").src = "about:blank";
    $("quiz-preview").src = "about:blank";
    return;
  }
  const info = await api(`/lectures/${state.lectureId}`);
  $("transcribe").disabled = !info.stages.audio;
  $("rec-status").textContent = info.stages.audio ? "Аудио в лекции есть" : "Аудио ещё не записано";
  $("transcript").value = (await api(`/lectures/${state.lectureId}/transcript`)).text;
  setLink("download-transcript", info.stages.transcript, "transcript.txt");
  setLink("sum-download", info.stages.summary, "summary.html");
  setLink("slides-download", info.stages.slides, "slides.html");
  setLink("quiz-download", info.stages.quiz, "quiz.html");
  $("sum-preview").src = info.stages.summary ? fileUrl("summary.html") : "about:blank";
  $("slides-preview").src = info.stages.slides ? fileUrl("slides.html") : "about:blank";
  $("quiz-preview").src = info.stages.quiz ? fileUrl("quiz.html") : "about:blank";
}

$("lecture-select").addEventListener("change", (e) => {
  state.lectureId = e.target.value || null;
  if (state.lectureId) localStorage.setItem("lectureId", state.lectureId);
  refreshLecture().catch((err) => toast(err.message, true));
});

$("new-lecture").addEventListener("click", async () => {
  try {
    const lecture = await postJson("/lectures", {
      title: $("new-title").value,
      group: $("new-group").value,
      subject: $("new-subject").value,
    });
    $("new-title").value = ""; // группу и предмет оставляем — обычно пары идут подряд
    await loadLectures(lecture.id);
    toast("Тема создана");
  } catch (err) {
    toast(err.message, true);
  }
});

$("delete-lecture").addEventListener("click", async () => {
  if (!state.lectureId) return;
  const title = $("lecture-select").selectedOptions[0]?.textContent || state.lectureId;
  if (!confirm(`Удалить тему «${title}» со всеми файлами (аудио, конспект, презентация, тест)?`)) return;
  try {
    await api(`/lectures/${state.lectureId}`, { method: "DELETE" });
    localStorage.removeItem("lectureId");
    state.lectureId = null;
    await loadLectures();
    toast("Тема удалена");
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------- блок 1: запись ----------

function setRecButtons(mode) {
  $("rec-start").disabled = mode !== "idle";
  $("rec-pause").disabled = mode === "idle";
  $("rec-pause").textContent = mode === "paused" ? "Продолжить" : "Пауза";
  $("rec-stop").disabled = mode === "idle";
}

function renderTimer() {
  const pad = (n) => String(n).padStart(2, "0");
  const s = state.seconds;
  $("rec-timer").textContent = `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

async function uploadChunk(blob, isFirst) {
  const resp = await fetch(`/api/lectures/${state.recordingId}/audio/chunk?first=${isFirst}`, {
    method: "POST",
    body: blob,
  });
  if (!resp.ok) throw new Error("Не удалось сохранить фрагмент записи на сервер");
}

async function finishRecording() {
  clearInterval(state.timer);
  state.stream.getTracks().forEach((track) => track.stop());
  await state.chain; // дождаться отправки последних фрагментов
  state.recorder = null;
  setRecButtons("idle");
  $("rec-status").textContent = "Запись сохранена. Можно распознать текст.";
  if (state.recordingId === state.lectureId) await refreshLecture();
  toast("Запись сохранена");
}

$("rec-start").addEventListener("click", async () => {
  if (!state.lectureId) return toast("Сначала создайте тему", true);
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    return toast("Нет доступа к микрофону: " + err.message, true);
  }
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? { mimeType: "audio/webm;codecs=opus" } : {};
  state.recorder = new MediaRecorder(state.stream, mime);
  state.recordingId = state.lectureId;
  state.chain = Promise.resolve();
  state.seconds = 0;
  renderTimer();

  let first = true;
  state.recorder.ondataavailable = (e) => {
    if (!e.data || !e.data.size) return;
    const isFirst = first;
    first = false;
    state.chain = state.chain
      .then(() => uploadChunk(e.data, isFirst))
      .catch((err) => toast(err.message, true));
  };
  state.recorder.onstop = finishRecording;
  state.recorder.start(5000); // отправка каждые 5 секунд, чтобы при сбое не потерять всё

  state.timer = setInterval(() => {
    if (state.recorder && state.recorder.state === "recording") {
      state.seconds += 1;
      renderTimer();
    }
  }, 1000);
  setRecButtons("recording");
  $("rec-status").textContent = "Идёт запись...";
});

$("rec-pause").addEventListener("click", () => {
  const recorder = state.recorder;
  if (!recorder) return;
  if (recorder.state === "recording") {
    recorder.pause();
    setRecButtons("paused");
    $("rec-status").textContent = "Пауза";
  } else if (recorder.state === "paused") {
    recorder.resume();
    setRecButtons("recording");
    $("rec-status").textContent = "Идёт запись...";
  }
});

$("rec-stop").addEventListener("click", () => {
  if (state.recorder && state.recorder.state !== "inactive") state.recorder.stop();
});

$("upload-audio").addEventListener("click", async () => {
  const file = $("audio-file").files[0];
  if (!file) return toast("Выберите аудиофайл", true);
  const form = new FormData();
  form.append("file", file);
  try {
    await api(`/lectures/${state.lectureId}/audio`, { method: "POST", body: form });
    toast("Файл загружен");
    await refreshLecture();
  } catch (err) {
    toast(err.message, true);
  }
});

$("transcribe").addEventListener("click", () =>
  runJob($("transcribe"), $("transcribe-status"),
    () => api(`/lectures/${state.lectureId}/transcribe`, { method: "POST" }),
    refreshLecture));

$("save-transcript").addEventListener("click", async () => {
  try {
    await putJson(`/lectures/${state.lectureId}/transcript`, { text: $("transcript").value });
    toast("Текст сохранён");
    await refreshLecture();
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------- общие для блоков 2, 3 и 4 ----------

function bindTextFile(fileInput, textarea, setHtmlFlag) {
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      textarea.value = reader.result;
      setHtmlFlag(/\.html?$/i.test(file.name));
      toast(`Загружен файл ${file.name}`);
    };
    reader.readAsText(file, "utf-8");
  });
  // Если текст правят вручную, он уже не считается HTML-файлом
  textarea.addEventListener("input", () => setHtmlFlag(false));
}

function bindSourceSwitch(radioName, box) {
  document.querySelectorAll(`input[name="${radioName}"]`).forEach((radio) => {
    radio.addEventListener("change", () => {
      $(box).hidden = document.querySelector(`input[name="${radioName}"]:checked`).value === "lecture";
    });
  });
}

async function loadTemplates() {
  const selects = { summary: "sum-template", slides: "slides-template", quiz: "quiz-template" };
  for (const [kind, selectId] of Object.entries(selects)) {
    const { names } = await api(`/templates/${kind}`);
    const select = $(selectId);
    const previous = select.value;
    select.innerHTML = "";
    for (const name of names) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name === "default" ? "Стандартный" : name;
      select.append(option);
    }
    if (names.includes(previous)) select.value = previous;
  }
}

function bindTemplateUpload(kind, fileId, nameId, buttonId, selectId) {
  $(buttonId).addEventListener("click", async () => {
    const file = $(fileId).files[0];
    const name = $(nameId).value.trim();
    if (!file || !name) return toast("Выберите HTML-файл и укажите имя шаблона", true);
    try {
      const content = await file.text();
      await postJson(`/templates/${kind}`, { name, content });
      await loadTemplates();
      $(selectId).value = name;
      toast(`Шаблон «${name}» сохранён`);
    } catch (err) {
      toast(err.message, true);
    }
  });
}

// ---------- блок 2: конспект ----------

function summarySource() {
  const kind = document.querySelector('input[name="sum-source"]:checked').value;
  if (kind === "lecture") return { type: "transcript" };
  return { type: "text", text: $("sum-text").value, is_html: state.sumIsHtml };
}

bindSourceSwitch("sum-source", "sum-text-box");
bindTextFile($("sum-file"), $("sum-text"), (v) => (state.sumIsHtml = v));
bindTemplateUpload("summary", "sum-tpl-file", "sum-tpl-name", "sum-tpl-save", "sum-template");

$("sum-run").addEventListener("click", () =>
  runJob($("sum-run"), $("sum-status"), () => postJson(`/lectures/${state.lectureId}/summary`, {
    source: summarySource(),
    options: {
      length: $("sum-length").value,
      include_offtopic: $("sum-offtopic").checked,
      remove_filler: $("sum-filler").checked,
      include_examples: $("sum-examples").checked,
      extra_instructions: $("sum-extra").value,
    },
    template: $("sum-template").value,
    prompt_id: $("sum-prompt").value,
  }), refreshLecture));

$("sum-rerender").addEventListener("click", async () => {
  try {
    await postJson(`/lectures/${state.lectureId}/summary/render`, { template: $("sum-template").value });
    toast("Конспект перерисован");
    await refreshLecture();
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------- блок 3: презентация ----------

function slidesSource() {
  const kind = document.querySelector('input[name="slides-source"]:checked').value;
  if (kind === "lecture") return { type: "summary" };
  return { type: "text", text: $("slides-text").value, is_html: state.slidesIsHtml };
}

bindSourceSwitch("slides-source", "slides-text-box");
bindTextFile($("slides-file"), $("slides-text"), (v) => (state.slidesIsHtml = v));
bindTemplateUpload("slides", "slides-tpl-file", "slides-tpl-name", "slides-tpl-save", "slides-template");

$("slides-run").addEventListener("click", () =>
  runJob($("slides-run"), $("slides-status"), () => postJson(`/lectures/${state.lectureId}/slides`, {
    source: slidesSource(),
    options: {
      count: Number($("slides-count").value),
      theme: $("slides-theme").value,
      audience: $("slides-audience").value,
      extra_instructions: $("slides-extra").value,
    },
    template: $("slides-template").value,
    prompt_id: $("slides-prompt").value,
  }), refreshLecture));

$("slides-rerender").addEventListener("click", async () => {
  try {
    await postJson(`/lectures/${state.lectureId}/slides/render`, { template: $("slides-template").value });
    toast("Презентация перерисована");
    await refreshLecture();
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------- блок 4: тест ----------

function quizSource() {
  const kind = document.querySelector('input[name="quiz-source"]:checked').value;
  if (kind === "lecture") return { type: "summary" };
  return { type: "text", text: $("quiz-text").value, is_html: state.quizIsHtml };
}

bindSourceSwitch("quiz-source", "quiz-text-box");
bindTextFile($("quiz-file"), $("quiz-text"), (v) => (state.quizIsHtml = v));
bindTemplateUpload("quiz", "quiz-tpl-file", "quiz-tpl-name", "quiz-tpl-save", "quiz-template");

$("quiz-run").addEventListener("click", () => {
  const types = [...document.querySelectorAll(".quiz-type:checked")].map((c) => c.value);
  if (!types.length) return toast("Выберите хотя бы один тип вопросов", true);
  return runJob($("quiz-run"), $("quiz-status"), () => postJson(`/lectures/${state.lectureId}/quiz`, {
    source: quizSource(),
    options: {
      count: Number($("quiz-count").value),
      difficulty: $("quiz-difficulty").value,
      types,
    },
    template: $("quiz-template").value,
    prompt_id: $("quiz-prompt").value,
  }), refreshLecture);
});

$("quiz-rerender").addEventListener("click", async () => {
  try {
    await postJson(`/lectures/${state.lectureId}/quiz/render`, { template: $("quiz-template").value });
    toast("Тест перерисован");
    await refreshLecture();
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------- мастер-промпты (конспект и тест) ----------

const PROMPT_UI = {
  summary: {
    select: "sum-prompt", preview: "sum-prompt-preview", newBtn: "sum-prompt-new",
    editBtn: "sum-prompt-edit", deleteBtn: "sum-prompt-delete", editor: "sum-prompt-editor",
    name: "sum-prompt-name", template: "sum-prompt-template", cancel: "sum-prompt-cancel",
  },
  quiz: {
    select: "quiz-prompt", preview: "quiz-prompt-preview", newBtn: "quiz-prompt-new",
    editBtn: "quiz-prompt-edit", deleteBtn: "quiz-prompt-delete", editor: "quiz-prompt-editor",
    name: "quiz-prompt-name", template: "quiz-prompt-template", cancel: "quiz-prompt-cancel",
  },
  slides: {
    select: "slides-prompt", preview: "slides-prompt-preview", newBtn: "slides-prompt-new",
    editBtn: "slides-prompt-edit", deleteBtn: "slides-prompt-delete", editor: "slides-prompt-editor",
    name: "slides-prompt-name", template: "slides-prompt-template", cancel: "slides-prompt-cancel",
  },
};

state.prompts = { summary: [], quiz: [], slides: [] };
state.promptEdit = { summary: null, quiz: null, slides: null }; // id редактируемого промпта (null — новый)
state.defaultPrompts = { summary: "", quiz: "", slides: "" };
const previewTimers = { summary: null, quiz: null, slides: null };

function currentOptions(kind) {
  if (kind === "summary") {
    return {
      length: $("sum-length").value,
      include_offtopic: $("sum-offtopic").checked,
      remove_filler: $("sum-filler").checked,
      include_examples: $("sum-examples").checked,
      extra_instructions: $("sum-extra").value,
    };
  }
  if (kind === "slides") {
    return {
      count: Number($("slides-count").value),
      theme: $("slides-theme").value,
      audience: $("slides-audience").value,
      extra_instructions: $("slides-extra").value,
    };
  }
  return {
    count: Number($("quiz-count").value),
    difficulty: $("quiz-difficulty").value,
    types: [...document.querySelectorAll(".quiz-type:checked")].map((c) => c.value),
  };
}

function selectedPrompt(kind) {
  const id = $(PROMPT_UI[kind].select).value;
  return state.prompts[kind].find((p) => p.id === id) || null;
}

function schedulePromptPreview(kind) {
  clearTimeout(previewTimers[kind]);
  previewTimers[kind] = setTimeout(() => refreshPromptPreview(kind), 300);
}

async function refreshPromptPreview(kind) {
  const ui = PROMPT_UI[kind];
  const prompt = selectedPrompt(kind);
  try {
    const resp = await postJson("/prompt/preview", {
      kind,
      options: currentOptions(kind),
      template: prompt ? prompt.template : "",
    });
    $(ui.preview).textContent = resp.prompt;
  } catch (err) {
    $(ui.preview).textContent = "Ошибка предпросмотра: " + err.message;
  }
}

function updatePromptButtons(kind) {
  const ui = PROMPT_UI[kind];
  const custom = Boolean(selectedPrompt(kind));
  $(ui.editBtn).disabled = !custom;
  $(ui.deleteBtn).disabled = !custom;
}

async function loadPrompts(kind) {
  const ui = PROMPT_UI[kind];
  const resp = await api(`/prompts/${kind}`);
  state.prompts[kind] = resp.prompts;
  state.defaultPrompts[kind] = resp.default_template;
  const select = $(ui.select);
  select.innerHTML = "";
  const builtin = document.createElement("option");
  builtin.value = "";
  builtin.textContent = "Встроенный (правила из настроек)";
  select.append(builtin);
  for (const p of resp.prompts) {
    const option = document.createElement("option");
    option.value = p.id;
    option.textContent = p.name;
    select.append(option);
  }
  const saved = localStorage.getItem(`masterPrompt:${kind}`);
  if (saved && resp.prompts.some((p) => p.id === saved)) select.value = saved;
  updatePromptButtons(kind);
  refreshPromptPreview(kind);
}

function openPromptEditor(kind) {
  const ui = PROMPT_UI[kind];
  const prompt = selectedPrompt(kind);
  state.promptEdit[kind] = prompt ? prompt.id : null;
  $(ui.name).value = prompt ? prompt.name : "";
  $(ui.template).value = prompt ? prompt.template : state.defaultPrompts[kind];
  $(ui.editor).hidden = false;
  $(ui.editor).scrollIntoView({ block: "nearest" });
  $(ui.name).focus();
}

for (const kind of ["summary", "quiz", "slides"]) {
  const ui = PROMPT_UI[kind];

  $(ui.editor).addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $(ui.name).value.trim();
    const template = $(ui.template).value;
    if (!name) return toast("Введите название промпта", true);
    try {
      const editingId = state.promptEdit[kind];
      const saved = editingId
        ? (await putJson(`/prompts/${editingId}`, { name, template })).prompt
        : (await postJson(`/prompts/${kind}`, { name, template })).prompt;
      $(ui.editor).hidden = true;
      await loadPrompts(kind);
      $(ui.select).value = saved.id;
      localStorage.setItem(`masterPrompt:${kind}`, saved.id);
      updatePromptButtons(kind);
      refreshPromptPreview(kind);
      toast(`Мастер-промпт «${name}» сохранён`);
    } catch (err) {
      toast(err.message, true);
    }
  });

  $(ui.cancel).addEventListener("click", () => {
    $(ui.editor).hidden = true;
  });

  $(ui.select).addEventListener("change", () => {
    const id = $(ui.select).value;
    if (id) localStorage.setItem(`masterPrompt:${kind}`, id);
    else localStorage.removeItem(`masterPrompt:${kind}`);
    updatePromptButtons(kind);
    refreshPromptPreview(kind);
  });

  $(ui.newBtn).addEventListener("click", () => openPromptEditor(kind));
  $(ui.editBtn).addEventListener("click", () => openPromptEditor(kind));

  $(ui.deleteBtn).addEventListener("click", async () => {
    const prompt = selectedPrompt(kind);
    if (!prompt) return;
    if (!confirm(`Удалить мастер-промпт «${prompt.name}»?`)) return;
    try {
      await api(`/prompts/${prompt.id}`, { method: "DELETE" });
      localStorage.removeItem(`masterPrompt:${kind}`);
      await loadPrompts(kind);
      toast("Мастер-промпт удалён");
    } catch (err) {
      toast(err.message, true);
    }
  });
}

// Предпросмотр следует за изменениями настроек конспекта и теста
for (const id of ["sum-length", "sum-offtopic", "sum-filler", "sum-examples", "sum-extra"]) {
  $(id).addEventListener("input", () => schedulePromptPreview("summary"));
  $(id).addEventListener("change", () => schedulePromptPreview("summary"));
}
for (const id of ["quiz-count", "quiz-difficulty"]) {
  $(id).addEventListener("input", () => schedulePromptPreview("quiz"));
  $(id).addEventListener("change", () => schedulePromptPreview("quiz"));
}
document.querySelectorAll(".quiz-type").forEach((cb) =>
  cb.addEventListener("change", () => schedulePromptPreview("quiz")));

// Предпросмотр следует за изменениями настроек презентации
for (const id of ["slides-count", "slides-theme", "slides-audience", "slides-extra"]) {
  $(id).addEventListener("input", () => schedulePromptPreview("slides"));
  $(id).addEventListener("change", () => schedulePromptPreview("slides"));
}

// ---------- настройки ----------

function updateAsrVisibility() {
  const local = $("tr-backend").value === "local";
  $("tr-local").hidden = !local;
  $("tr-remote").hidden = local;
}

async function loadWhisperStatus() {
  const st = await api("/whisper/status");
  $("whisper-disk").textContent = st.disk.length
    ? st.disk.map((d) => `${d.name} (${d.size_gb} ГБ)`).join(", ")
    : "пока ничего не скачано";
  const mem = st.memory[0];
  $("whisper-memory").textContent = mem
    ? `${mem.model} (${mem.device}, ${mem.compute_type})`
    : "ничего не загружено";
  $("whisper-unload").disabled = !mem;
}

$("whisper-unload").addEventListener("click", async () => {
  try {
    await postJson("/whisper/unload", {});
    toast("Модель выгружена из памяти");
    await loadWhisperStatus();
  } catch (err) {
    toast(err.message, true);
  }
});

function updateLlmVisibility() {
  const remote = $("llm-backend").value === "remote";
  $("llm-local").hidden = remote;
  $("llm-remote").hidden = !remote;
}

function llmEndpointIds(backend) {
  return {
    url: $(`llm-${backend}-url`),
    model: $(`llm-${backend}-model`),
    select: $(`llm-${backend}-model-select`),
    datalist: $(`llm-${backend}-models`),
    key: $(`llm-${backend}-key`),
  };
}

async function loadSettings() {
  const s = await api("/settings");
  state.settings = s;
  $("tr-backend").value = s.transcription.backend;
  // Если в config.yaml указана нестандартная модель — добавляем её в список, чтобы не потерять
  const modelSelect = $("tr-local-model");
  if (![...modelSelect.options].some((o) => o.value === s.transcription.local.model)) {
    const option = document.createElement("option");
    option.value = s.transcription.local.model;
    option.textContent = s.transcription.local.model;
    modelSelect.append(option);
  }
  modelSelect.value = s.transcription.local.model;
  $("tr-local-device").value = s.transcription.local.device;
  $("tr-local-compute").value = s.transcription.local.compute_type;
  $("tr-local-batch").value = s.transcription.local.batch_size ?? 8;
  $("tr-local-vad").checked = s.transcription.local.vad_filter !== false;
  $("tr-remote-url").value = s.transcription.remote.base_url;
  $("tr-remote-model").value = s.transcription.remote.model;
  $("tr-remote-key").value = s.transcription.remote.api_key;
  $("llm-backend").value = s.llm.backend || "local";
  for (const mode of ["local", "remote"]) {
    const ep = s.llm[mode] || {};
    const ids = llmEndpointIds(mode);
    ids.url.value = ep.base_url || "";
    ids.model.value = ep.model || "";
    ids.key.value = ep.api_key || "";
  }
  $("llm-temp").value = s.llm.temperature;
  $("llm-chunk").value = s.llm.max_chunk_chars;
  // Нестандартный язык из config.yaml добавляем в список, чтобы не потерять
  const langSelect = $("language");
  if (![...langSelect.options].some((o) => o.value === s.language)) {
    const option = document.createElement("option");
    option.value = s.language;
    option.textContent = s.language;
    langSelect.append(option);
  }
  langSelect.value = s.language;
  updateAsrVisibility();
  updateLlmVisibility();
  state.settingsLoaded = true;
  loadWhisperStatus().catch((e) => toast(e.message, true));
}

$("tr-backend").addEventListener("change", updateAsrVisibility);
$("llm-backend").addEventListener("change", updateLlmVisibility);

// ---------- автосохранение настроек ----------

function collectSettings() {
  const s = structuredClone(state.settings);
  s.language = $("language").value.trim() || "ru";
  s.transcription.backend = $("tr-backend").value;
  s.transcription.local.model = $("tr-local-model").value.trim();
  s.transcription.local.device = $("tr-local-device").value;
  s.transcription.local.compute_type = $("tr-local-compute").value;
  s.transcription.local.batch_size = Math.max(1, Math.round(Number($("tr-local-batch").value) || 8));
  s.transcription.local.vad_filter = $("tr-local-vad").checked;
  s.transcription.remote.base_url = $("tr-remote-url").value.trim();
  s.transcription.remote.model = $("tr-remote-model").value.trim();
  s.transcription.remote.api_key = $("tr-remote-key").value;
  s.llm.backend = $("llm-backend").value;
  for (const mode of ["local", "remote"]) {
    const ids = llmEndpointIds(mode);
    const modelSelect = ids.select;
    s.llm[mode] = {
      ...s.llm[mode],
      base_url: ids.url.value.trim(),
      model: (modelSelect.hidden ? ids.model.value : modelSelect.value).trim(),
      api_key: ids.key.value,
    };
  }
  s.llm.temperature = Number($("llm-temp").value);
  s.llm.max_chunk_chars = Number($("llm-chunk").value);
  return s;
}

let saveTimer = null;

function scheduleSettingsSave() {
  if (!state.settingsLoaded) return; // форма ещё не заполнена значениями с диска
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSettingsNow, 700); // пауза, чтобы не сохранять на каждом нажатии клавиши
}

async function saveSettingsNow() {
  try {
    state.settings = await putJson("/settings", collectSettings());
    $("settings-status").textContent = "Настройки сохранены ✓";
    $("asr-mode").textContent = describeAsr();
  } catch (err) {
    toast("Не удалось сохранить настройки: " + err.message, true);
  }
}

// Любое изменение в настройках сохраняется само
for (const id of ["tr-backend", "tr-local-model", "tr-local-device", "tr-local-compute",
                  "tr-local-batch", "tr-local-vad", "tr-remote-url", "tr-remote-model",
                  "tr-remote-key", "llm-backend", "llm-local-url", "llm-local-model",
                  "llm-local-model-select", "llm-local-key", "llm-remote-url",
                  "llm-remote-model", "llm-remote-model-select", "llm-remote-key",
                  "llm-temp", "llm-chunk", "language"]) {
  $(id).addEventListener("input", scheduleSettingsSave);
  $(id).addEventListener("change", scheduleSettingsSave);
}

$("llm-check").addEventListener("click", async () => {
  const result = $("llm-check-result");
  result.textContent = "Проверка...";
  const backend = $("llm-backend").value;
  const ep = llmEndpointIds(backend);
  const body = {
    ...state.settings.llm,
    backend,
    [backend]: {
      ...state.settings.llm[backend],
      base_url: ep.url.value.trim(),
      model: ep.model.value.trim(),
      api_key: ep.key.value,
    },
  };
  const resp = await postJson("/llm/check", body);
  if (!resp.ok) {
    result.textContent = `Ошибка: ${resp.error}`;
    return;
  }
  if (!resp.models.length) {
    result.textContent = "Подключение есть, но сервер не вернул список моделей";
    return;
  }
  // Показываем выбор из всех моделей сервера выпадающим списком
  const select = ep.select;
  select.innerHTML = "";
  for (const model of resp.models) {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    select.append(option);
  }
  const current = ep.model.value.trim();
  if (current && resp.models.includes(current)) select.value = current;
  ep.model.hidden = true;
  select.hidden = false;
  // Дублируем список и в подсказку обычного поля ввода
  ep.datalist.innerHTML = "";
  for (const model of resp.models) {
    const option = document.createElement("option");
    option.value = model;
    ep.datalist.append(option);
  }
  result.textContent = `Подключение есть, доступно моделей: ${resp.models.length}`;
});

// При смене адреса или ключа список моделей устаревает — возвращаем поле ввода
for (const backend of ["local", "remote"]) {
  const ids = llmEndpointIds(backend);
  for (const field of [ids.url, ids.key]) {
    field.addEventListener("input", () => {
      ids.select.hidden = true;
      ids.model.hidden = false;
    });
  }
}

// ---------- старт ----------

async function init() {
  state.settings = await api("/settings");
  await loadTemplates();
  await loadPrompts("summary");
  await loadPrompts("slides");
  await loadPrompts("quiz");
  await loadLectures();
}

init().catch((err) => toast(err.message, true));
