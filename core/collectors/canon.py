"""
جمع‌آوری داده‌های Canon با پشتیبانی از تونرها، سینی‌ها و شمارنده‌ها
تونر در Canon می‌تواند از طریق:
1. جدول استاندارد prtMarkerSuppliesTable
2. Canon-specific OIDs برای برخی مدل‌ها
"""

import re
import time
import logging
from datetime import datetime
from core.snmp.protocol import snmp_get_with_fallback
from core.collectors.base import si, ss, _g, _counters_event
from core import store

log = logging.getLogger("PrinterMonitor")

# ─── Canon OID Mapping ────────────────────────────────────────
_CANON_TONER_COLOR_MAP = {
    "black": "black", "bk": "black", "k": "black",
    "cyan": "cyan", "c": "cyan",
    "magenta": "magenta", "m": "magenta",
    "yellow": "yellow", "y": "yellow",
}


def _canon_toner_key(name: str) -> str:
    """استخراج کلید رنگ از نام تونر"""
    n = name.lower()
    
    # First try full word matches (for longer keywords like "black", "cyan", etc.)
    for kw, key in _CANON_TONER_COLOR_MAP.items():
        if len(kw) > 1 and kw in n:
            return key
    
    # Then try single-letter matches but only as complete words
    words = n.split()
    for kw, key in _CANON_TONER_COLOR_MAP.items():
        if len(kw) == 1 and kw in words:
            return key
    
    return None


# ─── Canon-specific OIDs ──────────────────────────────────────
# OID های مختلف برای دریافت سطح تونر در Canon
CANON_TONER_OIDS = [
    # استاندارد Printer MIB (اول سعی می‌کنیم)
    # prtMarkerSuppliesName: 1.3.6.1.2.1.43.11.1.1.6.1.X
    # prtMarkerSuppliesMaxCapacity: 1.3.6.1.2.1.43.11.1.1.8.1.X
    # prtMarkerSuppliesLevel: 1.3.6.1.2.1.43.11.1.1.9.1.X
    
    # Canon-specific OIDs for cartridge level
    # برخی مدل‌های Canon از این OID ها استفاده می‌کنند
    ("1.3.6.1.4.1.1602.1.2.1.1.1.1.1", "percent"),  # Canon Cartridge Level (likely percent)
    ("1.3.6.1.4.1.1602.1.2.1.1.1.2.1", "percent"),  # Alternate Canon Cartridge
    ("1.3.6.1.4.1.1602.1.2.1.1.1.3.1", "percent"),  # Another variant
    ("1.3.6.1.4.1.1602.1.2.1.1.1.4.1", "percent"),  # Yet another variant
]


def collect_canon(ip: str, name: str, community: str, start: float) -> dict:
    """جمع‌آوری داده Canon با timeout افزایش‌یافته"""
    try:
        # ─── Timeout management ───────────────────────────────────
        def g(oid, timeout=4.0):
            if time.time() - start > 30.0:  # Increased total timeout
                return None
            val = _g(ip, oid, community, timeout=timeout)
            log.debug(f"Canon {ip} OID {oid} -> {val} (took {time.time()-start:.2f}s)")
            return val

        # ─── Uptime ───────────────────────────────────────────────
        ut = si(g("1.3.6.1.2.1.1.3.0", timeout=4.0))
        us = ut // 100
        uptime_str = f"{us//86400}d {(us%86400)//3600:02d}:{(us%3600)//60:02d}"

        # ─── Model & Serial ────────────────────────────────────────
        # Try Canon-specific OIDs first
        model = ss(g("1.3.6.1.4.1.1602.1.1.1.1.0"), "N/A")
        if model == "N/A":
            # Try standard Printer MIB
            model = ss(g("1.3.6.1.2.1.43.5.1.1.16.1"), "N/A")
        if model == "N/A":
            # Try to extract from sysDescr
            desc = ss(g("1.3.6.1.2.1.1.1.0"), "")
            # Look for common Canon model patterns
            m = re.search(r'(MF\d+|LBP\d+|imageRUNNER|Canon [A-Za-z0-9\- ]+)', desc, re.IGNORECASE)
            model = m.group(1).strip() if m else "Unknown"

        serial = ss(g("1.3.6.1.4.1.1602.1.2.1.4.0"), "N/A")
        if serial == "N/A":
            serial = ss(g("1.3.6.1.2.1.43.5.1.1.17.1"), "N/A")

        firmware = ss(g("1.3.6.1.4.1.1602.1.1.1.4.0"), "N/A")

        # ─── Counters ─────────────────────────────────────────────
        # Canon uses its own OIDs for counters
        total = si(g("1.3.6.1.4.1.1602.1.11.2.1.1.3.1", timeout=4.0), 0)
        
        # If main OID fails, try standard Printer MIB
        if total == 0:
            total = si(g("1.3.6.1.2.1.43.10.2.1.4.1.1", timeout=4.0), 0)

        # Try Canon-specific color/mono counters
        color = si(g("1.3.6.1.4.1.1602.1.11.2.1.1.3.4", timeout=4.0), -1)  # Color print/copy
        
        # For Canon LBP printers
        if color == -1:
            color = si(g("1.3.6.1.4.1.1602.1.11.1.1.1.3.4", timeout=4.0), -1)
        
        # Fallback to standard counter
        if color == -1:
            color = si(g("1.3.6.1.2.1.43.10.2.1.4.1.2", timeout=4.0), -1)

        # Determine B/W
        if color >= 0 and total > 0:
            full_color = color
            bw = max(0, total - color)
        else:
            full_color = None
            bw = total

        # ─── Toners ───────────────────────────────────────────────
        toners = {}
        
        # Method 1: Try standard prtMarkerSuppliesTable (1-based index)
        has_valid_toners = False
        for idx in range(1, 6):  # Try up to 5 toners
            t_name = ss(g(f"1.3.6.1.2.1.43.11.1.1.6.1.{idx}", timeout=3.0), "")
            t_max = si(g(f"1.3.6.1.2.1.43.11.1.1.8.1.{idx}", timeout=3.0), -1)
            t_rem = si(g(f"1.3.6.1.2.1.43.11.1.1.9.1.{idx}", timeout=3.0), -2)
            
            # Stop if we get two consecutive failures
            if not t_name and t_max == -1 and t_rem == -2:
                break
            
            if not t_name:
                t_name = "Black Toner" if idx == 1 else f"Toner {idx}"
            
            toner_key = _canon_toner_key(t_name) or ("black" if idx == 1 else f"toner_{idx}")
            
            # Calculate toner level
            if t_rem == -2:
                # No sensor data
                toner_pct, toner_st = None, "no_sensor"
            elif t_max <= 0:
                # Invalid max capacity
                if t_rem >= 0 and t_rem <= 100:
                    # Assume remaining is already a percentage
                    toner_pct, toner_st = t_rem, "ok" if t_rem > 25 else ("low" if t_rem > 10 else "critical")
                    has_valid_toners = True
                else:
                    # Invalid data
                    toner_pct, toner_st = None, "unknown"
            elif t_rem <= 0:
                # Empty cartridge
                toner_pct, toner_st = 0, "empty"
                has_valid_toners = True
            else:
                # Valid calculation
                toner_pct = round(t_rem / t_max * 100)
                toner_st = "ok" if toner_pct > 25 else ("low" if toner_pct > 10 else "critical")
                has_valid_toners = True
            
            toners[toner_key] = {
                "level": toner_pct, "status": toner_st,
                "name": t_name, "remaining": t_rem, "max": t_max
            }

        # Method 2: If standard method didn't work, try Canon-specific OIDs
        if not has_valid_toners and toners:
            log.warning(f"Canon {ip}: Standard prtMarkerSuppliesTable didn't yield valid data, trying Canon-specific OIDs")
            for oid, unit_type in CANON_TONER_OIDS:
                val = g(oid, timeout=3.0)
                if val is not None:
                    try:
                        val_int = int(val)
                        if 0 <= val_int <= 100:
                            # Likely a percentage value
                            toner_key = "black"
                            toner_st = "ok" if val_int > 25 else ("low" if val_int > 10 else "critical")
                            if val_int == 0:
                                toner_st = "empty"
                            toners[toner_key]["level"] = val_int
                            toners[toner_key]["status"] = toner_st
                            has_valid_toners = True
                            break
                    except (ValueError, TypeError):
                        pass

        # If still no toners, add a default unknown toner
        if not toners or not has_valid_toners:
            log.warning(f"Canon {ip}: Could not retrieve valid toner data")
            toners["black"] = {
                "level": None, "status": "unknown",
                "name": "Black Toner", "remaining": -1, "max": -1
            }

        # ─── Trays ────────────────────────────────────────────────
        trays = []
        for idx, label in [(1, "Tray 1"), (2, "Tray 2"), (3, "Tray 3")]:
            cap = si(g(f"1.3.6.1.2.1.43.8.2.1.9.1.{idx}", timeout=2.0), 0)
            lvl = si(g(f"1.3.6.1.2.1.43.8.2.1.10.1.{idx}", timeout=2.0), -9)
            nm = ss(g(f"1.3.6.1.2.1.43.8.2.1.13.1.{idx}", timeout=2.0), label)
            
            if cap == 0 and lvl == -9:
                continue
            
            if lvl == -2:
                st = "no_sensor"
            elif lvl == -3 or lvl <= 0:
                st = "empty"
            elif cap > 0:
                pct = round(lvl / cap * 100)
                st = "low" if pct <= 25 else ("medium" if pct <= 75 else "ok")
            else:
                st = "unknown"
            
            trays.append({"name": nm, "level": lvl, "capacity": cap, "status": st})

        # ─── Alerts ───────────────────────────────────────────────
        alerts = []
        cover = si(g("1.3.6.1.2.1.43.6.1.1.3.1.1", timeout=2.0), 4)
        if cover != 4:
            alerts.append({"message": "درب پرینتر باز است", "code": cover})

        # ─── Event logging ─────────────────────────────────────────
        elapsed = int((time.time() - start) * 1000)
        prev = store._prev.get(ip) or {}
        _counters_event(ip, total, prev, alerts, [],
                        full_color=full_color, black_white=bw, paper_size=None)

        # ─── Logging ───────────────────────────────────────────────
        color_info = f"color={color}" if color >= 0 else "mono"
        toner_pct_log = next((v.get("level") for v in toners.values() if v.get("level") is not None), None)
        log.info(f"  ✓ {name} [canon] total={total:,} bw={bw:,} {color_info} "
                 f"toner={toner_pct_log}% {elapsed}ms")

        return {
            "ip": ip, "name": name, "brand": "canon",
            "online": True, "last_poll": datetime.now().isoformat(), "poll_ms": elapsed,
            "device": {"model": model, "serial": serial, "firmware": firmware, "uptime_str": uptime_str},
            "counters": {"total": total, "full_color": full_color, "black_white": bw,
                         "printer": total, "copy": None, "fax": None},
            "paper_sizes": {}, "trays": trays, "toners": toners, "alerts": alerts,
        }
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        log.error(f"  ✗ {name} [canon] error: {e} {elapsed}ms")
        return {
            "ip": ip, "name": name, "brand": "canon",
            "online": True, "last_poll": datetime.now().isoformat(), "poll_ms": elapsed,
            "device": {"model": "Unknown", "serial": "N/A", "firmware": "N/A", "uptime_str": "N/A"},
            "counters": {"total": 0, "full_color": None, "black_white": 0,
                         "printer": 0, "copy": None, "fax": None},
            "paper_sizes": {}, "trays": [],
            "toners": {"black": {"level": None, "status": "unknown"}},
            "alerts": [{"message": f"Collection error: {e}", "code": 9999}],
        }
