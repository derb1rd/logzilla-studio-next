"use strict";

// Наблюдаемость фронтенда. Делает GUI воспроизводимым для ИИ:
//   1. action log  — журнал действий пользователя (ring buffer);
//   2. перехват JS-ошибок (window.onerror + unhandledrejection) → /api/client-log;
//   3. корреляция: каждый HTTP-запрос несёт X-Session-Id / X-Correlation-Id,
//      что связывает action клиента с run_id сервера.
// Глобально доступен как `obs`; ручной дамп — window.__logzilla3000Debug.dump().

const obs = (() => {
  const sessionId = "s-" + Math.random().toString(36).slice(2, 10);
  const MAX_ACTIONS = 200;
  const actions = [];
  let corrSeq = 0;

  function action(type, detail) {
    actions.push({ t: new Date().toISOString(), type, detail: detail ?? null });
    if (actions.length > MAX_ACTIONS) actions.shift();
  }
  function recent(n = 30) { return actions.slice(-n); }
  function nextCorr() { return sessionId + "-c" + (++corrSeq); }

  function report(kind, message, stack) {
    const body = JSON.stringify({
      session_id: sessionId, kind,
      message: String(message || ""),
      stack: stack ? String(stack) : null,
      recent_actions: recent(30),
    });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/client-log", new Blob([body], { type: "application/json" }));
      } else {
        fetch("/api/client-log", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      }
    } catch (_) { /* отчёт об ошибке не должен ронять приложение */ }
  }

  // Обёртка fetch: добавляет корреляционные заголовки и логирует в action log.
  async function fetchObs(url, opts = {}) {
    const corr = nextCorr();
    opts.headers = Object.assign({}, opts.headers, {
      "X-Session-Id": sessionId,
      "X-Correlation-Id": corr,
    });
    action("http_request", { url, corr });
    try {
      const r = await fetch(url, opts);
      action("http_response", { url, corr, status: r.status });
      return r;
    } catch (e) {
      action("http_error", { url, corr, error: String(e) });
      report("network_error", String(e), e && e.stack);
      throw e;
    }
  }

  // Глобальный перехват — то, чего сейчас нет нигде и из-за чего фронт-баги невидимы.
  window.addEventListener("error", (ev) => {
    action("js_error", { message: ev.message, source: ev.filename, line: ev.lineno });
    report("js_error", ev.message, ev.error && ev.error.stack);
  });
  window.addEventListener("unhandledrejection", (ev) => {
    const msg = ev.reason && ev.reason.message ? ev.reason.message : String(ev.reason);
    action("unhandledrejection", { message: msg });
    report("unhandledrejection", msg, ev.reason && ev.reason.stack);
  });

  function dump() {
    const blob = new Blob([JSON.stringify({ session_id: sessionId, actions }, null, 2)],
      { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "action_log.json"; a.click();
    URL.revokeObjectURL(url);
  }

  window.__logzilla3000Debug = { dump, recent, sessionId, report };
  return { action, fetch: fetchObs, report, dump, sessionId };
})();
