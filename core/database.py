"""
توابع کار با SQLite:
- init_db: ایجاد جدول logs (با فیلد paper_size) و جدول printer_counters
- add_event: ثبت رویداد (با پشتیبانی از paper_size)
- get_log: دریافت لاگ یک پرینتر
- get_all_logs: دریافت همه لاگ‌ها با فیلتر
- clear_logs: پاک کردن لاگ‌های غیر از PRINT، SERVICE و REFILL
- prune_old_print_logs: پاکسازی خودکار لاگ‌های PRINT قدیمی
- load_printer_counters, save_printer_counters: ذخیره و بازیابی مقادیر قبلی شمارنده‌ها
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta

from config.settings import DB_PATH
# NOTE: from core import store حذف شده است (import پویا درون تابع add_event)

log = logging.getLogger("PrinterMonitor")

# فیلدهای top-level که مستقیم در ستون‌های جدول ذخیره می‌شوند (بقیه به details JSON می‌روند)
_LOG_TOP_LEVEL_FIELDS = frozenset(
    ("timestamp", "message", "pages", "color", "code", "severity", "paper_size", "username")
)


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    c = conn.cursor()
    
    # بهینه‌سازی performance
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            printer_ip TEXT NOT NULL,
            printer_name TEXT,
            timestamp TEXT NOT NULL,
            type TEXT,
            message TEXT,
            pages INTEGER,
            color TEXT,
            code TEXT,
            severity TEXT,
            paper_size TEXT,
            username TEXT,
            details TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE logs ADD COLUMN paper_size TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE logs ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute('CREATE INDEX IF NOT EXISTS idx_printer_ip ON logs(printer_ip)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON logs(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_type ON logs(type)')

    c.execute('''
        CREATE TABLE IF NOT EXISTS printer_counters (
            ip TEXT PRIMARY KEY,
            device_type TEXT,
            print_total INTEGER,
            full_color INTEGER,
            black_white INTEGER,
            a3_total INTEGER,
            a4_total INTEGER,
            alert_codes TEXT,
            updated_at TEXT
        )
    ''')
    # اضافه کردن ستون device_type به جداول قدیمی
    try:
        c.execute("ALTER TABLE printer_counters ADD COLUMN device_type TEXT")
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()


def add_event(ip: str, etype: str, details: dict):
    try:
        from core import store
        conn = sqlite3.connect(DB_PATH, timeout=10.0)  # timeout اضافه شد
        c = conn.cursor()
        timestamp = details.get("timestamp", datetime.now().isoformat())
        message = details.get("message", "")
        pages = details.get("pages")
        color = details.get("color")
        code = details.get("code")
        severity = details.get("severity", "info")
        paper_size = details.get("paper_size")
        username = details.get("username")
        other = {k: v for k, v in details.items() if k not in _LOG_TOP_LEVEL_FIELDS}
        printer_name = None
        with store.printers_lock:
            for p in store.PRINTERS:
                if p["ip"] == ip:
                    printer_name = p["name"]
                    break
        c.execute('''
            INSERT INTO logs (printer_ip, printer_name, timestamp, type, message,
                              pages, color, code, severity, paper_size, username, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ip, printer_name, timestamp, etype, message,
              pages, color, code, severity, paper_size, username,
              json.dumps(other, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Error adding event to DB: {e}")


def _row_to_dict(row) -> dict:
    return {
        "printer_ip":   row[0],
        "printer_name": row[1],
        "timestamp":    row[2],
        "type":         row[3],
        "message":      row[4],
        "pages":        row[5],
        "color":        row[6],
        "code":         row[7],
        "severity":     row[8],
        "paper_size":   row[9],
        "username":     row[10],
        **json.loads(row[11] or "{}"),
    }


def get_log(ip: str, limit: int = 500) -> list:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT printer_ip, printer_name, timestamp, type, message,
                   pages, color, code, severity, paper_size, username, details
            FROM logs WHERE printer_ip = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (ip, limit))
        rows = c.fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        log.error(f"Error reading logs from DB: {e}")
        return []


def get_all_logs(start=None, end=None, limit: int = 1000, ip=None) -> list:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        query = '''
            SELECT printer_ip, printer_name, timestamp, type, message,
                   pages, color, code, severity, paper_size, username, details
            FROM logs
        '''
        params = []
        conditions = []
        if ip:
            conditions.append("printer_ip = ?")
            params.append(ip)
        if start and end:
            conditions.append("timestamp BETWEEN ? AND ?")
            params.extend([start, end])
        elif start:
            conditions.append("timestamp >= ?")
            params.append(start)
        elif end:
            conditions.append("timestamp <= ?")
            params.append(end)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        log.error(f"Error reading all logs: {e}")
        return []


def clear_logs(ip=None) -> int:
    """
    پاک کردن رویدادهای غیر از PRINT، SERVICE و REFILL.
    رویدادهای PRINT، SERVICE و REFILL هرگز پاک نمی‌شوند.
    """
    keep_types = ('PRINT', 'SERVICE', 'REFILL')
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        placeholders = ','.join(['?'] * len(keep_types))
        if ip:
            c.execute(
                f"DELETE FROM logs WHERE printer_ip = ? AND type NOT IN ({placeholders})",
                (ip,) + keep_types
            )
        else:
            c.execute(
                f"DELETE FROM logs WHERE type NOT IN ({placeholders})",
                keep_types
            )
        deleted = c.rowcount
        conn.commit()
        conn.close()
        log.info(f"clear_logs: {deleted} رویداد پاک شد (PRINT, SERVICE, REFILL حفظ شد)")
        return deleted
    except Exception as e:
        log.error(f"Error clearing logs: {e}")
        return 0


def prune_old_print_logs(days=90) -> int:
    """
    حذف خودکار لاگ‌های نوع PRINT که قدیمی‌تر از days روز هستند.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM logs WHERE type='PRINT' AND timestamp < ?", (cutoff,))
        deleted = c.rowcount
        conn.commit()
        conn.close()
        if deleted:
            log.info(f"prune_old_print_logs: {deleted} رکورد PRINT قدیمی پاک شد")
        return deleted
    except Exception as e:
        log.error(f"Error pruning old logs: {e}")
        return 0


def load_printer_counters(ip: str) -> dict:
    """بارگذاری آخرین مقادیر شمارنده از دیتابیس"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT print_total, full_color, black_white, a3_total, a4_total, alert_codes FROM printer_counters WHERE ip = ?", (ip,))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                "print_total": row[0],
                "full_color": row[1],
                "black_white": row[2],
                "a3_total": row[3],
                "a4_total": row[4],
                "alert_codes": json.loads(row[5]) if row[5] else [],
            }
    except Exception as e:
        log.error(f"Error loading counters for {ip}: {e}")
    return None


def save_printer_counters(ip: str, data: dict):
    """ذخیره مقادیر شمارنده در دیتابیس"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO printer_counters
            (ip, print_total, full_color, black_white, a3_total, a4_total, alert_codes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            ip,
            data.get("print_total"),
            data.get("full_color"),
            data.get("black_white"),
            data.get("a3_total"),
            data.get("a4_total"),
            json.dumps(data.get("alert_codes", [])),
            datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Error saving counters for {ip}: {e}")