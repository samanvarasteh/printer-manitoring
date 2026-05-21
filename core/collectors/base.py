"""
توابع مشترک همه collectorها:
- si, ss: تبدیل ایمن مقادیر SNMP
- _g: wrapper ساده snmp_get با fallback خودکار
- _counters_event: ثبت رویدادهای چاپ و هشدار (با جلوگیری از ثبت اولیه و دلتاهای بزرگ غیرمنطقی)
- validate_counter_consistency: بررسی سازگاری شمارنده‌ها
- detect_brand: تشخیص خودکار برند
"""

import logging
from core.snmp.protocol import snmp_get_with_fallback
from core import store
from core.database import add_event

log = logging.getLogger("PrinterMonitor")

# ─── تنظیمات اعتبارسنجی ─────────────────────────────────────────
MAX_REASONABLE_DELTA = 200        # حداکثر صفحات منطقی در هر بازه ۳۰ ثانیه (کاهش یافته از ۱۰۰۰)
MIN_DELTA_FOR_FALLBACK = 1        # حداقل دلتا برای fallback مبتنی بر total
MIN_VALID_TOTAL_FOR_FIRST_POLL = 50  # اگر total < 50 باشد، شاید پرینتر جدید است
MAX_TOTAL_AFTER_RESET = 5000      # اگر مقدار جدید کمتر از این باشد و قبلی بزرگ بود، ریست شده


# ─── helpers ────────────────────────────────────────────────────
def si(v, d: int = 0) -> int:
    try:
        return int(v) if v is not None else d
    except:
        return d


def ss(v, d: str = "N/A") -> str:
    return str(v).strip() if v is not None and str(v).strip() else d


def _g(ip: str, oid: str, community: str, timeout: float = 2.5):
    """
    Wrapper برای snmp_get_with_fallback که سعی می‌کند ابتدا v2c و در صورت شکست v1 را امتحان کند.
    """
    return snmp_get_with_fallback(ip, oid, community, timeout=timeout)


# ─── رویدادها ─────────────────────────────────────────────────
def _counters_event(ip: str, total: int, prev: dict, alerts: list, curr_codes: list,
                    full_color: int = None, black_white: int = None,
                    paper_size: str = None, username: str = None):
    """
    ثبت رویدادهای هشدار و چاپ با پشتیبانی از رنگ، سایز کاغذ و نام کاربر.
    
    🔥 تغییرات مهم:
    - جلوگیری از ثبت رویداد در اولین poll پس از ریستارت پروژه
    - بررسی صحیح وجود مقدار قبلی با استفاده از prev_total
    - کاهش MAX_REASONABLE_DELTA به 200
    - ثبت رویداد COUNTER_RESET در صورت کاهش غیرمنتظره شمارنده
    """
    
    # ─── ثبت رویدادهای هشدار جدید ─────────────────────────────────
    if curr_codes:
        alert_codes_list = prev.get("alert_codes", []) if prev else []
        for code in curr_codes:
            if code not in alert_codes_list:
                msg = next((a["message"] for a in alerts if a["code"] == code), f"Error {code}")
                add_event(ip, "ALERT", {"message": msg, "code": code, "severity": "warning"})

    # ─── دریافت مقدار قبلی ────────────────────────────────────────
    prev_total = prev.get("print_total") if prev else None
    
    # 🔥 مهم: اگر مقدار قبلی وجود ندارد (اولین poll پس از راه‌اندازی)
    if prev_total is None:
        log.warning(f"  [{ip}] جلوگیری از ثبت رویداد PRINT در اولین poll (total={total:,})")
        store._prev.set(ip, {
            "print_total": total,
            "full_color": full_color if full_color is not None else 0,
            "black_white": black_white if black_white is not None else 0,
            "alert_codes": curr_codes,
        })
        return

    # ─── محاسبه دلتا ──────────────────────────────────────────────
    prev_fc = prev.get("full_color")
    prev_bw = prev.get("black_white")
    
    delta_fc = (full_color - prev_fc) if (full_color is not None and prev_fc is not None) else 0
    delta_bw = (black_white - prev_bw) if (black_white is not None and prev_bw is not None) else 0
    total_delta = delta_fc + delta_bw

    log.debug(f"  [{ip}] PRINT: prev_total={prev_total:,} curr_total={total:,} "
              f"delta_fc={delta_fc}, delta_bw={delta_bw}, total_delta={total_delta}")

    # ─── Fallback: اگر شمارنده‌های fc/bw کار نکردند، از دلتای total استفاده کن ───
    if total_delta == 0:
        actual_delta = total - prev_total
        if MIN_DELTA_FOR_FALLBACK <= actual_delta <= MAX_REASONABLE_DELTA:
            log.info(f"  [{ip}] fc/bw fallback → total-based delta={actual_delta}")
            total_delta = actual_delta
            if delta_fc > 0:
                delta_bw = max(0, actual_delta - delta_fc)
            else:
                delta_bw = actual_delta
                delta_fc = 0

    # ─── اعتبارسنجی دلتا و جلوگیری از ثبت اشتباه ───────────────────
    skip_print = False
    reason = None

    # حالت ۱: کاهش شدید شمارنده (ریست شدن دستگاه)
    if total < prev_total - MAX_TOTAL_AFTER_RESET:
        skip_print = True
        reason = f"کاهش شدید شمارنده: قبلی {prev_total:,} -> جدید {total:,}"
        add_event(ip, "COUNTER_RESET", {
            "message": f"شمارنده از {prev_total:,} به {total:,} کاهش یافت (ریست دستگاه)",
            "severity": "error",
            "prev_total": prev_total,
            "current_total": total,
        })
        log.warning(f"  [{ip}] {reason}")

    # حالت ۲: دلتا بیش از حد مجاز
    elif total_delta > MAX_REASONABLE_DELTA:
        skip_print = True
        reason = f"دلتا {total_delta} بیشتر از حد مجاز {MAX_REASONABLE_DELTA}"
        add_event(ip, "PRINT_OVERFLOW", {
            "message": f"افزایش غیرمنتظره صفحات: {total_delta} صفحه در یک بازه",
            "severity": "warning",
            "delta": total_delta,
            "prev_total": prev_total,
            "current_total": total,
        })
        log.warning(f"  [{ip}] {reason}")

    # ─── ثبت رویداد PRINT در صورت معتبر بودن ───────────────────────
    if not skip_print and total_delta > 0:
        if delta_fc > 0 and delta_bw > 0:
            msg = f"{delta_fc} صفحه رنگی + {delta_bw} صفحه سیاه‌سفید = {total_delta} صفحه چاپ شد"
            color = "مختلط"
        elif delta_fc > 0:
            msg = f"{delta_fc} صفحه رنگی چاپ شد"
            color = "رنگی"
        else:
            msg = f"{delta_bw} صفحه سیاه‌سفید چاپ شد"
            color = "سیاه‌سفید"
        
        event_data = {
            "message": msg,
            "pages": total_delta,
            "color": color,
            "paper_size": paper_size,
            "severity": "info",
        }
        if username:
            event_data["username"] = username
        
        add_event(ip, "PRINT", event_data)
        log.info(f"  [{ip}] ✓ ثبت چاپ: {total_delta} صفحه ({color})")
        
    elif total_delta > 0:
        log.warning(f"  [{ip}] رویداد PRINT ثبت نشد: {reason}")

    # ─── ذخیره مقادیر جدید برای poll بعدی ─────────────────────────
    new_prev = {
        "print_total": total,
        "full_color": full_color if full_color is not None else (prev_fc if prev_fc is not None else 0),
        "black_white": black_white if black_white is not None else (prev_bw if prev_bw is not None else 0),
        "alert_codes": curr_codes,
    }
    store._prev.set(ip, new_prev)


# ─── سازگاری شمارنده‌ها ─────────────────────────────────────────
def validate_counter_consistency(counters: dict, brand: str) -> list:
    warnings = []
    total   = counters.get("total",       0) or 0
    color   = counters.get("full_color",  0) or 0
    bw      = counters.get("black_white", 0) or 0
    copy_   = counters.get("copy",        0) or 0
    printer = counters.get("printer",     0) or 0

    if brand == "toshiba" and total > 0:
        twin_ = counters.get("twin", 0) or 0
        if color + bw > 0:
            diff = abs(total - (color + bw + twin_))
            if diff > max(100, total * 0.01):
                warnings.append(
                    f"⚠ Toshiba: fc({color:,})+bw({bw:,})+twin({twin_:,})={color+bw+twin_:,} ≠ total({total:,}) diff={diff:,}"
                )
        copy_fc = counters.get("copy_fc",   0) or 0
        ptr_fc  = counters.get("printer_fc",0) or 0
        if color > 0 and (copy_fc + ptr_fc) > color + 1000:
            warnings.append(
                f"⚠ Toshiba FC: copy_fc({copy_fc:,})+ptr_fc({ptr_fc:,})={copy_fc+ptr_fc:,} > fc_total({color:,})"
            )

    if brand == "canon" and total > 0 and copy_ > 0 and printer > 0:
        diff = abs(total - (copy_ + printer))
        if diff > max(300, total * 0.01):
            warnings.append(
                f"⚠ Canon: copy({copy_:,})+print({printer:,})={copy_+printer:,} ≠ total({total:,}) diff={diff:,}"
            )

    return warnings


# ─── تشخیص برند ─────────────────────────────────────────────────
def detect_brand(ip: str, community: str) -> str:
    sys_oid  = snmp_get_with_fallback(ip, "1.3.6.1.2.1.1.2.0", community, timeout=2.0)
    sys_desc = str(snmp_get_with_fallback(ip, "1.3.6.1.2.1.1.1.0", community, timeout=2.0) or "").lower()
    oid_str  = str(sys_oid) if sys_oid else ""

    if "ecs100g" in sys_desc:
        return "sensor"
    if "1.3.6.1.4.1.1129" in oid_str or "toshiba"   in sys_desc: return "toshiba"
    if "1.3.6.1.4.1.1602" in oid_str or "canon"     in sys_desc: return "canon"
    if "1.3.6.1.4.1.2435" in oid_str or "brother"   in sys_desc: return "brother"
    if ("1.3.6.1.4.1.11"   in oid_str or
            "jetdirect" in sys_desc or
            "hp " in sys_desc or
            "hewlett" in sys_desc or
            "laserjet" in sys_desc or
            "officejet" in sys_desc or
            "pagewide" in sys_desc):
        return "hp"
    return "unknown"