"""
nppg 앱의 핵심 추론 모듈.

동작 원리:
  - test_code.py를 수정 없이 동적 import합니다 (조건 6).
  - ppg_green만 사용합니다 (조건 8).
  - 디바이스별로 ppg_green 샘플을 서버 메모리에 누적합니다.
  - 베이스라인 90초 수집 후 mu/sd를 고정합니다 (조건 5).
  - 이후 누적 데이터 전체를 test_code.py 흐름 그대로 추론합니다.
  - 결과는 기존 IR_HOLDING 구조와 호환되게 반환합니다.
"""

from __future__ import annotations
import sys
import importlib.util
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional


import numpy as np
import torch
from django.conf import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# test_code.py 동적 import
# nppg/ 폴더 안에 test_code.py를 복사해두면 자동으로 로드됩니다.
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
_TEST_CODE_PATH = Path(__file__).resolve().parent / "test_code.py"

_tc        = None
_TC_LOADED = False

def _import_test_code():
    global _tc, _TC_LOADED
    if _TC_LOADED:
        return
    spec = importlib.util.spec_from_file_location("nppg_test_code", str(_TEST_CODE_PATH))
    mod  = importlib.util.module_from_spec(spec)
    
    # ★ 이 한 줄 추가: sys.modules에 등록해야 @dataclass가 정상 동작합니다.
    import sys
    sys.modules["nppg_test_code"] = mod
    
    spec.loader.exec_module(mod)
    _tc        = mod
    _TC_LOADED = True
    logger.info("[nppg] test_code.py loaded from %s", _TEST_CODE_PATH)


try:
    _import_test_code()
except Exception as e:
    import traceback
    print(f"[nppg] CRITICAL: test_code.py load failed: {e}")
    traceback.print_exc()
# ─────────────────────────────────────────────────────────────────────────────
# .pt 모델 로딩
# 경로: /Server/media/models/ppg/best_c_stream_change_model.pt
# 서버 시작 후 첫 추론 요청 시 한 번만 로드됩니다 (lazy loading).
# ─────────────────────────────────────────────────────────────────────────────
MODEL_PATH   = BASE_DIR / "media" / "models" / "ppg" / "best_c_stream_change_model.pt"
_MODEL_LOCK  = threading.Lock()
_MODEL       = None   # (torch_model, context_len, thr, hi, lo)
_MODEL_READY = False

def _load_model_once() -> bool:
    global _MODEL, _MODEL_READY
    if _MODEL_READY:
        return True
    with _MODEL_LOCK:
        if _MODEL_READY:
            return True
        if not _TC_LOADED:
            logger.warning("[nppg] test_code not loaded")
            return False
        if not MODEL_PATH.exists():
            logger.warning("[nppg] model file not found: %s", MODEL_PATH)
            return False
        try:
            ckpt = torch.load(str(MODEL_PATH), map_location="cpu")
            cfg  = ckpt["config"]

            context_len = int(cfg["context_len"])
            thr = float(cfg.get("threshold", 0.5))
            # test_code.py main()의 hi/lo 계산식과 동일합니다.
            hi  = max(0.55, thr)
            lo  = min(0.45, thr - 0.1)

            # test_code.py의 CausalTransformerBinary를 수정 없이 그대로 사용합니다.
            model = _tc.CausalTransformerBinary(
                input_dim   = len(_tc.BEAT_FEATURES),
                d_model     = int(cfg["d_model"]),
                nhead       = int(cfg["nhead"]),
                num_layers  = int(cfg["num_layers"]),
                dropout     = float(cfg["dropout"]),
                context_len = context_len,
            )
            model.load_state_dict(ckpt["model_state"])
            model.eval()

            _MODEL = (model, context_len, thr, hi, lo)
            _MODEL_READY = True
            logger.info(
                "[nppg] Model loaded. context_len=%d thr=%.3f hi=%.3f lo=%.3f",
                context_len, thr, hi, lo,
            )
            return True
        except Exception as e:
            logger.exception("[nppg] model load failed: %s", e)
            return False

def get_model_status() -> Dict[str, Any]:
    """API나 로그에서 모델 상태 확인용입니다."""
    return {
        "ready":     _MODEL_READY,
        "path":      str(MODEL_PATH),
        "exists":    MODEL_PATH.exists(),
        "tc_loaded": _TC_LOADED,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 디바이스별 스트리밍 상태 (서버 메모리에만 유지)
# ─────────────────────────────────────────────────────────────────────────────
BASELINE_SEC = 90.0
SMOOTH_WIN   = 5
MIN_RUN      = 3


class _DeviceState:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        # ppg_green 원시 샘플 누적 버퍼
        self.green_buf: List[float] = []
        # 베이스라인 90초 수집 후 계산되는 정규화 통계
        self.mu: Optional[np.ndarray] = None
        self.sd: Optional[np.ndarray] = None
        # True가 되면 mu/sd가 고정되고 추론을 시작합니다.
        self.baseline_frozen: bool = False
        # 베이스라인 시작 시각
        self.baseline_started_at: Optional[float] = None
        # 히스테리시스 smoothing을 위한 확률 히스토리
        self.prob_history: List[float] = []


_STATES: Dict[str, _DeviceState] = {}
_STATES_LOCK = threading.Lock()

def _get_state(device_id: str) -> _DeviceState:
    with _STATES_LOCK:
        if device_id not in _STATES:
            _STATES[device_id] = _DeviceState()
        return _STATES[device_id]


def start_baseline_session(device_id: str):
    """
    '측정 시작' 버튼 클릭 시 호출됩니다.
    디바이스 상태를 초기화하고 90초 타이머를 시작합니다.
    """
    st = _get_state(device_id)
    with st._lock:
        st.reset()
        st.baseline_started_at = time.time()
    logger.info("[nppg] baseline started for %s", device_id)


def get_baseline_status(device_id: str) -> Dict[str, Any]:
    """프런트 progress bar 갱신용 상태를 반환합니다."""
    st = _get_state(device_id)
    with st._lock:
        if st.baseline_started_at is None:
            return {"active": False, "frozen": False, "elapsed": 0.0, "total": BASELINE_SEC}
        elapsed = min(time.time() - st.baseline_started_at, BASELINE_SEC)
        return {
            "active":  not st.baseline_frozen,
            "frozen":  st.baseline_frozen,
            "elapsed": round(elapsed, 1),
            "total":   BASELINE_SEC,
        }


def _freeze_baseline(st: _DeviceState):
    """
    90초 경과 시 베이스라인 통계를 계산하고 고정합니다.
    st._lock 안에서 호출됩니다.
    실패해도 frozen=True로 설정해 무한 대기를 방지합니다.
    """
    ref_n   = int(BASELINE_SEC * _tc.FS)
    ref_raw = np.array(st.green_buf[:ref_n], dtype=float)
    try:
        ref_bt  = _tc.process_raw_to_beat_table(ref_raw, fs=_tc.FS)
        ref_seq = _tc.beat_table_to_seq(ref_bt)
        if len(ref_seq) >= 5:
            st.mu, st.sd = _tc.compute_baseline_stats(ref_seq)
            logger.info("[nppg] baseline frozen. beats=%d", len(ref_seq))
        else:
            logger.warning("[nppg] baseline too short: %d beats", len(ref_seq))
    except Exception as e:
        logger.error("[nppg] baseline freeze failed: %s", e)
    finally:
        st.baseline_frozen = True


def run_inference(device_id: str, ppg_green: List[float]) -> Dict[str, Any]:
    """
    test_code.py의 main() 스트리밍 루프를 그대로 구현합니다.
    
    test_code.py main()과 동일한 흐름:
      1. 전체 누적 버퍼를 ref(90초)와 qry로 분리
      2. ref로 beat feature 추출 → 베이스라인 통계 계산
      3. qry로 beat feature 추출 → 정규화
      4. ref_norm + qry_norm을 이어붙여 stream 구성
      5. beat 단위로 sliding window 추론 (test_code.py 루프와 동일)
      6. moving_average + hysteresis_labels 적용
      7. 마지막 beat의 prob/label 반환
    """
    if not _load_model_once():
        return _err("model_not_loaded")

    model, context_len, thr, hi, lo = _MODEL
    st = _get_state(device_id)

    with st._lock:
        # ── 1) ppg_green을 누적 버퍼에 추가 ──────────────────────────
        st.green_buf.extend(ppg_green)
        MAX_BUF = int((BASELINE_SEC + 300) * _tc.FS)
        if len(st.green_buf) > MAX_BUF:
            st.green_buf = st.green_buf[-MAX_BUF:]

        # ── 2) 베이스라인 경과 시간 계산 및 freeze 처리 ──────────────
        baseline_elapsed = 0.0
        baseline_active  = False
        if st.baseline_started_at is not None:
            elapsed = time.time() - st.baseline_started_at
            baseline_elapsed = min(elapsed, BASELINE_SEC)
            if elapsed < BASELINE_SEC:
                baseline_active = True
            elif not st.baseline_frozen:
                _freeze_baseline(st)

        # ── 3) 베이스라인 미완료 → 추론 스킵 ────────────────────────
        if not st.baseline_frozen:
            return {
                **_err("collecting_baseline"),
                "baseline_active":  baseline_active,
                "baseline_elapsed": baseline_elapsed,
            }

        if st.mu is None or st.sd is None:
            return _err("baseline_stats_unavailable")

        # ── 4) ref(90초)와 qry 분리 ──────────────────────────────────
        # test_code.py: ref_raw = raw[:ref_n], qry_raw = raw[ref_n:]
        ref_n   = int(BASELINE_SEC * _tc.FS)
        ref_raw = np.array(st.green_buf[:ref_n], dtype=float)
        qry_raw = np.array(st.green_buf[ref_n:], dtype=float)

        if len(qry_raw) < int(_tc.FS * 5):
            return _err("insufficient_query_data")

        # ── 5) beat feature 추출 ──────────────────────────────────────
        # test_code.py와 동일하게 ref/qry 각각 추출
        try:
            ref_bt  = _tc.process_raw_to_beat_table(ref_raw, fs=_tc.FS)
            qry_bt  = _tc.process_raw_to_beat_table(qry_raw, fs=_tc.FS)
            ref_seq = _tc.beat_table_to_seq(ref_bt)
            qry_seq = _tc.beat_table_to_seq(qry_bt)
        except Exception as e:
            return _err(f"beat_extract_failed: {e}")

        if len(ref_seq) < context_len + 2:
            return _err("ref_beat_sequence_too_short")
        if len(qry_seq) < 3:
            return _err("insufficient_beats")

        # ── 6) 정규화 ─────────────────────────────────────────────────
        # test_code.py: mu, sd = compute_baseline_stats(ref_seq)
        # 베이스라인 freeze 시점의 mu/sd를 사용합니다.
        ref_norm = _tc.normalize_seq(ref_seq, st.mu, st.sd)
        qry_norm = _tc.normalize_seq(qry_seq, st.mu, st.sd)

        # ── 7) stream 구성 ────────────────────────────────────────────
        # test_code.py: stream = np.concatenate([ref_norm, qry_norm])
        stream   = np.concatenate([ref_norm, qry_norm], axis=0)
        boundary = len(ref_norm)

        # ── 8) beat 단위 sliding window 추론 ─────────────────────────
        # test_code.py의 루프와 완전히 동일합니다.
        # for t in range(max(context_len, boundary), len(stream) + 1):
        #     xs.append(stream[t - context_len:t])
        xs = []
        for t in range(max(context_len, boundary), len(stream) + 1):
            xs.append(stream[t - context_len:t])

        if not xs:
            return _err("insufficient_beats")

        xs_tensor = torch.from_numpy(
            np.stack(xs, axis=0).astype(np.float32)
        )  # (N_beats, context_len, 5)

        with torch.no_grad():
            logits = model(xs_tensor)
            prob   = torch.sigmoid(logits).cpu().numpy()  # (N_beats,)

        
        # ── 9) prob 히스토리 누적 → moving_average + hysteresis ──────────
        # 매 청크의 마지막 beat prob을 히스토리에 추가합니다.
        # 이렇게 하면 청크 간 hysteresis 맥락이 유지됩니다.
        last_raw_prob = float(prob[-1])
        st.prob_history.append(last_raw_prob)
        if len(st.prob_history) > 300:
            st.prob_history = st.prob_history[-300:]

        # 히스토리 전체에 moving_average + hysteresis 적용
        hist_arr    = np.array(st.prob_history, dtype=float)
        smooth_arr  = _tc.moving_average(hist_arr, SMOOTH_WIN)
        pred_labels = _tc.hysteresis_labels(smooth_arr, hi=hi, lo=lo, min_run=MIN_RUN)

        last_prob   = last_raw_prob
        last_smooth = float(smooth_arr[-1])
        last_label  = int(pred_labels[-1])# ── 8) beat 단위 sliding window 추론 ─────────────────────────
        xs = []
        for t in range(max(context_len, boundary), len(stream) + 1):
            xs.append(stream[t - context_len:t])

        if not xs:
            return _err("insufficient_beats")

        xs_tensor = torch.from_numpy(
            np.stack(xs, axis=0).astype(np.float32)
        )

        with torch.no_grad():
            logits = model(xs_tensor)
            prob   = torch.sigmoid(logits).cpu().numpy()  # 이번 청크의 모든 beat prob

        # ── 9) 이번 청크의 beat prob을 히스토리에 누적 ───────────────
        # test_code.py처럼 beat 1개씩 추가하는 효과를 냅니다.
        # 단, 청크 전체를 한 번에 계산한 뒤 히스토리에 이어붙입니다.
        # 청크 경계에서 hysteresis가 끊기지 않도록 히스토리 전체에 적용합니다.
        st.prob_history.extend(prob.tolist())
        # 너무 오래된 데이터는 제거 (최근 300개 beat = 약 4~5분)
        if len(st.prob_history) > 300:
            st.prob_history = st.prob_history[-300:]

        # ── 10) 히스토리 전체에 moving_average + hysteresis ──────────
        # test_code.py와 동일하게 전체 beat 시퀀스에 한 번만 적용합니다.
        hist_arr    = np.array(st.prob_history, dtype=float)
        smooth_arr  = _tc.moving_average(hist_arr, SMOOTH_WIN)
        pred_labels = _tc.hysteresis_labels(smooth_arr, hi=hi, lo=lo, min_run=MIN_RUN)

        # 마지막 beat의 결과를 현재 상태로 반환합니다.
        last_prob   = float(prob[-1])
        last_smooth = float(smooth_arr[-1])
        last_label  = int(pred_labels[-1])

        return {
            "prob":             last_prob,
            "smooth_prob":      last_smooth,
            "label":            last_label,
            "valid":            True,
            "model":            "nppg",
            "thr":              thr,
            "hi":               hi,
            "lo":               lo,
            "n_beats":          len(st.prob_history),  # 누적 beat 수
            "baseline_active":  False,
            "baseline_elapsed": baseline_elapsed,
            "error":            None,
        }
        

def _err(msg: str) -> Dict[str, Any]:
    """추론 실패 시 IR_HOLDING 호환 구조로 반환합니다."""
    return {
        "prob": None, "smooth_prob": None, "label": None,
        "valid": False, "model": "nppg",
        "baseline_active": False, "baseline_elapsed": 0.0,
        "error": msg,
    }