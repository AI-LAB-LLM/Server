# core/utils_spo2.py

import numpy as np
from math import sqrt, isfinite

SR_DEFAULT = 100.0
WINDOW_SEC_DEFAULT = 1.0
DC_WIN_SEC_DEFAULT = 1.2

def _moving_average_centered(values: np.ndarray, win: int) -> np.ndarray:
    """
    중심(central) 이동평균 근사.
    - win >= n: 전체 평균으로 채운 벡터 반환 (pandas rolling center+min_periods≈1 유사)
    - win == 1: 원본 복사
    - 그 외: 단순 컨볼루션으로 근사(에지 영향은 작음)
    """
    x = np.asarray(values, dtype=float).ravel()
    n = x.size
    if n == 0:
        return x
    if win <= 1:
        return x.copy()
    if win >= n:
        mu = float(np.mean(x))
        return np.full(n, mu, dtype=float)
    kernel = np.ones(win, dtype=float) / float(win)
    return np.convolve(x, kernel, mode='same')

def _rms(arr: np.ndarray) -> float:
    if arr.size == 0:
        return float("nan")
    return sqrt(float(np.mean(arr.astype(float) ** 2)))

def compute_R_1s(ir_vals: np.ndarray, red_vals: np.ndarray,
                 sr: float = SR_DEFAULT, dc_win_sec: float = DC_WIN_SEC_DEFAULT) -> float | None:
    ir_vals  = np.asarray(ir_vals,  dtype=float).ravel()
    red_vals = np.asarray(red_vals, dtype=float).ravel()
    if ir_vals.size == 0 or red_vals.size == 0 or ir_vals.size != red_vals.size:
        return None

    n = ir_vals.size
    dc_win = max(3, int(round(dc_win_sec * sr)))

    dc_ir  = _moving_average_centered(ir_vals,  min(dc_win, n))
    dc_red = _moving_average_centered(red_vals, min(dc_win, n))

    ac_ir  = ir_vals  - dc_ir
    ac_red = red_vals - dc_red

    ac_ir_rms  = _rms(ac_ir)
    ac_red_rms = _rms(ac_red)
    dc_ir_mean  = float(np.mean(dc_ir))
    dc_red_mean = float(np.mean(dc_red))

    if dc_ir_mean > 0 and dc_red_mean > 0 and ac_ir_rms > 0 and isfinite(ac_red_rms):
        R = (ac_red_rms / dc_red_mean) / (ac_ir_rms / dc_ir_mean)
        if isfinite(R) and 0 < R <= 3.0:
            return float(R)
    return None

def compute_R_series_1s(ir_all, red_all,
                        sr: float = SR_DEFAULT,
                        window_sec: float = WINDOW_SEC_DEFAULT,
                        dc_win_sec: float = DC_WIN_SEC_DEFAULT) -> list[float | None]:
    ir_all  = np.asarray(ir_all,  dtype=float).ravel()
    red_all = np.asarray(red_all, dtype=float).ravel()
    win = int(round(sr * window_sec))
    n_windows = min(ir_all.size, red_all.size) // win
    out = []
    for i in range(n_windows):
        s = i * win
        e = s + win
        out.append(compute_R_1s(ir_all[s:e], red_all[s:e], sr=sr, dc_win_sec=dc_win_sec))
    return out
