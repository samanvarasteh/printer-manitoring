# config/settings.py

"""
تنظیمات سراسری برنامه
"""

# ─── فایل‌ها و مسیرها ───────────────────────────────────────────
PRINTERS_FILE        = "printers.json"
DB_PATH              = "logs.db"
OID_PROFILES_FILE    = "oid_profiles.json"
VALIDATION_LOG_FILE  = "oid_validation_errors.txt"

# ─── پرینترهای پیش‌فرض ─────────────────────────────────────────
DEFAULT_PRINTERS = [
    {"ip": "172.16.25.53", "name": "Toshiba #1", "community": "public"},
    {"ip": "172.16.25.54", "name": "Toshiba #2", "community": "public"},
    {"ip": "172.16.25.55", "name": "Toshiba #3", "community": "public"},
    {"ip": "172.16.25.57", "name": "Toshiba #4", "community": "public"},
]

# ─── SNMP ───────────────────────────────────────────────────────
SNMP_PORT     = 161

# ─── Polling ────────────────────────────────────────────────────
# هر 10 دقیقه یک بار poll انجام می‌شود (enhanced_collector برای walk SNMP زمان بیشتری نیاز دارد)
POLL_INTERVAL = 600   # ثانیه (10 دقیقه)

# ─── Flask ──────────────────────────────────────────────────────
FLASK_PORT = 5053
