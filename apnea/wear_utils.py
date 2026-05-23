# apnea/wear_utils.py
"""
기존 ppg 앱의 R ratio / wear 판단을 새 앱에서 재사용하기 위한 유틸.
apnea_engine.py의 함수를 re-export 해서 views.py에서 일관되게 사용.
"""

from .apnea_engine import detect_wear_green, compute_r_ratio_series

__all__ = ["detect_wear_green", "compute_r_ratio_series"]