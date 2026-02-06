from typing import Dict, Any, List
import numpy as np
import heartpy as hp
import logging
import os

access_log = logging.getLogger("django.request")
access_log.error(f"[WEAR_PROBE] wear_runtime LOADED file={__file__} pid={os.getpid()}")

SAMPLE_RATE = 25
TYPICAL_STD = 4000
THRESHOLD_LOW = TYPICAL_STD * 0.2      # 800
THRESHOLD_HIGH = TYPICAL_STD * 25      # 100000
SUBCHUNK_COUNT = 5

def _preprocess_green(values: list | np.ndarray, sr: int) -> np.ndarray:
    """레거시 임계치에 영향 최소화: float화, NaN/Inf 정리, 약한 스파이크 클립, DC 제거."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size == 0:
        return x

    finite = np.isfinite(x)
    if not finite.any():
        return np.array([], dtype=float)  # 전부 NaN/Inf면 invalid로 처리하게 빈 배열 반환
    med = np.median(x[finite])
    x[~finite] = med

    # 스파이크 억제
    mad = np.median(np.abs(x - med)) or 1e-9
    lo = med - 8.0 * mad
    hi = med + 8.0 * mad
    x = np.clip(x, lo, hi)

    # DC 제거
    x = x - np.median(x)

    return x

def analyze_ppg_chunk(ppg_values: List[float]) -> Dict[str, Any]:
    access_log.error("[WEAR_PROBE] analyze_ppg_chunk ENTER")

    try:
        # 전처리
        data_raw = _preprocess_green(ppg_values, SAMPLE_RATE)
        if data_raw.size == 0:
            access_log.info("[WEAR_DBG] empty_or_all_nonnumeric")
            return {"result": "invalid", "reason": "empty_or_all_nonnumeric"}

        # 필터링
        filtered = hp.filter_signal(
            data_raw, [0.5, 8], sample_rate=SAMPLE_RATE, order=3, filtertype='bandpass'
        )

        amp = float(np.ptp(filtered))
        std = float(np.std(filtered))
        access_log.info(f"[WEAR_DBG] len={len(filtered)} amp={amp:.2f} std={std:.2f}")

        # HeartPy 처리
        try:
            wd, m = hp.process(filtered, sample_rate=SAMPLE_RATE)
            bpm = float(m.get("bpm", 0.0))
            peaks = len(wd.get("peaklist", []))
            removed = len(wd.get("removed_beats", []))
            access_log.info(f"[WEAR_DBG] bpm={bpm:.1f} peaks={peaks} removed={removed}")
        except Exception as e:
            access_log.info(f"[WEAR_DBG] HeartPy 처리 실패: {type(e).__name__} - {e}")
            return {"result": "error", "detail": str(e)}

        # 유효성 판단
        is_valid = (
            (30 < bpm < 200)
            and (peaks - removed) > (len(filtered) / SAMPLE_RATE) / 2
        )

        if not is_valid:
            access_log.info(f"[WEAR_DBG] bpm={bpm:.1f} peaks={peaks} removed={removed}")
            if amp < THRESHOLD_LOW or amp > THRESHOLD_HIGH:
                access_log.info(f"[WEAR_DBG] → fallback: non_wear (amp={amp:.1f})")
                return {"result": "non_wear", "reason": "fallback_amp_check"}
            else:
                return {"result": "invalid"}

        chunk_len = len(filtered)
        subchunk_len = chunk_len // SUBCHUNK_COUNT
        votes = []

        for i in range(SUBCHUNK_COUNT):
            sub = filtered[i * subchunk_len : (i + 1) * subchunk_len]
            amp_sub = np.ptp(sub)
            std_sub = np.std(sub)
            state = "wear" if (THRESHOLD_LOW <= amp_sub <= THRESHOLD_HIGH) else "non_wear"
            votes.append(state)
            access_log.info(f"[WEAR_DBG] sub{i+1}: amp={amp_sub:.1f} std={std_sub:.1f} state={state}")

        wear_cnt = votes.count("wear")
        non_cnt = votes.count("non_wear")
        final = "wear" if wear_cnt >= non_cnt else "non_wear"

        access_log.info(f"[WEAR_DBG] final={final} ({wear_cnt}:{non_cnt})")

        return {"result": final}

    except Exception as e:
        access_log.exception(f"[WEAR_DBG] exception: {type(e).__name__}: {e}")
        return {"result": "error", "detail": str(e)}


def wear_green_to_pred(ppg_green: List[float]) -> Dict[str, Any]:
    """Analyze → ModelPredSerializer 형태로 매핑."""
    print(f"[WEAR_PROBE] >>> wear_green_to_pred CALLED len={len(ppg_green) if ppg_green else 0}")
    try:
        r = analyze_ppg_chunk(ppg_green or [])
        res = r.get("result", "error")

        if res == "wear":
            # 착용: label=1, valid=True
            return {"prob": None, "label": 1, "thr": None, "valid": True, "error": None}
        if res == "non_wear":
            # 비착용: label=0, valid=True
            return {"prob": None, "label": 0, "thr": None, "valid": True, "error": None}
        if res == "invalid":
            # 신호는 들어왔지만 판정 불가
            return {"prob": None, "label": None, "thr": None, "valid": False, "error": "invalid"}
        # error 포함
        return {"prob": None, "label": None, "thr": None, "valid": False, "error": r.get("detail") or "error"}
    except Exception as e:
        return {"prob": None, "label": None, "thr": None, "valid": False, "error": f"wear_detect_failed: {e}"}
