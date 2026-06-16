// Юнит-тесты чистого слоя web/core.js. Запуск: node tests/core.test.mjs
// Зависимостей нет — node:assert + node:test (встроенные).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const LZ = require("../web/core.js");

const txt = (raw) => ({ raw, timestamp_iso: "2026-01-15 14:23:01" });

test("levelOf: структурное поле и вытяжка из raw", () => {
  assert.equal(LZ.levelOf({ level: "error" }), "ERROR");
  assert.equal(LZ.levelOf({ severity: "WARNING" }), "WARN");      // нормализация
  assert.equal(LZ.levelOf(txt("... ERROR boom")), "ERROR");
  assert.equal(LZ.levelOf({ message: "all good" }), "");
  assert.equal(LZ.levelOf(null), "");                              // не падает
  // Фолбэк ищет уровень только в level- и текстовых полях, не во всём JSON:
  // ERROR/WARN в произвольном поле не должны завышать уровень (зеркало сервера).
  assert.equal(LZ.levelOf({ handler: "error_cb", message: "all good" }), "");
  assert.equal(LZ.levelOf({ lvl: "warn" }), "WARN");               // ключ lvl поддержан
});

test("levelOf: числовой уровень/severity (syslog/GELF/pino)", () => {
  // RFC5424/GELF severity 0–7 (меньше = severe) — раньше показывали «—».
  assert.equal(LZ.levelOf({ severity: 3 }), "ERROR");
  assert.equal(LZ.levelOf({ severity: 4 }), "WARN");
  assert.equal(LZ.levelOf({ severity: 6 }), "INFO");
  assert.equal(LZ.levelOf({ severity: 0 }), "FATAL");
  // pino/bunyan level 10–60 (больше = severe).
  assert.equal(LZ.levelOf({ level: 30 }), "INFO");
  assert.equal(LZ.levelOf({ level: 50 }), "ERROR");
  assert.equal(LZ.levelOf({ level: 60 }), "FATAL");
  // Мусорные/внедиапазонные числа не дают ложного уровня.
  assert.equal(LZ.levelOf({ level: 200 }), "");
  assert.equal(LZ.levelOf({ level: true }), "");                   // bool не число-уровень
  // Строковый уровень по-прежнему в приоритете.
  assert.equal(LZ.levelOf({ level: "ERROR", severity: 6 }), "ERROR");
});

test("sourceOf: поле, компонент из текста, отброс HTTP-глаголов", () => {
  assert.equal(LZ.sourceOf({ service: "api" }), "api");
  assert.equal(LZ.sourceOf({ service_name: "drills" }), "drills");  // ECS/zap-поле
  assert.equal(LZ.sourceOf(txt("2026 ERROR api-gateway boom")), "api-gateway");
  assert.equal(LZ.sourceOf(txt("2026 INFO POST /v2/checkout 200")), "", "HTTP-глагол не источник");
  assert.equal(LZ.sourceOf(txt("2026 INFO no level component")), "no");  // токен после уровня
  assert.equal(LZ.sourceOf({ message: "no level here" }), "");
});

test("reqIdOf: поля-синонимы, текст, короткий id, отсутствие", () => {
  assert.equal(LZ.reqIdOf({ trace_id: "7f3a-9c41" }), "7f3a-9c41");
  assert.equal(LZ.reqIdOf({ requestId: "X9" }), "X9");
  assert.equal(LZ.reqIdOf(txt("... req_id=A1 start")), "A1", "короткий id (регресс-баг {4,})");
  assert.equal(LZ.reqIdOf(txt("... trace_id: 7f3a9c41 ok")), "7f3a9c41");
  assert.equal(LZ.reqIdOf(txt("... correlation_id abc123 done")), "abc123");
  assert.equal(LZ.reqIdOf(txt("... no correlation here")), "");
});

test("tsOf: выжимает время; msgOf: raw или key=value", () => {
  assert.equal(LZ.tsOf({ timestamp_iso: "2026-01-15 14:23:01" }), "14:23:01");
  // ISO-время события предпочитается человекочитаемому @timestamp Kibana
  assert.equal(
    LZ.tsOf({ timestamp: "Jun 5, 2026 @ 20:29:13.329", time: "2026-06-05T17:29:02.558+0000" }),
    "17:29:02.558",
  );
  // нет ISO → откат на первое присутствующее поле
  assert.equal(LZ.tsOf({ timestamp: "Jun 5, 2026 @ 20:29:13.329" }), "20:29:13.329");
  assert.equal(LZ.msgOf(txt("2026 ERROR boom")), "2026 ERROR boom");
  assert.match(LZ.msgOf({ user: "ivan", code: 500 }), /user=ivan/);
});

test("summaryOf: обогащение HTTP-контекстом без дублей", () => {
  // запись со структурным msg → добавляем метод+путь
  assert.equal(
    LZ.summaryOf({ msg: "Start handling request", method: "GET", url: "/api/theme/v1/" }),
    "GET /api/theme/v1/ · Start handling request",
  );
  // finish: статус + длительность (request_time в наносекундах → ms)
  assert.equal(
    LZ.summaryOf({ msg: "Finish handling request", method: "GET", url: "/x", status: 200, request_time: 29718179 }),
    "GET /x · → 200 · 30ms · Finish handling request",
  );
  // субмиллисекундная длительность → µs
  assert.match(LZ.summaryOf({ msg: "ok", request_time: 228427 }), /228µs/);
  // url уже в тексте msg — не дублируем
  assert.equal(LZ.summaryOf({ msg: "proxy /api/x done", url: "/api/x" }), "proxy /api/x done");
  // нет http-полей → как msgOf
  assert.equal(LZ.summaryOf({ msg: "plain" }), "plain");
  // запись БЕЗ текстового поля обогащается так же, как с текстом: чипы вместо
  // сырого k=v (это и был главный баг консистентности предпросмотра).
  assert.equal(LZ.summaryOf({ method: "GET", url: "/x" }), "GET /x");
});

test("summaryOf: чистый структурный JSON — чипы, без сырых k=v и дублей длительности", () => {
  const r = { level: "INFO", service: "api", method: "GET", path: "/x", status: 200, req_id: "7f3a", request_time: 12500000 };
  assert.equal(LZ.summaryOf(r), "GET /x · → 200 · 13ms");
  const c = LZ.httpCtxOf(r);
  assert.equal(c.method, "GET");
  assert.equal(c.url, "/x");
  assert.equal(c.status, "200");
  assert.equal(c.dur, "13ms");
  assert.ok(!/request_time/.test(c.base), "сырая длительность не дублируется в base");
  assert.ok(!/method=|path=|status=/.test(c.base), "http-поля не сыпятся в base сырым k=v");
});

test("sqlSnippetOf / summaryOf: компактный SQL-кусочек запроса", () => {
  // многострочный sql → однострочный, обрезанный
  const long = "\n\tselect\n\t\tnr.schedule_id, ms.mcr_id\n\tfrom mcr.next_run nr\n\tinner join mcr.schedule ms on ms.id = nr.schedule_id\n\twhere nr.active";
  const snip = LZ.sqlSnippetOf({ sql: long });
  assert.ok(/^select nr\.schedule_id, ms\.mcr_id from mcr\.next_run/.test(snip));
  assert.ok(snip.length <= 81 && snip.endsWith("…"));            // обрезан
  assert.equal(LZ.sqlSnippetOf({ msg: "no sql here" }), "");      // нет поля → пусто
  // синонимы query/statement
  assert.equal(LZ.sqlSnippetOf({ query: "SELECT 1" }), "SELECT 1");
  // summaryOf добавляет «sql: …» к строке с msg
  const s = LZ.summaryOf({ msg: "executing query", sql: "select * from users where id = 42" });
  assert.equal(s, "executing query · sql: select * from users where id = 42");
  // запись без текстового поля: sql тоже выносим в чип-сниппет (а не в сырой k=v)
  assert.equal(LZ.summaryOf({ sql: "select 1" }), "sql: select 1");
  // многострочный (отформатированный ядром) SQL → во сниппете без переносов
  const ml = { level: "INFO", sql: "SELECT *\nFROM t\nWHERE x=1" };
  assert.equal(LZ.httpCtxOf(ml).sql, "SELECT * FROM t WHERE x=1");
  assert.ok(!/\n/.test(LZ.summaryOf(ml)), "сводка остаётся однострочной");
  // sqlOf отдаёт полную (многострочную) форму для выделенного блока инспектора
  assert.equal(LZ.sqlOf(ml), "SELECT *\nFROM t\nWHERE x=1");
});

test("httpCtxOf: структурные поля для цветных чипов, без дублей", () => {
  const a = LZ.httpCtxOf({ msg: "Finish handling request", method: "get", url: "/x", status: 200, request_time: 29718179 });
  assert.equal(a.method, "GET");          // нормализация регистра
  assert.equal(a.url, "/x");
  assert.equal(a.status, "200");
  assert.equal(a.dur, "30ms");
  assert.equal(a.base, "Finish handling request");
  // url уже в тексте → не дублируем, и метод без пути не показываем
  const b = LZ.httpCtxOf({ msg: "proxy /api/x done", method: "GET", url: "/api/x" });
  assert.equal(b.url, "");
  assert.equal(b.method, "");
  // нет http-полей
  const c = LZ.httpCtxOf({ msg: "plain" });
  assert.deepEqual([c.method, c.url, c.status, c.dur], ["", "", "", ""]);
});

test("fmtNanos: гуманизация наносекунд", () => {
  assert.equal(LZ.fmtNanos(500), "500ns");
  assert.equal(LZ.fmtNanos(1500), "2µs");
  assert.equal(LZ.fmtNanos(29718179), "30ms");
  assert.equal(LZ.fmtNanos(2_500_000_000), "2.50s");
});

test("buildQuery: токены lvl/src/req + свободный текст", () => {
  const q = LZ.buildQuery("lvl:ERROR src:api req:A1 timeout");
  assert.deepEqual(q.levels, ["error"]);
  assert.deepEqual(q.srcs, ["api"]);
  assert.deepEqual(q.reqs, ["a1"]);
  assert.deepEqual(q.text, ["timeout"]);
  assert.ok(LZ.isEmptyQuery(LZ.buildQuery("   ")));
  assert.ok(!LZ.isEmptyQuery(q));
});

test("matchesQuery: уровень/источник substring/req exact/текст AND", () => {
  const r = { level: "ERROR", service: "api-gateway", req_id: "A1", message: "timeout boom" };
  assert.ok(LZ.matchesQuery(r, LZ.buildQuery("lvl:ERROR")));
  assert.ok(LZ.matchesQuery(r, LZ.buildQuery("src:api")), "src — substring");
  assert.ok(LZ.matchesQuery(r, LZ.buildQuery("req:A1")));
  assert.ok(!LZ.matchesQuery(r, LZ.buildQuery("req:A")), "req — точное совпадение, не substring");
  assert.ok(LZ.matchesQuery(r, LZ.buildQuery("timeout boom")), "текст — AND всех терминов");
  assert.ok(!LZ.matchesQuery(r, LZ.buildQuery("timeout nope")));
  assert.ok(!LZ.matchesQuery(r, LZ.buildQuery("lvl:WARN")));
});

test("crossContext: трасса req_id по всем файлам сессии", () => {
  const files = [
    { fileId: "f1", name: "gateway.log", records: [
      { req_id: "A1", message: "in" }, { req_id: "B2", message: "other" }, { req_id: "A1", message: "out" },
    ] },
    { fileId: "f2", name: "worker.log", records: [
      { trace_id: "A1", message: "job start" }, { message: "no id" },
    ] },
  ];
  const rows = LZ.crossContext(files, "A1");
  // три A1 из f1 (две) + f2 (одна), в порядке файлов и строк
  assert.deepEqual(rows.map((r) => [r.fileId, r.i]), [["f1", 0], ["f1", 2], ["f2", 0]]);
  assert.equal(rows.length, 3);
  assert.equal(rows[2].name, "worker.log");
  // пустой/несуществующий id → пусто, не падает
  assert.deepEqual(LZ.crossContext(files, ""), []);
  assert.deepEqual(LZ.crossContext(files, "ZZ"), []);
  assert.deepEqual(LZ.crossContext(null, "A1"), []);
});

test("highlightSegments: сегментация и склейка совпадений", () => {
  const segs = LZ.highlightSegments("Connection refused", ["refused"]);
  assert.deepEqual(segs, [{ t: "Connection ", hit: false }, { t: "refused", hit: true }]);
  // несколько/пересекающиеся термины склеиваются
  const s2 = LZ.highlightSegments("abcabc", ["ab", "bc"]);
  assert.equal(s2.map((x) => x.t).join(""), "abcabc", "склейка без потери символов");
  assert.ok(s2.every((x) => x.hit), "abc полностью покрыт ab+bc");
  // нет терминов → один сегмент без подсветки
  assert.deepEqual(LZ.highlightSegments("plain", []), [{ t: "plain", hit: false }]);
});
