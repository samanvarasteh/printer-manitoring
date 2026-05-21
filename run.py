#!/usr/bin/env python3
"""
نقطه ورود برنامه Multi-Brand Printer Monitor
اجرا: python run.py   (از داخل پوشه pm2/)
"""

import os
import sys
import socket
import logging
import threading
import signal
import time
import platform

# اطمینان از اینکه ریشه پروژه در sys.path است
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.settings import FLASK_PORT
from core.database import init_db, prune_old_print_logs
from core import store
from core.poller import poll_all, polling_loop
from core.oid.scanner import startup_scan_all, weekly_scan_loop
from web import create_app

# ─── رویداد توقف نرم (graceful shutdown) ───
stop_event = threading.Event()

def signal_handler(sig, frame):
    print("\n🛑 دریافت سیگنال توقف – در حال بستن نرم برنامه...")
    stop_event.set()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ─── تابع پاکسازی خودکار ترمینال (هر 24 ساعت) ───
def clear_terminal_loop():
    """
    هر 24 ساعت یک بار صفحه ترمینال را پاک می‌کند.
    فقط خروجی کنسول را پاک می‌کند و تأثیری بر فایل‌های لاگ ندارد.
    """
    if platform.system() == "Windows":
        clear_cmd = "cls"
    else:
        clear_cmd = "clear"

    while not stop_event.is_set():
        try:
            for _ in range(86400):
                if stop_event.is_set():
                    break
                time.sleep(1)
            if not stop_event.is_set():
                os.system(clear_cmd)
                print("🧹 صفحه ترمینال پاک شد (پاکسازی خودکار هر 24 ساعت)")
        except Exception as e:
            print(f"Terminal clear error: {e}")

def main():
    # راه‌اندازی DB
    init_db()

    # Flask app
    app = create_app()

    # نمایش وضعیت اولیه
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        host_ip = "127.0.0.1"

    print("""
╔══════════════════════════════════════════════════════╗
║    Multi-Brand Printer Monitor  |  پایش لحظه‌ای     ║
╠══════════════════════════════════════════════════════╣""")
    with store.printers_lock:
        for p in store.PRINTERS:
            print(f"  🖨  {p['name']:<14} {p['ip']}  (community: {p['community']})")
    print(f"""╠══════════════════════════════════════════════════════╣
  Local   → http://localhost:{FLASK_PORT}/
  Network → http://{host_ip}:{FLASK_PORT}/
╚══════════════════════════════════════════════════════╝""")

    # Startup OID Scan (background)
    with store.printers_lock:
        printers_copy = list(store.PRINTERS)

    threading.Thread(
        target=lambda: startup_scan_all(printers_copy, force=True),
        daemon=True, name="startup-scan",
    ).start()

    # Polling (حلقه‌های بی‌نهایت با پشتیبانی از stop_event)
    threading.Thread(target=poll_all,     daemon=True, name="poll-init").start()
    threading.Thread(target=polling_loop, daemon=True, name="poll-loop").start()

    # اسکن هفتگی
    threading.Thread(
        target=weekly_scan_loop,
        args=(lambda: list(store.PRINTERS),),
        daemon=True, name="weekly-scan",
    ).start()

    # پاکسازی خودکار ترمینال هر 24 ساعت
    threading.Thread(target=clear_terminal_loop, daemon=True, name="clear-terminal").start()

    # Flask server (debug=False برای محیط production)
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
