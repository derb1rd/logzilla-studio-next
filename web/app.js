"use strict";

// logZilla3000 · studio — Inspector Split.
// Та же граница UI ↔ ядро (HTTP/JSON-контракт /api/parse · /api/export), что и в
// базовом studio; здесь переписан только слой представления под 3-pane layout
// концепта 11: дерево источника слева, поток логов в центре, инспектор записи справа.
//
// МУЛЬТИФАЙЛОВАЯ СЕССИЯ. Сессия живёт во фронте: каждый файл = независимый
// ParseRequest через тот же single-source контракт (backend не тронут). Левая
// колонка — дерево файлов; активный файл питает поток/инспектор. «Контекст»
// собирает трассу по req_id ПО ВСЕМ файлам сессии (распределённый трейс).

const state = {
  // Сессия = упорядоченный список файлов + id активного.
  // FileEntry: { id, name, size, text, file(File|null), request, result,
  //              records, metrics, format, status, error }
  // status: new | parsing | parsed | error | stale (нужен ре-парс)
  session: { files: [], activeId: null },

  records: [],         // зеркало записей АКТИВНОГО файла (источник для потока/инспектора)
  view: [],            // отфильтрованные строкой поиска (то, что в потоке)
  selected: -1,        // индекс выбранной записи в state.view (-1 если скрыта/нет)
  selectedRec: null,   // сама выбранная запись (источник истины для инспектора:
                       // может быть выбрана из контекста и скрыта фильтром в потоке)
  queryTerms: [],      // текстовые термины поиска (для подсветки в строках)
  activeTab: "struct",
  totalLines: 0,
};

let _seq = 0;
const newId = () => "f" + (++_seq);
const activeEntry = () => state.session.files.find((f) => f.id === state.session.activeId) || null;

const $ = (id) => document.getElementById(id);
// LEVEL_RE и прочий чистый слой берём из core.js (см. деструктуризацию из LZ ниже).

const BROWSER_ENCODING = {
  "utf-8": "utf-8", "utf-8-sig": "utf-8",
  "cp1251": "windows-1251", "koi8-r": "koi8-r", "latin-1": "iso-8859-1",
};
const browserEncoding = (name) => BROWSER_ENCODING[name] || "utf-8";

const MAX_FILE_MB = 64;
const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024;
// Окно предпросмотра. Совпадает с PREVIEW_MAX на сервере (он всё равно капнёт);
// сервер вернёт min(limit, PREVIEW_MAX). Поток рендерит окно целиком (без
// виртуализации) — на верхней границе возможен лёгкий лаг, это осознанный размен.
const PREVIEW_LIMIT = 10000;

const formatBytes = (n) =>
  n >= 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + " МБ" : (n / 1024).toFixed(1) + " КБ";
const formatDuration = (ms) => (ms < 1000 ? `${ms} мс` : `${(ms / 1000).toFixed(2)} c`);
const ru = (n) => Number(n).toLocaleString("ru");
const shortName = (n) => (n.length > 14 ? n.slice(0, 7) + "…" + n.slice(-5) : n);

// --- чистый слой ------------------------------------------------------------
// Извлечение полей, разбор запросов и подсветка вынесены в core.js (глобал LZ)
// и покрыты юнит-тестами (tests/core.test.mjs). Здесь — только DOM/IO.
const {
  LEVEL_RE, norm, levelOf, fieldOf, tsOf, sourceOf, reqIdOf, msgOf, httpCtxOf, recordToLine,
  buildQuery, isEmptyQuery, matchesQuery, highlightSegments, crossContext,
} = LZ;

// --- health ----------------------------------------------------------------
async function checkHealth() {
  const el = $("health");
  try {
    const r = await obs.fetch("/api/health");
    const j = await r.json();
    el.textContent = `● ${j.service} v${j.version}`;
    el.className = "status ok";
  } catch {
    el.textContent = "● сервер недоступен";
    el.className = "status bad";
  }
}

// --- сбор ParseOptions (глобальные для всей сессии) -------------------------
function collectOptions() {
  const levels = [...document.querySelectorAll(".lvl:checked")].map((c) => c.value);
  const allLevels = document.querySelectorAll(".lvl").length;
  return {
    encoding: $("encoding").value,
    log_levels: levels.length === allLevels ? [] : levels,
    remove_duplicates: $("remove_duplicates").checked,
    remove_ansi: $("remove_ansi").checked,
    expand_message: $("expand_message").checked,
    compact_json: $("compact_json").checked,
    format_sql: true,
  };
}

// --- сессия: добавление / удаление / переключение файлов --------------------
function addEntry({ name, size, text, file }) {
  const entry = {
    id: newId(), name, size: size ?? (text ? text.length : 0),
    text: text ?? null, file: file || null,
    request: null, result: null, records: [], metrics: null, format: null,
    status: "new", error: null,
  };
  state.session.files.push(entry);
  state.session.activeId = entry.id;
  renderTree();
  return entry;
}

function loadFiles(fileList) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  let added = 0;
  for (const file of files) {
    if (file.size > MAX_FILE_BYTES) {
      setFooter(`Файл ${file.name} (${formatBytes(file.size)}) превышает лимит ${MAX_FILE_MB} МБ — пропущен.`);
      obs.action("file_too_large", { name: file.name, size: file.size });
      continue;
    }
    const entry = addEntry({ name: file.name, size: file.size, file });
    readEntryFile(entry);   // async — заполнит entry.text, затем renderTree
    added++;
  }
  if (added) {
    obs.action("files_added", { count: added });
    setFooter(`Добавлено файлов: ${added}. Нажмите «Парсить».`);
    renderActive();         // показать имя активного в breadcrumb сразу
  }
}

// Чтение файла с текущей кодировкой. Перечитываем при смене кодировки.
function readEntryFile(entry) {
  if (!entry.file) return;
  const enc = $("encoding").value;
  const reader = new FileReader();
  reader.onload = () => {
    entry.text = reader.result;
    if (entry.status === "parsed") entry.status = "stale";  // кодировка сменилась → нужен ре-парс
    renderTree();
  };
  reader.readAsText(entry.file, browserEncoding(enc));
}

function setActive(id) {
  if (state.session.activeId === id) return;
  state.session.activeId = id;
  state.selectedRec = null; state.selected = -1;
  obs.action("file_switch", { id });
  renderTree();
  renderActive();
}

function removeEntry(id) {
  const i = state.session.files.findIndex((f) => f.id === id);
  if (i < 0) return;
  state.session.files.splice(i, 1);
  if (state.session.activeId === id) {
    const next = state.session.files[i] || state.session.files[i - 1] || null;
    state.session.activeId = next ? next.id : null;
    state.selectedRec = null; state.selected = -1;
  }
  obs.action("file_remove", { id });
  renderTree();
  renderActive();
}

function renderTree() {
  const tree = $("fileTree");
  const files = state.session.files;
  tree.hidden = files.length === 0;
  tree.innerHTML = "";
  const dot = { new: "○", parsing: "…", parsed: "●", error: "✕", stale: "~" };
  for (const f of files) {
    const li = document.createElement("li");
    li.className = "file-row" + (f.id === state.session.activeId ? " active" : "");
    li.dataset.id = f.id;
    const errs = f.metrics ? f.metrics.errors : 0;
    li.innerHTML =
      `<span class="f-stat s-${f.status}" title="${f.error ? escapeHtml(f.error) : f.status}">${dot[f.status] || ""}</span>` +
      `<span class="f-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>` +
      `<span class="f-meta">${formatBytes(f.size)}${f.format ? " · " + escapeHtml(f.format) : ""}</span>` +
      (errs ? `<span class="f-err" title="ошибок">${ru(errs)}✕</span>` : `<span class="f-err"></span>`) +
      `<button class="f-del" title="Убрать из сессии">✕</button>`;
    li.addEventListener("click", (e) => {
      if (e.target.classList.contains("f-del")) { e.stopPropagation(); removeEntry(f.id); return; }
      setActive(f.id);
    });
    tree.appendChild(li);
  }
}

// --- парсинг (вся сессия) ---------------------------------------------------
// Ограниченная конкурентность: stdlib-сервер потоковый, но N одновременных
// тяжёлых прогонов лучше не запускать. Пул из `limit` воркеров разбирает очередь.
async function runPool(items, limit, fn) {
  let idx = 0;
  const worker = async () => { while (idx < items.length) { const i = idx++; await fn(items[i]); } };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
}

async function parseEntry(entry) {
  if (entry.text == null) return;             // файл ещё читается
  const req = {
    version: "1",
    source: { kind: "inline", text: entry.text },
    options: collectOptions(),
    preview: { limit: PREVIEW_LIMIT, offset: 0 },
  };
  entry.request = req;                        // нужен экспорту (тот же ParseRequest)
  entry.status = "parsing";
  renderTree();
  try {
    const r = await obs.fetch("/api/parse", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req),
    });
    const result = await r.json();
    if (!r.ok) {
      entry.status = "error";
      entry.error = result.error?.message || ("HTTP " + r.status);
      entry.records = []; entry.metrics = null; entry.format = null; entry.result = result;
    } else {
      entry.result = result;
      entry.records = result.records || [];
      entry.metrics = result.metrics;
      entry.format = result.format_detected;
      entry.status = "parsed";
      entry.error = null;
    }
  } catch (e) {
    entry.status = "error"; entry.error = e.message; entry.records = []; entry.metrics = null;
  }
  renderTree();
}

async function doParse() {
  // Вставка из textarea → отдельный файл сессии (затем поле очищаем).
  const pasted = $("paste").value;
  if (pasted.trim().length > 0) {
    const n = state.session.files.filter((f) => !f.file).length + 1;
    addEntry({ name: `вставка ${n}`, text: pasted, size: pasted.length });
    $("paste").value = "";
  }
  const files = state.session.files;
  if (files.length === 0) { setFooter("Нет данных: добавьте файл или вставьте текст."); return; }

  obs.action("parse_clicked", { files: files.length, levels: collectOptions().log_levels });
  $("parseBtn").disabled = true;
  setFooter(`Парсинг… (${files.length} файл(ов))`);
  // Записи устаревают: их объекты пересоздаются прогоном.
  state.selectedRec = null; state.selected = -1;
  try {
    await runPool(files, 3, parseEntry);
  } finally {
    $("parseBtn").disabled = false;
  }
  renderActive();
  const ok = files.filter((f) => f.status === "parsed").length;
  const bad = files.filter((f) => f.status === "error").length;
  const act = activeEntry();
  const rid = act && act.result ? act.result.run_id : "—";
  // плашка усечения окна активного файла (полный результат — в «Экспорт»)
  const pw = (act && act.result && act.result.preview_window) || {};
  const note = pw.total_records > pw.returned
    ? ` · показаны первые ${ru(pw.returned)} из ${ru(pw.total_records)} — полный результат в «Экспорт»`
    : "";
  obs.action("parse_result", { ok, bad, run_id: rid, windowed: !!note });
  setFooter(`Сессия: ${ok} распарсено${bad ? `, ${bad} с ошибкой` : ""} · активный: ${act ? act.name : "—"} · run_id=${rid}${note}`);
}

// --- рендер активного файла -------------------------------------------------
// Перерисовывает тулбар/счётчики/поток/инспектор из АКТИВНОГО файла сессии.
function renderActive() {
  const entry = activeEntry();
  $("bc-source").textContent = entry ? entry.name : "нет источника";
  updateFilterBreadcrumb();

  if (!entry || entry.status !== "parsed") {
    state.records = [];
    state.totalLines = 0;
    $("t-format").textContent = entry && entry.status === "error" ? "ошибка" : "—";
    $("t-err").textContent = "—"; $("t-warn").textContent = "—"; $("t-duration").textContent = "";
    updateLevelCounts();
    applySearch();
    return;
  }
  const m = entry.metrics;
  state.records = entry.records;
  state.totalLines = m.total_lines;
  $("t-format").textContent = entry.format || "—";
  $("t-err").textContent = ru(m.errors);
  $("t-warn").textContent = ru(m.warnings);
  $("t-duration").textContent = formatDuration(m.duration_ms);
  updateLevelCounts();

  const pw = (entry.result && entry.result.preview_window) || {};
  let note = "";
  if (pw.total_records > pw.returned) {
    note = ` · показаны первые ${ru(pw.returned)} из ${ru(pw.total_records)} — полный результат в «Экспорт»`;
  }
  applySearch();
  if (note) setFooter(`${entry.name}: ${ru(m.filtered)} записей${note}`);
}

function updateLevelCounts() {
  const counts = { ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0 };
  for (const rec of state.records) {
    const lvl = levelOf(rec);
    if (lvl in counts) counts[lvl]++;
  }
  for (const lvl of Object.keys(counts)) $("cnt-" + lvl).textContent = ru(counts[lvl]);
}

// Синхронизирует визуальное состояние плиток уровней с чекбоксами (.off = выкл).
function syncLevelTiles() {
  document.querySelectorAll(".lvl-tile").forEach((tile) => {
    const cb = tile.querySelector(".lvl");
    tile.classList.toggle("off", cb ? !cb.checked : false);
  });
}

function updateFilterBreadcrumb() {
  const on = [...document.querySelectorAll(".lvl:checked")].map((c) => c.value);
  const all = document.querySelectorAll(".lvl").length;
  $("bc-filter").textContent = on.length === all ? "все уровни" : on.join(" ") || "ничего";
}

// --- поток (центр) ----------------------------------------------------------
// Уровни с выключенной плиткой — скрываем в потоке мгновенно (клиентский фильтр).
// Это поверх серверного: плитки уходят в options.log_levels при следующем «Парсить»,
// но гасить строки сразу — то, чего ждёшь от клика по плитке.
function hiddenLevels() {
  const off = new Set();
  document.querySelectorAll(".lvl").forEach((c) => { if (!c.checked) off.add(c.value); });
  return off;
}

function applySearch() {
  const q = buildQuery($("search").value);
  state.queryTerms = q.text;                      // термины для подсветки в строках
  const noQuery = isEmptyQuery(q);
  const off = hiddenLevels();
  state.view = state.records.filter((rec) => {
    const lvl = levelOf(rec);
    if (lvl && off.has(lvl)) return false;        // плитка уровня выключена
    return noQuery || matchesQuery(rec, q);
  });
  // Выбор хранится по идентичности записи (state.selectedRec), а индекс в потоке
  // пересчитываем. Если запись выпала из выборки — индекс -1 (подсветки в потоке
  // нет), но инспектор продолжает показывать её с пометкой «скрыта фильтром».
  state.selected = state.selectedRec ? state.view.indexOf(state.selectedRec) : -1;
  renderStream();
  renderInspector();
}

function renderStream() {
  const box = $("stream");
  box.innerHTML = "";
  // счётчик потока: видно из окна (если клиентский фильтр сузил — показываем оба числа)
  const visible = state.view.length, win = state.records.length;
  $("t-shown").textContent = visible === win
    ? `${ru(win)} / ${ru(state.totalLines || win)} строк`
    : `${ru(visible)} из ${ru(win)} в окне`;
  if (state.records.length === 0) {
    const entry = activeEntry();
    const msg = !entry ? "Добавьте файлы в сессию, затем «Парсить»."
      : entry.status === "error" ? "Ошибка парсинга: " + escapeHtml(entry.error || "")
      : entry.status === "parsed" ? "Парсер не извлёк записей."
      : "Нажмите «Парсить».";
    box.innerHTML = `<div class="stream-empty">${msg}</div>`;
    return;
  }
  if (state.view.length === 0) {
    box.innerHTML = '<div class="stream-empty">Поиск ничего не нашёл.</div>';
    return;
  }
  const frag = document.createDocumentFragment();
  state.view.forEach((rec, i) => {
    const lvl = levelOf(rec);
    const row = document.createElement("div");
    row.className = "stream-row" + (lvl ? " lvl-" + lvl : "") + (i === state.selected ? " selected" : "");
    row.dataset.idx = i;
    const cell = (cls, txt) => { const s = document.createElement("span"); s.className = cls; s.textContent = txt; return s; };
    const src = sourceOf(rec);
    row.appendChild(cell("r-n", i + 1));
    row.appendChild(cell("r-ts", tsOf(rec) || "—"));
    row.appendChild(cell("r-lvl " + lvl, lvl || "—"));
    row.appendChild(cell("r-src" + (src ? "" : " empty"), src || "—"));
    row.appendChild(msgCell(rec));
    row.addEventListener("click", () => selectRow(i));
    frag.appendChild(row);
  });
  box.appendChild(frag);
}

// HTTP-метод/статус → класс-модификатор для цветовой кодировки чипа.
function methodMod(m) {
  switch (m.toUpperCase()) {
    case "GET": return "get";
    case "POST": return "post";
    case "PUT": case "PATCH": return "put";
    case "DELETE": return "del";
    case "HEAD": case "OPTIONS": return "meta";
    default: return "other";
  }
}
function statusMod(s) {
  const n = parseInt(s, 10);
  if (!n) return "other";
  if (n < 300) return "ok";
  if (n < 400) return "redir";
  if (n < 500) return "warn";
  return "err";
}

// Добавляет текст в узел, подсвечивая <mark> совпадения активных терминов поиска.
function appendHighlighted(parent, text) {
  for (const s of highlightSegments(text, state.queryTerms)) {
    if (!s.hit) { parent.appendChild(document.createTextNode(s.t)); continue; }
    const m = document.createElement("mark");
    m.className = "hl";
    m.textContent = s.t;
    parent.appendChild(m);
  }
}

// Ячейка message: цветные чипы HTTP-метода и кода статуса + путь/длительность/текст
// с подсветкой терминов поиска. Строим из DOM-узлов (без innerHTML) → XSS невозможен.
function msgCell(rec) {
  const span = document.createElement("span");
  span.className = "r-msg";
  const c = httpCtxOf(rec);
  let started = false;
  const sep = () => { if (started) span.appendChild(document.createTextNode(" · ")); };
  const chip = (cls, txt) => { const s = document.createElement("span"); s.className = cls; s.textContent = txt; span.appendChild(s); started = true; };

  if (c.method) chip("r-method m-" + methodMod(c.method), c.method);
  if (c.url) { if (c.method) span.appendChild(document.createTextNode(" ")); appendHighlighted(span, c.url); started = true; }
  if (c.status) { sep(); chip("r-status s-" + statusMod(c.status), c.status); }
  if (c.dur) { sep(); span.appendChild(document.createTextNode(c.dur)); started = true; }
  if (c.base) { sep(); appendHighlighted(span, c.base); }
  if (c.sql) { sep(); chip("r-sql", "sql"); span.appendChild(document.createTextNode(" ")); appendHighlighted(span, c.sql); }
  if (!started && !c.base) span.textContent = msgOf(rec);   // запас на пустую запись
  return span;
}

// Обогащённый предпросмотр как безопасный HTML (для инспектора и списков контекста/
// похожих, где рендер идёт через innerHTML). Те же чипы, но текст экранирован.
function summaryHtml(rec) {
  const c = httpCtxOf(rec);
  const parts = [];
  let head = "";
  if (c.method) head += `<span class="r-method m-${methodMod(c.method)}">${escapeHtml(c.method)}</span>`;
  if (c.url) head += (c.method ? " " : "") + escapeHtml(c.url);
  if (head) parts.push(head);
  if (c.status) parts.push(`<span class="r-status s-${statusMod(c.status)}">${escapeHtml(c.status)}</span>`);
  if (c.dur) parts.push(escapeHtml(c.dur));
  if (c.base) parts.push(escapeHtml(c.base));
  if (c.sql) parts.push(`<span class="r-sql">sql</span> ${escapeHtml(c.sql)}`);
  return parts.join(" · ") || escapeHtml(msgOf(rec));
}

// Перекраска выделения без перестроения списка (дёшево при клике/стрелках —
// убирает джанк скролла полного ре-рендера).
function paintSelection() {
  $("stream").querySelectorAll(".stream-row").forEach((el) =>
    el.classList.toggle("selected", Number(el.dataset.idx) === state.selected));
}

function scrollToSelected() {
  if (state.selected < 0) return;
  const el = $("stream").querySelector(`.stream-row[data-idx="${state.selected}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}

function selectRow(i) {
  state.selected = i;
  state.selectedRec = state.view[i] || null;
  obs.action("inspect_row", { idx: i });
  paintSelection();
  renderInspector();
  scrollToSelected();
}

// Выбор записи по идентичности (из контекста/похожих) В ТЕКУЩЕМ файле. Если запись
// видна в потоке — подсвечиваем и проматываем; если скрыта фильтром — инспектор
// всё равно открывает её (с пометкой), поток не трогаем.
function selectRecord(rec) {
  if (!rec) return;
  state.selectedRec = rec;
  state.selected = state.view.indexOf(rec);
  obs.action("inspect_record", { visible: state.selected >= 0, from: state.activeTab });
  paintSelection();
  renderInspector();
  scrollToSelected();
}

// Выбор записи из ДРУГОГО файла сессии (cross-file контекст). Переключаем активный
// файл на её источник, затем выбираем — так трасса запроса проходима через сервисы.
function selectRecordIn(fileId, rec) {
  if (!rec) return;
  if (fileId && fileId !== state.session.activeId) {
    state.session.activeId = fileId;
    state.selectedRec = rec;
    obs.action("inspect_xfile", { fileId });
    renderTree();
    renderActive();          // applySearch пересчитает state.selected по selectedRec
    scrollToSelected();
    return;
  }
  selectRecord(rec);
}

// Шаг выбора по потоку (стрелки ↑/↓).
function moveSelection(delta) {
  if (state.view.length === 0) return;
  let i = state.selected;
  i = i < 0 ? (delta > 0 ? 0 : state.view.length - 1) : i + delta;
  i = Math.max(0, Math.min(state.view.length - 1, i));
  selectRow(i);
}

// --- инспектор (право) ------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function renderInspector() {
  const head = $("inspHead");
  const tabs = $("inspTabs");
  const body = $("inspBody");
  const rec = state.selectedRec;
  if (!rec) {
    head.innerHTML = '<div class="insp-empty">Выберите строку в потоке, чтобы раскрыть запись.</div>';
    tabs.hidden = true;
    body.innerHTML = "";
    return;
  }
  const lvl = levelOf(rec) || "DEBUG";
  const ri = state.records.indexOf(rec);
  const rid = reqIdOf(rec);
  const hidden = state.selected < 0
    ? '<span class="insp-hidden" title="Запись не входит в текущий фильтр потока">скрыта фильтром</span>'
    : "";
  // req_id — кликабельный чип: пивотит поток на этот запрос (поиск → req:<id>)
  const reqChip = rid
    ? `<span class="req-chip" data-rid="${escapeHtml(rid)}" title="Показать в потоке только запрос ${escapeHtml(rid)}">req ${escapeHtml(rid)}</span>`
    : "";
  head.innerHTML =
    `<div class="insp-meta">` +
    `<span class="insp-badge ${lvl}">${lvl}</span>` +
    `<span class="insp-id">#${ri + 1} · ${escapeHtml(tsOf(rec) || "—")}</span>` +
    reqChip +
    hidden +
    `</div>` +
    `<div class="insp-title">${summaryHtml(rec)}</div>`;
  const chipEl = head.querySelector(".req-chip");
  if (chipEl) chipEl.addEventListener("click", () => pivotToReq(chipEl.dataset.rid));
  tabs.hidden = false;
  renderTab();
}

// Пивот потока на запрос: ставим поиск `req:<id>`, применяем фильтр, держим выбор.
function pivotToReq(rid) {
  $("search").value = `req:${rid}`;
  obs.action("pivot_req", { rid });
  applySearch();          // пересчёт view; selectedRec сохраняется по идентичности
  paintSelection();
  scrollToSelected();
}

function renderTab() {
  const body = $("inspBody");
  const rec = state.selectedRec;
  if (!rec) { body.innerHTML = ""; return; }
  if (state.activeTab === "struct") {
    body.innerHTML = jsonHtml(rec) + histoHtml();
  } else if (state.activeTab === "context") {
    body.innerHTML = contextHtml();
    bindCtxRows();
  } else {
    body.innerHTML = similarHtml(rec);
    bindCtxRows();
  }
}

// Делает строки контекста/похожих кликабельными → выбор той же записи (с учётом
// файла-источника: data-fid). Cross-file клик переключает активный файл.
function bindCtxRows() {
  $("inspBody").querySelectorAll(".ctx-row[data-ri]").forEach((el) => {
    el.addEventListener("click", () => {
      const fid = el.dataset.fid || state.session.activeId;
      const f = state.session.files.find((x) => x.id === fid);
      const rec = f ? f.records[+el.dataset.ri] : null;
      selectRecordIn(fid, rec);
    });
  });
}

// pretty-print JSON с подсветкой; уровень окрашиваем по значению
function jsonHtml(rec) {
  const lines = ['<div class="json-line">{</div>'];
  const entries = Object.entries(rec);
  entries.forEach(([k, v], idx) => {
    const comma = idx < entries.length - 1 ? "," : "";
    lines.push(`<div class="json-line" style="padding-left:14px"><span class="j-key">"${escapeHtml(k)}"</span>: ${valHtml(k, v)}${comma}</div>`);
  });
  lines.push('<div class="json-line">}</div>');
  return lines.join("");
}

function valHtml(key, v) {
  if (v === null) return '<span class="j-null">null</span>';
  if (typeof v === "boolean") return `<span class="j-bool">${v}</span>`;
  if (typeof v === "number") return `<span class="j-num">${v}</span>`;
  if (typeof v === "object") return `<span class="j-str">${escapeHtml(JSON.stringify(v))}</span>`;
  const s = escapeHtml(v);
  const m = String(v).match(LEVEL_RE);
  if (m && String(v).length < 12) return `<span class="j-lvl-${norm(m[1])}">"${s}"</span>`;
  return `<span class="j-str">"${s}"</span>`;
}

// контекст — все строки того же запроса (req_id/trace_id) ПО ВСЕЙ СЕССИИ. Это и
// есть распределённый трейс: одна ось req_id, склеивающая логи разных сервисов/
// файлов. Если id нет — откат на соседние строки активного файла (±6). Берём из
// полных окон state.session.files (не из отфильтрованного view), чтобы фильтр
// уровней не рвал трассу запроса.
function contextHtml() {
  const sel = state.selectedRec;
  const rid = reqIdOf(sel);
  const active = activeEntry();
  let label, rows;          // rows: [{ fileId, name, i, rec }]
  if (rid) {
    const sessionFiles = state.session.files
      .filter((f) => f.status === "parsed")
      .map((f) => ({ fileId: f.id, name: f.name, records: f.records }));
    rows = crossContext(sessionFiles, rid);
    const nFiles = new Set(rows.map((r) => r.fileId)).size;
    label = `req_id = ${escapeHtml(rid)} · ${ru(rows.length)} строк` + (nFiles > 1 ? ` · ${nFiles} файла` : "");
  } else {
    const recs = active ? active.records : [];
    const o = recs.indexOf(sel);
    const lo = Math.max(0, o - 6), hi = Math.min(recs.length, o + 7);
    rows = [];
    for (let i = lo; i < hi; i++) rows.push({ fileId: active.id, name: active.name, i, rec: recs[i] });
    label = "нет req_id · соседние строки активного файла (±6)";
  }
  const multi = new Set(rows.map((r) => r.fileId)).size > 1;
  let html = `<div class="histo-label">${label}</div>`;
  for (const { fileId, name, i, rec } of rows) {
    const center = rec === sel ? " center" : "";
    const fileBadge = multi ? `<span class="ctx-file" title="${escapeHtml(name)}">${escapeHtml(shortName(name))}</span>` : "";
    html += `<div class="ctx-row${multi ? " xfile" : ""}${center}" data-fid="${fileId}" data-ri="${i}">` +
      `<span class="c-n">${i + 1}</span>` +
      fileBadge +
      `<span class="r-lvl ${levelOf(rec)}">${levelOf(rec) || "—"}</span>` +
      `<span>${summaryHtml(rec)}</span></div>`;
  }
  return html;
}

// похожие — записи того же уровня (и источника, если он есть) В АКТИВНОМ файле
function similarHtml(rec) {
  const lvl = levelOf(rec);
  const src = sourceOf(rec);
  const fid = state.session.activeId;
  const matches = state.records
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => levelOf(r) === lvl && (!src || sourceOf(r) === src));
  let html = `<div class="histo-label">${ru(matches.length)} записей уровня ${lvl || "?"}${src ? " · " + escapeHtml(src) : ""}</div>`;
  matches.slice(0, 40).forEach(({ r, i }) => {
    const center = r === rec ? " center" : "";
    html += `<div class="ctx-row${center}" data-fid="${fid}" data-ri="${i}"><span class="c-n">${i + 1}</span>` +
      `<span class="c-ts">${escapeHtml(tsOf(r) || "—")}</span>` +
      `<span>${summaryHtml(r)}</span></div>`;
  });
  return html;
}

// гистограмма — распределение выбранного уровня по окну предпросмотра (20 бинов)
function histoHtml() {
  const n = state.records.length;
  if (n === 0) return "";
  const lvl = levelOf(state.selectedRec);
  const BINS = 20;
  const bins = new Array(BINS).fill(0);
  state.records.forEach((rec, i) => {
    if (levelOf(rec) === lvl) bins[Math.min(BINS - 1, Math.floor((i / n) * BINS))]++;
  });
  const max = Math.max(1, ...bins);
  // позиция выбранной записи в исходном окне
  const origIdx = state.records.indexOf(state.selectedRec);
  const selBin = Math.min(BINS - 1, Math.floor((origIdx / n) * BINS));
  const color = { ERROR: "var(--error)", WARN: "var(--warn)", INFO: "var(--info)", DEBUG: "var(--debug)" }[lvl] || "var(--accent)";
  const bars = bins.map((v, i) =>
    `<div class="histo-bar" style="height:${(v / max) * 100}%;background:${i === selBin ? "var(--accent)" : color};opacity:${i === selBin ? 1 : 0.5}"></div>`
  ).join("");
  return `<div class="histo">` +
    `<div class="histo-label">Уровень ${lvl || "?"} по окну · всего ${ru(bins.reduce((a, b) => a + b, 0))}</div>` +
    `<div class="histo-bars">${bars}</div>` +
    `<div class="histo-axis"><span>начало</span><span>здесь</span><span>конец</span></div></div>`;
}

// --- экспорт ----------------------------------------------------------------
async function doExport() {
  if ($("exportScope").value === "all") return doExportAll();
  const entry = activeEntry();
  if (!entry || !entry.request) { setFooter("Сначала выполните парсинг."); return; }
  const req = { version: "1", parse_request: entry.request, options: { gzip: $("gzip").checked } };
  obs.action("export_clicked", { gzip: req.options.gzip, file: entry.name });
  setFooter("Экспорт…");
  try {
    const r = await obs.fetch("/api/export", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      setFooter("Ошибка экспорта: " + (j.error?.message || r.status));
      return;
    }
    const blob = await r.blob();
    const ext = req.options.gzip ? "json.gz" : "json";
    const name = `${baseName(entry.name)}.${ext}`;
    downloadBlob(blob, name);
    if (r.headers.get("X-Truncated") === "true") {
      const total = r.headers.get("X-Total-Records") || "?";
      const got = r.headers.get("X-Exported-Records") || "?";
      obs.action("export_truncated", { total, exported: got });
      setFooter(`⚠ Экспортировано ${got} из ${total} записей — файл НЕПОЛНЫЙ (превышен лимит экспорта).`);
    } else {
      setFooter("Экспортировано: " + name);
    }
  } catch (e) {
    setFooter("Сетевая ошибка экспорта: " + e.message);
  }
}

// Экспорт всех файлов сессии — каждый отдельным <имя>.json (по файлу на источник).
// Каждый файл перепарсивается через свой ParseRequest (полный набор, до MAX_RECORDS)
// и скачивается самостоятельно. Между загрузками — пауза: браузеры троттлят
// множественные программные скачивания. Ошибка одного файла не прерывает остальные.
async function doExportAll() {
  const parsed = state.session.files.filter((f) => f.request && f.status === "parsed");
  if (!parsed.length) { setFooter("Нет распарсенных файлов для экспорта."); return; }
  const gzip = $("gzip").checked;
  const ext = gzip ? "json.gz" : "json";
  obs.action("export_all_clicked", { files: parsed.length, gzip });
  setFooter(`Экспорт файлов… (0/${parsed.length})`);

  const failures = [];
  let truncatedAny = false;
  for (let k = 0; k < parsed.length; k++) {
    const f = parsed[k];
    const req = { version: "1", parse_request: f.request, options: { gzip } };
    try {
      const r = await obs.fetch("/api/export", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error?.message || r.status);
      }
      if (r.headers.get("X-Truncated") === "true") truncatedAny = true;
      downloadBlob(await r.blob(), `${baseName(f.name)}.${ext}`);
      setFooter(`Экспорт файлов… (${k + 1}/${parsed.length})`);
    } catch (e) {
      failures.push(`${f.name}: ${e.message}`);
    }
    if (k < parsed.length - 1) await sleep(250);   // дать браузеру принять загрузку
  }

  const ok = parsed.length - failures.length;
  obs.action("export_all_done", { files: parsed.length, ok, failed: failures.length, truncated: truncatedAny });
  let msg = `Экспортировано файлов: ${ok}/${parsed.length}`;
  if (truncatedAny) msg += " · ⚠ часть файлов обрезана по лимиту";
  if (failures.length) msg += " · ошибки: " + failures.join("; ");
  setFooter(msg);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

// «лог.txt» → «лог» (для имени экспортируемого файла)
function baseName(name) {
  const dot = name.lastIndexOf(".");
  return (dot > 0 ? name.slice(0, dot) : name).replace(/[^\w.\-]+/g, "_") || "logzilla_export";
}

// --- prefs (localStorage) ---------------------------------------------------
const PREFS_KEY = "logzilla-studio-next.prefs.v1";
const PREF_CHECKS = ["compact_json", "remove_duplicates", "remove_ansi", "expand_message", "gzip"];

function savePrefs() {
  const prefs = {
    encoding: $("encoding").value,
    levels: [...document.querySelectorAll(".lvl")].map((c) => [c.value, c.checked]),
  };
  for (const id of PREF_CHECKS) prefs[id] = $(id).checked;
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) {}
}

function restorePrefs() {
  let prefs;
  try { prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || "null"); } catch (_) { return; }
  if (!prefs) return;
  if (typeof prefs.encoding === "string") $("encoding").value = prefs.encoding;
  if (Array.isArray(prefs.levels)) {
    const saved = new Map(prefs.levels);
    document.querySelectorAll(".lvl").forEach((c) => { if (saved.has(c.value)) c.checked = !!saved.get(c.value); });
  }
  for (const id of PREF_CHECKS) if (typeof prefs[id] === "boolean") $(id).checked = prefs[id];
}

// --- обвязка ----------------------------------------------------------------
function setFooter(t) { $("footer-status").textContent = t; }

function wireUp() {
  restorePrefs();
  document.addEventListener("change", savePrefs);

  const dz = $("dropzone");
  dz.addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", (e) => { loadFiles(e.target.files); e.target.value = ""; });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); dz.classList.remove("drag");
    loadFiles(e.dataTransfer.files);
  });

  $("parseBtn").addEventListener("click", doParse);
  $("exportBtn").addEventListener("click", doExport);

  $("search").addEventListener("input", (e) => {
    obs.action("search", { len: e.target.value.length });
    applySearch();
  });
  // хоткеи: ⌘K — поиск; ↑/↓ — навигация по потоку; Esc — снять выбор/выйти из поиска.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("search").focus(); return; }
    const tag = (document.activeElement?.tagName || "").toUpperCase();
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (e.key === "Escape" && tag === "INPUT") { e.target.blur(); return; }
    if (typing) return;                         // не мешаем вводу
    if (e.key === "ArrowDown") { e.preventDefault(); moveSelection(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveSelection(-1); }
  });

  document.querySelectorAll(".lvl").forEach((c) =>
    c.addEventListener("change", () => {
      obs.action("level_toggle", { value: c.value, on: c.checked });
      syncLevelTiles();
      updateFilterBreadcrumb();
      applySearch();   // мгновенно перефильтровать поток по плиткам
    }));
  syncLevelTiles();  // начальное состояние (после restorePrefs)

  document.querySelectorAll(".insp-tabs .tab").forEach((t) =>
    t.addEventListener("click", () => {
      document.querySelectorAll(".insp-tabs .tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      state.activeTab = t.dataset.tab;
      if (state.selectedRec) renderTab();
    }));

  $("encoding").addEventListener("change", () => {
    obs.action("encoding_change", { value: $("encoding").value });
    // кодировка глобальна — перечитываем все файловые источники сессии
    state.session.files.forEach((f) => { if (f.file) readEntryFile(f); });
  });

  $("dumplog").addEventListener("click", (e) => { e.preventDefault(); obs.dump(); });
}

wireUp();
checkHealth();
