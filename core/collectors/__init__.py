"""
بسته جمع‌آوری‌کننده‌های داده برای برندهای مختلف
شامل کالکتورهای اختصاصی برای Toshiba, HP, Brother, Canon
"""

from .toshiba import collect_toshiba
from .hp import collect_hp
from .brother import collect_brother
from .canon import collect_canon
from .base import _counters_event, detect_brand, si, ss, validate_counter_consistency

__all__ = [
    'collect_toshiba',
    'collect_hp',
    'collect_brother',
    'collect_canon',
    '_counters_event',
    'detect_brand',
    'si',
    'ss',
    'validate_counter_consistency',
]
