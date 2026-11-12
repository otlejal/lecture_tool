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
  sumIsHtml: false,
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

async function loadLectures(preferId) {
  const list = await api("/lectures");
  const select = $("lecture-select");
  select.innerHTML = "";
  for (const lecture of list) {
    const option = document.createElement("option");
    option.value = lecture.id;
    option.textContent = `${lecture.title} (${lecture.id})`;
    select.append(option);
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
                    "sum-tpl-save", "quiz-run", "quiz-rerender"]) {
    $(id).disabled = !hasLecture;
  }
  $("asr-mode").textContent = describeAsr();
  if (!hasLecture) {
    $("transcribe").disabled = true;
    $("transcript").value = "";
    $("sum-preview").src = "about:blank";
    $("quiz-preview").src = "about:blank";
    return;
  }
  const info = await api(`/lectures/${state.lectureId}`);
  $("transcribe").disabled = !info.stages.audio;
  $("rec-status").textContent = info.stages.audio ? "Аудио в лекции есть" : "Аудио ещё не записано";
  $("transcript").value = (await api(`/lectures/${state.lectureId}/transcript`)).text;
  setLink("download-transcript", info.stages.transcript, "transcript.txt");
  setLink("sum-download", info.stages.summary, "summary.html");
  setLink("quiz-download", info.stages.quiz, "quiz.html");
  $("sum-preview").src = info.stages.summary ? fileUrl("summary.html") : "about:blank";
  $("quiz-preview").src = info.stages.quiz ? fileUrl("quiz.html") : "about:blank";
}

$("lecture-select").addEventListener("change", (e) => {
  state.lectureId = e.target.value || null;
  if (state.lectureId) localStorage.setItem("lectureId", state.lectureId);
  refreshLecture().catch((err) => toast(err.message, true));
});

$("new-lecture").addEventListener("click", async () => {
  try {
    const lecture = await postJson("/lectures", { title: $("new-title").value });
    $("new-title").value = "";
    await loadLectures(lecture.id);
    toast("Лекция создана");
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
  if (!state.lectureId) return toast("Сначала создайте лекцию", true);
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

// ---------- общие для блоков 2 и 3 ----------

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
  for (const kind of ["summary", "quiz"]) {
    const { names } = await api(`/templates/${kind}`);
    const select = kind === "summary" ? $("sum-template") : $("quiz-template");
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

// ---------- блок 3: тест ----------

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

// ---------- настройки ----------

function updateAsrVisibility() {
  const local = $("tr-backend").value === "local";
  $("tr-local").hidden = !local;
  $("tr-remote").hidden = local;
}

async function loadSettings() {
  const s = await api("/settings");
  state.settings = s;
  $("tr-backend").value = s.transcription.backend;
  $("tr-local-model").value = s.transcription.local.model;
  $("tr-local-device").value = s.transcription.local.device;
  $("tr-local-compute").value = s.transcription.local.compute_type;
  $("tr-remote-url").value = s.transcription.remote.base_url;
  $("tr-remote-model").value = s.transcription.remote.model;
  $("tr-remote-key").value = s.transcription.remote.api_key;
  $("llm-url").value = s.llm.base_url;
  $("llm-model").value = s.llm.model;
  $("llm-key").value = s.llm.api_key;
  $("llm-temp").value = s.llm.temperature;
  $("llm-chunk").value = s.llm.max_chunk_chars;
  $("language").value = s.language;
  updateAsrVisibility();
}

$("tr-backend").addEventListener("change", updateAsrVisibility);

$("settings-save").addEventListener("click", async () => {
  const s = structuredClone(state.settings);
  s.language = $("language").value.trim() || "ru";
  s.transcription.backend = $("tr-backend").value;
  s.transcription.local.model = $("tr-local-model").value.trim();
  s.transcription.local.device = $("tr-local-device").value;
  s.transcription.local.compute_type = $("tr-local-compute").value;
  s.transcription.remote.base_url = $("tr-remote-url").value.trim();
  s.transcription.remote.model = $("tr-remote-model").value.trim();
  s.transcription.remote.api_key = $("tr-remote-key").value;
  s.llm.base_url = $("llm-url").value.trim();
  s.llm.model = $("llm-model").value.trim();
  s.llm.api_key = $("llm-key").value;
  s.llm.temperature = Number($("llm-temp").value);
  s.llm.max_chunk_chars = Number($("llm-chunk").value);
  try {
    state.settings = await putJson("/settings", s);
    toast("Настройки сохранены");
    $("asr-mode").textContent = describeAsr();
  } catch (err) {
    toast(err.message, true);
  }
});

$("llm-check").addEventListener("click", async () => {
  const result = $("llm-check-result");
  result.textContent = "Проверка...";
  const body = {
    ...state.settings.llm,
    base_url: $("llm-url").value.trim(),
    api_key: $("llm-key").value,
  };
  const resp = await postJson("/llm/check", body);
  const list = $("llm-models");
  list.innerHTML = "";
  if (resp.ok) {
    for (const model of resp.models) {
      const option = document.createElement("option");
      option.value = model;
      list.append(option);
    }
    result.textContent = `Подключение есть, моделей: ${resp.models.length}`;
  } else {
    result.textContent = `Ошибка: ${resp.error}`;
  }
});

// ---------- старт ----------

async function init() {
  state.settings = await api("/settings");
  await loadTemplates();
  await loadLectures();
}

init().catch((err) => toast(err.message, true));
