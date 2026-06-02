"""Тестовые данные для проверки парсера."""

# ============================================================
# CSV лог с полными данными
# ============================================================
CSV_FULL = """timestamp,level,service,message,request_id
2025-01-15T10:30:00,INFO,auth-service,User logged in successfully,abc-123-def
2025-01-15T10:30:01,DEBUG,auth-service,Token validated,abc-123-def
2025-01-15T10:30:05,WARN,api-gateway,Slow response time: 2500ms,xyz-456-ghi
2025-01-15T10:30:10,ERROR,payment-service,Payment failed: timeout,xyz-456-ghi
2025-01-15T10:30:15,INFO,notification-service,Email sent to user@example.com,abc-123-def"""

# ============================================================
# CSV лог с пропущенными строками и полями
# ============================================================
CSV_INCOMPLETE = """timestamp;level;service;message
2025-01-15T10:30:00;INFO;auth-service;User logged in
2025-01-15T10:30:05;WARN;api-gateway;Slow response
;ERROR;payment-service;Payment failed
2025-01-15T10:30:15;INFO;;Email sent
2025-01-15T10:30:20;DEBUG;cache-service;
2025-01-15T10:30:25;INFO;auth-service;Token refreshed"""

# ============================================================
# CSV без заголовка
# ============================================================
CSV_NO_HEADER = """2025-01-15T10:30:00,INFO,auth-service,Login OK
2025-01-15T10:30:05,WARN,api-gateway,Slow response
2025-01-15T10:30:10,ERROR,payment-service,Payment failed"""

# Данные для проверки has_header=False (числовые)
CSV_NUMERIC = """100,200,300,400
101,201,301,401
102,202,302,402"""

# ============================================================
# JSON лог
# ============================================================
JSON_LOG = """{
  "service": "api-gateway",
  "logs": [
    {"timestamp": "2025-01-15T10:30:00", "level": "INFO", "message": "Request received"},
    {"timestamp": "2025-01-15T10:30:01", "level": "DEBUG", "message": "Processing"},
    {"timestamp": "2025-01-15T10:30:05", "level": "ERROR", "message": "Connection refused"}
  ]
}"""

# ============================================================
# JSONL лог (JSON Lines)
# ============================================================
JSONL_LOG = """{"timestamp":"2025-01-15T10:30:00","level":"INFO","service":"auth","message":"Login OK"}
{"timestamp":"2025-01-15T10:30:01","level":"DEBUG","service":"auth","message":"Token check"}
{"timestamp":"2025-01-15T10:30:05","level":"WARN","service":"gateway","message":"Slow response"}
{"timestamp":"2025-01-15T10:30:10","level":"ERROR","service":"payment","message":"Timeout"}"""

# ============================================================
# Apache Combined Log Format
# ============================================================
APACHE_LOG = """192.168.1.100 user1 frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326 "http://www.example.com/start.html" "Mozilla/4.08"
192.168.1.101 user2 - [10/Oct/2000:13:55:37 -0700] "POST /api/login HTTP/1.1" 401 128 "" "curl/7.68.0"
10.0.0.5 user3 admin [10/Oct/2000:13:55:38 -0700] "GET /dashboard HTTP/1.1" 200 8543 "http://www.example.com/" "Mozilla/5.0"
192.168.1.102 user4 - [10/Oct/2000:13:55:39 -0700] "DELETE /api/users/123 HTTP/1.1" 403 64 "" "Python-requests/2.28.0"
10.0.0.6 user5 - [10/Oct/2000:13:55:40 -0700] "GET /static/style.css HTTP/1.1" 304 0 "http://www.example.com/dashboard" "Mozilla/5.0" """

# ============================================================
# Nginx Combined Log Format
# ============================================================
NGINX_LOG = """127.0.0.1 - - [15/Jan/2025:10:30:00 +0300] "GET /api/health HTTP/1.1" 200 15 "-" "kube-probe/1.27"
192.168.0.1 - john [15/Jan/2025:10:30:01 +0300] "POST /api/login HTTP/1.1" 200 312 "https://example.com/login" "Mozilla/5.0"
10.0.0.1 - - [15/Jan/2025:10:30:02 +0300] "GET /api/users HTTP/1.1" 401 48 "-" "curl/7.68.0"
172.16.0.1 - - [15/Jan/2025:10:30:03 +0300] "GET /static/app.js HTTP/1.1" 304 0 "https://example.com/" "Mozilla/5.0" """

# ============================================================
# Syslog
# ============================================================
SYSLOG_DATA = """Jan 15 10:30:00 webserver sshd[1234]: Accepted publickey for admin from 192.168.1.100
Jan 15 10:30:01 webserver kernel: [UFW BLOCK] IN=eth0 SRC=10.0.0.5 DST=192.168.1.1
Jan 15 10:30:05 webserver nginx: 192.168.1.101 - - "GET /api HTTP/1.1" 200 1234
Jan 15 10:30:10 webserver crond[5678]: (root) CMD (/usr/bin/cleanup.sh)"""

# ============================================================
# Текстовый лог с ANSI и мусором
# ============================================================
TEXT_LOG_DIRTY = """\x1b[32m2025-01-15 10:30:00\x1b[0m \x1b[1m[INFO]\x1b[0m  Application started successfully
\x1b[32m2025-01-15 10:30:01\x1b[0m \x1b[1m[DEBUG]\x1b[0m Loading configuration from /etc/app/config.yml
\x1b[33m2025-01-15 10:30:05\x1b[0m \x1b[1m[WARN]\x1b[0m  Connection pool almost full: 95% used
\x1b[31m2025-01-15 10:30:10\x1b[0m \x1b[1m[ERROR]\x1b[0m Failed to connect to database: Connection refused
\x1b[31m2025-01-15 10:30:11\x1b[0m \x1b[1m[ERROR]\x1b[0m Failed to connect to database: Connection refused
================================================================================
\x1b[32m2025-01-15 10:30:15\x1b[0m \x1b[1m[INFO]\x1b[0m  Retrying connection... attempt 3/5
\x1b[32m2025-01-15 10:30:20\x1b[0m \x1b[1m[INFO]\x1b[0m  Connected to database successfully
\x00\x00\x00
"""

# ============================================================
# Текстовый лог с HTML-тегами
# ============================================================
TEXT_LOG_HTML = """<div class="log-entry">2025-01-15 10:30:00 [INFO] User login: admin</div>
<div class="log-entry">2025-01-15 10:30:01 [INFO] Request processed in 45ms</div>
<div class="log-entry">2025-01-15 10:30:05 [WARN] High memory usage: 85%</div>
<div class="log-entry">2025-01-15 10:30:10 [ERROR] NullPointerException at com.app.Service.process(Service.java:42)</div>"""

# ============================================================
# Полуструктурированный лог (смешанный формат)
# ============================================================
MIXED_LOG = """2025-01-15 10:30:00 INFO  [main] Application started
2025-01-15 10:30:01 DEBUG [main] Config loaded: {"db": "postgres", "port": 5432}
2025-01-15 10:30:05 WARN  [pool-1] Slow query: SELECT * FROM users WHERE id = 'abc-123' took 2500ms
2025-01-15 10:30:10 ERROR [pool-2] Connection to 10.0.0.5:5432 refused
2025-01-15 10:30:15 INFO  [main] Health check passed
some random line without structure
another random line"""

# ============================================================
# Лог с пропущенными строками (разрывы)
# ============================================================
BROKEN_LOG = """2025-01-15 10:30:00 INFO  Service started
2025-01-15 10:30:01 DEBUG Processing request from 192.168.1.100

2025-01-15 10:30:05 WARN  Memory usage high


2025-01-15 10:30:10 ERROR Timeout connecting to db.example.com:5432
2025-01-15 10:30:15 INFO  Retry successful"""

# ============================================================
# CSV с мусорными строками
# ============================================================
CSV_DIRTY = """# Generated by log exporter v2.1
# Date: 2025-01-15
timestamp,level,service,message
2025-01-15T10:30:00,INFO,auth,Login OK
2025-01-15T10:30:05,WARN,gateway,Slow response
2025-01-15T10:30:10,ERROR,payment,Failed
# End of report"""
