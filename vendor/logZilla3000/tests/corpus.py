"""
Корпус реальных вариантов логов для проверки правильности распарса.

Каждый кейс — это (имя, сырой текст, ожидаемый формат, проверки полей первой
записи). Используется в test_corpus.py: data-driven прогон меряет долю
корректно распознанных и разобранных вариантов.

Цель — высокая уверенность, что «типичный лог из прода» парсится правильно:
покрываем самые частые формы JSON/CSV/syslog/web и (главное) множество вариантов
текстовых app-логов, которые в реальности встречаются чаще всего.

Формат кейса (dict):
    name    — человекочитаемое имя
    raw     — сырой лог (одна или несколько строк)
    fmt     — ожидаемый LogFormat.value (строка) или None, если формат не важен
    checks  — dict {поле: ожидаемое значение} для records[0]
    present — list полей, которые должны присутствовать в records[0]
    absent  — list полей, которых НЕ должно быть (антимусор)
    count   — ожидаемое число записей (опционально)
"""

CASES: list[dict] = [
    # ============================ JSON / JSONL ============================
    {
        "name": "jsonl-docker",
        "raw": '{"log":"hello","stream":"stdout","time":"2023-01-02T03:04:05Z"}\n'
               '{"log":"world","stream":"stderr","time":"2023-01-02T03:04:06Z"}',
        "fmt": "jsonl",
        "checks": {"stream": "stdout"},
        "count": 2,
    },
    {
        "name": "json-pretty-array",
        "raw": '[\n  {"a": 1, "msg": "x"},\n  {"a": 2, "msg": "y"}\n]',
        "fmt": "json",
        "count": 2,
    },
    {
        "name": "jsonl-ecs",
        "raw": '{"@timestamp":"2023-01-02T03:04:05Z","log.level":"info","message":"ok"}\n'
               '{"@timestamp":"2023-01-02T03:04:06Z","log.level":"error","message":"bad"}',
        "fmt": "jsonl",
        "present": ["message"],
    },
    {
        "name": "gelf",
        "raw": '{"version":"1.1","host":"app1","short_message":"boom","level":3,"_app":"api"}',
        "fmt": "json",
        "present": ["short_message"],
    },

    # ================================ CSV/TSV ================================
    {
        "name": "csv-standard",
        "raw": "timestamp,level,service,message\n"
               "2025-01-15T10:30:00,INFO,auth,Login OK\n"
               "2025-01-15T10:30:05,WARN,gw,Slow",
        "fmt": "csv",
        "checks": {"level": "INFO", "service": "auth"},
        "count": 2,
    },
    {
        "name": "csv-spaces-in-header",
        "raw": "Request Time,Status Code,Client IP\n"
               "2023-01-01 10:00:00,200,10.0.0.1\n"
               "2023-01-01 10:00:01,500,10.0.0.2",
        "fmt": "csv",
        "checks": {"status_code": 200, "client_ip": "10.0.0.1"},
    },
    {
        "name": "csv-semicolon-euro",
        "raw": "datum;level;text\n"
               "2025-01-15;INFO;ok\n"
               "2025-01-16;WARN;slow",
        "fmt": "csv",
        "checks": {"level": "INFO"},
    },
    {
        "name": "tsv",
        "raw": "ts\tlevel\tmsg\n2025-01-15\tINFO\tok\n2025-01-16\tERROR\tbad",
        "fmt": "tsv",
        "checks": {"level": "INFO"},
    },
    {
        "name": "csv-quoted-comma",
        "raw": 'name,note,n\nalice,"hello, world",1\nbob,plain,2',
        "fmt": "csv",
        "checks": {"note": "hello, world", "n": 1},
    },
    {
        "name": "csv-leading-zero-id",
        "raw": "id,zip,phone\n007,01234,0991234567\n008,00010,0501112233",
        "fmt": "csv",
        "checks": {"id": "007", "zip": "01234", "phone": "0991234567"},
    },
    {
        "name": "csv-kibana-at-timestamp",
        "raw": '@timestamp,level,message\n'
               '2023-01-02T03:04:05.000Z,info,started\n'
               '2023-01-02T03:04:06.000Z,error,failed',
        "fmt": "csv",
        "checks": {"level": "info"},
    },
    {
        "name": "csv-units-in-header",
        "raw": "Duration (ms),error %,p95/p99\n12.5,0.1,3\n14.0,0.2,4",
        "fmt": "csv",
        "checks": {"duration_ms": 12.5},
    },
    {
        # Over-quoted экспорт (вся строка в кавычках + хвостовой ;) с титульной
        # преамбулой — реальный артефакт ре-сейва. Рамка снимает обёртку и
        # пропускает преамбулу → нормальный CSV.
        "name": "csv-overquoted-with-preamble",
        "raw": 'Отчёт по сервису;\n;\n'
               '"ts,""level"",""msg""";\n'
               '"2026-01-01,""INFO"",""ok""";\n'
               '"2026-01-01,""ERROR"",""boom"""',
        "fmt": "csv",
        "checks": {"level": "INFO", "msg": "ok"},
        "count": 2,
    },
    {
        "name": "csv-trailing-empty-col",
        "raw": "a,b,c\n1,2,\n3,4,",
        "fmt": "csv",
        "checks": {"a": 1, "b": 2, "c": None},
    },
    {
        "name": "csv-multiline-json-column",
        "raw": 'id,payload\n1,"{""k"":1,\n""j"":2}"\n2,"{""k"":3}"',
        "fmt": "csv",
        "checks": {"id": 1, "k": 1, "j": 2},
    },

    # ============================== web-серверы ==============================
    {
        "name": "apache-combined",
        "raw": '192.168.1.100 user1 frank [10/Oct/2000:13:55:36 -0700] '
               '"GET /a.gif HTTP/1.0" 200 2326 "http://x/" "Mozilla/4.08"',
        "fmt": "apache",
        "checks": {"method": "GET", "status": 200},
    },
    {
        "name": "nginx-combined",
        "raw": '127.0.0.1 - - [15/Jan/2025:10:30:00 +0300] "GET /h HTTP/1.1" 200 15 "-" "kube-probe/1.27"\n'
               '10.0.0.1 - john [15/Jan/2025:10:30:01 +0300] "POST /l HTTP/1.1" 201 5 "-" "curl/8"',
        "fmt": "nginx",
        "checks": {"method": "GET", "status": 200},
    },
    {
        # Common Log Format (без referer/UA). Детектится как apache, но ключевые
        # поля (метод/путь/статус) извлекаются верно — это и важно.
        "name": "web-common-log-no-ua",
        "raw": '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /i.html HTTP/1.0" 200 2326',
        "fmt": None,
        "checks": {"method": "GET", "path": "/i.html", "status": 200},
    },

    # ================================ syslog ================================
    {
        "name": "syslog-bsd",
        "raw": "Jan 15 10:30:00 webserver sshd[1234]: Accepted publickey for admin\n"
               "Jan 15 10:30:01 webserver cron[5678]: (root) CMD (/usr/bin/x)",
        "fmt": "syslog",
        "checks": {"host": "webserver", "app": "sshd", "pid": 1234},
    },
    {
        "name": "syslog-rfc5424",
        "raw": "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su 1234 ID47 - 'su root' failed\n"
               "<165>1 2003-10-11T22:14:16.005Z host evntslog 9999 ID48 - another",
        "fmt": "syslog",
        "checks": {"host": "mymachine.example.com", "app": "su", "facility": 4, "severity": 2},
    },
    {
        "name": "syslog-no-pid",
        "raw": "Jan 15 10:30:01 webserver kernel: [UFW BLOCK] IN=eth0 SRC=10.0.0.5\n"
               "Jan 15 10:30:02 webserver systemd: Started service",
        "fmt": "syslog",
        "checks": {"app": "kernel"},
    },
    {
        "name": "syslog-rsyslog-iso",
        "raw": "2023-01-02T03:04:05.123456+00:00 myhost myapp[1234]: Something happened\n"
               "2023-01-02T03:04:06.000+00:00 myhost myapp[1234]: again",
        "fmt": "syslog",
        "checks": {"host": "myhost", "app": "myapp", "pid": 1234, "message": "Something happened"},
    },
    {
        "name": "syslog-iso-no-pid",
        "raw": "2023-01-02T03:04:05Z myhost systemd: Started unit\n"
               "2023-01-02T03:04:06Z myhost systemd: Stopped unit",
        "fmt": "syslog",
        "checks": {"host": "myhost", "app": "systemd", "message": "Started unit"},
    },
    {
        "name": "haproxy-via-syslog",
        "raw": 'Jan 15 10:30:00 lb haproxy[123]: 10.0.0.1:5234 [15/Jan/2025:10:30:00.123] f b/s 0/0/1/2/3 200 512',
        "fmt": "syslog",
        "checks": {"host": "lb", "app": "haproxy", "pid": 123},
    },

    # ================================ logfmt ================================
    {
        "name": "logfmt-go",
        "raw": 'level=info ts=2023-01-02T03:04:05Z msg="request done" status=200 dur=1.23\n'
               'level=error ts=2023-01-02T03:04:06Z msg="db fail" status=500',
        "fmt": "logfmt",
        "checks": {"level": "info", "status": 200, "dur": 1.23, "msg": "request done"},
        "count": 2,
    },
    {
        "name": "logfmt-heroku-router",
        "raw": 'at=info method=GET path="/users" host=app.io status=200 bytes=1024 protocol=https\n'
               'at=error method=POST path="/login" host=app.io status=503 bytes=0 protocol=https',
        "fmt": "logfmt",
        "checks": {"method": "GET", "status": 200},
    },

    # ===================== текстовые app-логи (вариативность) =====================
    {
        "name": "java-logback",
        "raw": "2023-01-02 03:04:05,123 INFO  [main] com.foo.Bar - started ok\n"
               "2023-01-02 03:04:06,456 ERROR [pool-1] com.foo.Baz - boom",
        "fmt": "text",
        "checks": {"level": "INFO", "thread": "main", "logger": "com.foo.Bar", "message": "started ok"},
        "absent": ["http_status", "json_snippet", "raw"],
    },
    {
        "name": "python-logging-default",
        "raw": "2023-01-02 03:04:05,123 - myapp.module - INFO - Something happened\n"
               "2023-01-02 03:04:06,456 - myapp.db - ERROR - Connection lost",
        "fmt": "text",
        "checks": {"level": "INFO", "logger": "myapp.module", "message": "Something happened"},
    },
    {
        "name": "iso8601-t-zulu",
        "raw": "2025-01-15T10:30:00.123Z ERROR boom happened\n"
               "2025-01-15T10:30:01.456Z INFO recovered",
        "fmt": "text",
        "checks": {"level": "ERROR", "message": "boom happened"},
        "absent": ["http_status"],
    },
    {
        "name": "bracketed-timestamp-level",
        "raw": "[2025-01-15 10:30:00] [error] upstream timed out\n"
               "[2025-01-15 10:30:01] [warn] retrying",
        "fmt": "text",
        "checks": {"level": "ERROR", "message": "upstream timed out"},
    },
    {
        "name": "nginx-error-log",
        "raw": "2023/01/02 03:04:05 [error] 1234#0: *1 connect() failed, client: 10.0.0.1\n"
               "2023/01/02 03:04:06 [warn] 1234#0: *2 slow",
        "fmt": "text",
        "checks": {"level": "ERROR"},
        "present": ["timestamp"],
    },
    {
        "name": "go-standard-log",
        "raw": "2009/11/10 23:00:00 starting server\n2009/11/10 23:00:01 listening on :8080",
        "fmt": "text",
        "present": ["timestamp", "message"],
        "checks": {"message": "starting server"},
    },
    {
        "name": "klog-glog",
        "raw": "I0102 03:04:05.123456  1234 server.go:42] Starting server\n"
               "E0102 03:04:06.123456  1234 server.go:99] failed to bind",
        "fmt": "text",
        "checks": {"level": "INFO", "tid": 1234, "source": "server.go:42", "message": "Starting server"},
    },
    {
        "name": "ansi-colored-bracket-level",
        "raw": "\x1b[32m2025-01-15 10:30:00\x1b[0m \x1b[1m[INFO]\x1b[0m Application started\n"
               "\x1b[31m2025-01-15 10:30:10\x1b[0m \x1b[1m[ERROR]\x1b[0m DB down",
        "fmt": "text",
        "checks": {"level": "INFO", "message": "Application started"},
    },
    {
        "name": "rails-style-level-first",
        "raw": "ERROR -- : Processing failed for request 42\n"
               "INFO -- : Completed 200 OK in 35ms",
        "fmt": "text",
        "checks": {"level": "ERROR"},
    },
    {
        "name": "time-only-head",
        "raw": "10:30:00.123 DEBUG cache warm\n10:30:01.000 INFO ready",
        "fmt": "text",
        "checks": {"level": "DEBUG", "message": "cache warm"},
    },
    {
        "name": "dotnet-level-colon",
        "raw": "2025-01-15 10:30:00 [Information] Service online\n"
               "2025-01-15 10:30:01 [Warning] Disk low",
        "fmt": "text",
        "checks": {"level": "INFO"},
    },

    {
        # Экспорт Grafana/Loki Explore: преамбула + "<ts>\t{json}" на строку.
        # Поля JSON поднимаются на верх (level/service становятся колонками),
        # строки-метаданные экспорта отсеиваются.
        "name": "loki-explore-json-export",
        "raw": 'Common labels: {"app":"event-mapper"}\n'
               'Line limit: "1000 (77 returned)"\n'
               'Total bytes processed: "80.4 kB"\n'
               '2026-06-09 17:00:19.624\t{"level": "ERROR", "msg": "boom", "service_name": "event-mapper", "trace_id": "abc"}\n'
               '2026-06-09 17:00:19.617\t{"level": "INFO", "msg": "ok", "service_name": "event-mapper"}',
        "fmt": "text",
        "checks": {"level": "ERROR", "msg": "boom", "service_name": "event-mapper", "trace_id": "abc"},
        "absent": ["message"],
        "count": 2,
    },

    {
        # JSONL, где поле — JSON-строка: рекурсивное вскрытие достаёт объект
        # (OpenSearch/ECS-экспорты сплошь так: event.original/_source строкой).
        "name": "jsonl-nested-json-string-field",
        "raw": '{"ts":"2026-01-01T00:00:00Z","level":"INFO","payload":"{\\"name\\":\\"a\\",\\"rows\\":5}"}\n'
               '{"ts":"2026-01-01T00:00:01Z","level":"ERROR","payload":"{\\"name\\":\\"b\\",\\"rows\\":0}"}',
        "fmt": "jsonl",
        "checks": {"level": "INFO", "payload": {"name": "a", "rows": 5}},
        "count": 2,
    },

    # ===================== CRI / containerd (kubectl logs) =====================
    {
        # CRI/containerd: <RFC3339Nano> stream F|P msg. message-JSON раскрывается.
        "name": "cri-containerd",
        "raw": '2024-05-01T10:00:00.123456789Z stdout F {"level":"info","msg":"started"}\n'
               '2024-05-01T10:00:01.000000000Z stderr F boom',
        "fmt": "cri",
        "checks": {"stream": "stdout", "message": {"level": "info", "msg": "started"}},
        "present": ["_ts", "_ts_iso"],
        "count": 2,
    },
    {
        # Partial-строки (logtag=P) рантайм режет по 16 КБ — склеиваются с финальной F.
        "name": "cri-partial-merge",
        "raw": '2024-05-01T10:00:00.1Z stdout P first chunk \n'
               '2024-05-01T10:00:00.2Z stdout F second',
        "fmt": "cri",
        "checks": {"message": "first chunk second"},
        "count": 1,
    },

    # ============================ CEF / LEEF (SIEM) ============================
    {
        "name": "cef-arcsight",
        "raw": 'CEF:0|Security|threatmanager|1.0|100|worm successfully stopped|10|src=10.0.0.1 dst=2.1.2.2 spt=1232\n'
               'CEF:0|Security|threatmanager|1.0|101|port scan|8|src=10.0.0.3 dst=2.1.2.2',
        "fmt": "cef",
        "checks": {"device_vendor": "Security", "name": "worm successfully stopped",
                   "src": "10.0.0.1", "spt": 1232},
        "count": 2,
    },
    {
        "name": "leef-qradar",
        "raw": 'LEEF:1.0|Lancope|StealthWatch|1.0|41|src=192.0.2.0\tdst=172.50.123.1\tsev=5\n'
               'LEEF:1.0|Lancope|StealthWatch|1.0|42|src=192.0.2.9\tdst=172.50.123.2\tsev=3',
        "fmt": "leef",
        "checks": {"vendor": "Lancope", "event_id": "41", "src": "192.0.2.0", "sev": 5},
        "count": 2,
    },

    # ===================== многострочные записи (стек-трейсы) =====================
    {
        # Java-трейс под лог-строкой: кадры сшиваются в поле stack, а не плодят
        # мусорные {message}. Запись остаётся одна на исключение.
        "name": "java-stacktrace-stitched",
        "raw": "2023-01-02 03:04:05,123 ERROR [main] com.foo.Bar - boom\n"
               "java.lang.NullPointerException: x is null\n"
               "\tat com.foo.Bar.run(Bar.java:10)\n"
               "\tat com.foo.Baz.call(Baz.java:20)\n"
               "2023-01-02 03:04:06,000 INFO [main] com.foo.Bar - recovered",
        "fmt": "text",
        "checks": {"level": "ERROR", "message": "boom"},
        "present": ["stack"],
        "count": 2,
    },

    # ===================== канонический timestamp (_ts/_ts_iso) =====================
    {
        # Unix epoch в поле ts → нормализуется в _ts/_ts_iso (раньше не распознавался).
        "name": "jsonl-epoch-ts",
        "raw": '{"ts":1736935800,"level":"INFO","msg":"a"}\n'
               '{"ts":1736935860,"level":"ERROR","msg":"b"}',
        "fmt": "jsonl",
        "checks": {"_ts_iso": "2025-01-15T10:10:00Z"},
        "present": ["_ts"],
        "count": 2,
    },

    # ============================ анти-кейсы ============================
    # Проза со случайными запятыми НЕ должна стать CSV.
    {
        "name": "prose-not-csv",
        "raw": "Hello, world, this is text\nAnother, line, here goes\nThird, row, words",
        "fmt": "text",
        "checks": {"message": "Hello, world, this is text"},
    },
    # Проза с заглавным словом-уровнем без таймстампа не должна получить level.
    {
        "name": "prose-not-level",
        "raw": "Information about the system is available\nStarted the migration process",
        "fmt": "text",
        "checks": {"message": "Information about the system is available"},
        "absent": ["level", "timestamp"],
    },
    # Одинокое key=value в прозе не должно опознаваться как logfmt.
    {
        "name": "prose-not-logfmt",
        "raw": "the variable x=1 was set in the config\nand then the service restarted cleanly",
        "fmt": "text",
    },
]
