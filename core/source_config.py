"""
تعریف الگوی یکنواخت برای source در core collectors.
قالب: "<channel>:<method>"
"""

SOURCE_TEMPLATE = "{channel}:{method}"


def build_source(channel: str, method: str) -> str:
    ch = str(channel or "").strip().lower() or "unknown"
    md = str(method or "").strip().lower() or "unknown"
    return SOURCE_TEMPLATE.format(channel=ch, method=md)


# منابع عمومی
SOURCE_STANDARD_PRINTER_MIB = build_source("snmp", "standard_printer_mib")
SOURCE_ALTERNATE_OID = build_source("snmp", "alternate_oid")
SOURCE_NO_SENSOR = build_source("snmp", "no_sensor")
SOURCE_NOT_SUPPORTED = build_source("snmp", "not_supported")

# منابع Toshiba
SOURCE_TOPACCESS_SCRAPE = build_source("http", "topaccess_scrape")
SOURCE_USAGE_ESTIMATED = build_source("calc", "usage_estimated")
SOURCE_SNMP_WALK = build_source("snmp", "walk_supplies")
SOURCE_NE_FLAG_ESTIMATED = build_source("calc", "ne_flag_estimated")
