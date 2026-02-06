from __future__ import annotations
import os, threading, warnings, logging
from typing import List, Dict
import numpy as np
import heartpy as hp
from scipy.signal import butter, sosfiltfilt
import joblib
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
print(f"[IR_IMPORT_PRINT] __name__={__name__} file={__file__}")

SR = 100
CHUNK_SEC = 12.0
DC_WIN_SEC = 2.0
DETECT_MODE = "bp"
BP_LO, BP_HI = 0.5, 8.0
MIN_RR_SEC = 0.30
FEATURES = ['PI', 'RMSSD']
FEATS_Z  = [f"{f}_z" for f in FEATURES]
ROBUST_BASE_STATS = True
MIN_BASE_VALID = 6
SD_FLOOR = {'PI': 0.02, 'RMSSD': 3.0}
ZSCORE_CLIP = 5.0
DELTA_THR = 0.05
CALIBRATE_FROM_BASE = False

ALLOW_SINGLE_FEATURE = True
MISSING_FEAT_FILL    = 0.0
BASELINE_THR_PENALTY = 0.00

# 유틸
def _safe_predict(clf, pi_z, rmssd_z, base_thr, during_base=False):
    thr_used = min(0.99, float(base_thr) + DELTA_THR)
    if during_base:
        thr_used = max(0.0, thr_used - BASELINE_THR_PENALTY)
    use_pi, use_rm = np.isfinite(pi_z), np.isfinite(rmssd_z)
    if not (use_pi or use_rm):
        return None, None, thr_used, "zscore_unavailable"
    fv = [pi_z if use_pi else MISSING_FEAT_FILL,
          rmssd_z if use_rm else MISSING_FEAT_FILL]
    X = np.array([fv], dtype=float)
    p1 = float(clf.predict_proba(X)[:, 1][0])
    return p1, int(p1 >= thr_used), thr_used, None

def _moving_avg(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1: return x.astype(float)
    pad = win // 2
    xp = np.pad(x.astype(float), (pad, pad), mode='edge')
    c = np.cumsum(xp, dtype=float); c[win:] = c[win:] - c[:-win]
    return c[win-1:][:len(x)] / float(win)

def _bandpass_sos(x: np.ndarray, sr: float, lo=0.5, hi=8.0, order=2):
    nyq = 0.5 * sr
    sos = butter(order, [lo/nyq, hi/nyq], btype='band', output='sos')
    return sosfiltfilt(sos, x)

def _refine_peaks(peaks: list[int], sr: float, min_rr_sec=0.30) -> np.ndarray:
    if len(peaks) < 2: return np.array(peaks, dtype=int)
    out = [peaks[0]]
    for p in peaks[1:]:
        if (p - out[-1]) / sr >= min_rr_sec:
            out.append(p)
    return np.array(out, dtype=int)

def _detect_peaks_ir(raw_chunk: np.ndarray, sr: int):
    dc = _moving_avg(raw_chunk.astype(float), int(DC_WIN_SEC * sr))
    ac = raw_chunk - dc
    sig_det = ac if DETECT_MODE == "ac" else _bandpass_sos(ac, sr, lo=BP_LO, hi=BP_HI, order=2)
    try:
        wd, m = hp.process(sig_det, sample_rate=sr)
        raw_peaks = [p for p in wd.get('peaklist', []) if p not in wd.get('removed_beats', [])]
        peaks = _refine_peaks(raw_peaks, sr, min_rr_sec=MIN_RR_SEC)
        bpm = float(m.get('bpm', np.nan))
    except Exception:
        return False, np.array([], dtype=int), np.nan, ac, {}
    sec = len(raw_chunk) / sr
    valid = (30 < bpm < 200) and (len(peaks) >= 6)
    return bool(valid), peaks, bpm, ac, m

def _compute_pi_rmssd(raw_chunk: np.ndarray, sr: int, m: dict):
    dc_full = _moving_avg(raw_chunk.astype(float), int(DC_WIN_SEC * sr))
    ac_full = raw_chunk - dc_full
    dc_mean = float(np.nanmean(dc_full)) if np.isfinite(dc_full).any() else np.nan
    ac_rms  = float(np.sqrt(np.nanmean((ac_full - np.nanmean(ac_full))**2))) if np.isfinite(ac_full).any() else np.nan
    PI = 100.0 * ac_rms / dc_mean if (np.isfinite(dc_mean) and dc_mean != 0.0) else np.nan
    RMSSD = float(m.get('rmssd', np.nan)) if m.get('rmssd', None) is not None else np.nan
    return PI, RMSSD

# 베이스라인 상태
SESSION_WINDOW_SEC = 72
class _BaselineState:
    __slots__ = ("pi", "rmssd", "valid_flags", "started_at", "frozen")

    def __init__(self):
        self.pi: List[float] = []
        self.rmssd: List[float] = []
        self.valid_flags: List[bool] = []
        self.started_at: datetime | None = None
        self.frozen: bool = False

    def start_session(self):
        """새로운 측정 세션 시작 (기존 데이터 초기화)"""
        self.pi.clear(); self.rmssd.clear(); self.valid_flags.clear()
        self.started_at = datetime.now(timezone.utc)
        self.frozen = False

    def update(self, pi: float, rmssd: float, valid: bool, max_keep: int = 600):
        """72초 이내 유효 데이터만 누적, 이후에는 freeze"""
        if self.frozen:
            return

        # 세션 시작 전이면 자동 시작
        if self.started_at is None:
            self.start_session()

        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        if elapsed > SESSION_WINDOW_SEC:
            self.frozen = True
            return  # 세션 종료 → 이후 업데이트 안 함

        # 유효 청크만 누적
        self.valid_flags.append(bool(valid))
        if valid and np.isfinite(pi) and np.isfinite(rmssd):
            self.pi.append(float(pi))
            self.rmssd.append(float(rmssd))

        self.pi = self.pi[-max_keep:]
        self.rmssd = self.rmssd[-max_keep:]
        self.valid_flags = self.valid_flags[-max_keep:]

    def stats(self):
        """세션 중/후 기준 통계 (median, MAD)"""
        def robust(x, floor):
            x = np.asarray(x, dtype=float)
            x = x[np.isfinite(x)]
            if x.size == 0:
                return np.nan, floor
            if ROBUST_BASE_STATS:
                med = float(np.median(x))
                mad = float(np.median(np.abs(x - med)))
                s_raw = 1.4826 * mad
                s_used = max(s_raw, floor)
                return med, s_used
            mu = float(np.mean(x))
            sd = float(np.std(x))
            sd = floor if (not np.isfinite(sd) or sd < floor) else sd
            return mu, sd

        mu_pi, sd_pi = robust(self.pi, SD_FLOOR['PI'])
        mu_rm, sd_rm = robust(self.rmssd, SD_FLOOR['RMSSD'])
        return {"mu": {"PI": mu_pi, "RMSSD": mu_rm},
                "sd": {"PI": sd_pi, "RMSSD": sd_rm},
                "frozen": self.frozen,
                "n": len(self.pi)}

_DEVICE_STATES: Dict[str, _BaselineState] = {}
_SESSION_ACTIVE: Dict[str, bool] = {}

def _get_state(device_id: str) -> _BaselineState:
    st = _DEVICE_STATES.get(device_id)
    if st is None:
        st = _BaselineState()
        _DEVICE_STATES[device_id] = st
    return st

def start_baseline_session(device_id: str):
    st = _get_state(device_id)
    # 명시적으로 세션 시작 (started_at 초기화 & frozen 해제)
    st.start_session()

    # 이전 값 정리 (선택: start_session 이 이미 clear하지만, 중복 방지 차원에서 유지해도 무해)
    st.pi.clear(); st.rmssd.clear(); st.valid_flags.clear()

    logger.info(f"[baseline] started new 72s session for {device_id}")
    _SESSION_ACTIVE[device_id] = True

    def finish_later():
        import time
        time.sleep(SESSION_WINDOW_SEC)   # 72초
        # 세션 종료 처리
        st.frozen = True
        _SESSION_ACTIVE[device_id] = False

        stats = st.stats()
        logger.info(f"[baseline] {device_id} μ={stats['mu']} σ={stats['sd']} n={stats['n']}")
        print(f"\n=== Baseline {device_id} ===\n"
              f"PI mean={stats['mu']['PI']:.4f}, sd={stats['sd']['PI']:.4f}\n"
              f"RMSSD mean={stats['mu']['RMSSD']:.4f}, sd={stats['sd']['RMSSD']:.4f}\n")

    threading.Thread(target=finish_later, daemon=True).start()


# 모델 로딩
_LOCK = threading.Lock()
_MODEL = None      # (clf, base_thr)
_MODEL_NAME = None

def load_ir_model(path: str):
    print("[IR_PRINT] load_ir_model ENTER path=", path)
    global _MODEL, _MODEL_NAME
    with _LOCK:
        try:
            with warnings.catch_warnings():
                try:
                    from sklearn.exceptions import InconsistentVersionWarning
                    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
                except Exception:
                    pass
                obj = joblib.load(path)
        except Exception as e:
            print("[IR_PRINT][EXC] load_ir_model:", repr(e))
            raise RuntimeError(f"IR model load failed: {type(e).__name__}: {e}")
        if isinstance(obj, dict) and 'bundle' in obj: obj = obj['bundle']
        if isinstance(obj, dict) and 'model' in obj and 'threshold' in obj:
            clf, thr = obj['model'], float(obj['threshold'])
        elif hasattr(obj, 'predict_proba'):
            clf, thr = obj, 0.5
        else:
            raise TypeError("지원되지 않는 모델 포맷: {'model','threshold'} 또는 predict_proba 필요")
        _MODEL = (clf, thr); _MODEL_NAME = os.path.basename(path)
        print(f"[ir_runtime] IR model loaded: {path} (base_thr={thr})")

# 길이 보정
def _fit_len(x: list|np.ndarray, expect_len: int) -> np.ndarray:
    arr = np.asarray(x, dtype=float).ravel()
    if arr.size > expect_len: arr = arr[:expect_len]
    elif arr.size < expect_len: arr = np.concatenate([arr, np.zeros(expect_len - arr.size, dtype=float)], axis=0)
    return arr

# 단일 12초 청크 추론
def predict_chunk(device_id: str, ir_chunk: list|np.ndarray, sr: int = SR) -> dict:
    print("[IR_PRINT] predict_chunk ENTER dev=", device_id,
          " type=", (type(ir_chunk).__name__ if ir_chunk is not None else None),
          " haslen=", (hasattr(ir_chunk, "__len__") and len(ir_chunk)))
    try:
        print("[IR_PRINT] logger name=", logger.name,
              " eff=", logger.getEffectiveLevel(),
              " ownlvl=", logger.level,
              " handlers=", len(logger.handlers),
              " propagate=", logger.propagate)
    except Exception:
        pass

    logger.info("[IR_ENTER] dev=%s type=%s haslen=%s",
                device_id, type(ir_chunk).__name__ if ir_chunk is not None else "NoneType",
                (hasattr(ir_chunk, "__len__") and len(ir_chunk)))
    if _MODEL is None:
        print("[IR_PRINT] EARLY_RETURN: model_not_loaded")
        return {"prob": None, "label": None, "valid": False, "pi": None, "rmssd": None, "thr": None, "error": "model_not_loaded"}

    clf, base_thr = _MODEL
    expect_len = int(sr * CHUNK_SEC)
    x = _fit_len(ir_chunk, expect_len) if ir_chunk is not None else np.array([], dtype=float)
    if x.size == 0:
        print("[IR_PRINT] EARLY_RETURN: empty_input ir_is_none=", (ir_chunk is None))
        return {"prob": None, "label": None, "valid": False, "pi": None, "rmssd": None, "thr": float(base_thr), "error": "empty_input"}

    print("[IR_PRINT] BEFORE_DET len=", x.size, " sr=", sr)
    valid, peaks, bpm, ac, m = _detect_peaks_ir(x, sr)
    pi, rmssd = _compute_pi_rmssd(x, sr, m)
    print("[IR_PRINT] AFTER_DET valid=", valid, " bpm=", bpm, " peaks=", len(peaks), " PI=", pi, " RMSSD=", rmssd)

    if _SESSION_ACTIVE.get(device_id):
        st = _get_state(device_id);
        st.update(pi, rmssd, valid)
        mid = st.stats();
        n_mid = mid["n"]

        # z-score 계산
        def z(v, mu, sd):
            if not np.isfinite(v) or not np.isfinite(mu) or not np.isfinite(sd) or sd == 0.0: return np.nan
            return float(np.clip((v - mu) / sd, -ZSCORE_CLIP, ZSCORE_CLIP))

        pi_z, rm_z = z(pi, mid["mu"]["PI"], mid["sd"]["PI"]), z(rmssd, mid["mu"]["RMSSD"], mid["sd"]["RMSSD"])

        # 수집 중에도 항상 확률 반환
        prob, label, thr_used, err = _safe_predict(_MODEL[0], pi_z, rm_z, _MODEL[1], during_base=True)
        return {
            "label": label if err is None else None,
            "prob": prob,
            "valid": bool(valid),
            "pi": float(pi) if np.isfinite(pi) else None,
            "rmssd": float(rmssd) if np.isfinite(rmssd) else None,
            "thr": float(thr_used),
            "error": err or "collecting_baseline",
            "baseline": {"n": n_mid, "mu": mid["mu"], "sd": mid["sd"], "frozen": mid["frozen"]},
            "msg": "collecting baseline (predicted)"
        }

    try:
        logger.info("[IR_CHUNK] dev=%s len=%d valid=%s bpm=%.1f peaks=%d PI=%.4f RMSSD=%.4f",
                    device_id, x.size, bool(valid),
                    (bpm if np.isfinite(bpm) else float("nan")),
                    len(peaks),
                    (pi if np.isfinite(pi) else float("nan")),
                    (rmssd if np.isfinite(rmssd) else float("nan")))
    except Exception:
        pass

    st = _get_state(device_id or "_default_")
    st.update(pi, rmssd, valid)
    stats = st.stats()

    def z(v, mu, sd):
        if not np.isfinite(v) or not np.isfinite(mu) or not np.isfinite(sd) or sd == 0.0: return np.nan
        zval = (v - mu) / sd
        if ZSCORE_CLIP is not None: zval = np.clip(zval, -ZSCORE_CLIP, ZSCORE_CLIP)
        return float(zval)

    pi_z, rmssd_z = z(pi, stats["mu"]["PI"], stats["sd"]["PI"]), z(rmssd, stats["mu"]["RMSSD"], stats["sd"]["RMSSD"])
    print("[IR_PRINT] ZSCORES pi_z=", pi_z, " rmssd_z=", rmssd_z,
          " mu_PI=", stats["mu"]["PI"], " sd_PI=", stats["sd"]["PI"],
          " mu_RMSSD=", stats["mu"]["RMSSD"], " sd_RMSSD=", stats["sd"]["RMSSD"])

    try:
        logger.info("[IR_Z] dev=%s pi_z=%s rmssd_z=%s (mu_PI=%.4f sd_PI=%.4f mu_RMSSD=%.4f sd_RMSSD=%.4f)",
                    device_id,
                    ("%.3f" % pi_z) if np.isfinite(pi_z) else "nan",
                    ("%.3f" % rmssd_z) if np.isfinite(rmssd_z) else "nan",
                    stats["mu"]["PI"], stats["sd"]["PI"], stats["mu"]["RMSSD"], stats["sd"]["RMSSD"])
    except Exception:
        pass

    prob = None; label = None; thr_used = min(0.99, float(base_thr) + DELTA_THR)
    if valid and np.isfinite(pi_z) and np.isfinite(rmssd_z):
        X = np.array([[pi_z, rmssd_z]], dtype=float)
        try:
            p1 = clf.predict_proba(X)[:, 1][0]
        except Exception as e:
            print("[IR_PRINT][EXC] predict_proba:", repr(e))
            logger.info("[IR_PRED] dev=%s PRED_FAIL thr=%.4f err=%s", device_id, thr_used, repr(e))
            return {"prob": None, "label": None, "valid": bool(valid), "pi": float(pi) if np.isfinite(pi) else None, "rmssd": float(rmssd) if np.isfinite(rmssd) else None, "thr": float(thr_used), "error": "predict_failed"}
        prob = float(p1); label = int(prob >= thr_used); err = None
        print("[IR_PRINT] PRED prob=", prob, " thr=", thr_used, " label=", label)
        try:
            logger.info("[IR_PRED] dev=%s prob=%.4f thr=%.4f label=%d", device_id, prob, thr_used, label)
        except Exception:
            pass
    else:
        reasons = []
        if not valid: reasons.append("invalid_chunk")
        if not np.isfinite(pi_z) or not np.isfinite(rmssd_z): reasons.append("zscore_unavailable")
        err = "|".join(reasons) if reasons else "unknown"
        print("[IR_PRINT] PRED_SKIP reasons=", err, " valid=", valid, " pi_z=", pi_z, " rmssd_z=", rmssd_z)
        try:
            logger.info("[IR_PRED] dev=%s SKIP thr=%.4f reasons=%s (valid=%s pi_z=%s rmssd_z=%s)",
                        device_id, thr_used, err, bool(valid),
                        ("%.3f" % pi_z) if np.isfinite(pi_z) else "nan",
                        ("%.3f" % rmssd_z) if np.isfinite(rmssd_z) else "nan")
        except Exception:
            pass

    return {"prob": prob, "label": label, "valid": bool(valid), "pi": float(pi) if np.isfinite(pi) else None, "rmssd": float(rmssd) if np.isfinite(rmssd) else None, "thr": float(thr_used), "error": err}

def infer_ir(*args, **kwargs) -> dict:
    try:
        if len(args) == 1:
            ir_values = args[0]; device_id = kwargs.get("device_id") or "_legacy_"
        elif len(args) >= 2:
            device_id, ir_values = args[0], args[1]
        else:
            raise TypeError("infer_ir(ir_values) 또는 infer_ir(device_id, ir_values) 형식")
        print("[IR_PRINT] infer_ir wrapper device_id=", device_id,
              " len=", (len(ir_values) if hasattr(ir_values, "__len__") else None))
        out = predict_chunk(device_id, ir_values, sr=SR)
        out.setdefault("pi", out.get("pi")); out.setdefault("rmssd", out.get("rmssd")); out.setdefault("thr", out.get("thr"))
        out["error"] = None if out.get("valid") else "invalid_or_insufficient_features"
        return out
    except Exception as e:
        print("[IR_PRINT][EXC] infer_ir:", repr(e))
        return {"prob": None, "label": None, "thr": None, "valid": False, "pi": None, "rmssd": None, "error": repr(e)}

__all__ = ["SR","CHUNK_SEC","DETECT_MODE","BP_LO","BP_HI","load_ir_model","predict_chunk","infer_ir"]

