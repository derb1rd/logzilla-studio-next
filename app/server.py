"""HTTP/JSON-сервер на stdlib (zero-dependency).

Реализует границу UI ↔ ядро как HTTP — самый отлаживаемый слой: любой баг =
сохранённый JSON-запрос, воспроизводимый одним curl. Обработчик намеренно тонкий —
вся логика в сервисном слое, поэтому позже его легко заменить на FastAPI без
изменения контракта.

Эндпоинты:
  GET  /                 → web/index.html
  GET  /<static>         → файлы из web/
  GET  /api/health       → {"status":"ok", ...}
  POST /api/parse        → ParseRequest  → ParseResult
  POST /api/export       → ExportRequest → файл (с заголовком Content-Disposition)
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from . import __version__
from .contract import ContractError, ExportRequest, ParseRequest
from .export_service import export
from .logging_setup import log_event, setup_logging
from .parse_service import parse, parse_all_records

logger = logging.getLogger("studio.server")

_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_MAX_BODY = 64 * 1024 * 1024  # 64 МБ — защита от чрезмерного ввода (MVP)


class Handler(BaseHTTPRequestHandler):
    server_version = f"logzilla-studio/{__version__}"

    # --- утилиты ответа ------------------------------------------------- #
    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json({"status": "error", "error": {"code": code, "message": message}}, status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            raise ContractError("Пустое тело запроса")
        if length > _MAX_BODY:
            raise ContractError("Тело запроса слишком большое")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ContractError(f"Невалидный JSON: {e}") from e
        if not isinstance(data, dict):
            raise ContractError("Тело запроса должно быть JSON-объектом")
        return data

    # --- маршрутизация -------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._send_json({"status": "ok", "service": "logzilla-studio", "version": __version__})
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/parse":
            self._handle_parse()
        elif path == "/api/export":
            self._handle_export()
        elif path == "/api/client-log":
            self._handle_client_log()
        else:
            self._send_error_json(404, "NOT_FOUND", f"Неизвестный путь: {path}")

    def _corr(self) -> str | None:
        return self.headers.get("X-Correlation-Id")

    def _reject_file_source(self, req: ParseRequest) -> bool:
        """HIGH-2: запрещаем source.kind='file' на HTTP-границе (LFI-примитив).

        Через HTTP можно было прочитать любой файл процесса (`{"kind":"file","path":
        "/etc/passwd"}`) — при запуске на 0.0.0.0 это неаутентифицированное чтение
        произвольных файлов. UI всегда шлёт 'inline' (читает файл в браузере), а
        file-источник остаётся только для in-process вызовов (CLI/тесты). Возвращает
        True, если запрос отклонён (ответ уже отправлен).
        """
        if req.source.kind == "file":
            log_event(
                logger, "file_source_rejected", level=logging.WARNING,
                correlation_id=self._corr(), client=self.client_address[0],
            )
            self._send_error_json(
                403, "FILE_SOURCE_FORBIDDEN",
                "Источник kind='file' запрещён через HTTP API. "
                "Передавайте содержимое файла как kind='inline'.",
            )
            return True
        return False

    # --- обработчики ---------------------------------------------------- #
    def _handle_parse(self) -> None:
        try:
            body = self._read_body()
            req = ParseRequest.from_dict(body)
        except ContractError as e:
            self._send_error_json(400, "CONTRACT_ERROR", str(e))
            return
        if self._reject_file_source(req):
            return
        result = parse(req, correlation_id=self._corr())
        self._send_json(result.to_dict())

    def _handle_export(self) -> None:
        try:
            body = self._read_body()
            req = ExportRequest.from_dict(body)
        except ContractError as e:
            self._send_error_json(400, "CONTRACT_ERROR", str(e))
            return
        if self._reject_file_source(req.parse_request):
            return

        run_id = "x-" + uuid4().hex[:8]
        try:
            records, truncated, total = parse_all_records(req.parse_request)
            payload, mime, ext = export(records, req.options)
        except Exception as e:  # noqa: BLE001 — граница
            log_event(logger, "export_failed", level=logging.ERROR, run_id=run_id, error=str(e))
            self._send_error_json(500, "EXPORT_ERROR", str(e))
            return

        log_event(
            logger, "export_done", run_id=run_id, correlation_id=self._corr(),
            format="json", gzip=req.options.gzip, records=len(records), bytes=len(payload),
            truncated=truncated, total_records=total,
        )
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", f'attachment; filename="logzilla_export.{ext}"')
        # HIGH-1: если результат обрезан — сигналим явно, чтобы UI предупредил пользователя,
        # а не отдавал неполный файл молча.
        if truncated:
            self.send_header("X-Truncated", "true")
            self.send_header("X-Total-Records", str(total))
            self.send_header("X-Exported-Records", str(len(records)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle_client_log(self) -> None:
        """Принимает события фронта (JS-ошибки, action log) в ОБЩИЙ структурный лог.

        Закрывает слепую зону «JS-ошибки не попадают в Python-лог».
        Размеры полей ограничены, чтобы лог нельзя было залить с клиента.
        """
        try:
            body = self._read_body()
        except ContractError as e:
            self._send_error_json(400, "CONTRACT_ERROR", str(e))
            return

        kind = str(body.get("kind", "unknown"))[:32]
        is_error = kind in ("js_error", "unhandledrejection", "network_error")
        actions = body.get("recent_actions")
        log_event(
            logger, "client_event",
            level=logging.WARNING if is_error else logging.INFO,
            session_id=str(body.get("session_id", ""))[:64],
            kind=kind,
            message=str(body.get("message", ""))[:2000],
            stack=(str(body.get("stack"))[:4000] if body.get("stack") else None),
            recent_actions=(actions[:50] if isinstance(actions, list) else None),
            correlation_id=self._corr(),
        )
        self._send_json({"status": "ok"})

    # --- статика -------------------------------------------------------- #
    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (_WEB_DIR / rel).resolve()
        # Защита от path traversal: target обязан лежать ВНУТРИ _WEB_DIR.
        # Сравниваем по границе пути (is_relative_to), а не префиксом строки —
        # иначе соседний каталог вроде «../web-secret/...» прошёл бы startswith.
        if not target.is_relative_to(_WEB_DIR.resolve()) or not target.is_file():
            self._send_error_json(404, "NOT_FOUND", f"Файл не найден: {path}")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Не кэшируем статику: локальный инструмент, файлы крошечные, а кэш браузера
        # приводил к тому, что правки JS/CSS не подхватывались без жёсткой перезагрузки.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # --- логирование запросов через структурный логгер ------------------ #
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log_event(
            logger, "http_request", level=logging.DEBUG,
            client=self.address_string(), request=fmt % args,
        )


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    setup_logging()
    httpd = ThreadingHTTPServer((host, port), Handler)
    log_event(logger, "server_started", host=host, port=port, url=f"http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log_event(logger, "server_stopped")
        httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="logzilla-studio HTTP-сервер")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
