from __future__ import annotations
from typing import Any, Dict
from typing import List, Optional
from math import sqrt, isfinite
import numpy as np
import heartpy as hp
import logging
import math

log = logging.getLogger(__name__)
try:
    from dateutil import parser as dtparser

    def parse_ts(s: str):
        return dtparser.isoparse(s)

except Exception:
    from datetime import datetime

    def parse_ts(s: str):
        # "....Z" -> +00:00
        if isinstance(s, str) and s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)

# SensorData 직렬화
def serialize_sensor_row(r) -> Dict[str, Any]:
    """
    SensorData 인스턴스를 dict로 변환.
    (런타임 import 회피를 위해 타입 힌트는 느슨하게 둠)
    """
    return {
        "device_id": getattr(r, "device_id", None),
        "timestamp": getattr(r, "timestamp").isoformat() if getattr(r, "timestamp", None) else None,
        "ppg_green": getattr(r, "ppg_green", None),
        "ppg_ir": getattr(r, "ppg_ir", None),
        "predictions": getattr(r, "predictions", None),
    }

__all__ = ["parse_ts", "serialize_sensor_row"]

def fix_len_pad_trim(x, target_len: int, max_delta: int = 2):
    """
    1) |len(x) - target_len| <= max_delta → 패드/트림(가장 안전 & 빠름)
    2) 그 외 → 선형 보간으로 정확히 target_len으로 리샘플
    """
    x = np.asarray(x, dtype=float)
    n = x.size
    if n == target_len:
        return x

    delta = n - target_len

    # Off-by-one/Two 정도는 패드/트림 처리
    if abs(delta) <= max_delta:
        if delta < 0:
            # 부족 → 마지막 값으로 패딩
            pad = np.full(target_len - n, x[-1] if n > 0 else 0.0, dtype=float)
            y = np.concatenate([x, pad])
            log.debug("pad %d → %d (Δ=%d)", n, target_len, delta)
            return y
        else:
            # 초과 → 뒤에서 자르기
            y = x[:target_len]
            log.debug("trim %d → %d (Δ=+%d)", n, target_len, delta)
            return y

    # 격차가 큰 예외 상황만 리샘플
    src = np.linspace(0, n - 1, num=n)
    dst = np.linspace(0, n - 1, num=target_len)
    y = np.interp(dst, src, x)
    log.warning("resample %d → %d (Δ=%d)", n, target_len, delta)
    return y

def fix_pair_same_len(a, b, target_len: int, max_delta: int = 2):
    """
    IR/RED 같이 쌍으로 들어온 신호를 동일 길이로 맞춤.
    각각 fix_len_pad_trim 적용 후, 최소 길이에 맞춰 슬라이스(동길이 보장).
    """
    aa = fix_len_pad_trim(a, target_len, max_delta=max_delta)
    bb = fix_len_pad_trim(b, target_len, max_delta=max_delta)
    L = min(len(aa), len(bb), target_len)
    return aa[:L], bb[:L]


def _to_float_or_none(x):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None
