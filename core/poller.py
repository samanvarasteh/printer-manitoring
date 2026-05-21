# core/poller.py

"""
چرخه polling:
- collect: جمع‌آوری داده از یک پرینتر با routing به enhanced_collector
- poll_all: polling موازی همه پرینترها (با lock thread-safe)
- polling_loop: حلقه بی‌نهایت با POLL_INTERVAL
"""

import time
import threading
import logging
from datetime import datetime

from config.settings import POLL_INTERVAL
from core import store
from core.database import add_event
from core.snmp.protocol import snmp_get_with_fallback
from core.snmp.oid_map import OIDS
from core.collectors.base import si, detect_brand

# enhanced_collector — کالکتور اصلی برای همه پرینترها
from core.enhanced_collector import collect_enhanced

# کالکتور سنسور (ECS100G) — مجزا نگه داشته می‌شود
from core.collectors.sensor import collect_sensor

log = logging.getLogger("PrinterMonitor")

# قفل برای جلوگیری از اجرای هم‌زمان poll_all
_polling_lock = threading.Lock()

# قفل برای processed_ips (رفع race condition)
_processed_lock = threading.Lock()


def collect(printer: dict) -> dict:
    """
    جمع‌آوری داده از یک پرینتر.
    از enhanced_collector برای همه دستگاه‌ها (به جز سنسور) استفاده می‌کند.
    داده‌های دقیق تونر (walk prtMarkerSuppliesTable) و سینی‌ها از enhanced_collector
    مستقیماً در نتیجه گنجانده می‌شوند.
    """
    ip = printer["ip"]
    name = printer["name"]
    nickname = printer.get("nickname", "")
    community = printer.get("community", "public")
    brand = printer.get("brand", "").lower()
    device_type = printer.get("device_type", "unknown")

    log.info(f"Polling {name} ({ip}) [{brand or 'auto'}] - enhanced collector")
    start = time.time()

    # تست اولیه آنلاین بودن (دو OID برای اطمینان)
    test = snmp_get_with_fallback(ip, "1.3.6.1.2.1.1.1.0", community, timeout=2.0)
    if test is None:
        test = snmp_get_with_fallback(ip, OIDS.get("uptime", "1.3.6.1.2.1.1.3.0"), community, timeout=2.0)
    online = test is not None

    with store.data_lock:
        was_online = store.printer_data.get(ip, {}).get("online", None)

    if not online:
        if was_online:
            add_event(ip, "STATUS", {"message": "دستگاه آفلاین شد", "severity": "error"})
        elapsed = int((time.time() - start) * 1000)
        return {
            "ip": ip, "name": name, "nickname": nickname, "brand": brand, "device_type": device_type,
            "online": False,
            "last_poll": datetime.now().isoformat(),
            "poll_ms": elapsed,
            "error": "Device unreachable",
        }

    if was_online is False:
        add_event(ip, "STATUS", {"message": "دستگاه آنلاین شد", "severity": "success"})

    # سنسورها با کالکتور مخصوص خود
    if brand == "sensor":
        result = collect_sensor(ip, name, community, start)
        result["nickname"] = nickname
        result["device_type"] = "sensor"
        return result

    # تشخیص خودکار برند (اگر هنوز مشخص نشده)
    if not brand or brand == "unknown":
        brand = detect_brand(ip, community)
        log.info(f"  → برند شناسایی شد: {brand}")
        with store.printers_lock:
            for p in store.PRINTERS:
                if p["ip"] == ip:
                    p["brand"] = brand
                    store.save_printers(store.PRINTERS)
                    break

    # جمع‌آوری کامل با enhanced_collector
    # (شامل: walk تونر، walk سینی، شمارنده‌های تفکیکی، ثبت در toner_report.txt)
    try:
        result = collect_enhanced(printer)
        result["nickname"] = nickname
        result["device_type"] = result.get("device_type", device_type)
        log.debug(
            f"  ✓ {name} enhanced: toners={len(result.get('toners', {}))}, "
            f"trays={len(result.get('trays', []))}, "
            f"total={result.get('counters', {}).get('total', '?')}, "
            f"{result.get('poll_ms', '?')}ms"
        )
        return result
    except Exception as e:
        log.error(f"Enhanced collector failed for {ip}: {e}", exc_info=True)
        elapsed = int((time.time() - start) * 1000)
        return {
            "ip": ip, "name": name, "nickname": nickname, "brand": brand,
            "online": True,
            "last_poll": datetime.now().isoformat(),
            "poll_ms": elapsed,
            "device": {"model": "Unknown", "serial": "N/A", "firmware": "N/A", "uptime_str": "N/A"},
            "counters": {"total": 0, "full_color": None, "black_white": 0},
            "paper_sizes": {}, "trays": [], "toners": {}, "alerts": [],
            "error": str(e),
        }


def poll_one(p: dict):
    """Poll یک پرینتر واحد (برای poll دستی از داشبورد)"""
    data = collect(p)
    with store.data_lock:
        store.printer_data[p["ip"]] = data


def poll_all():
    """
    اجرای poll موازی برای همه پرینترها.
    از _polling_lock برای جلوگیری از اجرای هم‌زمان استفاده می‌کند.
    از _processed_lock برای thread-safe بودن processed_ips استفاده می‌کند.
    """
    with _polling_lock:
        with store.printers_lock:
            current = list(store.PRINTERS)

        log.info(f"🔄 Starting poll cycle for {len(current)} devices (interval={POLL_INTERVAL}s)")
        results = {}
        processed_ips = set()

        def _poll(p):
            ip = p["ip"]
            # بررسی thread-safe قبل از poll
            with _processed_lock:
                if ip in processed_ips:
                    return
                processed_ips.add(ip)
            results[ip] = collect(p)

        threads = [threading.Thread(target=_poll, args=(p,), daemon=True) for p in current]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)  # timeout کافی برای enhanced_collector (walk SNMP)

        with store.data_lock:
            store.printer_data.update(results)
            store.poll_stats["count"] += 1
            store.poll_stats["last"] = datetime.now().isoformat()
            store.poll_stats["errors"] = sum(1 for d in results.values() if not d.get("online"))

        log.info(
            f"✅ Poll cycle done: {len(results)} devices, "
            f"{store.poll_stats['errors']} offline, "
            f"next poll in {POLL_INTERVAL}s"
        )


def polling_loop():
    """حلقه بی‌نهایت polling با POLL_INTERVAL"""
    while True:
        try:
            poll_all()
        except Exception as e:
            log.error(f"Error in polling loop: {e}")
        time.sleep(POLL_INTERVAL)
