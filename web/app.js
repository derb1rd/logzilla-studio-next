"use strict";

// logZilla3000 — Inspector Split.
// Граница UI ↔ ядро (HTTP/JSON-контракт /api/parse · /api/export).
// Слой представления под 3-pane layout
// концепта 11: дерево источника слева, поток логов в центре, инспектор записи справа.
//
// МУЛЬТИФАЙЛОВАЯ СЕССИЯ. Сессия живёт во фронте: каждый файл = независимый
// ParseRequest через тот же single-source контракт (backend не тронут). Левая
// колонка — дерево файлов; активный файл питает поток/инспектор. «Контекст»
// собирает трассу по req_id ПО ВСЕМ файлам сессии (распределённый трейс).

let _serverVersion = "?";

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
  contextAnchor: null, // «якорь» вкладки Контекст: запись, ОТ которой строится трасса
                       // (req_id или окно ±6). Меняется только при выборе из потока
                       // (selectRow/стрелки); клики ВНУТРИ контекста его не двигают —
                       // иначе список пересчитывался на каждый клик и «скакал».
  queryTerms: [],      // текстовые термины поиска (для подсветки в строках)
  activeTab: "struct",
  totalLines: 0,
  // Закладки — ручной контекст: пользователь сам отмечает записи (★ / клавиша B),
  // они собираются в 3-ю вкладку инспектора единым плоским списком и могут быть
  // выгружены отдельно. Cross-file: храним {fileId, rec} в порядке добавления.
  // Session-only: объекты записей пересоздаются прогоном, поэтому закладки
  // сбрасываются при ре-парсе/очистке (и чистятся при удалении файла-источника).
  bookmarks: [],
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
  LEVEL_RE, norm, levelOf, bucketOf, fieldOf, tsOf, sourceOf, reqIdOf, msgOf, httpCtxOf, sqlOf, recordToLine,
  buildQuery, isEmptyQuery, matchesQuery, highlightSegments, crossContext,
} = LZ;

// --- health ----------------------------------------------------------------
async function checkHealth() {
  const el = $("health");
  try {
    const r = await obs.fetch("/api/health");
    const j = await r.json();
    _serverVersion = j.version || "?";
    el.textContent = `● ${j.service} v${j.version}`;
    el.className = "status ok";
  } catch {
    el.textContent = "● сервер недоступен";
    el.className = "status bad";
  }
}

// --- сбор ParseOptions (глобальные для всей сессии) -------------------------
function collectOptions() {
  // OTHER — клиентская категория, сервер её не знает; отправляем только реальные уровни.
  const SERVER_LEVELS = new Set(["ERROR", "WARN", "INFO", "DEBUG"]);
  const serverCbs = [...document.querySelectorAll(".lvl")].filter((c) => SERVER_LEVELS.has(c.value));
  const checkedServer = serverCbs.filter((c) => c.checked);
  return {
    // Источник всегда inline (текст уже раскодирован в браузере), поэтому серверу
    // кодировка не нужна; «авто» отдаём как utf-8, чтобы не упереться в enum контракта.
    encoding: $("encoding").value === "auto" ? "utf-8" : $("encoding").value,
    log_levels: checkedServer.length === serverCbs.length ? [] : checkedServer.map((c) => c.value),
    remove_duplicates: $("remove_duplicates").checked,
    remove_ansi: $("remove_ansi").checked,
    expand_message: $("expand_message").checked,
    strip_k8s: $("strip_k8s").checked,
    compact_json: $("compact_json").checked,
    format_sql: true,
    bind_sql_args: $("bind_sql_args").checked,
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
// Авто-определение кодировки: валидный UTF-8 (fatal) → UTF-8; иначе windows-1251
// (самая частая для ru-экспортов: cp1251-файл, прочитанный как utf-8, давал
// кракозябры «### ####» и мусорный парс). ASCII-файлы валидны как utf-8 → не
// трогаются.
function decodeAuto(buf) {
  const bytes = new Uint8Array(buf);
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    try { return new TextDecoder("windows-1251").decode(bytes); }
    catch { return new TextDecoder("utf-8").decode(bytes); }
  }
}

function readEntryFile(entry) {
  if (!entry.file) return;
  const enc = $("encoding").value;
  const reader = new FileReader();
  const finish = (text) => {
    entry.text = text;
    if (entry.status === "parsed") entry.status = "stale";  // кодировка сменилась → нужен ре-парс
    renderTree();
  };
  if (enc === "auto") {
    reader.onload = () => finish(decodeAuto(reader.result));
    reader.readAsArrayBuffer(entry.file);
  } else {
    reader.onload = () => finish(reader.result);
    reader.readAsText(entry.file, browserEncoding(enc));
  }
}

function setActive(id) {
  if (state.session.activeId === id) return;
  state.session.activeId = id;
  state.selectedRec = null; state.selected = -1; state.contextAnchor = null;
  obs.action("file_switch", { id });
  renderTree();
  renderActive();
}

function clearSession() {
  if (state.session.files.length === 0) return;
  state.session.files = [];
  state.session.activeId = null;
  state.selectedRec = null; state.selected = -1; state.contextAnchor = null;
  state.bookmarks = [];
  obs.action("session_clear", {});
  renderTree();
  renderActive();
  setFooter("Сессия очищена.");
}

function removeEntry(id) {
  const i = state.session.files.findIndex((f) => f.id === id);
  if (i < 0) return;
  state.session.files.splice(i, 1);
  // Записи удалённого файла больше не существуют — снимаем их закладки.
  state.bookmarks = state.bookmarks.filter((b) => b.fileId !== id);
  if (state.session.activeId === id) {
    const next = state.session.files[i] || state.session.files[i - 1] || null;
    state.session.activeId = next ? next.id : null;
    state.selectedRec = null; state.selected = -1; state.contextAnchor = null;
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

  const _opts = collectOptions();
  obs.action("parse_clicked", { files: files.length, levels: _opts.log_levels, bind_sql_args: _opts.bind_sql_args });
  $("parseBtn").disabled = true;
  $("parseBtn").classList.add("loading");
  showProgress(0);
  setFooter(`Парсинг… (0/${files.length})`);
  // Записи устаревают: их объекты пересоздаются прогоном. Закладки держат ссылки
  // на старые объекты записей → после ре-парса они «висячие». Session-only: сброс.
  state.selectedRec = null; state.selected = -1; state.contextAnchor = null;
  state.bookmarks = [];
  let _parseDone = 0;
  try {
    await runPool(files, 3, async (entry) => {
      await parseEntry(entry);
      _parseDone++;
      showProgress((_parseDone / files.length) * 100);
      setFooter(`Парсинг… (${_parseDone}/${files.length})`);
    });
  } finally {
    $("parseBtn").disabled = false;
    $("parseBtn").classList.remove("loading");
    hideProgress();
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
    renderSourcePanel();
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
  renderSourcePanel();

  const pw = (entry.result && entry.result.preview_window) || {};
  let note = "";
  if (pw.total_records > pw.returned) {
    note = ` · показаны первые ${ru(pw.returned)} из ${ru(pw.total_records)} — полный результат в «Экспорт»`;
  }
  applySearch();
  if (note) setFooter(`${entry.name}: ${ru(m.filtered)} записей${note}`);
}

function renderSourcePanel() {
  const container = $("srcFilters");
  if (!container) return;

  // Собираем уникальные источники с подсчётом
  const srcCounts = new Map();
  for (const rec of state.records) {
    const src = sourceOf(rec);
    srcCounts.set(src, (srcCounts.get(src) || 0) + 1);
  }

  if (srcCounts.size === 0) {
    container.innerHTML = '<span class="src-hint">появятся после парсинга</span>';
    return;
  }

  // Если единственный «источник» — пустая строка, источники не определяются.
  const namedCount = srcCounts.size - (srcCounts.has("") ? 1 : 0);
  if (namedCount === 0) {
    container.innerHTML = '<span class="src-hint">источник не определён</span>';
    return;
  }

  // Сохраняем предыдущий выбор (чтобы не сбрасывать при переключении файлов)
  const prevOff = new Set();
  container.querySelectorAll(".src-cb").forEach((c) => { if (!c.checked) prevOff.add(c.value); });

  // Сортируем: по убыванию числа записей, потом алфавитно; "" — всегда в конце
  const sorted = [...srcCounts.entries()].sort((a, b) => {
    if (a[0] === "" && b[0] !== "") return 1;
    if (a[0] !== "" && b[0] === "") return -1;
    return b[1] - a[1] || a[0].localeCompare(b[0]);
  });

  container.innerHTML = "";

  if (namedCount >= 2) {
    const actions = document.createElement("div");
    actions.className = "src-actions";
    const btnAll = document.createElement("button");
    btnAll.textContent = "Все";
    const btnNone = document.createElement("button");
    btnNone.textContent = "Снять";
    actions.append(btnAll, btnNone);
    btnAll.addEventListener("click", () => {
      container.querySelectorAll(".src-cb").forEach((c) => { c.checked = true; c.closest(".src-tile").classList.remove("off"); });
      applySearch();
    });
    btnNone.addEventListener("click", () => {
      container.querySelectorAll(".src-cb").forEach((c) => { c.checked = false; c.closest(".src-tile").classList.add("off"); });
      applySearch();
    });
    container.appendChild(actions);
  }

  for (const [src, cnt] of sorted) {
    const label = document.createElement("label");
    label.className = "src-tile" + (prevOff.has(src) ? " off" : "") + (src === "" ? " src-unknown" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "src-cb";
    cb.value = src;
    cb.checked = !prevOff.has(src);
    label.appendChild(cb);
    const nameEl = document.createElement("span");
    nameEl.className = "src-name";
    nameEl.textContent = src || "—";
    if (src === "") label.title = "записи без определённого источника";
    label.appendChild(nameEl);
    const cntEl = document.createElement("span");
    cntEl.className = "src-count";
    cntEl.textContent = ru(cnt);
    label.appendChild(cntEl);
    cb.addEventListener("change", () => {
      label.classList.toggle("off", !cb.checked);
      applySearch();
    });
    container.appendChild(label);
  }
}

function updateLevelCounts() {
  const counts = { ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0, OTHER: 0 };
  for (const rec of state.records) {
    counts[bucketOf(rec)]++;   // bucketOf всегда возвращает один из пяти ключей counts
  }
  for (const lvl of Object.keys(counts)) $("cnt-" + lvl).textContent = ru(counts[lvl]);
}

// Обновляет счётчики плиток уровней и источников с учётом активных фильтров.
// Уровни считаются из записей, прошедших фильтр источников+поиска (без фильтра уровней),
// источники — из записей, прошедших фильтр уровней+поиска (без фильтра источников).
function _refreshFilterCounts(off, offSrc, q, noQuery) {
  const lvlCounts = { ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0, OTHER: 0 };
  const srcCounts = {};
  for (const rec of state.records) {
    const effectiveLvl = bucketOf(rec);   // корзина из пяти — FATAL/CRITICAL→ERROR, TRACE→DEBUG
    const src = sourceOf(rec);
    const passesQuery = noQuery || matchesQuery(rec, q);
    if (passesQuery && (offSrc.size === 0 || !offSrc.has(src)) && effectiveLvl in lvlCounts)
      lvlCounts[effectiveLvl]++;
    if (passesQuery && !off.has(effectiveLvl))
      srcCounts[src] = (srcCounts[src] || 0) + 1;
  }
  for (const lvl of Object.keys(lvlCounts)) $("cnt-" + lvl).textContent = ru(lvlCounts[lvl]);
  document.querySelectorAll(".src-cb").forEach((cb) => {
    const cntEl = cb.parentElement?.querySelector(".src-count");
    if (cntEl) cntEl.textContent = ru(srcCounts[cb.value] || 0);
  });
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

// --- виртуальный скроллер --------------------------------------------------
// При больших view рендерим только видимые строки + VIRT_BUF с каждой стороны.
// Полный список (все div-ы в DOM) при 8k+ строк вешает браузер на несколько секунд;
// виртуализация держит DOM в пределах ~150 узлов независимо от размера файла.
const VIRT_THRESH = 200;   // ниже — прямой рендер (без оверхеда виртуализации)
const VIRT_BUF   = 30;     // строк-буфер сверху и снизу от вьюпорта
let _rowH = 28;            // оценка высоты строки px; уточняется после первого рендера
let _virtActive = false;   // true пока активен виртуальный скроллер
let _scrollRaf  = null;    // guard для rAF — один перерисов за кадр

function _makeStreamRow(rec, i) {
  const lvl = levelOf(rec) || "OTHER";   // сырая метка (FATAL/CRITICAL/TRACE сохраняем в тексте)
  const bucket = bucketOf(rec);          // корзина из пяти — для цвета строки/чипа
  const bm = isBookmarked(rec);
  const row = document.createElement("div");
  row.className = "stream-row lvl-" + bucket +
    (i === state.selected ? " selected" : "") +
    (bm ? " bookmarked" : "");
  row.dataset.idx = i;
  const cell = (cls, txt) => { const s = document.createElement("span"); s.className = cls; s.textContent = txt; return s; };

  // gutter: ★ прозрачная → видна при hover → accent при закладке; клик = тоггл без выбора
  const bmCell = cell("r-bm", "★");
  bmCell.title = bm ? "Убрать из закладок (B)" : "В закладки (B)";
  bmCell.addEventListener("click", (e) => {
    e.stopPropagation();
    const r = state.view[i];
    if (r) { toggleBookmark(r); paintBookmarks(); renderInspector(); }
  });
  row.appendChild(bmCell);

  const src = sourceOf(rec);
  row.appendChild(cell("r-n", i + 1));
  row.appendChild(cell("r-ts", tsOf(rec) || "—"));
  row.appendChild(cell("r-lvl " + bucket, lvl));
  row.appendChild(cell("r-src" + (src ? "" : " empty"), src || "?"));
  row.appendChild(msgCell(rec));
  row.addEventListener("click", () => selectRow(i));
  return row;
}

// Обновляет gutter-маркеры закладок в видимых строках без полного перерисовки потока.
function paintBookmarks() {
  const root = _virtActive ? $("v-rows") : $("stream");
  if (!root) return;
  root.querySelectorAll(".stream-row").forEach((el) => {
    const rec = state.view[Number(el.dataset.idx)];
    const bm = rec && isBookmarked(rec);
    el.classList.toggle("bookmarked", !!bm);
    const g = el.querySelector(".r-bm");
    if (g) g.title = bm ? "Убрать из закладок (B)" : "В закладки (B)";
  });
}

function _virtPaint() {
  _scrollRaf = null;
  const box = $("stream");
  const rows = $("v-rows");
  if (!rows) return;
  const n = state.view.length;
  const viewH = box.clientHeight;
  const top   = box.scrollTop;

  const first = Math.max(0, Math.floor(top / _rowH) - VIRT_BUF);
  const last  = Math.min(n - 1, Math.ceil((top + viewH) / _rowH) + VIRT_BUF);

  $("v-top").style.height = (first * _rowH) + "px";
  $("v-bot").style.height = (Math.max(0, n - 1 - last) * _rowH) + "px";

  rows.textContent = "";  // быстрее innerHTML="" для DOM-узлов
  const frag = document.createDocumentFragment();
  for (let i = first; i <= last; i++) frag.appendChild(_makeStreamRow(state.view[i], i));
  rows.appendChild(frag);

  // уточняем высоту строки по первому реально отрисованному элементу
  const sample = rows.firstElementChild;
  if (sample && sample.offsetHeight > 0) _rowH = sample.offsetHeight;
}

function hiddenLevels() {
  const off = new Set();
  document.querySelectorAll(".lvl").forEach((c) => { if (!c.checked) off.add(c.value); });
  return off;
}

function hiddenSources() {
  const off = new Set();
  document.querySelectorAll(".src-cb").forEach((c) => { if (!c.checked) off.add(c.value); });
  return off;
}

function applySearch() {
  const q = buildQuery($("search").value);
  state.queryTerms = q.text;                      // термины для подсветки в строках
  const noQuery = isEmptyQuery(q);
  const off = hiddenLevels();
  const offSrc = hiddenSources();
  state.view = state.records.filter((rec) => {
    if (off.has(bucketOf(rec))) return false;     // плитка уровня выключена
    if (offSrc.size > 0 && offSrc.has(sourceOf(rec))) return false;  // плитка источника выключена
    return noQuery || matchesQuery(rec, q);
  });
  // Выбор хранится по идентичности записи (state.selectedRec), а индекс в потоке
  // пересчитываем. Если запись выпала из выборки — индекс -1 (подсветки в потоке
  // нет), но инспектор продолжает показывать её с пометкой «скрыта фильтром».
  state.selected = state.selectedRec ? state.view.indexOf(state.selectedRec) : -1;
  _refreshFilterCounts(off, offSrc, q, noQuery);
  renderStream();
  renderInspector();
}

// Маскот Zilla для пустых состояний (то же тело, что в топбаре) — приглушённый.
const MASCOT_SVG =
  `<svg class="empty-mascot" width="44" height="44" viewBox="0 0 64 64" aria-hidden="true">` +
  `<path d="M6 38 C6 26, 14 18, 26 17 C32 16, 38 18, 42 22 L58 22 L54 28 L58 30 L52 34 L54 40 C54 50, 44 56, 32 56 C16 56, 6 50, 6 38 Z" fill="var(--accent)" stroke="#0f0b1a" stroke-width="2" stroke-linejoin="round"/>` +
  `<circle cx="36" cy="30" r="5" fill="#fff" stroke="#0f0b1a" stroke-width="1.4"/>` +
  `<circle cx="36.5" cy="30.5" r="2.2" fill="#0f0b1a"/></svg>`;

// Разметка пустого состояния: маскот + заголовок + (опц.) подсказка.
function emptyState(title, hint) {
  return MASCOT_SVG +
    `<span class="empty-title">${title}</span>` +
    (hint ? `<span class="empty-hint">${hint}</span>` : "");
}

function renderStream() {
  const box = $("stream");
  box.onscroll = null;
  _virtActive = false;
  box.innerHTML = "";

  // счётчик потока: видно из окна (если клиентский фильтр сузил — показываем оба числа)
  const visible = state.view.length, win = state.records.length;
  $("t-shown").textContent = visible === win
    ? `${ru(win)} / ${ru(state.totalLines || win)} строк`
    : `${ru(visible)} из ${ru(win)} в окне`;

  if (state.records.length === 0) {
    const entry = activeEntry();
    const body = !entry ? emptyState("Здесь появится поток логов", "Добавьте файлы в сессию, затем «Парсить».")
      : entry.status === "error" ? emptyState("Ошибка парсинга", escapeHtml(entry.error || ""))
      : entry.status === "parsed" ? emptyState("Парсер не извлёк записей", "Проверьте формат и кодировку файла.")
      : emptyState("Готово к парсингу", "Нажмите «Парсить».");
    box.innerHTML = `<div class="stream-empty">${body}</div>`;
    return;
  }
  if (state.view.length === 0) {
    box.innerHTML = `<div class="stream-empty">${emptyState("Ничего не найдено", "Смягчите фильтр или измените запрос.")}</div>`;
    return;
  }

  if (state.view.length > VIRT_THRESH) {
    // Виртуальный скроллер: три зоны — верхний спейсер, видимые строки, нижний спейсер.
    _virtActive = true;
    box.scrollTop = 0;
    box.insertAdjacentHTML("afterbegin",
      '<div id="v-top"></div><div id="v-rows"></div><div id="v-bot"></div>');
    box.onscroll = () => {
      if (_scrollRaf) return;
      _scrollRaf = requestAnimationFrame(_virtPaint);
    };
    _virtPaint();
    return;
  }

  // Прямой рендер для небольших списков
  const frag = document.createDocumentFragment();
  state.view.forEach((rec, i) => frag.appendChild(_makeStreamRow(rec, i)));
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
  // Тип исключения для трейсбеков: показываем короткое имя (последний сегмент).
  if (rec.type && (rec.frames || rec.traceback || rec.sub_exceptions)) {
    sep(); chip("r-exc-type", String(rec.type).split(".").pop());
  }
  // Network error: адрес/порт назначения и причина ошибки.
  if (rec.error_addr) { sep(); chip("r-net-addr", rec.error_port ? `${rec.error_addr}:${rec.error_port}` : String(rec.error_addr)); }
  if (rec.error_type && !rec.error_addr) { sep(); chip("r-net-error", String(rec.error_type).replace(/_/g, " ")); }
  if (rec.rpc_code) { sep(); chip("r-net-error", `rpc:${rec.rpc_code}`); }
  if (c.base) { sep(); appendHighlighted(span, c.base); }
  if (c.sql) { sep(); chip("r-sql", "sql"); span.appendChild(document.createTextNode(" ")); appendHighlighted(span, c.sql); }
  if (!started && !c.base) span.textContent = msgOf(rec);   // запас на пустую запись
  return span;
}

// Обогащённый предпросмотр как безопасный HTML (для инспектора и списков контекста,
// где рендер идёт через innerHTML). Те же чипы, но текст экранирован.
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
  const root = _virtActive ? $("v-rows") : $("stream");
  if (!root) return;
  root.querySelectorAll(".stream-row").forEach((el) =>
    el.classList.toggle("selected", Number(el.dataset.idx) === state.selected));
}

function scrollToSelected() {
  if (state.selected < 0) return;
  if (_virtActive) {
    const box = $("stream");
    const targetTop = state.selected * _rowH;
    const targetBot = targetTop + _rowH;
    // Скроллим только если строка вне видимой зоны
    if (targetTop < box.scrollTop || targetBot > box.scrollTop + box.clientHeight) {
      box.scrollTop = Math.max(0, targetTop - box.clientHeight / 2);
    }
    // Перерисовываем виртуальное окно немедленно (без ожидания события scroll)
    _virtPaint();
    return;
  }
  const el = $("stream").querySelector(`.stream-row[data-idx="${state.selected}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}

function selectRow(i) {
  state.selected = i;
  state.selectedRec = state.view[i] || null;
  // Выбор из потока — «свежая» точка навигации: пере-якорим контекст на неё.
  state.contextAnchor = state.selectedRec;
  obs.action("inspect_row", { idx: i });
  paintSelection();
  renderInspector();
  scrollToSelected();
}

// Выбор записи по идентичности (из контекста) В ТЕКУЩЕМ файле. Если запись
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

// Снять выбор: закрывает инспектор (и drawer на узких экранах), гасит подсветку.
function closeInspector() {
  state.selectedRec = null;
  state.selected = -1;
  obs.action("inspect_close", {});
  paintSelection();
  renderInspector();
}

// Шаг выбора по потоку (стрелки ↑/↓).
function moveSelection(delta) {
  if (state.view.length === 0) return;
  let i = state.selected;
  i = i < 0 ? (delta > 0 ? 0 : state.view.length - 1) : i + delta;
  i = Math.max(0, Math.min(state.view.length - 1, i));
  selectRow(i);
}

// --- закладки (ручной контекст) ---------------------------------------------
// id файла-владельца записи: ищем по всем файлам сессии (cross-file). Нужен,
// чтобы строки закладок были кликабельны через тот же bindCtxRows (data-fid/ri).
function fileIdOf(rec) {
  const f = state.session.files.find((x) => x.records && x.records.includes(rec));
  return f ? f.id : null;
}

const isBookmarked = (rec) => state.bookmarks.some((b) => b.rec === rec);

// Переключить закладку для записи. Идентичность — по объекту записи (как и весь
// выбор в приложении). Возвращает новое состояние (true = добавлена).
function toggleBookmark(rec) {
  if (!rec) return false;
  const i = state.bookmarks.findIndex((b) => b.rec === rec);
  if (i >= 0) {
    state.bookmarks.splice(i, 1);
    obs.action("bookmark_remove", { count: state.bookmarks.length });
    return false;
  }
  state.bookmarks.push({ fileId: fileIdOf(rec) || state.session.activeId, rec });
  obs.action("bookmark_add", { count: state.bookmarks.length });
  return true;
}

// Тоггл закладки для текущей записи инспектора (pill-кнопка / клавиша B).
function toggleBookmarkSelected() {
  if (!state.selectedRec) return;
  toggleBookmark(state.selectedRec);
  paintBookmarks();    // обновит gutter-маркеры в потоке
  renderInspector();   // обновит pill-кнопку, счётчик вкладки и (если открыта) список
}

// Счётчик в ярлыке вкладки: «Закладки» / «Закладки · N».
function updateBookmarkTab() {
  const tab = $("tabMarks");
  if (!tab) return;
  const n = state.bookmarks.length;
  tab.textContent = n ? `Закладки · ${n}` : "Закладки";
}

// --- инспектор (право) ------------------------------------------------------
// Экранируем и кавычки: summaryHtml/contextHtml вставляют значения в HTML-атрибуты
// (data-rid, title), а они приходят из содержимого логов (req_id) и имён файлов.
// Без экранирования " и ' возможна инъекция атрибута/XSS из лога.
const _HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => _HTML_ESCAPES[c]);
}

function renderInspector() {
  const head = $("inspHead");
  const tabs = $("inspTabs");
  const body = $("inspBody");
  const rec = state.selectedRec;
  document.body.classList.toggle("inspect", !!rec);   // drawer-режим на узких экранах
  updateBookmarkTab();
  if (!rec) {
    head.innerHTML = `<div class="insp-empty">${emptyState("Запись не выбрана", "Выберите строку в потоке, чтобы раскрыть её.")}</div>`;
    tabs.hidden = true;
    body.innerHTML = "";
    return;
  }
  const lvl = levelOf(rec) || "OTHER";
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
    `<span class="insp-badge ${bucketOf(rec)}">${lvl}</span>` +
    `<span class="insp-id">#${ri + 1} · ${escapeHtml(tsOf(rec) || "—")}</span>` +
    reqChip +
    hidden +
    `</div>` +
    `<div class="insp-title">${summaryHtml(rec)}</div>`;
  const chipEl = head.querySelector(".req-chip");
  if (chipEl) chipEl.addEventListener("click", () => pivotToReq(chipEl.dataset.rid));
  tabs.hidden = false;
  // обновляем pill-кнопку «В закладки» в баре вкладок
  const pill = $("bmPill");
  if (pill) {
    const marked = isBookmarked(rec);
    pill.textContent = marked ? "★ Убрать" : "☆ В закладки";
    pill.title = marked ? "Убрать из закладок (B)" : "Добавить в закладки (B)";
    pill.className = "bm-pill" + (marked ? " on" : "");
    pill.onclick = toggleBookmarkSelected;
  }
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
    body.innerHTML = sqlBlockHtml(rec) + tracebackBlockHtml(rec) + jsonHtml(rec) + histoHtml();
  } else if (state.activeTab === "marks") {
    body.innerHTML = bookmarksHtml();
    bindBookmarkRows();
  } else {
    body.innerHTML = contextHtml();
    bindCtxRows();
  }
}

// Вкладка «Закладки» — плоский список вручную отмеченных записей по всей сессии
// (в порядке добавления), в том же визуальном ключе, что и «Контекст». Кликабельны
// (выбор записи, при необходимости — переключение файла); ✕ убирает из закладок.
function bookmarksHtml() {
  if (!state.bookmarks.length) {
    return `<div class="insp-empty">` +
      emptyState("Закладок нет",
        "Откройте запись и нажмите ★ (или клавишу B), чтобы собрать ручной контекст. " +
        "Закладки можно выгрузить отдельно: «только закладки» в панели экспорта.") +
      `</div>`;
  }
  const nFiles = new Set(state.bookmarks.map((b) => b.fileId)).size;
  const multi = nFiles > 1;
  const label = `${ru(state.bookmarks.length)} закладок` + (multi ? ` · ${nFiles} файла` : "");
  let html = `<div class="histo-label">${label}</div>`;
  state.bookmarks.forEach((b, n) => {
    const f = state.session.files.find((x) => x.id === b.fileId);
    const i = f ? f.records.indexOf(b.rec) : -1;
    const center = b.rec === state.selectedRec ? " center" : "";
    const fileBadge = multi && f ? `<span class="ctx-file" title="${escapeHtml(f.name)}">${escapeHtml(shortName(f.name))}</span>` : "";
    html += `<div class="ctx-row mark-row${multi ? " xfile" : ""}${center}" data-fid="${b.fileId}" data-ri="${i}" data-bn="${n}">` +
      `<span class="c-n">${i >= 0 ? i + 1 : "—"}</span>` +
      fileBadge +
      `<span class="r-lvl ${bucketOf(b.rec)}">${levelOf(b.rec) || "OTHER"}</span>` +
      `<span class="mark-msg">${summaryHtml(b.rec)}</span>` +
      `<button class="mark-del" data-bn="${n}" title="Убрать из закладок">✕</button></div>`;
  });
  return html;
}

// Клик по строке закладки → выбор записи (cross-file через selectRecordIn);
// клик по ✕ убирает из закладок, не открывая запись (stopPropagation).
function bindBookmarkRows() {
  const body = $("inspBody");
  body.querySelectorAll(".mark-del").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const n = +btn.dataset.bn;
      if (n >= 0 && n < state.bookmarks.length) state.bookmarks.splice(n, 1);
      obs.action("bookmark_remove", { count: state.bookmarks.length });
      paintBookmarks();
      renderInspector();   // перерисует список и счётчик (activeTab остаётся marks)
    });
  });
  body.querySelectorAll(".mark-row[data-fid]").forEach((el) => {
    el.addEventListener("click", () => {
      const b = state.bookmarks[+el.dataset.bn];
      if (b) selectRecordIn(b.fileId, b.rec);
    });
  });
}

// Делает строки контекста кликабельными → выбор той же записи (с учётом
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

// Выделенный SQL-блок инспектора: полная (отформатированная ядром через sqlparse)
// форма запроса с подсветкой ключевых слов. Здесь форматирование ядра наконец
// окупается — в потоке/шапке SQL остаётся однострочным сниппетом. Для записей без
// SQL — пустая строка (блок не появляется).
const SQL_KW = /\b(SELECT|FROM|WHERE|INNER|LEFT|RIGHT|OUTER|FULL|CROSS|JOIN|ON|USING|AND|OR|NOT|IN|EXISTS|AS|ORDER|GROUP|BY|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|INSERT|INTO|VALUES|UPDATE|SET|DELETE|RETURNING|WITH|CASE|WHEN|THEN|ELSE|END|NULL|IS|LIKE|ILIKE|BETWEEN|ASC|DESC|ON\s+CONFLICT)\b/gi;
function sqlHighlight(sql) {
  return escapeHtml(sql).replace(SQL_KW, (m) => `<span class="sql-kw">${m}</span>`);
}
function sqlBlockHtml(rec) {
  const sql = sqlOf(rec);
  if (!sql) return "";
  return `<div class="sql-block"><div class="sql-block-label">SQL</div>` +
    `<pre class="sql-code">${sqlHighlight(sql)}</pre></div>`;
}

// Traceback-блок инспектора: полный текст трейсбека/стека в pre-блоке с типом ошибки.
// traceback/stacktrace скрываются из jsonHtml (TB_KEYS) — здесь они показываются полностью.
function tracebackBlockHtml(rec) {
  const tb = (typeof rec.traceback === "string" && rec.traceback)
           || (typeof rec.stacktrace === "string" && rec.stacktrace);
  if (!tb) return "";
  const typeHtml = rec.type
    ? ` · <span class="tb-type">${escapeHtml(String(rec.type))}</span>` : "";
  const fc = Array.isArray(rec.frames) ? rec.frames.length : 0;
  const fcHtml = fc ? `<span class="tb-frames">${fc} frame${fc !== 1 ? "s" : ""}</span>` : "";
  return `<div class="tb-block">` +
    `<div class="tb-block-label">TRACEBACK${typeHtml}${fcHtml}</div>` +
    `<pre class="tb-code">${escapeHtml(tb)}</pre>` +
    `</div>`;
}

// traceback/stacktrace показываются в tb-block — в JSON-виде дублировать не нужно.
const TB_KEYS = new Set(["traceback", "stacktrace"]);

// pretty-print JSON с подсветкой; уровень окрашиваем по значению
function jsonHtml(rec) {
  const lines = ['<div class="json-line">{</div>'];
  const entries = Object.entries(rec).filter(([k]) => !TB_KEYS.has(k));
  entries.forEach(([k, v], idx) => {
    const comma = idx < entries.length - 1 ? "," : "";
    lines.push(`<div class="json-line" style="padding-left:14px"><span class="j-key">"${escapeHtml(k)}"</span>: ${valHtml(k, v)}${comma}</div>`);
  });
  lines.push('<div class="json-line">}</div>');
  return lines.join("");
}

// Компактный рендер массива frames / sub_exceptions вместо JSON.stringify-blob.
function framesHtml(arr, key) {
  const items = arr.map((f, i) => {
    if (key === "sub_exceptions") {
      const t = escapeHtml(String(f.type || "?"));
      const m = f.message ? `: <span class="j-str">"${escapeHtml(String(f.message))}"</span>` : "";
      return `<div class="frame-item"><span class="j-key">${t}</span>${m}</div>`;
    }
    const loc = escapeHtml(`${f.file || "?"}:${f.line || "?"}`);
    const fn = f.function ? ` <span class="frame-fn">${escapeHtml(f.function)}</span>` : "";
    return `<div class="frame-item"><span class="frame-n">${i}</span><span class="j-str">${loc}</span>${fn}</div>`;
  }).join("");
  return `<div class="frames-list">[${items}]</div>`;
}

function valHtml(key, v) {
  if (v === null) return '<span class="j-null">null</span>';
  if (typeof v === "boolean") return `<span class="j-bool">${v}</span>`;
  if (typeof v === "number") return `<span class="j-num">${v}</span>`;
  if (typeof v === "object") {
    if (Array.isArray(v) && v.length && (key === "frames" || key === "sub_exceptions")) {
      return framesHtml(v, key);
    }
    return `<span class="j-str">${escapeHtml(JSON.stringify(v))}</span>`;
  }
  // Схлопываем переносы внутри значения: компактная запись остаётся однострочной
  // (полную форму многострочного SQL показывает выделенный SQL-блок выше).
  const flat = String(v).replace(/\s*\n\s*/g, " ");
  const s = escapeHtml(flat);
  const m = flat.match(LEVEL_RE);
  if (m && String(v).length < 12) return `<span class="j-lvl-${norm(m[1])}">"${s}"</span>`;
  return `<span class="j-str">"${s}"</span>`;
}

// контекст — все строки того же запроса (req_id/trace_id) ПО ВСЕЙ СЕССИИ. Это и
// есть распределённый трейс: одна ось req_id, склеивающая логи разных сервисов/
// файлов. Если id нет — контекста нет (соседние строки не показываем: разные
// процессы вперемешку → шум). Берём из полных окон state.session.files (не из
// отфильтрованного view), чтобы фильтр уровней не рвал трассу запроса.
function contextHtml() {
  // Контекст строится от ЯКОРЯ, а не от текущего выбора: клик по строке внутри
  // контекста меняет selectedRec, но НЕ якорь — поэтому список (и его счётчик)
  // стабильны, двигается только подсветка «center». Якорь меняется лишь при
  // выборе из потока (selectRow). Фолбэк на selectedRec — для первого открытия.
  const anchor = state.contextAnchor || state.selectedRec;
  const rid = reqIdOf(anchor);
  // Контекст = ТОЛЬКО трасса одного req_id/trace_id по всей сессии. Соседние
  // строки не показываем: в проде логи разных процессов перемешаны, и «соседи»
  // — случайный шум, а не контекст запроса. Нет id → честно говорим, что связать
  // не с чем.
  if (!rid) {
    return `<div class="insp-empty">` +
      emptyState("Нет req_id / trace_id",
        "У записи нет идентификатора запроса — связать не с чем. " +
        "Контекст показывает только строки одного req_id по всей сессии.") +
      `</div>`;
  }
  const sessionFiles = state.session.files
    .filter((f) => f.status === "parsed")
    .map((f) => ({ fileId: f.id, name: f.name, records: f.records }));
  const rows = crossContext(sessionFiles, rid);   // [{ fileId, name, i, rec }]
  const nFiles = new Set(rows.map((r) => r.fileId)).size;
  const label = `req_id = ${escapeHtml(rid)} · ${ru(rows.length)} строк` + (nFiles > 1 ? ` · ${nFiles} файла` : "");
  const multi = nFiles > 1;
  let html = `<div class="histo-label">${label}</div>`;
  for (const { fileId, name, i, rec } of rows) {
    // Подсвечиваем строку, которую сейчас инспектируем (selectedRec), — она ходит
    // внутри СТАБИЛЬНОГО по якорю списка.
    const center = rec === state.selectedRec ? " center" : "";
    const fileBadge = multi ? `<span class="ctx-file" title="${escapeHtml(name)}">${escapeHtml(shortName(name))}</span>` : "";
    html += `<div class="ctx-row${multi ? " xfile" : ""}${center}" data-fid="${fileId}" data-ri="${i}">` +
      `<span class="c-n">${i + 1}</span>` +
      fileBadge +
      `<span class="r-lvl ${bucketOf(rec)}">${levelOf(rec) || "OTHER"}</span>` +
      `<span>${summaryHtml(rec)}</span></div>`;
  }
  return html;
}

// гистограмма — распределение выбранного уровня по окну предпросмотра (20 бинов)
function histoHtml() {
  const n = state.records.length;
  if (n === 0) return "";
  const lvl = bucketOf(state.selectedRec);   // группируем по корзине → согласовано с плитками
  const BINS = 20;
  const bins = new Array(BINS).fill(0);
  state.records.forEach((rec, i) => {
    if (bucketOf(rec) === lvl) bins[Math.min(BINS - 1, Math.floor((i / n) * BINS))]++;
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
    `<div class="histo-label">Уровень ${lvl} по окну · всего ${ru(bins.reduce((a, b) => a + b, 0))}</div>` +
    `<div class="histo-bars">${bars}</div>` +
    `<div class="histo-axis"><span>начало</span><span>здесь</span><span>конец</span></div></div>`;
}

// --- экспорт ----------------------------------------------------------------
async function doExport() {
  if ($("exportScope").value === "all") return doExportAll();
  if ($("exportScope").value === "bookmarks") return doExportBookmarks();
  const entry = activeEntry();
  if (!entry || !entry.request) { setFooter("Сначала выполните парсинг."); return; }
  const ndjson = $("ndjson").checked;
  const flatten = $("flatten").checked;

  if ($("export_filtered").checked) {
    const records = state.view;   // уже отфильтровано: уровни + источники + поиск
    const ext = ndjson ? "ndjson" : "json";
    const mime = ndjson ? "application/x-ndjson" : "application/json";
    const name = `${baseName(entry.name)}_filtered_${exportTs()}.${ext}`;
    obs.action("export_filtered_clicked", { records: records.length, ndjson, file: entry.name });
    downloadBlob(new Blob([_serializeFiltered(records, ndjson, flatten)], { type: mime }), name);
    setFooter(`Экспортировано (видимые): ${ru(records.length)} записей → ${name}`);
    return;
  }

  const req = { version: "1", parse_request: entry.request, options: { ndjson, flatten } };
  obs.action("export_clicked", { ndjson: req.options.ndjson, file: entry.name });
  $("exportBtn").disabled = true;
  $("exportBtn").classList.add("loading");
  showProgress(null);
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
    const ext = req.options.ndjson ? "ndjson" : "json";
    const name = `${baseName(entry.name)}_${exportTs()}.${ext}`;
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
  } finally {
    $("exportBtn").disabled = false;
    $("exportBtn").classList.remove("loading");
    hideProgress();
  }
}

// Экспорт всех файлов сессии — каждый отдельным <имя>.json (по файлу на источник).
// Каждый файл перепарсивается через свой ParseRequest (полный набор, до MAX_RECORDS)
// и скачивается самостоятельно. Между загрузками — пауза: браузеры троттлят
// множественные программные скачивания. Ошибка одного файла не прерывает остальные.
async function doExportAll() {
  const parsed = state.session.files.filter((f) => f.records?.length && f.status === "parsed");
  if (!parsed.length) { setFooter("Нет распарсенных файлов для экспорта."); return; }
  const ndjson = $("ndjson").checked;
  const flatten = $("flatten").checked;
  const ext = ndjson ? "ndjson" : "json";
  const mime = ndjson ? "application/x-ndjson" : "application/json";
  obs.action("export_all_clicked", { files: parsed.length, ndjson });

  if ($("export_filtered").checked) {
    $("exportBtn").disabled = true;
    $("exportBtn").classList.add("loading");
    showProgress(0);
    const ts = exportTs();
    const nameCounts = new Map();
    try {
      for (let k = 0; k < parsed.length; k++) {
        const f = parsed[k];
        const recs = _filteredRecords(f.records);
        const base = baseName(f.name);
        const n = (nameCounts.get(base) || 0) + 1;
        nameCounts.set(base, n);
        const fname = n > 1 ? `${base}_filtered_${ts}_${n}.${ext}` : `${base}_filtered_${ts}.${ext}`;
        downloadBlob(new Blob([_serializeFiltered(recs, ndjson, flatten)], { type: mime }), fname);
        setFooter(`Экспорт (видимые)… (${k + 1}/${parsed.length})`);
        showProgress(((k + 1) / parsed.length) * 100);
        if (k < parsed.length - 1) await sleep(250);
      }
    } finally {
      $("exportBtn").disabled = false;
      $("exportBtn").classList.remove("loading");
      hideProgress();
    }
    obs.action("export_all_filtered_done", { files: parsed.length });
    setFooter(`Экспорт (видимые) завершён: ${parsed.length} файлов`);
    return;
  }


  $("exportBtn").disabled = true;
  $("exportBtn").classList.add("loading");
  showProgress(0);
  setFooter(`Экспорт файлов… (0/${parsed.length})`);

  const ts = exportTs();
  const nameCounts = new Map();
  const failures = [];
  let truncatedAny = false;
  try {
    for (let k = 0; k < parsed.length; k++) {
      const f = parsed[k];
      const req = { version: "1", parse_request: f.request, options: { ndjson, flatten } };
      try {
        const r = await obs.fetch("/api/export", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error(j.error?.message || r.status);
        }
        if (r.headers.get("X-Truncated") === "true") truncatedAny = true;
        const base = baseName(f.name);
        const n = (nameCounts.get(base) || 0) + 1;
        nameCounts.set(base, n);
        const fname = n > 1 ? `${base}_${ts}_${n}.${ext}` : `${base}_${ts}.${ext}`;
        downloadBlob(await r.blob(), fname);
        setFooter(`Экспорт файлов… (${k + 1}/${parsed.length})`);
        showProgress(((k + 1) / parsed.length) * 100);
      } catch (e) {
        failures.push(`${f.name}: ${e.message}`);
      }
      if (k < parsed.length - 1) await sleep(250);   // дать браузеру принять загрузку
    }
  } finally {
    $("exportBtn").disabled = false;
    $("exportBtn").classList.remove("loading");
    hideProgress();
  }

  const ok = parsed.length - failures.length;
  obs.action("export_all_done", { files: parsed.length, ok, failed: failures.length, truncated: truncatedAny });
  let msg = `Экспортировано файлов: ${ok}/${parsed.length}`;
  if (truncatedAny) msg += " · ⚠ часть файлов обрезана по лимиту";
  if (failures.length) msg += " · ошибки: " + failures.join("; ");
  setFooter(msg);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Экспорт ТОЛЬКО закладок — клиентский, как и «только видимые»: серверный
// /api/export перепарсивает файл и о ручном выборе не знает, поэтому сериализуем
// сами объекты отмеченных записей через тот же _serializeFiltered (флаги NDJSON и
// «Плоская структура» учтены). Закладки cross-file → берём из state.bookmarks.
function doExportBookmarks() {
  if (!state.bookmarks.length) { setFooter("Нет закладок для экспорта."); return; }
  const ndjson = $("ndjson").checked;
  const flatten = $("flatten").checked;
  const records = state.bookmarks.map((b) => b.rec);
  const ext = ndjson ? "ndjson" : "json";
  const mime = ndjson ? "application/x-ndjson" : "application/json";
  const name = `bookmarks_${exportTs()}.${ext}`;
  obs.action("export_bookmarks", { records: records.length, ndjson, flatten });
  downloadBlob(new Blob([_serializeFiltered(records, ndjson, flatten)], { type: mime }), name);
  setFooter(`Экспортировано закладок: ${ru(records.length)} → ${name}`);
}

// --- клиентская сериализация для «только видимые» ----------------------------
function _flattenRec(obj, prefix = "", sep = ".") {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}${sep}${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      Object.assign(out, _flattenRec(v, key, sep));
    } else {
      out[key] = v;
    }
  }
  return out;
}

function _serializeFiltered(records, ndjson, doFlatten) {
  const recs = doFlatten ? records.map((r) => _flattenRec(r)) : records;
  const meta = { logzilla_version: _serverVersion, exported_at: new Date().toISOString(), filtered: true };
  if (ndjson) {
    return [JSON.stringify({ _logzilla: meta }), ...recs.map((r) => JSON.stringify(r))].join("\n") + "\n";
  }
  return JSON.stringify({ _logzilla: meta, records: recs }, null, 2) + "\n";
}

function _filteredRecords(recs) {
  const off = hiddenLevels();
  const offSrc = hiddenSources();
  if (off.size === 0 && offSrc.size === 0) return recs;
  return recs.filter((rec) => {
    if (off.has(bucketOf(rec))) return false;   // корзина из пяти — иначе FATAL/CRITICAL/TRACE утекают
    if (offSrc.size > 0 && offSrc.has(sourceOf(rec))) return false;
    return true;
  });
}

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

// Компактная метка времени для имени файла: 20240617T143022
function exportTs() {
  const d = new Date();
  return d.getFullYear().toString()
    + String(d.getMonth() + 1).padStart(2, "0")
    + String(d.getDate()).padStart(2, "0") + "T"
    + String(d.getHours()).padStart(2, "0")
    + String(d.getMinutes()).padStart(2, "0")
    + String(d.getSeconds()).padStart(2, "0");
}

// --- prefs (localStorage) ---------------------------------------------------
const PREFS_KEY = "logzilla3000.prefs.v1";
const PREF_CHECKS = ["compact_json", "remove_duplicates", "remove_ansi", "expand_message", "strip_k8s", "bind_sql_args", "ndjson", "flatten", "export_filtered"];

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

// --- прогресс-бар -----------------------------------------------------------
function showProgress(pct) {           // pct = 0-100 или null → неопределённый
  const bar = $("progressBar");
  const fill = $("progressFill");
  bar.hidden = false;
  if (pct == null) {
    bar.classList.add("indeterminate");
    fill.style.width = "";
  } else {
    bar.classList.remove("indeterminate");
    fill.style.width = pct + "%";
  }
}
function hideProgress() {
  const bar = $("progressBar");
  bar.classList.remove("indeterminate");
  $("progressFill").style.width = "100%";
  setTimeout(() => { bar.hidden = true; $("progressFill").style.width = "0%"; }, 350);
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
  $("clearBtn").addEventListener("click", clearSession);
  $("exportBtn").addEventListener("click", doExport);
  $("inspClose").addEventListener("click", closeInspector);
  $("inspBackdrop").addEventListener("click", closeInspector);

  $("search").addEventListener("input", (e) => {
    obs.action("search", { len: e.target.value.length });
    applySearch();
  });
  // Safari и некоторые Chrome-версии бросают "search" (а не "input") при клике на × кнопку.
  $("search").addEventListener("search", () => applySearch());
  // хоткеи: ⌘K — поиск; ↑/↓ — навигация по потоку; Esc — снять выбор/выйти из поиска.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("search").focus(); return; }
    const tag = (document.activeElement?.tagName || "").toUpperCase();
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (e.key === "Escape") {
      if (tag === "INPUT") {
        if (e.target.id === "search") { e.preventDefault(); e.target.value = ""; applySearch(); }
        e.target.blur();
        return;
      }
      // Escape вне поля ввода: закрываем инспектор и сбрасываем поиск (напр. после pivotToReq)
      if (state.selectedRec) closeInspector();
      if ($("search").value) { $("search").value = ""; applySearch(); }
      return;
    }
    if (typing) return;                         // не мешаем вводу
    if (e.key === "ArrowDown") { e.preventDefault(); moveSelection(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveSelection(-1); }
    else if (e.key.toLowerCase() === "b" && state.selectedRec) { e.preventDefault(); toggleBookmarkSelected(); }
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
