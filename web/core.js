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
  const SRC_KEYS = ["source", "src", "logger", "service", "module", "name", "channel", "host"];
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

  // Короткий однострочный сниппет SQL: схлопываем переносы/отступы, обрезаем по max.
  function sqlSnippetOf(rec, max = 80) {
    if (rec == null || typeof rec !== "object") return "";
    for (const k of QUERY_KEYS) {
      const v = rec[k];
      if (typeof v === "string" && v.trim()) {
        const flat = v.replace(/\s+/g, " ").trim();
        return flat.length > max ? flat.slice(0, max).trimEnd() + "…" : flat;
      }
    }
    return "";
  }

  function levelOf(record) {
    if (record == null || typeof record !== "object") return "";
    for (const k of ["level", "levelname", "log_level", "loglevel", "severity"]) {
      if (typeof record[k] === "string") {
        const mm = record[k].match(LEVEL_RE);
        if (mm) return norm(mm[1]);
      }
    }
    const mm = JSON.stringify(record).match(LEVEL_RE);
    return mm ? norm(mm[1]) : "";
  }

  function fieldOf(rec, keys) {
    for (const k of keys) {
      const v = rec[k];
      if (typeof v === "string" && v) return v;
      if (typeof v === "number") return String(v);
    }
    return "";
  }

  function tsOf(rec) {
    const t = fieldOf(rec, TS_KEYS);
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
    let text = null;
    for (const k of TEXT_KEYS) {
      if (typeof rec[k] === "string") { text = rec[k]; break; }
    }
    if (text == null) {
      text = Object.entries(rec)
        .filter(([k]) => !TS_KEYS.includes(k) && !SRC_KEYS.includes(k))
        .map(([k, v]) => `${k}=${v !== null && typeof v === "object" ? JSON.stringify(v) : v}`)
        .join("  ·  ");
    }
    return text;
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
    const inBase = (v) => v !== "" && base.includes(String(v));

    // SQL-сниппет добавляем, только если у записи есть отдельное текстовое поле
    // (иначе msgOf уже развернул все поля в base и sql там присутствует — не дублируем).
    const hasText = TEXT_KEYS.some((k) => typeof rec[k] === "string" && rec[k]);
    if (hasText) out.sql = sqlSnippetOf(rec);

    const methodRaw = fieldOf(rec, METHOD_KEYS);
    const url = fieldOf(rec, URL_KEYS);
    const status = fieldOf(rec, STATUS_KEYS);

    if (url && !inBase(url)) out.url = url;
    // метод показываем как «ось» только рядом с путём (или когда поля url нет вовсе)
    if (methodRaw && HTTP_VERB.test(methodRaw) && (out.url || !url)) out.method = methodRaw.toUpperCase();
    if (status && !inBase(status)) out.status = String(status);

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
    norm, levelOf, fieldOf, tsOf, sourceOf, reqIdOf, msgOf, httpCtxOf, summaryOf, sqlSnippetOf, fmtNanos, recordToLine,
    buildQuery, isEmptyQuery, matchesQuery, highlightSegments, crossContext,
  };
});
