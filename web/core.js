"use strict";

// logZilla3000 · studio — core (чистая логика, без DOM).
// Извлечение полей записи, разбор поисковых запросов, подсветка совпадений.
// Не зависит от браузера → покрыто юнит-тестами (tests/core.test.mjs, node:assert).
// Подключается и как браузерный глобал `LZ`, и как Node-модуль (без шага сборки).

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;   // Node
  else root.LZ = api;                                                          // браузер
})(typeof self !== "undefined" ? self : globalThis, function () {

  const LEVEL_RE = /\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL|TRACE)\b/i;
  const TEXT_KEYS = ["raw", "message", "msg", "line", "text", "log"];
  const TS_KEYS = ["timestamp_iso", "timestamp", "time", "ts", "datetime", "date"];
  // Источник/компонент. Кроме классических полей — k8s/контейнерные имена,
  // которые после свёртки инфраструктуры лежат в _meta (fieldOf ищет и там).
  const SRC_KEYS = [
    "source", "src", "logger", "service", "service_name", "module", "name", "channel",
    "app", "container_name", "service_instance",
    "kubernetes_labels_service_name", "kubernetes_labels_app_name",
    "kubernetes_container_name", "kubernetes_pod_name", "host",
  ];
  const HTTP_VERB = /^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|CONNECT)$/i;
  const REQ_KEYS = [
    "req_id", "request_id", "requestId", "correlation_id", "correlationId",
    "corr_id", "trace_id", "traceId", "traceID", "rid", "span_id",
  ];
  const REQ_RE = /\b(?:req(?:uest)?[_-]?id|correlation[_-]?id|corr[_-]?id|trace[_-]?id|rid)\b\s*[=:]?\s*"?([0-9a-zA-Z._-]{2,})"?/i;

  // Структурные поля HTTP-контекста — для обогащения предпросмотра (summaryOf).
  const METHOD_KEYS = ["method", "http_method", "verb"];
  const URL_KEYS = ["url", "uri", "path", "request_uri", "route", "endpoint", "target"];
  const STATUS_KEYS = ["status", "status_code", "statusCode", "http_status", "response_code", "resp_status"];
  // Длительность в наносекундах (zap/Go: request_time и т.п.) — гуманизируем.
  const NS_DUR_KEYS = ["request_time", "latency_ns", "duration_ns", "elapsed_ns", "took_ns", "response_time_ns"];
  // SQL-запрос записи — показываем компактный кусочек, чтобы было видно ЧТО за запрос.
  const QUERY_KEYS = ["sql", "query", "statement", "stmt", "cql"];

  const norm = (s) => (s.toUpperCase() === "WARNING" ? "WARN" : s.toUpperCase());

  // Значение поля → однострочное представление: объекты сериализуем, у строк
  // схлопываем любые пробелы/переносы. Нужен, чтобы многострочный (отформатированный
  // ядром) SQL/JSON не рвал однострочный предпросмотр в потоке и шапке инспектора.
  const flatVal = (v) =>
    v === null ? "null"
    : typeof v === "object" ? JSON.stringify(v)
    : String(v).replace(/\s+/g, " ").trim();

  // Сырое поле SQL записи (как вернуло ядро — возможно, многострочное, отформатиро-
  // ванное sqlparse). Для выделенного SQL-блока в инспекторе.
  function sqlOf(rec) {
    if (rec == null || typeof rec !== "object") return "";
    for (const k of QUERY_KEYS) {
      const v = rec[k];
      if (typeof v === "string" && v.trim()) return v.replace(/\s+$/, "");
    }
    return "";
  }

  // Короткий однострочный сниппет SQL: схлопываем переносы/отступы, обрезаем по max.
  function sqlSnippetOf(rec, max = 80) {
    const v = sqlOf(rec);
    if (!v) return "";
    const flat = v.replace(/\s+/g, " ").trim();
    return flat.length > max ? flat.slice(0, max).trimEnd() + "…" : flat;
  }

  // Уровни-ключи и текстовые поля — зеркало серверного _record_level
  // (parse_service.py). Фолбэк ищет уровень ТОЛЬКО в тексте строки лога, а не во
  // всём JSON.stringify(record): иначе слово ERROR/WARN в произвольном поле
  // (`{"handler":"error_cb"}`) ложно завышает уровень — и счётчики UI расходятся
  // с серверными метриками.
  const LEVEL_KEYS = ["level", "levelname", "log_level", "loglevel", "severity", "lvl"];

  // Числовой уровень → имя. Частый источник «пустого» уровня в структурных логах:
  // RFC5424/GELF severity (0–7, меньше = severe) и pino/bunyan level (10–60, больше
  // = severe). Диапазон значения сам разводит две шкалы (pino начинается с 10).
  const SYSLOG_SEVERITY = ["FATAL", "FATAL", "CRITICAL", "ERROR", "WARN", "INFO", "INFO", "DEBUG"];
  const PINO_LEVELS = { 10: "TRACE", 20: "DEBUG", 30: "INFO", 40: "WARN", 50: "ERROR", 60: "FATAL" };
  function levelFromNumber(n) {
    if (!Number.isInteger(n)) return "";
    if (n >= 0 && n <= 7) return SYSLOG_SEVERITY[n];
    if (n >= 10 && n <= 60) return PINO_LEVELS[Math.min(60, Math.round(n / 10) * 10)] || "";
    return "";
  }

  // Ключи, которые предпросмотр показывает отдельными осями (время/уровень/источник/
  // req_id/HTTP-метод/путь/статус/длительность/SQL). В «остаток» base (msgOf для
  // записей без текстового поля) их не включаем — иначе строка дублирует цветные
  // чипы сырым k=v и в неё текут наносекунды/переносы SQL.
  const CONSUMED_KEYS = new Set(
    [...TEXT_KEYS, ...TS_KEYS, ...SRC_KEYS, ...LEVEL_KEYS, ...REQ_KEYS,
     ...METHOD_KEYS, ...URL_KEYS, ...STATUS_KEYS, ...NS_DUR_KEYS, ...QUERY_KEYS]
      .map((k) => k.toLowerCase()),
  );

  function levelOf(record) {
    if (record == null || typeof record !== "object") return "";
    for (const k of LEVEL_KEYS) {
      const v = record[k];
      if (typeof v === "string") {
        const mm = v.match(LEVEL_RE);
        if (mm) return norm(mm[1]);
      } else if (typeof v === "number") {
        const lv = levelFromNumber(v);
        if (lv) return lv;
      }
    }
    for (const k of TEXT_KEYS) {
      if (typeof record[k] === "string") {
        const mm = record[k].match(LEVEL_RE);
        if (mm) return norm(mm[1]);
      }
    }
    return "";
  }

  function fieldOf(rec, keys) {
    for (const k of keys) {
      const v = rec[k];
      if (typeof v === "string" && v) return v;
      if (typeof v === "number") return String(v);
    }
    // Фолбэк в _meta: после свёртки инфраструктуры источник/служебные поля
    // (service_name, k8s-имена) уезжают туда — но как ось предпросмотра они нужны.
    const meta = rec._meta;
    if (meta && typeof meta === "object") {
      for (const k of keys) {
        const v = meta[k];
        if (typeof v === "string" && v) return v;
        if (typeof v === "number") return String(v);
      }
    }
    return "";
  }

  const ISO_TS_RE = /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/;
  function tsOf(rec) {
    // Среди полей-кандидатов предпочитаем ISO-8601 (машинное время СОБЫТИЯ —
    // сортируемое и однозначное) человекочитаемым строкам вроде «Jun 5, 2026 @ …»:
    // в Kibana-экспортах @timestamp — это время приёма (батчами), а реальное время
    // события лежит в ISO-поле time/timestamp_iso. Иначе — первое присутствующее.
    let iso = "", first = "";
    for (const k of TS_KEYS) {
      const v = rec[k];
      const s = typeof v === "string" ? v : (typeof v === "number" ? String(v) : "");
      if (!s) continue;
      if (!first) first = s;
      if (!iso && ISO_TS_RE.test(s)) { iso = s; break; }
    }
    const t = iso || first;
    if (!t) return "";
    // выжимаем HH:MM:SS(.ms) если есть — компактнее для колонки
    const m = t.match(/\d{2}:\d{2}:\d{2}(?:[.,]\d{1,3})?/);
    return m ? m[0] : t.slice(0, 12);
  }

  // Источник/компонент записи. Сначала — структурное поле (JSON/CSV). Для plain-text
  // поля source нет, поэтому вытаскиваем «вызываемый» компонент: токен сразу после
  // уровня (`… ERROR api-gateway …` → api-gateway). HTTP-глаголы и пути отбрасываем.
  function sourceOf(rec) {
    const s = fieldOf(rec, SRC_KEYS);
    if (s) return s;
    const raw = fieldOf(rec, TEXT_KEYS);
    if (!raw) return "";
    const m = raw.match(LEVEL_RE);
    if (!m) return "";
    const tok = raw.slice(m.index + m[0].length).trim().split(/\s+/)[0] || "";
    if (!/^[A-Za-z][\w.\-]{1,30}$/.test(tok) || HTTP_VERB.test(tok)) return "";
    return tok;
  }

  // Идентификатор запроса/трассы — ось «контекста». Структурное поле или вытяжка
  // из текста (`req_id=...`, `trace_id: ...`, `correlation_id ...`).
  function reqIdOf(rec) {
    for (const k of REQ_KEYS) {
      const v = rec[k];
      if (v != null && v !== "") return String(v);
    }
    const raw = fieldOf(rec, TEXT_KEYS);
    if (raw) {
      const m = raw.match(REQ_RE);
      if (m) return m[1];
    }
    return "";
  }

  function msgOf(rec) {
    for (const k of TEXT_KEYS) {
      if (typeof rec[k] === "string") return rec[k];
    }
    // Нет текстового поля → собираем «остаток»: только поля, не показанные отдельно
    // как структурные оси (см. CONSUMED_KEYS). Значения флэтим, чтобы предпросмотр
    // оставался однострочным. _meta (свёрнутая инфраструктура) в строку не тянем —
    // это служебный блок, не содержимое лога.
    return Object.entries(rec)
      // _meta и синтетические col_N (переполнение рваной строки битого CSV) в
      // строку сообщения не тянем — иначе стены `col_2=…col_21=…` засоряют поток.
      .filter(([k]) => k !== "_meta" && !/^col_\d+$/.test(k) && !CONSUMED_KEYS.has(k.toLowerCase()))
      .map(([k, v]) => `${k}=${flatVal(v)}`)
      .join(" · ");
  }

  // Наносекунды → компактная человекочитаемая длительность (µs/ms/s/min).
  function fmtNanos(v) {
    const n = Number(v);
    if (!isFinite(n) || n < 0) return "";
    if (n >= 6e10) return (n / 6e10).toFixed(1) + "min";
    if (n >= 1e9) return (n / 1e9).toFixed(2) + "s";
    if (n >= 1e6) return Math.round(n / 1e6) + "ms";
    if (n >= 1e3) return Math.round(n / 1e3) + "µs";
    return n + "ns";
  }

  // HTTP-контекст записи для обогащения предпросмотра: метод/путь/статус/длительность
  // из структурных полей. Поля, уже присутствующие в тексте msg, не возвращаем (чтобы
  // не дублировать). Для записей без текстового поля msgOf разворачивает все поля в
  // k=v — тогда контекст уже внутри base, и поля гасятся проверкой inBase. Возвращает
  // строковые поля (или "") + сам base — основа и для строки (summaryOf), и для
  // цветных чипов в UI.
  function httpCtxOf(rec) {
    const base = msgOf(rec);
    const out = { base, method: "", url: "", status: "", dur: "", sql: "" };
    if (rec == null || typeof rec !== "object") return out;
    // Если base пришёл из текстового поля — не дублируем то, что уже в человеко-
    // читаемом тексте. Для записей без текста base — «остаток» без этих полей
    // (CONSUMED_KEYS), поэтому чипы строим всегда: структурный JSON обогащается так
    // же, как лог с message.
    const hasText = TEXT_KEYS.some((k) => typeof rec[k] === "string" && rec[k]);
    const inText = (v) => hasText && v !== "" && base.includes(String(v));

    out.sql = sqlSnippetOf(rec);   // sql исключён из base → дублирования нет

    const methodRaw = fieldOf(rec, METHOD_KEYS);
    const url = fieldOf(rec, URL_KEYS);
    const status = fieldOf(rec, STATUS_KEYS);

    if (url && !inText(url)) out.url = url;
    // метод показываем как «ось» только рядом с путём (или когда поля url нет вовсе)
    if (methodRaw && HTTP_VERB.test(methodRaw) && (out.url || !url)) out.method = methodRaw.toUpperCase();
    if (status && !inText(status)) out.status = String(status);

    for (const k of NS_DUR_KEYS) {
      if (rec[k] != null && rec[k] !== "") { out.dur = fmtNanos(rec[k]); break; }
    }
    return out;
  }

  // «Обогащённый» предпросмотр одной строкой (для свободного поиска через recordToLine
  // и текстовых контекстов). Цветной рендер строит UI из тех же полей httpCtxOf.
  function summaryOf(rec) {
    const c = httpCtxOf(rec);
    const ctx = [];
    if (c.method && c.url) ctx.push(`${c.method} ${c.url}`);
    else if (c.method) ctx.push(c.method);
    else if (c.url) ctx.push(c.url);
    if (c.status) ctx.push("→ " + c.status);
    if (c.dur) ctx.push(c.dur);
    const parts = [];
    if (ctx.length) parts.push(ctx.join(" · "));
    if (c.base) parts.push(c.base);
    if (c.sql) parts.push("sql: " + c.sql);
    return parts.join(" · ");
  }

  // «человеческая» строка записи (для свободного текстового поиска)
  function recordToLine(rec) {
    if (rec == null || typeof rec !== "object") return String(rec);
    return [tsOf(rec), levelOf(rec), sourceOf(rec), summaryOf(rec)].filter(Boolean).join("  ");
  }

  // Разбор строки поиска. Токены `lvl:` / `src:` / `req:` фильтруют по полям,
  // остальное — свободный текст (substring по recordToLine). Поддержаны синонимы
  // ключей; `req:` принимает req_id/request/trace/correlation.
  function buildQuery(raw) {
    const q = { levels: [], srcs: [], reqs: [], text: [] };
    for (const tok of String(raw || "").trim().split(/\s+/).filter(Boolean)) {
      const m = tok.match(/^([a-z_]+):(.+)$/i);
      if (m) {
        const key = m[1].toLowerCase(), val = m[2].toLowerCase();
        if (key === "lvl" || key === "level") { q.levels.push(val); continue; }
        if (key === "src" || key === "source") { q.srcs.push(val); continue; }
        if (key === "req" || key === "req_id" || key === "request" || key === "trace" || key === "corr") { q.reqs.push(val); continue; }
        // неизвестный ключ — трактуем как свободный текст целиком
        q.text.push(tok.toLowerCase());
      } else {
        q.text.push(tok.toLowerCase());
      }
    }
    return q;
  }

  function isEmptyQuery(q) {
    return !q.levels.length && !q.srcs.length && !q.reqs.length && !q.text.length;
  }

  function matchesQuery(rec, q) {
    if (q.levels.length && !q.levels.includes(levelOf(rec).toLowerCase())) return false;
    if (q.srcs.length) {
      const src = sourceOf(rec).toLowerCase();
      if (!q.srcs.some((s) => src.includes(s))) return false;
    }
    if (q.reqs.length) {
      const rid = reqIdOf(rec).toLowerCase();
      if (!rid || !q.reqs.includes(rid)) return false;   // req — точное совпадение id
    }
    if (q.text.length) {
      const line = recordToLine(rec).toLowerCase();
      if (!q.text.every((t) => line.includes(t))) return false;
    }
    return true;
  }

  // Cross-file контекст: строки того же req_id по ВСЕМ файлам сессии. Это и есть
  // «распределённый трейс» — одна ось req_id, склеивающая логи разных сервисов.
  // files: [{ fileId, name, records }]. Возвращает [{ fileId, name, i, rec }] в
  // порядке файлов и строк. Чистая функция (без DOM) → покрыта юнит-тестом.
  function crossContext(files, reqId) {
    const out = [];
    if (!reqId) return out;
    for (const f of files || []) {
      const recs = (f && f.records) || [];
      for (let i = 0; i < recs.length; i++) {
        if (reqIdOf(recs[i]) === reqId) out.push({ fileId: f.fileId, name: f.name, i, rec: recs[i] });
      }
    }
    return out;
  }

  // Сегментирует текст на части {t, hit} по списку текстовых терминов запроса —
  // для подсветки. Регистронезависимо, склеивает пересекающиеся совпадения.
  function highlightSegments(text, terms) {
    const src = String(text);
    const needles = (terms || []).filter(Boolean);
    if (!needles.length) return [{ t: src, hit: false }];
    const low = src.toLowerCase();
    const marks = new Array(src.length).fill(false);
    for (const n of needles) {
      const t = n.toLowerCase();
      if (!t) continue;
      let i = low.indexOf(t);
      while (i !== -1) {
        for (let j = i; j < i + t.length; j++) marks[j] = true;
        i = low.indexOf(t, i + t.length);
      }
    }
    const segs = [];
    let cur = "", curHit = marks[0] || false;
    for (let i = 0; i < src.length; i++) {
      if (marks[i] === curHit) { cur += src[i]; }
      else { segs.push({ t: cur, hit: curHit }); cur = src[i]; curHit = marks[i]; }
    }
    if (cur) segs.push({ t: cur, hit: curHit });
    return segs;
  }

  return {
    LEVEL_RE, TEXT_KEYS, TS_KEYS, SRC_KEYS, REQ_KEYS,
    norm, levelOf, fieldOf, tsOf, sourceOf, reqIdOf, msgOf, httpCtxOf, summaryOf, sqlOf, sqlSnippetOf, fmtNanos, recordToLine,
    buildQuery, isEmptyQuery, matchesQuery, highlightSegments, crossContext,
  };
});
