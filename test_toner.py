#!/usr/bin/env python3
"""
اسکریپت مستقل برای تست و نمایش دقیق اطلاعات تونر، کارتریج، ظرفیت و سینی‌ها
پشتیبانی از SNMP v1 و v2c با تشخیص خودکار
خروجی: فایل toner_report.txt
بخش پایانی: خلاصه مدل کارتریج و ظرفیت عددی
"""

import json
import sys
import os
import time
import socket
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.snmp.protocol import snmp_get_with_fallback as original_snmp_get_with_fallback
from core.collectors.toshiba import _scrape_toshiba_toners, _walk_toner_remaining

# ─── تنظیمات ─────────────────────────────────────────────────────
OVERALL_TIMEOUT = 10
SKIP_OFFLINE = True

# OID های جایگزین برای Brother
BROTHER_TONER_OIDS = {
    "black": "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.1.1",
    "drum": "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.1.2",
}

# OID های جایگزین برای HP (طبق مدل کارتریج)
HP_ALTERNATE_OIDS = {
    "CE505A": [
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
        "1.3.6.1.4.1.11.2.3.9.1.1.7.0",
    ],
    "CF283A": [
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
    ],
    "CF287A": [
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
    ],
    "W9008MC": [
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
        "1.3.6.1.4.1.11.2.3.9.1.1.7.0",
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.3.1.5.5.1.1",
    ],
    "CC388A": [
        "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",
    ],
}

# OIDهای جایگزین برای Canon (توسعه یافته)
CANON_ALTERNATE_OIDS = [
    "1.3.6.1.4.1.1602.1.2.1.1.1.1.1",
    "1.3.6.1.4.1.1602.1.2.1.1.1.2.1",
    "1.3.6.1.4.1.1602.1.2.1.1.1.3.1",
    "1.3.6.1.4.1.1602.1.2.1.1.1.4.1",
]

# ذخیره نسخه SNMP تشخیص داده شده برای هر IP
SNMP_VERSION_CACHE = {}


def snmp_get_with_fallback(ip, oid, community="public", version=None, timeout=2.0):
    """
    تلاش با SNMP نسخه مشخص شده، یا fallback خودکار بین v2c و v1
    """
    from core.snmp.protocol import snmp_get
    
    # اگر نسخه مشخص شده، فقط همان را امتحان کن
    if version is not None:
        try:
            return snmp_get(ip, oid, community, version=version, timeout=timeout)
        except Exception as e:
            return None
    
    # ابتدا v2c را امتحان کن
    try:
        result = snmp_get(ip, oid, community, version=2, timeout=timeout)
        if result is not None:
            return result
    except:
        pass
    
    # سپس v1 را امتحان کن
    try:
        return snmp_get(ip, oid, community, version=1, timeout=timeout)
    except:
        return None


def detect_snmp_version(ip, community="public", timeout=2.0):
    """
    تشخیص اینکه پرینتر با کدام نسخه SNMP جواب می‌دهد
    نتیجه در cache ذخیره می‌شود
    """
    from core.snmp.protocol import snmp_get
    
    # چک کردن cache
    cache_key = f"{ip}_{community}"
    if cache_key in SNMP_VERSION_CACHE:
        return SNMP_VERSION_CACHE[cache_key]
    
    oid = "1.3.6.1.2.1.1.1.0"  # sysDescr
    
    # تست v2c
    try:
        result = snmp_get(ip, oid, community, version=2, timeout=timeout)
        if result is not None and str(result).strip():
            SNMP_VERSION_CACHE[cache_key] = 2
            return 2
    except:
        pass
    
    # تست v1
    try:
        result = snmp_get(ip, oid, community, version=1, timeout=timeout)
        if result is not None and str(result).strip():
            SNMP_VERSION_CACHE[cache_key] = 1
            return 1
    except:
        pass
    
    SNMP_VERSION_CACHE[cache_key] = None
    return None  # آفلاین


def is_printer_online(ip, community, timeout=2.0):
    """بررسی آنلاین بودن با هر دو نسخه SNMP"""
    oid = "1.3.6.1.2.1.1.1.0"  # sysDescr
    from core.snmp.protocol import snmp_get
    
    # تست v2c
    try:
        result = snmp_get(ip, oid, community, version=2, timeout=timeout)
        if result is not None and str(result).strip():
            return True
    except:
        pass
    
    # تست v1
    try:
        result = snmp_get(ip, oid, community, version=1, timeout=timeout)
        if result is not None and str(result).strip():
            return True
    except:
        pass
    
    return False


def try_alternative_oids(ip, community, brand, cartridge_model="", snmp_version=None, timeout=3.0):
    """تلاش با OID های جایگزین برای دریافت سطح تونر"""
    
    # برای HP
    if brand == "hp":
        # اول OID های مخصوص مدل کارتریج را امتحان کن
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
        
        # OID عمومی HP
        general_oids = [
            "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.1.5.5.1.1",  # HP Toner Level
            "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.4.1.2.1.5.5.1.1",  # HP Alternative
            "1.3.6.1.4.1.11.2.3.9.1.1.7.0",  # HP Toner Remaining
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
    
    # برای Canon
    if brand == "canon":
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


def try_http_scrape(ip, brand, timeout=5.0):
    """تلاش برای دریافت اطلاعات از طریق HTTP (وب اینترفیس)"""
    try:
        import requests
        
        # برای HP
        if brand == "hp":
            urls = [
                f"http://{ip}/hp/device/this.LCDispatcher?nav=hp.Print?SC=1",
                f"http://{ip}/DevMgmt/ProductStatus.html",
            ]
            for url in urls:
                try:
                    response = requests.get(url, timeout=timeout, auth=('admin', ''))
                    if response.status_code == 200:
                        text = response.text.lower()
                        # جستجوی درصد تونر
                        import re
                        patterns = [
                            r'black.*?(\d+)%',
                            r'toner.*?(\d+)%',
                            r'remaining.*?(\d+)%',
                        ]
                        for pattern in patterns:
                            match = re.search(pattern, text)
                            if match:
                                return int(match.group(1))
                except:
                    pass
        
        # برای Canon
        if brand == "canon":
            urls = [
                f"http://{ip}/?lang=EN",
                f"http://{ip}/status.html",
            ]
            for url in urls:
                try:
                    response = requests.get(url, timeout=timeout)
                    if response.status_code == 200:
                        text = response.text.lower()
                        import re
                        patterns = [
                            r'cartridge.*?(\d+)%',
                            r'toner.*?(\d+)%',
                            r'remaining.*?(\d+)%',
                        ]
                        for pattern in patterns:
                            match = re.search(pattern, text)
                            if match:
                                return int(match.group(1))
                except:
                    pass
                    
    except ImportError:
        pass
    
    return None


def walk_supplies_table(ip, community, brand="unknown", snmp_version=None, timeout=2.0):
    """Walk کامل روی جدول prtMarkerSuppliesTable"""
    raw_supplies = []
    
    # برای برادر، روش اختصاصی
    if brand == "brother":
        brother_oid = "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.1.1"
        brother_level = snmp_get_with_fallback(ip, brother_oid, community, version=snmp_version, timeout=timeout)
        if brother_level is not None:
            try:
                level = int(brother_level)
                if 0 <= level <= 100:
                    return [{
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
                    }]
            except:
                pass
    
    # روش استاندارد
    for idx in range(1, 15):
        try:
            name_oid = f"1.3.6.1.2.1.43.11.1.1.6.1.{idx}"
            name = snmp_get_with_fallback(ip, name_oid, community, version=snmp_version, timeout=timeout)
            
            if name is None:
                if idx >= 5 and brand in ["canon", "hp", "brother"]:
                    break
                continue
            
            name_str = str(name).strip()
            
            # فیلتر Unknown
            if name_str.startswith("Unknown"):
                unknown_count = sum(1 for s in raw_supplies if s["name"].startswith("Unknown"))
                if unknown_count > 2:
                    continue
            
            type_oid = f"1.3.6.1.2.1.43.11.1.1.5.1.{idx}"
            stype = snmp_get_with_fallback(ip, type_oid, community, version=snmp_version, timeout=timeout)
            
            max_oid = f"1.3.6.1.2.1.43.11.1.1.8.1.{idx}"
            max_val = snmp_get_with_fallback(ip, max_oid, community, version=snmp_version, timeout=timeout)
            
            rem_oid = f"1.3.6.1.2.1.43.11.1.1.9.1.{idx}"
            rem_val = snmp_get_with_fallback(ip, rem_oid, community, version=snmp_version, timeout=timeout)
            
            stype_int = int(stype) if stype and str(stype).lstrip('-').isdigit() else 0
            
            type_names = {1: "other", 2: "unknown", 3: "toner", 4: "wasteToner", 
                         5: "ink", 6: "wasteInk", 7: "OPC", 8: "developer",
                         9: "fuser", 10: "cleaner", 11: "transfer", 12: "staples",
                         21: "cartridge"}
            type_name = type_names.get(stype_int, f"type_{stype_int}")
            
            percent = None
            max_int = -2
            rem_int = -2
            
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
                elif rem_int == -2 or rem_int == -3:
                    # بدون سنسور یا پشتیبانی نمی‌شود
                    pass
                elif rem_int > 100:
                    # ممکن است مقدار بر حسب میلی‌گرم یا صفحات باشد
                    if max_int > 0 and max_int < 1000:
                        percent = round(rem_int / max_int * 100)
            except:
                pass
            
            # اگه درصد نداریم، OID های جایگزین را امتحان کن
            if percent is None and brand in ["hp", "canon"]:
                alt_percent = try_alternative_oids(ip, community, brand, name_str, snmp_version, timeout)
                if alt_percent is not None:
                    percent = alt_percent
                    rem_int = alt_percent
                    max_int = 100
            
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
            elif rem_int > 0 and max_int == -2:
                # مقدار دارد اما max ندارد - احتمالاً خودش درصد است
                if rem_int <= 100:
                    percent = rem_int
                    status = "ok" if percent > 25 else "low" if percent > 10 else "critical"
            
            raw_supplies.append({
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
            })
            
        except Exception as e:
            if idx <= 3:
                print(f"  ⚠ خطا در خواندن supply {idx}: {e}")
    
    # تمیز کردن نهایی
    cleaned = []
    seen_names = set()
    
    for s in raw_supplies:
        # حذف duplicate
        if s["name"] in seen_names:
            continue
        seen_names.add(s["name"])
        
        # حذف entries بی‌مصرف
        if s["status"] in ["no_sensor", "not_supported"] and s["type_name"] == "unknown":
            continue
        if s["percent"] is None and s["max"] == "N/A" and s["type_name"] not in ["wasteToner"]:
            # نگه دار اگر مقدار remaining معتبر دارد
            if s["remaining"] in ["N/A", "unsupported"]:
                continue
        
        cleaned.append(s)
    
    return cleaned


def walk_input_trays(ip, community, snmp_version=None, timeout=2.0):
    """Walk روی جدول prtInputTable برای سینی‌ها"""
    trays = []
    
    for idx in range(1, 8):
        try:
            name_oid = f"1.3.6.1.2.1.43.8.2.1.13.1.{idx}"
            name = snmp_get_with_fallback(ip, name_oid, community, version=snmp_version, timeout=timeout)
            
            cap_oid = f"1.3.6.1.2.1.43.8.2.1.9.1.{idx}"
            cap_val = snmp_get_with_fallback(ip, cap_oid, community, version=snmp_version, timeout=timeout)
            
            level_oid = f"1.3.6.1.2.1.43.8.2.1.10.1.{idx}"
            level_val = snmp_get_with_fallback(ip, level_oid, community, version=snmp_version, timeout=timeout)
            
            if name is None and cap_val is None and level_val is None:
                continue
            
            name_str = str(name).strip() if name else f"Tray {idx}"
            
            try: cap_int = int(cap_val) if cap_val is not None and str(cap_val).lstrip('-').isdigit() else 0
            except: cap_int = 0
            
            try: 
                if level_val is not None and str(level_val).lstrip('-').isdigit():
                    level_int = int(level_val)
                else:
                    level_int = -2
            except: level_int = -2
            
            fill_percent = None
            status = "unknown"
            
            if level_int == -2: status = "no_sensor"
            elif level_int == -3: status = "not_supported"
            elif cap_int > 0 and level_int >= 0:
                fill_percent = round(level_int / cap_int * 100)
                if level_int == 0: status = "empty"
                elif fill_percent <= 25: status = "low"
                elif fill_percent <= 75: status = "medium"
                else: status = "ok"
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
            pass
    
    return trays


def detect_printer_type(supplies):
    """تشخیص نوع پرینتر از روی مواد مصرفی"""
    toners = [s for s in supplies if s.get("type") == 3 or s.get("type_name") == "toner"]
    if not toners:
        toners = supplies
    
    color_keywords = ["cyan", "magenta", "yellow", "سیان", "مژنتا", "color", "colour"]
    has_color = False
    for t in toners:
        name_lower = t.get("name", "").lower()
        for c in color_keywords:
            if c in name_lower:
                has_color = True
                break
    
    return "رنگی 🎨" if has_color else "تک‌رنگ ⚫"


def test_printer(ip, name, community, brand):
    """تست کامل یک پرینتر"""
    
    if SKIP_OFFLINE:
        if not is_printer_online(ip, community, timeout=2.0):
            print(f"⏭ {name} ({ip}) - آفلاین")
            return None, None, None
    
    # تشخیص نسخه SNMP
    snmp_version = detect_snmp_version(ip, community)
    
    if snmp_version is None:
        print(f"⏭ {name} ({ip}) - بدون پاسخ SNMP")
        return None, None, None
    
    print(f"\n{'='*80}")
    print(f"🖨  {name} ({ip})  |  برند: {brand}  |  SNMP v{snmp_version}")
    print(f"{'='*80}")
    
    sys_desc = snmp_get_with_fallback(ip, "1.3.6.1.2.1.1.1.0", community, version=snmp_version, timeout=2.0)
    if sys_desc and "ECS100G" in str(sys_desc).upper():
        print("   نوع: 🌡️ سنسور ECS100G")
        return None, None, snmp_version
    
    print("   📦 خواندن کارتریج‌ها...")
    supplies = walk_supplies_table(ip, community, brand, snmp_version=snmp_version)
    
    print("   🗃 خواندن سینی‌ها...")
    trays = walk_input_trays(ip, community, snmp_version=snmp_version)
    
    if not supplies and not trays:
        print("   ❌ هیچ اطلاعاتی یافت نشد")
        return None, None, snmp_version
    
    if supplies:
        printer_type = detect_printer_type(supplies)
        print(f"\n   🎨 مواد مصرفی — {printer_type} ({len(supplies)} عدد)")
        print(f"   {'─'*100}")
        print(f"   {'#':<3} {'نام کارتریج':<35} {'نوع':<12} {'ظرفیت':<12} {'باقی‌مانده':<12} {'درصد':<8} {'وضعیت':<12}")
        print(f"   {'─'*100}")
        
        for s in supplies:
            pct_str = f"{s['percent']}%" if s['percent'] is not None else "N/A"
            cap_str = str(s['max']) if s['max'] != "N/A" else "N/A"
            rem_str = str(s['remaining']) if s['remaining'] != "N/A" else "N/A"
            
            status_icon = {"ok": "✅", "low": "🟡", "critical": "🟠", "empty": "🔴", 
                          "unknown": "❓", "not_supported": "⬜", "no_sensor": "⬜"}.get(s["status"], "❓")
            print(f"   {s['index']:<3} {s['name'][:33]:<35} {s['type_name']:<12} {cap_str:<12} {rem_str:<12} {pct_str:<8} {status_icon} {s['status']}")
    
    if trays:
        print(f"\n   🗃 سینی‌های کاغذ ({len(trays)} عدد)")
        print(f"   {'─'*100}")
        print(f"   {'#':<3} {'نام سینی':<25} {'ظرفیت':<12} {'سطح':<12} {'پر بودن':<12} {'وضعیت':<12}")
        print(f"   {'─'*100}")
        
        for t in trays:
            fill_str = f"{t['fill_percent']}%" if t['fill_percent'] is not None else "N/A"
            level_str = str(t['level']) if t['level'] != "N/A" and t['level'] != "unsupported" else "N/A"
            cap_str = str(t['capacity']) if t['capacity'] > 0 else "N/A"
            
            status_icon = {"ok": "✅", "medium": "🟡", "low": "🟠", "empty": "🔴",
                          "no_sensor": "⬜", "not_supported": "⬜", "unknown": "❓"}.get(t["status"], "❓")
            print(f"   {t['index']:<3} {t['name'][:23]:<25} {cap_str:<12} {level_str:<12} {fill_str:<12} {status_icon} {t['status']}")
    
    return supplies, trays, snmp_version


def cartridge_summary(all_results):
    """ایجاد جدول خلاصه مدل کارتریج و ظرفیت عددی"""
    lines = []
    lines.append("")
    lines.append("╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗")
    lines.append("║                                 📋 خلاصه مدل کارتریج و ظرفیت                                                   ║")
    lines.append("╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝")
    lines.append("")
    lines.append(f"{'IP':<16} {'نام پرینتر':<28} {'SNMP':<6} {'مدل کارتریج':<32} {'ظرفیت':<12} {'باقیمانده':<12} {'درصد':<8} {'وضعیت':<12}")
    lines.append("─" * 130)
    
    for result in all_results:
        ip, name, supplies, snmp_version = result
        ver_str = f"v{snmp_version}" if snmp_version else "?"
        
        if not supplies:
            lines.append(f"{ip:<16} {name[:26]:<28} {ver_str:<6} {'—':<32} {'—':<12} {'—':<12} {'—':<8} {'—':<12}")
            continue
        
        for s in supplies:
            if s["type_name"] in ("wasteToner", "fuser", "cleaner", "transfer", "other"):
                continue
            
            capacity_str = f"{s['max']:,}" if s['max'] != "N/A" and s['max'] != -2 else "N/A"
            remain_str = f"{s['remaining']:,}" if s['remaining'] != "N/A" and s['remaining'] != -2 and s['remaining'] != -3 and s['remaining'] != "unsupported" else "N/A"
            pct_str = f"{s['percent']}%" if s['percent'] is not None else "N/A"
            
            status_icon = {"ok": "✅", "low": "🟡", "critical": "🟠", "empty": "🔴"}.get(s["status"], "❓")
            status_display = f"{status_icon} {s['status']}" if s["status"] != "N/A" else "❓"
            
            lines.append(
                f"{ip:<16} {name[:26]:<28} {ver_str:<6} {s['model'][:30]:<32} {capacity_str:<12} {remain_str:<12} {pct_str:<8} {status_display:<12}"
            )
    
    lines.append("─" * 130)
    
    critical_toners = []
    for result in all_results:
        ip, name, supplies, _ = result
        if not supplies:
            continue
        for s in supplies:
            if s.get("type_name") in ("toner", "cartridge") and s.get("percent") is not None and s["percent"] <= 15:
                critical_toners.append((ip, name, s["name"], s["percent"]))
    
    if critical_toners:
        lines.append("")
        lines.append("⚠️  هشدار: تونرهای زیر ۱۵٪ نیاز به تعویض دارند:")
        lines.append("─" * 80)
        for ip, name, toner, percent in critical_toners:
            lines.append(f"   🔴 {ip} - {name[:38]} : {toner} ({percent}%)")
    
    # اضافه کردن دستگاه‌های بدون اطلاعات تونر
    no_data_devices = []
    for result in all_results:
        ip, name, supplies, _ = result
        if supplies:
            has_toner = any(s.get("type_name") in ("toner", "cartridge") for s in supplies)
            if not has_toner:
                no_data_devices.append((ip, name))
        else:
            no_data_devices.append((ip, name))
    
    if no_data_devices:
        lines.append("")
        lines.append("⚠️  دستگاه‌های بدون اطلاعات تونر (نیاز به بررسی دستی):")
        lines.append("─" * 80)
        for ip, name in no_data_devices[:10]:  # حداکثر 10 تا
            lines.append(f"   ⚠ {ip} - {name[:50]}")
        if len(no_data_devices) > 10:
            lines.append(f"   ... و {len(no_data_devices) - 10} دستگاه دیگر")
    
    lines.append("")
    lines.append(f"تعداد کل: {len(all_results)} پرینتر | تاریخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    # اضافه کردن آمار نسخه SNMP
    v1_count = sum(1 for r in all_results if r[3] == 1)
    v2_count = sum(1 for r in all_results if r[3] == 2)
    unknown_count = sum(1 for r in all_results if r[3] is None)
    
    lines.append(f"📊 آمار SNMP: v1={v1_count} دستگاه | v2c={v2_count} دستگاه | بدون پاسخ={unknown_count} دستگاه")
    lines.append("")
    
    return "\n".join(lines)


def main():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   تست جامع تونر، کارتریج، سینی‌ها و ظرفیت کاغذ             ║")
    print("║   پشتیبانی از SNMP v1 و v2c با تشخیص خودکار                 ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"زمان شروع: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    if not os.path.exists("printers.json"):
        print("❌ فایل printers.json یافت نشد")
        sys.exit(1)
    
    with open("printers.json", encoding="utf-8") as f:
        printers = json.load(f)
    
    print(f"تعداد پرینترها: {len(printers)}\n")
    
    all_results = []
    stats = {"online": 0, "offline": 0, "error": 0}
    
    with open("toner_report.txt", "w", encoding="utf-8") as report:
        header = f"""
╔══════════════════════════════════════════════════════════════════╗
║         گزارش جامع تونر، کارتریج، سینی‌ها و ظرفیت کاغذ        ║
╠══════════════════════════════════════════════════════════════════╣
║  تاریخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          ║
║  تعداد پرینترها: {len(printers)}                                          ║
║  SNMP: تشخیص خودکار v1/v2c                                         ║
╚══════════════════════════════════════════════════════════════════╝
"""
        report.write(header)
        
        for p in printers:
            ip = p.get("ip", "")
            name = p.get("name", ip)
            community = p.get("community", "public")
            brand = p.get("brand", "unknown")
            
            try:
                supplies, trays, snmp_version = test_printer(ip, name, community, brand)
                
                if supplies or trays:
                    stats["online"] += 1
                    all_results.append((ip, name, supplies, snmp_version))
                    
                    report.write(f"\n{'='*80}\n")
                    report.write(f"🖨  {name}\n")
                    report.write(f"   IP: {ip}  |  برند: {brand}  |  SNMP: v{snmp_version if snmp_version else '?'}\n")
                    report.write(f"{'='*80}\n")
                    
                    if supplies:
                        report.write(f"\n📦 مواد مصرفی ({len(supplies)} عدد):\n")
                        report.write(f"{'─'*80}\n")
                        for s in supplies:
                            pct_str = f"{s['percent']}%" if s['percent'] is not None else "N/A"
                            cap_str = str(s['max']) if s['max'] != "N/A" else "N/A"
                            rem_str = str(s['remaining']) if s['remaining'] != "N/A" else "N/A"
                            report.write(f"  [{s['index']}] {s['name']}\n")
                            report.write(f"      مدل: {s['model']}\n")
                            report.write(f"      نوع: {s['type_name']}\n")
                            report.write(f"      ظرفیت: {cap_str} | باقی‌مانده: {rem_str} | درصد: {pct_str}\n")
                            report.write(f"      وضعیت: {s['status']}\n")
                    
                    if trays:
                        report.write(f"\n🗃 سینی‌های کاغذ ({len(trays)} عدد):\n")
                        report.write(f"{'─'*80}\n")
                        for t in trays:
                            fill_str = f"{t['fill_percent']}%" if t['fill_percent'] is not None else "N/A"
                            level_str = str(t['level']) if t['level'] != "N/A" and t['level'] != "unsupported" else "N/A"
                            cap_str = str(t['capacity']) if t['capacity'] > 0 else "N/A"
                            report.write(f"  [{t['index']}] {t['name']}\n")
                            report.write(f"      ظرفیت: {cap_str} برگ | سطح: {level_str} | پر: {fill_str}\n")
                            report.write(f"      وضعیت: {t['status']}\n")
                    
                else:
                    stats["offline"] += 1
                    all_results.append((ip, name, None, None))
                    report.write(f"\n🖨  {name} ({ip}) — {brand}\n")
                    report.write("   ⏭ آفلاین یا بدون پاسخ\n")
                
                report.flush()
                
            except Exception as e:
                stats["error"] += 1
                all_results.append((ip, name, None, None))
                print(f"❌ {name}: {e}")
                report.write(f"\n🖨  {name} ({ip})\n")
                report.write(f"   ❌ خطا: {e}\n")
        
        summary_table = cartridge_summary(all_results)
        report.write("\n" + summary_table)
    
    print(f"""
{'='*80}
📊 خلاصه:
   ✅ موفق: {stats['online']}
   ⏭ آفلاین: {stats['offline']}
   ❌ خطا: {stats['error']}
   📁 گزارش کامل: toner_report.txt
{'='*80}
""")


if __name__ == "__main__":
    main()