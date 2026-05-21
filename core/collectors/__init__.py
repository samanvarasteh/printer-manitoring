"""
بسته جمع‌آوری‌کننده‌های داده برای برندهای مختلف
(کالکتور Canon به enhanced_collector منتقل شده)
"""

from .toshiba import collect_toshiba
from .hp import collect_hp
from .brother import collect_brother
from .base import _counters_event, detect_brand, si, ss, validate_counter_consistency

__all__ = [
    'collect_toshiba',
    'collect_hp',
    'collect_brother',
    '_counters_event',
    'detect_brand',
    'si',
    'ss',
    'validate_counter_consistency',
]
