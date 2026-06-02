"""
GUI-интерфейс logZilla3000.

Требуется установка зависимости:
    pip install tkinterdnd2

    На macOS + Tk 9.0 нативная библиотека tkdnd может отсутствовать.
    В этом случае скомпилируйте её из исходников:
        ./setup_gui.sh

Запуск:
    python3 -m logZilla3000.gui
    или через лаунчер:
    ./launch_gui.sh
"""

import logging
import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

# Поддерживаемые расширения файлов
SUPPORTED_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".log", ".txt", ".syslog",
}

# Версия приложения
APP_VERSION = "2.1.0"

logger = logging.getLogger(__name__)

# Поддержка запуска как модуля (python3 -m logZilla3000.gui)
# и через launch_gui.sh (PYTHONPATH + -m)
try:
    from .parser import UniversalLogParser
except ImportError:
    # Прямой запуск: добавляем родительский каталог в sys.path
    _parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _parent_dir)
    from logZilla3000.parser import UniversalLogParser

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False


def _user_friendly_error(exc: Exception) -> str:
    """Преобразует техническое исключение в человекочитаемое сообщение.

    Для коммерческого продукта клиент не должен видеть Python tracebacks.
    """
    msg = str(exc)

    if isinstance(exc, UnicodeDecodeError):
        return (
            "Не удалось прочитать файл: проблема с кодировкой.\n"
            "Попробуйте выбрать другую кодировку в настройках\n"
            "(например, cp1251 или latin-1)."
        )
    if isinstance(exc, FileNotFoundError):
        return "Файл не найден. Возможно, он был перемещён или удалён."
    if isinstance(exc, PermissionError):
        return "Нет доступа к файлу. Проверьте права на чтение."
    if isinstance(exc, MemoryError):
        return "Недостаточно памяти для обработки файла. Попробуйте файл поменьше."
    if isinstance(exc, IsADirectoryError):
        return "Выбранная папка не является файлом. Выберите файл логов."

    # Общие паттерны ошибок
    if "No such file" in msg:
        return "Файл не найден. Возможно, он был перемещён или удалён."
    if "Permission denied" in msg:
        return "Нет доступа к файлу. Проверьте права на чтение."
    if "JSON" in msg or "json" in msg:
        return "Файл содержит невалидный JSON. Проверьте формат файла."

    # Fallback — показываем тип ошибки без стека
    exc_type = type(exc).__name__
    return f"Ошибка обработки ({exc_type}).\nФайл не может быть обработан."


class LogZillaGUI:
    """Главное окно приложения logZilla3000."""

    def __init__(self, root: tk.Tk, dnd_enabled: bool = False):
        self.root = root
        self.dnd_enabled = dnd_enabled
        self.root.title("logZilla3000 — Универсальный парсер логов")
        self.root.geometry("850x650")
        self.root.minsize(700, 550)

        # Флаг: идёт ли парсинг (для блокировки UI)
        self._parsing = False

        self._build_ui()

    def _build_ui(self):
        """Создание виджетов интерфейса."""
        # Главный фрейм
        main_frame = tk.Frame(self.root)
        main_frame.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        # Заголовок
        title_label = tk.Label(
            main_frame,
            text="logZilla3000",
            font=("Helvetica", 16, "bold"),
        )
        title_label.pack(pady=(0, 5))

        hint_label = tk.Label(
            main_frame,
            text="Перетащите файлы в список или выберите через кнопку ниже",
            fg="gray",
        )
        hint_label.pack(pady=(0, 5))

        # Список файлов
        list_frame = tk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_listbox = tk.Listbox(
            list_frame,
            width=60,
            height=8,
            yscrollcommand=scrollbar.set,
            selectmode=tk.EXTENDED,
        )
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.file_listbox.yview)

        # Drag & Drop (если доступен tkinterdnd2 и нативная библиотека загрузилась)
        if self.dnd_enabled:
            self.file_listbox.drop_target_register(DND_FILES)
            self.file_listbox.dnd_bind("<<Drop>>", self._on_drop)

        # ── Панель настроек парсера ──────────────────────────────────
        settings_frame = ttk.LabelFrame(main_frame, text="Настройки парсера")
        settings_frame.pack(fill=tk.X, pady=(10, 0))

        # Ряд 1: Уровни логирования + Кодировка
        row1 = tk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=5, pady=3)

        tk.Label(row1, text="Уровни:").pack(side=tk.LEFT)
        self.level_error_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row1, text="ERROR", variable=self.level_error_var).pack(side=tk.LEFT, padx=(5, 0))
        self.level_warn_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row1, text="WARN", variable=self.level_warn_var).pack(side=tk.LEFT, padx=(5, 0))
        self.level_info_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row1, text="INFO", variable=self.level_info_var).pack(side=tk.LEFT, padx=(5, 0))
        self.level_debug_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row1, text="DEBUG", variable=self.level_debug_var).pack(side=tk.LEFT, padx=(5, 0))

        tk.Label(row1, text="Кодировка:").pack(side=tk.LEFT, padx=(15, 0))
        self.encoding_var = tk.StringVar(value="utf-8")
        encoding_combo = ttk.Combobox(
            row1,
            textvariable=self.encoding_var,
            values=["utf-8", "utf-8-sig", "cp1251", "latin-1", "koi8-r"],
            width=10,
            state="readonly",
        )
        encoding_combo.pack(side=tk.LEFT, padx=5)

        # Ряд 2: Компактный вывод + Удаление дубликатов
        row2 = tk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=5, pady=3)

        self.compact_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="Компактный JSON", variable=self.compact_var).pack(side=tk.LEFT)
        tk.Label(row2, text="без отступов", fg="gray", font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(2, 10))

        self.remove_dupes_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row2, text="Удалять дубликаты", variable=self.remove_dupes_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(row2, text="только для текстовых логов", fg="gray", font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(2, 10))

        self.remove_ansi_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row2, text="Удалять ANSI", variable=self.remove_ansi_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(row2, text="escape-коды", fg="gray", font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(2, 10))

        self.expand_message_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row2, text="Раскрыть JSON в message", variable=self.expand_message_var).pack(side=tk.LEFT, padx=(10, 0))

        # ── Прогресс-бар ─────────────────────────────────────────────
        self.progress_frame = tk.Frame(main_frame)
        self.progress_frame.pack(fill=tk.X, pady=(5, 0))

        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            mode="determinate",
        )
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True)

        self.progress_label = tk.Label(
            self.progress_frame,
            text="",
            fg="gray",
            width=30,
            anchor="e",
        )
        self.progress_label.pack(side=tk.RIGHT, padx=(5, 0))

        # ── Кнопки ───────────────────────────────────────────────────
        button_frame = tk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        select_btn = tk.Button(
            button_frame,
            text="📁 Выбрать файлы",
            command=self._select_files,
            width=18,
        )
        select_btn.pack(side=tk.LEFT, padx=5)

        clear_btn = tk.Button(
            button_frame,
            text="🗑 Очистить список",
            command=self._clear_files,
            width=18,
        )
        clear_btn.pack(side=tk.LEFT, padx=5)

        about_btn = tk.Button(
            button_frame,
            text="ℹ️ О программе",
            command=self._show_about,
            width=12,
        )
        about_btn.pack(side=tk.LEFT, padx=5)

        self.parse_btn = tk.Button(
            button_frame,
            text="🚀 Парсить",
            command=self._parse_files,
            bg="#4CAF50",
            fg="white",
            width=18,
        )
        self.parse_btn.pack(side=tk.RIGHT, padx=5)

    # ------------------------------------------------------------------
    # Обработчики
    # ------------------------------------------------------------------

    @staticmethod
    def _check_extensions(file_paths: list) -> tuple[list, list]:
        """
        Проверяет расширения файлов на поддержку.

        Returns:
            Кортеж (supported, unsupported) — списки путей.
        """
        supported, unsupported = [], []
        for fp in file_paths:
            ext = os.path.splitext(fp)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                supported.append(fp)
            else:
                unsupported.append(fp)
        return supported, unsupported

    def _add_files(self, file_paths: list):
        """Добавляет файлы в список с валидацией расширений."""
        if not file_paths:
            return
        supported, unsupported = self._check_extensions(file_paths)
        for fp in supported:
            self.file_listbox.insert(tk.END, fp)
        if unsupported:
            names = "\n  • ".join(os.path.basename(f) for f in unsupported)
            messagebox.showerror(
                "Неподдерживаемый формат",
                f"Следующие файлы не поддерживаются:\n\n  • {names}\n\n"
                f"Поддерживаемые форматы:\n  {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )

    def _select_files(self):
        """Открытие диалога выбора файлов."""
        files = filedialog.askopenfilenames(
            title="Выберите файлы логов",
            filetypes=[
                ("Все поддерживаемые", "*.csv *.tsv *.json *.jsonl *.ndjson *.log *.txt *.syslog"),
                ("CSV", "*.csv *.tsv"),
                ("JSON", "*.json *.jsonl *.ndjson"),
                ("Логи", "*.log *.txt *.syslog"),
                ("Все файлы", "*.*"),
            ],
        )
        self._add_files(files)

    def _clear_files(self):
        """Очистка списка файлов."""
        self.file_listbox.delete(0, tk.END)

    def _on_drop(self, event):
        """Обработка перетаскивания файлов (Drag & Drop)."""
        file_paths = event.data.replace("{", "").replace("}", "").split("\n")
        file_paths = [f.strip() for f in file_paths if f.strip()]
        self._add_files(file_paths)

    def _build_parser(self) -> UniversalLogParser:
        """Создаёт парсер с текущими настройками из GUI."""
        kwargs: dict = {
            "encoding": self.encoding_var.get(),
            "indent": None if self.compact_var.get() else 2,
            "remove_ansi": self.remove_ansi_var.get(),
            "remove_duplicates": self.remove_dupes_var.get(),
            "expand_message": self.expand_message_var.get(),
        }

        selected_levels = []
        if self.level_error_var.get():
            selected_levels.append("ERROR")
        if self.level_warn_var.get():
            selected_levels.append("WARN")
        if self.level_info_var.get():
            selected_levels.append("INFO")
        if self.level_debug_var.get():
            selected_levels.append("DEBUG")
        if selected_levels:
            kwargs["log_levels"] = selected_levels

        logger.debug("Создание парсера с параметрами: %s", kwargs)
        return UniversalLogParser(**kwargs)

    def _set_ui_busy(self, busy: bool):
        """Блокирует/разблокирует UI во время парсинга."""
        self._parsing = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.parse_btn.config(state=state)
        if busy:
            self.parse_btn.config(text="⏳ Парсинг...")
        else:
            self.parse_btn.config(text="🚀 Парсить")

    def _parse_files(self):
        """Запуск парсинга выбранных файлов в отдельном потоке."""
        if self._parsing:
            return

        selected_files = self.file_listbox.get(0, tk.END)
        if not selected_files:
            messagebox.showwarning(
                "Предупреждение",
                "Выберите хотя бы один файл для парсинга.",
            )
            return

        # Финальная проверка — на случай, если файлы попали в список
        # до добавления валидации (например, через старую версию).
        supported, unsupported = self._check_extensions(selected_files)
        if unsupported:
            names = "\n  • ".join(os.path.basename(f) for f in unsupported)
            messagebox.showerror(
                "Неподдерживаемый формат",
                f"Следующие файлы будут пропущены:\n\n  • {names}\n\n"
                f"Поддерживаемые форматы:\n  {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
            # Удаляем неподдерживаемые файлы из списка
            # Используем enumerate для корректных индексов (BUG-03 fix)
            unsupported_set = set(unsupported)
            indices_to_remove = sorted(
                idx for idx, fp in enumerate(selected_files)
                if fp in unsupported_set
            )
            for idx in reversed(indices_to_remove):
                self.file_listbox.delete(idx)
            selected_files = supported
            if not selected_files:
                return

        # Выбор выходной папки через диалог (UX-01 fix)
        output_dir = filedialog.askdirectory(
            title="Выберите папку для сохранения результатов",
            initialdir=self._get_default_output_dir(),
        )
        if not output_dir:
            return  # Пользователь отменил выбор

        os.makedirs(output_dir, exist_ok=True)

        # Проверка перезаписи файлов (UX-05 fix)
        existing_outputs = []
        for file_path in selected_files:
            base = os.path.basename(file_path)
            output_path = os.path.join(output_dir, base + "_formatted.json")
            if os.path.exists(output_path):
                existing_outputs.append(output_path)

        if existing_outputs:
            names = "\n  • ".join(os.path.basename(f) for f in existing_outputs)
            answer = messagebox.askyesno(
                "Файлы уже существуют",
                f"Следующие файлы будут перезаписаны:\n\n  • {names}\n\n"
                f"Продолжить?",
            )
            if not answer:
                return

        # Строим парсер ЗДЕСЬ, на главном потоке: _build_parser читает
        # tkinter-переменные (encoding_var.get() и др.), а Tcl нельзя трогать
        # из фонового потока — иначе RuntimeError "main thread is not in main loop".
        parser = self._build_parser()

        # Запуск парсинга в отдельном потоке (BUG-02 fix)
        self._set_ui_busy(True)
        self.progress_bar["value"] = 0
        self.progress_bar["maximum"] = len(selected_files)
        self.progress_label.config(text="")

        thread = threading.Thread(
            target=self._do_parse,
            args=(parser, list(selected_files), output_dir),
            daemon=True,
        )
        thread.start()

    def _do_parse(self, parser: UniversalLogParser, file_paths: list, output_dir: str):
        """Парсинг файлов (выполняется в отдельном потоке).

        parser строится на главном потоке и передаётся сюда готовым —
        внутри потока tkinter-переменные не трогаем (только root.after).
        """
        success_count = 0
        error_count = 0
        error_messages: list[str] = []

        try:
            for i, file_path in enumerate(file_paths):
                self.root.after(0, self._update_progress, i + 1, len(file_paths), file_path)

                try:
                    result = parser.parse_file(file_path)
                    base = os.path.basename(file_path)
                    output_path = os.path.join(output_dir, base + "_formatted.json")
                    parser.to_json_file(result, output_path)
                    success_count += 1
                    logger.info("Обработан: %s → %s", file_path, output_path)
                except Exception as e:
                    error_count += 1
                    friendly_msg = _user_friendly_error(e)
                    error_messages.append(f"{os.path.basename(file_path)}: {friendly_msg}")
                    logger.error("Ошибка обработки %s: %s", file_path, e)
        except BaseException as e:
            # Ловим всё остальное (MemoryError и т.д.) — UI должен разблокироваться
            error_count += 1
            error_messages.append(f"Критическая ошибка: {type(e).__name__}: {e}")
            logger.error("Критическая ошибка в потоке парсинга: %s", e)
        finally:
            # Гарантируем разблокировку UI при любом исходе
            self.root.after(
                0,
                self._on_parse_complete,
                success_count,
                error_count,
                error_messages,
                output_dir,
            )

    def _update_progress(self, current: int, total: int, current_file: str):
        """Обновляет прогресс-бар (вызывается через root.after)."""
        self.progress_bar["value"] = current
        filename = os.path.basename(current_file)
        if len(filename) > 25:
            filename = filename[:22] + "..."
        self.progress_label.config(text=f"{current}/{total}: {filename}")

    def _on_parse_complete(
        self,
        success_count: int,
        error_count: int,
        error_messages: list,
        output_dir: str,
    ):
        """Вызывается после завершения парсинга (в главном потоке)."""
        self._set_ui_busy(False)
        self.progress_label.config(text="")

        if error_count == 0:
            answer = messagebox.askyesno(
                "Готово ✅",
                f"Все {success_count} файл(ов) успешно обработаны!\n\n"
                f"Результаты в: {output_dir}\n\n"
                f"Открыть папку с результатами?",
            )
            if answer:
                self._open_in_finder(output_dir)
        else:
            err_detail = "\n".join(error_messages[:5])
            if len(error_messages) > 5:
                err_detail += f"\n... и ещё {len(error_messages) - 5} ошибок"
            messagebox.showwarning(
                "Завершено с ошибками",
                f"Успешно: {success_count}, с ошибками: {error_count}.\n\n"
                f"Ошибки:\n{err_detail}\n\n"
                f"Результаты в: {output_dir}",
            )

    @staticmethod
    def _get_default_output_dir() -> str:
        """Возвращает путь к выходной папке по умолчанию.

        НЕ используем Desktop/Documents/Downloads: и открытие диалога выбора
        с initialdir внутри этих папок, и запись результата туда из процесса
        без TCC-доступа (GUI запущен из .app) упираются в системный TCC-промпт
        и «зависают». Корень дома (~) не защищён TCC. Папку создаём заранее,
        чтобы диалог открывался в существующем безопасном каталоге.
        """
        out = os.path.expanduser("~/logzilla3000-output")
        try:
            os.makedirs(out, exist_ok=True)
        except OSError:
            pass
        return out

    @staticmethod
    def _open_in_finder(path: str):
        """Открывает папку в Finder (macOS) или файловом менеджере."""
        if not os.path.exists(path):
            return
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform == "linux":
            subprocess.run(["xdg-open", path], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", path], check=False)

    def _show_about(self):
        """Показывает диалог «О программе»."""
        dnd_status = "✅ Доступен" if self.dnd_enabled else "❌ Недоступен (используйте кнопку выбора файлов)"
        messagebox.showinfo(
            "О программе logZilla3000",
            f"🦎 logZilla3000 v{APP_VERSION}\n\n"
            f"Универсальный парсер логов.\n"
            f"Очищает логи от мусора и преобразует в JSON.\n\n"
            f"Платформа: {platform.system()} {platform.machine()}\n"
            f"Python: {platform.python_version()}\n"
            f"Drag & Drop: {dnd_status}\n\n"
            f"Поддерживаемые форматы:\n"
            f"  CSV, TSV, JSON, JSONL, NDJSON,\n"
            f"  Apache, Nginx, syslog, текстовые логи",
        )


def _show_error_dialog(message: str) -> None:
    """Показывает диалог ошибки через subprocess (macOS) или stderr.

    Использует subprocess.run вместо os.system для предотвращения
    shell-инъекции (BUG-01 fix).
    """
    if sys.platform == "darwin":
        try:
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display dialog "{message}" '
                    f'buttons {{"OK"}} with icon stop '
                    f'with title "logZilla3000"',
                ],
                check=False,
                capture_output=True,
            )
        except Exception:
            print(message, file=sys.stderr)
    else:
        print(message, file=sys.stderr)


def _get_log_dir() -> str:
    """Возвращает каталог для логов GUI в зависимости от платформы.

    BUG-06 fix: захардкоженный ~/Library/Logs работает только на macOS.
    На других платформах используем XDG-совместимый путь.

    - macOS: ~/Library/Logs
    - Linux: $XDG_STATE_HOME/logzilla3000/logs или ~/.local/state/logzilla3000/logs
    - Windows: %LOCALAPPDATA%/logzilla3000/logs или ~/AppData/Local/...
    """
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        return os.path.join(base, "logzilla3000", "logs")
    # Linux / другие POSIX
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "logzilla3000", "logs")


def main():
    """Точка входа для GUI."""
    log_dir = _get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "logzilla3000_gui.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )

    try:
        dnd_enabled = False

        if HAS_DND:
            try:
                root = TkinterDnD.Tk()
                dnd_enabled = True
            except (tk.TclError, RuntimeError):
                # tkdnd не загрузился (типично для Tcl 9.x). Но tk.Tk.__init__
                # внутри TkinterDnD.Tk уже отработал и создал РАБОЧЕЕ окно —
                # оно лежит в tk._default_root. Переиспользуем его как обычный
                # root: не создаём второе окно (иначе появляется пустой "tk")
                # и НЕ вызываем destroy() на частично сконструированном
                # объекте — на Tcl 9 это роняет процесс нативно (краш-репорт
                # macOS, окно вообще не появляется). Drag&Drop остаётся
                # отключённым: drop_target_register стоит под if self.dnd_enabled.
                root = getattr(tk, "_default_root", None) or tk.Tk()
                logger.warning(
                    "Drag & Drop недоступен: нативная библиотека tkdnd не загрузилась. "
                    "Запустите ./setup_gui.sh для компиляции tkdnd из исходников.",
                )
        else:
            root = tk.Tk()
            logger.warning(
                "Модуль tkinterdnd2 не установлен. Drag & Drop отключён. "
                "Установите: ./setup_gui.sh",
            )

        app = LogZillaGUI(root, dnd_enabled=dnd_enabled)

        # Выводим окно на передний план (важно при запуске из .app)
        root.lift()
        root.focus_force()
        root.attributes("-topmost", True)
        root.after(100, lambda: root.attributes("-topmost", False))

        root.mainloop()

    except Exception as e:
        logger.critical("Фатальная ошибка: %s", e, exc_info=True)
        _show_error_dialog(
            f"Фатальная ошибка:\n{e}\n\n"
            f"Лог: {log_file}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
