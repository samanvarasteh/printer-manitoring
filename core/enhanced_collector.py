# core/enhanced_collector.py

"""
جمع‌آوری پیشرفته داده‌ها با استفاده از روش‌های test_toner.py:
- Walk کامل جدول prtMarkerSuppliesTable
- Walk جدول prtInputTable (سینی‌ها)
- OIDهای جایگزین برای HP, Canon, Brother
- تشخیص خودکار نسخه SNMP
- ذخیره اطلاعات دقیق تونر در دیتابیس
- ثبت لاگ در toner_report.txt
"""

import time
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from config.settings import DB_PATH, VALIDATION_LOG_FILE
from core.snmp.protocol import snmp_get_with_fallback, snmp_get, _SNMP_VERSION_CACHE, is_network_reachable
from core import store
from core.database import add_event

log = logging.getLogger("PrinterMonitor")

# ─── تنظیمات ─────────────────────────────────────────────────────
ENHANCED_TIMEOUT = 3.0   # timeout برای هر OID
ENHANCED_MAX_SUPPLIES = 15  # حداکثر تعداد مواد مصرفی برای walk
SOURCE_STANDARD_PRINTER_MIB = "standard_printer_mib"
SOURCE_ALTERNATE_OID = "alternate_oid"
SOURCE_NO_SENSOR = "no_sensor"
SOURCE_NOT_SUPPORTED = "not_supported"

# OIDهای جایگزین برای HP
HP_ALTERNATE_OIDS = {
    "CE505A": ["1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
               "1.3.6.1.4.1.11.2.3.9.1.1.7.0"],
    "CF283A": ["1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1"],
    "CF287A": ["1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
               "1.3.6.1.4.1.11.2.3.9.1.1.7.0"],
    "W9008MC": ["1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
                "1.3.6.1.4.1.11.2.3.9.1.1.7.0",
                "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.3.1.5.5.1.1"],
    "CC388A": ["1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1"],
}

# OIDهای جایگزین برای Canon
CANON_ALTERNATE_OIDS = [
    "1.3.6.1.4.1.1602.1.2.1.1.1.1.1",
    "1.3.6.1.4.1.1602.1.2.1.1.1.2.1",
    "1.3.6.1.4.1.1602.1.2.1.1.1.3.1",
    "1.3.6.1.4.1.1602.1.2.1.1.1.4.1",
]

# OID تونر Brother
BROTHER_TONER_OID = "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.1.1"
BROTHER_DRUM_OID = "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.1.2"


# ─── توابع کمکی ───────────────────────────────────────────────────
def _log_to_toner_report(content: str):
    """اضافه کردن خط به فایل toner_report.txt"""
    try:
        with open("toner_report.txt", "a", encoding="utf-8") as f:
            f.write(content + "\n")
    except Exception as e:
        log.error(f"خطا در نوشتن toner_report: {e}")


def _log_validation_error(ip: str, error_type: str, details: str):
    """ثبت خطا در فایل validation log"""
    try:
        timestamp = datetime.now().isoformat()
        with open(VALIDATION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] IP: {ip} | Type: enhanced_{error_type}\n")
            f.write(f"  Details: {details}\n\n")
    except Exception as e:
        log.error(f"خطا در نوشتن validation log: {e}")


def detect_snmp_version(ip: str, community: str = "public", timeout: float = 2.0) -> Optional[int]:
    """
    تشخیص نسخه SNMP با تست sysDescr
    بازگشت: 1, 2, یا None
    """
    cache_key = f"{ip}_{community}"
    if cache_key in _SNMP_VERSION_CACHE:
        return _SNMP_VERSION_CACHE[cache_key]

    oid = "1.3.6.1.2.1.1.1.0"
    
    # تست v2c اول
    try:
        result = snmp_get(ip, oid, community, timeout=timeout, version=2)
        if result is not None and str(result).strip():
            _SNMP_VERSION_CACHE[cache_key] = 2
            return 2
    except:
        pass
    
    # تست v1
    try:
        result = snmp_get(ip, oid, community, timeout=timeout, version=1)
        if result is not None and str(result).strip():
            _SNMP_VERSION_CACHE[cache_key] = 1
            return 1
    except:
        pass
    
    _SNMP_VERSION_CACHE[cache_key] = None
    return None


def try_alternative_oids(ip: str, community: str, brand: str, cartridge_model: str = "", 
                         snmp_version: int = None, timeout: float = 3.0) -> Optional[int]:
    """تلاش با OIDهای جایگزین برای دریافت سطح تونر"""
    
    if brand == "hp":
        # اول OIDهای مخصوص مدل کارتریج
        for model_key, oid_list in HP_ALTERNATE_OIDS.items():
            if model_key in cartridge_model:
                for oid in oid_list:
                    val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=timeout)
                    if val is not None:
                        try:
                            int_val = int(val)
                            if 0 <= int_val <= 100:
                                return int_val
                        except:
                            pass
        
        # OIDهای عمومی HP
        general_oids = [
            "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.1.5.5.1.1",
            "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
            "1.3.6.1.4.1.11.2.3.9.1.1.7.0",
        ]
        for oid in general_oids:
            val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=timeout)
            if val is not None:
                try:
                    int_val = int(val)
                    if 0 <= int_val <= 100:
                        return int_val
                except:
                    pass
    
    elif brand == "canon":
        for oid in CANON_ALTERNATE_OIDS:
            val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=timeout)
            if val is not None:
                try:
                    int_val = int(val)
                    if 0 <= int_val <= 100:
                        return int_val
                except:
                    pass
    
    return None


def walk_supplies_table(ip: str, community: str, brand: str = "unknown", 
                        snmp_version: int = None, timeout: float = 2.0) -> List[Dict]:
    """
    Walk کامل روی جدول prtMarkerSuppliesTable
    بازگشت: لیستی از دیکشنری‌های حاوی اطلاعات کارتریج‌ها
    """
    supplies = []
    
    # برای Canon، روش اختصاصی (استفاده از dedicated collector)
    if brand == "canon":
        try:
            from core.collectors.canon import collect_canon
            import time as time_module
            # Use only the toner data from Canon collector
            canon_result = collect_canon({"ip": ip, "name": "", "community": community, "brand": "canon"}, 
                                        "", community, time_module.time())
            if canon_result and canon_result.get("toners"):
                # Convert Canon collector format to supplies format
                for color_key, toner_info in canon_result["toners"].items():
                    supplies.append({
                        "index": len(supplies) + 1,
                        "name": toner_info.get("name", color_key),
                        "model": toner_info.get("name", color_key),
                        "type": 3,  # toner type
                        "type_name": "toner",
                        "unit": "percent",
                        "max": toner_info.get("max", 100),
                        "remaining": toner_info.get("remaining", -2),
                        "percent": toner_info.get("level"),
                        "status": toner_info.get("status", "unknown"),
                        "source": toner_info.get("source", SOURCE_STANDARD_PRINTER_MIB),
                    })
                if supplies:
                    return supplies
        except Exception as e:
            log.debug(f"  Canon {ip}: Failed to use dedicated collector: {e}")
            # Fall through to standard method
    
    # برای Brother، روش اختصاصی
    if brand == "brother":
        toner_level = snmp_get_with_fallback(ip, BROTHER_TONER_OID, community, 
                                              version=snmp_version, timeout=timeout)
        if toner_level is not None:
            try:
                level = int(toner_level)
                if 0 <= level <= 100:
                    supplies.append({
                        "index": 1,
                        "name": "Black Toner",
                        "model": "Toner Cartridge",
                        "type": 3,
                        "type_name": "toner",
                        "unit": "percent",
                        "max": 100,
                        "remaining": level,
                        "percent": level,
                        "status": "critical" if level <= 10 else "low" if level <= 25 else "ok",
                        "source": SOURCE_ALTERNATE_OID,
                    })
            except:
                pass
        
        # درام هم بخوانیم
        drum_level = snmp_get_with_fallback(ip, BROTHER_DRUM_OID, community,
                                             version=snmp_version, timeout=timeout)
        if drum_level is not None:
            try:
                level = int(drum_level)
                if 0 <= level <= 100:
                    supplies.append({
                        "index": 2,
                        "name": "Drum Unit",
                        "model": "Drum Unit",
                        "type": 7,  # OPC type
                        "type_name": "opc",
                        "unit": "percent",
                        "max": 100,
                        "remaining": level,
                        "percent": level,
                        "status": "critical" if level <= 10 else "low" if level <= 25 else "ok",
                        "source": SOURCE_ALTERNATE_OID,
                    })
            except:
                pass
        
        return supplies
    
    # روش استاندارد برای سایر برندها
    for idx in range(1, ENHANCED_MAX_SUPPLIES + 1):
        try:
            name_oid = f"1.3.6.1.2.1.43.11.1.1.6.1.{idx}"
            name = snmp_get_with_fallback(ip, name_oid, community, 
                                          version=snmp_version, timeout=timeout)
            
            if name is None:
                if idx >= 5 and brand in ["canon", "hp", "brother"]:
                    break
                continue
            
            name_str = str(name).strip()
            
            type_oid = f"1.3.6.1.2.1.43.11.1.1.5.1.{idx}"
            stype = snmp_get_with_fallback(ip, type_oid, community,
                                           version=snmp_version, timeout=timeout)
            
            max_oid = f"1.3.6.1.2.1.43.11.1.1.8.1.{idx}"
            max_val = snmp_get_with_fallback(ip, max_oid, community,
                                             version=snmp_version, timeout=timeout)
            
            rem_oid = f"1.3.6.1.2.1.43.11.1.1.9.1.{idx}"
            rem_val = snmp_get_with_fallback(ip, rem_oid, community,
                                             version=snmp_version, timeout=timeout)
            
            stype_int = 0
            if stype is not None and str(stype).lstrip('-').isdigit():
                stype_int = int(stype)
            
            type_names = {
                1: "other", 2: "unknown", 3: "toner", 4: "wasteToner",
                5: "ink", 6: "wasteInk", 7: "OPC", 8: "developer",
                9: "fuser", 10: "cleaner", 11: "transfer", 12: "staples",
                21: "cartridge"
            }
            type_name = type_names.get(stype_int, f"type_{stype_int}")
            
            percent = None
            max_int = -2
            rem_int = -2
            source = SOURCE_STANDARD_PRINTER_MIB
            
            try:
                if max_val is not None and str(max_val).lstrip('-').isdigit():
                    max_int = int(max_val)
                if rem_val is not None and str(rem_val).lstrip('-').isdigit():
                    rem_int = int(rem_val)
                
                if max_int > 0 and rem_int >= 0:
                    percent = round(rem_int / max_int * 100)
                elif rem_int >= 0 and rem_int <= 100:
                    percent = rem_int
                    max_int = 100
            except:
                pass
            
            # اگر درصد نداریم یا برای HP بدون max موثق، OIDهای جایگزین را امتحان کن
            # برای HP: اگر max_val None بود، ترجیح دهیم از OID جایگزین استفاده کنیم
            if brand in ["hp", "canon"]:
                should_try_alt = (percent is None) or (brand == "hp" and max_val is None)
                if should_try_alt:
                    alt_percent = try_alternative_oids(ip, community, brand, name_str, snmp_version, timeout)
                    if alt_percent is not None:
                        # برای Canon: اگر مقدار 0 است و نام کارتریج موجود است، احتمالاً خطا است
                        # در این صورت، N/A نشان بده نه empty
                        if brand == "canon" and alt_percent == 0 and name_str and name_str != "Unknown":
                            log.debug(f"  Canon {ip}: Alternative OID returned 0% for {name_str}, marking as no_sensor")
                            # علامت‌گذاری به عنوان بدون سنسور، نه خالی
                            # نمی‌توانیم status را اینجا تغییر دهیم، بنابراین percent را None نگه می‌داریم
                            percent = None
                            source = SOURCE_NO_SENSOR
                        else:
                            # برای HP و Canon: استفاده از مقدار جایگزین
                            # اگر max_val None بود، این از OID جایگزین است
                            if brand == "hp" and max_val is None:
                                log.debug(f"  HP {ip}: Using alternative OID {alt_percent}% (standard OID had no max) for {name_str}")
                            elif percent is None:
                                log.debug(f"  {brand.upper()} {ip}: Using alternative OID {alt_percent}% for {name_str}")
                            percent = alt_percent
                            rem_int = alt_percent
                            max_int = 100
                            source = SOURCE_ALTERNATE_OID
            
            # وضعیت
            status = "N/A"
            if percent is not None:
                if percent == 0: status = "empty"
                elif percent <= 10: status = "critical"
                elif percent <= 25: status = "low"
                else: status = "ok"
            elif rem_int == -2:
                # بررسی: آیا OID بدون سنسور است یا اصلاً موجود نیست؟
                # برای HP و Canon: اگر نام کارتریج موجود است، شاید سنسور باشد
                # برای branded devices که نام دارند اما rem -2، بیشتر "not_reported" است
                status = "no_sensor" if name_str and name_str != "Unknown" else "not_supported"
            elif rem_int == -3:
                status = "not_supported"
                source = SOURCE_NOT_SUPPORTED
            elif rem_int > 0 and max_int == -2:
                if rem_int <= 100:
                    percent = rem_int
                    status = "ok" if percent > 25 else "low" if percent > 10 else "critical"
            
            # فیلتر Unknownهای تکراری
            if name_str.startswith("Unknown"):
                unknown_count = sum(1 for s in supplies if s["name"].startswith("Unknown"))
                if unknown_count > 2:
                    continue
            
            supplies.append({
                "index": idx,
                "name": name_str,
                "model": name_str,
                "type": stype_int,
                "type_name": type_name,
                "unit": "unknown",
                "max": max_int if max_int != -2 else (100 if percent is not None else "N/A"),
                "remaining": rem_int if rem_int >= 0 else ("N/A" if rem_int == -2 else "unsupported"),
                "percent": percent,
                "status": status,
                "source": source,
            })
            
        except Exception as e:
            if idx <= 3:
                _log_validation_error(ip, "walk_supplies_exception", f"idx={idx}: {e}")
    
    return supplies


def walk_input_trays(ip: str, community: str, snmp_version: int = None, timeout: float = 2.0) -> List[Dict]:
    """Walk روی جدول prtInputTable برای سینی‌ها"""
    trays = []
    
    for idx in range(1, 8):
        try:
            name_oid = f"1.3.6.1.2.1.43.8.2.1.13.1.{idx}"
            name = snmp_get_with_fallback(ip, name_oid, community, 
                                          version=snmp_version, timeout=timeout)
            
            cap_oid = f"1.3.6.1.2.1.43.8.2.1.9.1.{idx}"
            cap_val = snmp_get_with_fallback(ip, cap_oid, community,
                                             version=snmp_version, timeout=timeout)
            
            level_oid = f"1.3.6.1.2.1.43.8.2.1.10.1.{idx}"
            level_val = snmp_get_with_fallback(ip, level_oid, community,
                                               version=snmp_version, timeout=timeout)
            
            if name is None and cap_val is None and level_val is None:
                continue
            
            name_str = str(name).strip() if name else f"Tray {idx}"
            
            try:
                cap_int = int(cap_val) if cap_val is not None and str(cap_val).lstrip('-').isdigit() else 0
            except:
                cap_int = 0
            
            try:
                if level_val is not None and str(level_val).lstrip('-').isdigit():
                    level_int = int(level_val)
                else:
                    level_int = -2
            except:
                level_int = -2
            
            fill_percent = None
            status = "unknown"
            
            if level_int == -2:
                status = "no_sensor"
            elif level_int == -3:
                status = "not_supported"
            elif cap_int > 0 and level_int >= 0:
                fill_percent = round(level_int / cap_int * 100)
                if level_int == 0:
                    status = "empty"
                elif fill_percent <= 25:
                    status = "low"
                elif fill_percent <= 75:
                    status = "medium"
                else:
                    status = "ok"
            elif level_int >= 0 and level_int <= 100 and cap_int == 0:
                fill_percent = level_int
                status = "ok" if level_int > 25 else "low" if level_int > 10 else "critical"
            
            trays.append({
                "index": idx,
                "name": name_str,
                "capacity": cap_int,
                "level": level_int if level_int >= 0 else ("N/A" if level_int == -2 else "unsupported"),
                "fill_percent": fill_percent,
                "status": status,
            })
            
        except Exception as e:
            continue
    
    return trays


def detect_printer_type_from_supplies(supplies: List[Dict]) -> str:
    """تشخیص نوع پرینتر از روی مواد مصرفی"""
    toners = [s for s in supplies if s.get("type") == 3 or s.get("type_name") == "toner"]
    if not toners:
        toners = supplies
    
    color_keywords = ["cyan", "magenta", "yellow", "سیان", "مژنتا", "color", "colour"]
    for t in toners:
        name_lower = t.get("name", "").lower()
        for c in color_keywords:
            if c in name_lower:
                return "color"
    
    return "mono"


def collect_enhanced(printer: dict, save_to_db: bool = True) -> dict:
    """
    جمع‌آوری پیشرفته داده‌ها با استفاده از روش test_toner.py
    """
    ip = printer["ip"]
    name = printer["name"]
    nickname = printer.get("nickname", "")
    community = printer.get("community", "public")
    brand = printer.get("brand", "").lower()
    start_time = time.time()
    
    log.info(f"[ENHANCED] Polling {name} ({ip})")
    _log_to_toner_report(f"\n{'='*80}")
    _log_to_toner_report(f"🖨  {name} ({ip}) | زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # ─── تشخیص SNMP ───────────────────────────────────────────────
    snmp_version = detect_snmp_version(ip, community, timeout=2.0)
    if snmp_version is None:
        elapsed = int((time.time() - start_time) * 1000)
        _log_to_toner_report(f"   ❌ بدون پاسخ SNMP")
        
        # بررسی دسترسی‌پذیری شبکه
        network_ok = is_network_reachable(ip, port=161, timeout=2.0)
        online_status = network_ok  # اگر شبکه در دسترس است، دستگاه آنلاین است
        
        if network_ok:
            log.info(f"SNMP failed but network reachable for {ip}: returning minimal data")
            _log_to_toner_report(f"   ⚠️  SNMP خراب ولی شبکه در دسترس")
        
        return {
            "ip": ip, "name": name, "nickname": nickname, "brand": brand,
            "online": online_status, "last_poll": datetime.now().isoformat(), "poll_ms": elapsed,
            "error": "No SNMP response" + (" (network reachable)" if network_ok else " (network unreachable)"),
        }
    
    # ─── اطلاعات پایه ────────────────────────────────────────────
    sys_desc = snmp_get_with_fallback(ip, "1.3.6.1.2.1.1.1.0", community, version=snmp_version, timeout=2.0)
    sys_desc_str = str(sys_desc) if sys_desc else ""
    
    # تشخیص سنسور
    if "ECS100G" in sys_desc_str.upper():
        from core.collectors.sensor import collect_sensor
        result = collect_sensor(ip, name, community, start_time)
        result["nickname"] = nickname
        return result
    
    # ─── خواندن اطلاعات پیشرفته ───────────────────────────────────
    supplies = walk_supplies_table(ip, community, brand, snmp_version, timeout=ENHANCED_TIMEOUT)
    trays = walk_input_trays(ip, community, snmp_version, timeout=ENHANCED_TIMEOUT)
    
    # ─── شمارنده‌های اصلی ─────────────────────────────────────────
    # تلاش برای خواندن total از OIDهای مختلف
    total = 0
    total_oids = [
        "1.3.6.1.2.1.43.10.2.1.4.1.1",  # standard
        "1.3.6.1.4.1.1129.2.3.50.1.3.21.6.1.2.1.4",  # Toshiba
    ]
    for oid in total_oids:
        val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=2.0)
        if val is not None:
            try:
                total = int(val)
                if total > 0:
                    break
            except:
                pass
    
    # ─── تشخیص رنگ ───────────────────────────────────────────────
    device_type = detect_printer_type_from_supplies(supplies) if supplies else "mono"
    if device_type == "color":
        # تلاش برای خواندن شمارنده رنگی
        color = 0
        color_oids = [
            "1.3.6.1.2.1.43.10.2.1.4.1.2",  # standard color
            "1.3.6.1.4.1.1129.2.3.50.1.3.21.6.1.2.1.1",  # Toshiba color
        ]
        for oid in color_oids:
            val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=2.0)
            if val is not None:
                try:
                    color = int(val)
                    if color > 0:
                        break
                except:
                    pass
        bw = max(0, total - color) if total > 0 else 0
    else:
        color = None
        bw = total
    
    # ─── مدل و سریال ─────────────────────────────────────────────
    model = "Unknown"
    serial = "N/A"
    
    # تلاش برای خواندن مدل از OIDهای مختلف
    model_oids = [
        "1.3.6.1.2.1.43.5.1.1.16.1",  # standard
        "1.3.6.1.4.1.1129.2.3.50.1.2.3.1.3.1.1",  # Toshiba
        "1.3.6.1.4.1.11.2.3.9.1.1.3.1.1.1.1.2.0",  # HP
        "1.3.6.1.4.1.1602.1.1.1.1.0",  # Canon
    ]
    for oid in model_oids:
        val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=2.0)
        if val and str(val).strip() not in ("", "N/A", "None"):
            model = str(val).strip()[:100]
            break
    
    serial_oids = [
        "1.3.6.1.2.1.43.5.1.1.17.1",
        "1.3.6.1.4.1.1129.2.3.50.1.2.4.1.8.1.1",
        "1.3.6.1.4.1.11.2.3.9.1.1.3.1.1.1.1.3.0",
        "1.3.6.1.4.1.1602.1.2.1.4.0",
    ]
    for oid in serial_oids:
        val = snmp_get_with_fallback(ip, oid, community, version=snmp_version, timeout=2.0)
        if val and str(val).strip() not in ("", "N/A", "None"):
            serial = str(val).strip()[:100]
            break
    
    # ─── تبدیل تونرها به فرمت toners ─────────────────────────────
    toners = {}
    for s in supplies:
        if s["type_name"] in ("toner", "cartridge"):
            color_key = None
            name_lower = s["name"].lower()
            if "black" in name_lower or "bk" in name_lower:
                color_key = "black"
            elif "cyan" in name_lower or "c" in name_lower.split():
                color_key = "cyan"
            elif "magenta" in name_lower or "m" in name_lower.split():
                color_key = "magenta"
            elif "yellow" in name_lower or "y" in name_lower.split():
                color_key = "yellow"
            else:
                color_key = "black"  # fallback
            
            toners[color_key] = {
                "level": s["percent"],
                "status": s["status"] if s["status"] != "N/A" else "unknown",
                "name": s["name"],
                "remaining": s["remaining"],
                "max": s["max"],
                "source": s.get("source", SOURCE_STANDARD_PRINTER_MIB),
            }
    
    # اگر تونری پیدا نشد، یک تونر مشکی پیش‌فرض
    if not toners:
        toners["black"] = {"level": None, "status": "unknown", "name": "Toner", "remaining": -1, "max": -1, "source": SOURCE_NOT_SUPPORTED}
    
    # ─── هشدارها ─────────────────────────────────────────────────
    alerts = []
    for s in supplies:
        if s["status"] in ("critical", "empty", "low") and s["type_name"] in ("toner", "cartridge"):
            alerts.append({
                "message": f"{s['name']}: {s['status']} ({s['percent']}%)",
                "code": s["index"]
            })
    
    # ─── uptime ──────────────────────────────────────────────────
    ut_raw = snmp_get_with_fallback(ip, "1.3.6.1.2.1.1.3.0", community, version=snmp_version, timeout=2.0)
    ut = int(ut_raw) if ut_raw else 0
    us = ut // 100
    uptime_str = f"{us//86400}d {(us%86400)//3600:02d}:{(us%3600)//60:02d}" if ut else "N/A"
    
    elapsed = int((time.time() - start_time) * 1000)
    
    # ─── ثبت در toner_report.txt ─────────────────────────────────
    _log_to_toner_report(f"   SNMP v{snmp_version} | مدل: {model} | نوع: {device_type}")
    _log_to_toner_report(f"   کل صفحات: {total:,} | رنگی: {color if color else 0:,} | سیاه‌سفید: {bw:,}")
    for color_key, t in toners.items():
        pct_str = f"{t['level']}%" if t['level'] is not None else "N/A"
        status_icon = {"ok": "✅", "low": "🟡", "critical": "🟠", "empty": "🔴"}.get(t["status"], "❓")
        _log_to_toner_report(f"   {color_key}: {pct_str} {status_icon} | source={t.get('source', SOURCE_STANDARD_PRINTER_MIB)}")
    _log_to_toner_report(f"   زمان پاسخ: {elapsed}ms")
    
    # ─── ذخیره در دیتابیس (printer_counters) ────────────────────
    if save_to_db:
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH, timeout=10.0)
            c = conn.cursor()
            
            # ذخیره مقادیر قبلی در جدول printer_counters
            c.execute('''
                INSERT OR REPLACE INTO printer_counters 
                (ip, print_total, full_color, black_white, updated_at, device_type)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (ip, total, color if color else 0, bw, datetime.now().isoformat(), device_type))
            
            # ذخیره اطلاعات تونرها در فیلد alert_codes (JSON)
            # این کار اطلاعات تونر را هم نگهداری می‌کند
            import json
            toner_data = {
                "toners": {
                    k: {"level": v["level"], "status": v["status"], "name": v["name"], "source": v.get("source", SOURCE_STANDARD_PRINTER_MIB)}
                    for k, v in toners.items()
                },
                "supplies": [
                    {"name": s["name"], "percent": s["percent"], "status": s["status"], "type": s["type_name"], "source": s.get("source", SOURCE_STANDARD_PRINTER_MIB)}
                    for s in supplies if s["percent"] is not None
                ]
            }
            c.execute('''
                UPDATE printer_counters SET alert_codes = ? WHERE ip = ?
            ''', (json.dumps(toner_data, ensure_ascii=False), ip))
            
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"خطا در ذخیره enhanced data در دیتابیس: {e}")
    
    # ─── ثبت رویداد PRINT ───────────────────────────────────────
    prev = store._prev.get(ip) or {}
    prev_total = prev.get("print_total")
    
    if prev_total is not None and total > prev_total:
        delta = total - prev_total
        if 0 < delta <= 1000:  # دلتای منطقی
            add_event(ip, "PRINT", {
                "message": f"{delta} صفحه چاپ شد",
                "pages": delta,
                "color": "رنگی" if color and color > prev.get("full_color", 0) else "سیاه‌سفید",
                "severity": "info"
            })
    
    # ذخیره مقادیر جدید در prev
    store._prev.set(ip, {
        "print_total": total,
        "full_color": color if color else 0,
        "black_white": bw,
        "alert_codes": [a["code"] for a in alerts],
    })
    
    return {
        "ip": ip, "name": name, "nickname": nickname, "brand": brand,
        "device_type": device_type,
        "online": True,
        "last_poll": datetime.now().isoformat(),
        "poll_ms": elapsed,
        "device": {
            "model": model,
            "serial": serial,
            "firmware": "N/A",
            "uptime_str": uptime_str,
        },
        "counters": {
            "total": total,
            "full_color": color if color else None,
            "black_white": bw,
            "printer": total,
            "copy": None,
            "fax": None,
        },
        "paper_sizes": {},
        "trays": trays,
        "toners": toners,
        "alerts": alerts,
    }