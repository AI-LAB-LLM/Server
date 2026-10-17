from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from scipy.signal import butter, filtfilt, find_peaks
from .models import (
    ApneaSession,
    ApneaDeviceConfig,
)

logger = logging.getLogger(__name__)

FS               = 25.0
SHORT_GAP_THRESH = 3
BEAT_FEATURES    = ["FO_SP_s", "Downstroke_vel", "HR_bpm", "RR_s", "QI"]
BASELINE_PACKETS = 3
MIN_BASELINE_SAMPLES = int(FS * 30)


def butter_bandpass(low, high, fs, order=3):
    nyq    = 0.5 * fs
    low_n  = max(1e-6, low / nyq)
    high_n = min(0.999, high / nyq)
    return butter(order, [low_n, high_n], btype="band")


def bandpass_filter(x, fs, low=0.5, high=8.0, order=3):
    b, a = butter_bandpass(low, high, fs, order)
    if len(x) < 30:
        return x.astype(float)
    return filtfilt(b, a, x)


def robust_minmax(x, p_low=1.0, p_high=99.0):
    x  = np.asarray(x, dtype=float)
    lo = np.nanpercentile(x, p_low)
    hi = np.nanpercentile(x, p_high)
    if not np.isfinite(lo) or not np.isfinite(hi) or abs(hi - lo) < 1e-12:
        return np.zeros_like(x, dtype=float)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def zero_crossings(x):
    return np.where(np.diff(np.sign(x)) != 0)[0]


def adaptive_prominence(y, base_prom=0.03):
    q1, q3 = np.nanquantile(y, [0.25, 0.75])
    return base_prom * max(1e-6, float(q3 - q1))


def detect_sp(ppg_norm, fs, min_rr_sec=0.45, base_prom=0.03):
    prom     = adaptive_prominence(ppg_norm, base_prom)
    peaks, _ = find_peaks(
        ppg_norm,
        distance=max(1, int(min_rr_sec * fs)),
        prominence=prom
    )
    return peaks.astype(int)


def refine_sp_indices(ppg_norm, peaks, fs, window_sec=0.08):
    if window_sec <= 0.0 or peaks.size == 0:
        return peaks
    w, n    = max(1, int(window_sec * fs)), len(ppg_norm)
    refined = []
    for pk in peaks:
        i1, i2 = max(0, pk - w), min(n, pk + w + 1)
        refined.append(i1 + int(np.nanargmax(ppg_norm[i1:i2])))
    return np.array(refined, dtype=int)


@dataclass
class Fiducials:
    FO: Optional[int]
    SP: Optional[int]
    DN: Optional[int]
    DP: Optional[int]


def find_onset_before_peak(ppg_norm, ppg_diff, i_peak, fs, search_sec=0.6):
    i0   = int(max(0, i_peak - search_sec * fs))
    seg  = ppg_diff[i0:i_peak + 1]
    zc   = zero_crossings(seg)
    cand = [i0 + k for k in zc if k < len(seg) - 1 and seg[k] < 0 <= seg[k + 1]]
    if cand:
        return int(cand[-1])
    return int(i0 + int(np.nanargmin(ppg_norm[i0:i_peak + 1])))


# def find_notch_after_peak(ppg_norm, i_peak, fs, search_lo=0.06, search_hi=0.40):
#     i1 = i_peak + int(search_lo * fs)
#     i2 = min(len(ppg_norm) - 1, i_peak + int(search_hi * fs))
#     if i1 >= i2:
#         return None
#     mins, _ = find_peaks(-ppg_norm[i1:i2], prominence=0.005)
#     return int(i1 + mins[0]) if mins.size > 0 else None

def find_notch_after_peak(
    ppg_norm,
    i_peak,
    fs,
    search_lo=0.06,
    search_hi=0.40,
):
    i1 = i_peak + int(search_lo * fs)
    i2 = min(
        len(ppg_norm) - 1,
        i_peak + int(search_hi * fs),
    )

    if i1 >= i2:
        print(
            f"[DN_FAIL] invalid range "
            f"peak={i_peak} "
            f"i1={i1} i2={i2}"
        )
        return None

    segment = ppg_norm[i1:i2]

    mins, props = find_peaks(
        -segment,
        prominence=0.005,
    )

    if mins.size == 0:
        print(
            "[DN_FAIL] "
            f"peak={i_peak} "
            f"range={i1}:{i2} "
            f"n={len(segment)} "
            f"min={float(np.min(segment)):.5f} "
            f"max={float(np.max(segment)):.5f} "
            f"values={np.round(segment, 4).tolist()}"
        )
        return None

    dn = int(i1 + mins[0])

    print(
        "[DN_OK] "
        f"peak={i_peak} "
        f"dn={dn} "
        f"delay={(dn - i_peak) / fs:.3f}s "
        f"prominence={float(props['prominences'][0]):.5f}"
    )

    return dn

def _check_fiducial_order(fid):
    order  = ["FO", "SP", "DN", "DP"]
    points = [
        (n, v)
        for n, v in [("FO", fid.FO), ("SP", fid.SP), ("DN", fid.DN), ("DP", fid.DP)]
        if v is not None
    ]
    prev = -1
    for name, _ in sorted(points, key=lambda x: x[1]):
        curr = order.index(name)
        if curr <= prev:
            return False
        prev = curr
    return True


def clean_fiducials(fid, fs):
    c = Fiducials(FO=fid.FO, SP=fid.SP, DN=fid.DN, DP=fid.DP)
    if c.FO is not None and c.SP is not None and (c.SP - c.FO) / fs < 0.08:
        c.FO = None
    if c.SP is not None and c.DN is not None and (c.DN - c.SP) / fs < 0.05:
        c.DN = None
    if not _check_fiducial_order(c):
        c.DN = None
    return c


def _is_local_minimum(ppg, idx, window_samples=3):
    if idx is None:
        return False
    start = max(0, idx - window_samples)
    end   = min(len(ppg), idx + window_samples + 1)
    return ppg[idx] <= np.min(ppg[start:end]) * 1.05


def quality_index(fid, feats, ppg_norm, fs):
    if fid.FO is None or fid.SP is None or not _check_fiducial_order(fid):
        return 0.0
    score   = 1.0
    amp_sp  = feats.get("Amp_SP", np.nan)
    t_fo_sp = feats.get("FO_SP_time_s", np.nan)
    if math.isnan(amp_sp) or amp_sp <= 0:
        score -= 0.4
    if not (0.1 <= t_fo_sp <= 0.6):
        score -= 0.3
    if math.isnan(feats.get("Downstroke_vel", np.nan)):
        score -= 0.05
    if fid.DN is not None and not _is_local_minimum(ppg_norm, fid.DN, int(0.04 * fs)):
        score -= 0.2
    return max(0.0, float(score))


def impute_column_short(col, thresh=SHORT_GAP_THRESH):
    col  = col.copy().astype(float)
    n, i = len(col), 0
    while i < n:
        if not np.isfinite(col[i]):
            j = i
            while j < n and not np.isfinite(col[j]):
                j += 1
            gap = j - i
            if gap <= thresh:
                vl = col[i - 1] if i > 0 and np.isfinite(col[i - 1]) else 0.0
                vr = col[j] if j < n and np.isfinite(col[j]) else 0.0
                for k in range(gap):
                    col[i + k] = vl + (vr - vl) * (k + 1) / (gap + 1)
            i = j
        else:
            i += 1
    return col


def process_raw_to_beat_table_offline(raw, fs=FS):
    import pandas as pd

    raw = np.asarray(raw, dtype=float)
    raw = raw[np.isfinite(raw)]
    if len(raw) < int(fs * 5):
        return pd.DataFrame()

    ppg      = bandpass_filter(raw, fs=fs, low=0.5, high=8.0)
    ppg_norm = robust_minmax(ppg)
    diff     = np.gradient(ppg_norm) * fs

    sp_idx = detect_sp(ppg_norm, fs, min_rr_sec=0.45, base_prom=0.03)
    sp_idx = refine_sp_indices(ppg_norm, sp_idx, fs, window_sec=0.08)

    if len(sp_idx) < 5:
        return pd.DataFrame()

    rows, prev_sp = [], None

    for i, sp in enumerate(sp_idx):
        fo  = find_onset_before_peak(ppg_norm, diff, sp, fs)
        dn  = find_notch_after_peak(ppg_norm, sp, fs)
        fid = clean_fiducials(Fiducials(FO=fo, SP=sp, DN=dn, DP=None), fs)

        def tdiff(a, b):
            return max(0.0, (b - a) / fs) if (a is not None and b is not None) else np.nan

        fo_sp_s = tdiff(fid.FO, fid.SP)

        amp_sp = (
            float(ppg_norm[fid.SP] - ppg_norm[fid.FO])
            if (fid.FO is not None and fid.SP is not None)
            else np.nan
        )

        dn_v = (
            float(np.nanmin(diff[fid.SP: fid.DN + 1]))
            if (fid.SP is not None and fid.DN is not None and fid.DN > fid.SP)
            else np.nan
        )

        qi = quality_index(
            fid,
            {
                "FO_SP_time_s": fo_sp_s,
                "Amp_SP": amp_sp,
                "Downstroke_vel": dn_v,
            },
            ppg_norm,
            fs,
        )

        rr_s = np.nan if prev_sp is None else (fid.SP - prev_sp) / fs
        hr_bpm = 60.0 / rr_s if (np.isfinite(rr_s) and rr_s > 0) else np.nan
        prev_sp = fid.SP

        rows.append({
            "FO_SP_s": fo_sp_s,
            "Downstroke_vel": dn_v,
            "HR_bpm": hr_bpm,
            "RR_s": rr_s,
            "QI": qi,
            "sp_sample": int(fid.SP),
            "beat_idx": i,
        })

    return pd.DataFrame(rows)


def beat_table_to_seq_for_ref(beat_df):
    if beat_df.empty or len(beat_df) < 5:
        return np.empty((0, len(BEAT_FEATURES)), dtype=np.float32)

    df = beat_df.copy()
    for col in BEAT_FEATURES:
        df[col] = impute_column_short(df[col].to_numpy(float))

    return df[BEAT_FEATURES].to_numpy(dtype=np.float32)


def compute_baseline_stats(base_seq):
    mu = np.nanmean(base_seq, axis=0)
    sd = np.nanstd(base_seq, axis=0)
    sd = np.where(sd > 1e-6, sd, 1.0)
    return mu, sd


def normalize_feature_vector(feat_vec, mu, sd):
    out = (feat_vec - mu) / sd
    return np.where(np.isfinite(out), out, 0.0).astype(np.float32)


class CausalFeatureImputer:
    def __init__(self, feature_cols, max_gap=3):
        self.feature_cols = feature_cols
        self.max_gap      = max_gap
        self.last_valid   = {c: np.nan for c in feature_cols}
        self.gap_count    = {c: 0 for c in feature_cols}

    def transform_one(self, feat):
        out = dict(feat)

        for c in self.feature_cols:
            v = out.get(c, np.nan)

            if np.isfinite(v):
                self.last_valid[c] = float(v)
                self.gap_count[c]  = 0
            else:
                self.gap_count[c] += 1

                if np.isfinite(self.last_valid[c]) and self.gap_count[c] <= self.max_gap:
                    out[c] = self.last_valid[c]
                else:
                    out[c] = np.nan

        return out


class CausalTransformerBinary(nn.Module):
    def __init__(self, input_dim, d_model, nhead, num_layers, dropout, context_len):
        super().__init__()

        self.input_proj = nn.Linear(input_dim, d_model)
        self.pos_emb    = nn.Embedding(context_len, d_model)

        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )

        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)

        causal = torch.triu(
            torch.full((context_len, context_len), float("-inf")),
            diagonal=1,
        )
        self.register_buffer("causal_mask", causal)

        self.head = nn.Sequential(
            nn.Linear(d_model, d_model),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model, 1),
        )

    def forward(self, x):
        pos = torch.arange(x.shape[1], device=x.device)
        h   = self.input_proj(x) + self.pos_emb(pos)
        h   = self.encoder(h, mask=self.causal_mask)
        return self.head(h[:, -1, :]).squeeze(-1)


class RealtimeSmoother:
    def __init__(self, win=5):
        self.q = deque(maxlen=max(1, int(win)))

    def update(self, p):
        self.q.append(float(p))
        return float(np.mean(self.q))


class RealtimeHysteresis:
    def __init__(self, hi, lo, min_run):
        self.hi, self.lo, self.min_run = float(hi), float(lo), int(min_run)
        self.state = 0
        self.hi_count = 0
        self.lo_count = 0

    def update(self, p_smooth):
        if self.state == 0:
            self.hi_count = self.hi_count + 1 if p_smooth >= self.hi else 0
            if self.hi_count >= self.min_run:
                self.state = 1
                self.hi_count = 0
                self.lo_count = 0
        else:
            self.lo_count = self.lo_count + 1 if p_smooth <= self.lo else 0
            if self.lo_count >= self.min_run:
                self.state = 0
                self.hi_count = 0
                self.lo_count = 0

        return int(self.state)


class RealtimeBeatExtractor:
    def __init__(
        self,
        fs=FS,
        rolling_seconds=13.0,
        safe_margin_seconds=0.6,
    ):
        self.fs            = fs
        self.rolling_n     = int(rolling_seconds * fs)
        self.safe_margin_n = int(safe_margin_seconds * fs)


        self.raw_buffer = deque(maxlen=self.rolling_n)

        self.global_sample_idx        = -1
        self.buffer_start_global_idx  = 0
        self.last_processed_sp_global = None
        self.prev_sp_global           = None
        self.beat_idx                 = 0

    def feed_packet(self, values):
        """
        샘플을 buffer에 추가한 뒤
        패킷 전체를 한 번에 처리
        """
        for v in values:
            self.global_sample_idx += 1
            self.raw_buffer.append(float(v))

        self.buffer_start_global_idx = (
            self.global_sample_idx - len(self.raw_buffer) + 1
        )

        return self._extract_new_beats()

    def _extract_new_beats(self):
        if len(self.raw_buffer) < int(self.fs * 5):
            return []

        raw = np.asarray(self.raw_buffer, dtype=float)
        raw = raw[np.isfinite(raw)]

        if len(raw) < int(self.fs * 5):
            return []

        ppg      = bandpass_filter(raw, fs=self.fs, low=0.5, high=8.0)
        ppg_norm = robust_minmax(ppg)
        diff     = np.gradient(ppg_norm) * self.fs

        sp_local = detect_sp(ppg_norm, self.fs, min_rr_sec=0.45, base_prom=0.03)
        sp_local = refine_sp_indices(ppg_norm, sp_local, self.fs, window_sec=0.08)

        if len(sp_local) == 0:
            return []

        new_rows = []
        latest_allowed = len(raw) - 1 - self.safe_margin_n

        for sp in sp_local:
            if sp > latest_allowed:
                continue

            sp_global = self.buffer_start_global_idx + int(sp)

            if (
                self.last_processed_sp_global is not None
                and sp_global <= self.last_processed_sp_global
            ):
                continue

            rr_s = (
                np.nan
                if self.prev_sp_global is None
                else (sp_global - self.prev_sp_global) / self.fs
            )

            fo = find_onset_before_peak(
                ppg_norm, diff, int(sp), self.fs
            )
            dn  = find_notch_after_peak(ppg_norm, int(sp), self.fs)
            fid = clean_fiducials(Fiducials(FO=fo, SP=int(sp), DN=dn, DP=None), self.fs)

            def tdiff(a, b):
                return max(0.0, (b - a) / self.fs) if (a is not None and b is not None) else np.nan

            fo_sp_s = tdiff(fid.FO, fid.SP)

            amp_sp = (
                float(ppg_norm[fid.SP] - ppg_norm[fid.FO])
                if (fid.FO is not None and fid.SP is not None)
                else np.nan
            )

            dn_v = (
                float(np.nanmin(diff[fid.SP: fid.DN + 1]))
                if (fid.SP is not None and fid.DN is not None and fid.DN > fid.SP)
                else np.nan
            )

            qi = quality_index(
                fid,
                {
                    "FO_SP_time_s": fo_sp_s,
                    "Amp_SP": amp_sp,
                    "Downstroke_vel": dn_v,
                },
                ppg_norm,
                self.fs,
            )

            hr_bpm = 60.0 / rr_s if (np.isfinite(rr_s) and rr_s > 0) else np.nan

            new_rows.append({
                "FO_SP_s": fo_sp_s,
                "Downstroke_vel": dn_v,
                "HR_bpm": hr_bpm,
                "RR_s": rr_s,
                "QI": qi,
                "sp_sample": int(sp_global),
                "beat_idx": self.beat_idx,
            })

            self.beat_idx += 1
            self.prev_sp_global = sp_global
            self.last_processed_sp_global = sp_global

        return new_rows


class RealtimeApneaDetector:
    def __init__(
        self,
        model,
        context_len,
        ref_mu,
        ref_sd,
        threshold_from_train,
        hi=None,
        lo=None,
        smooth_win=5,
        min_run=3,
        device="cpu",
    ):
        self.model = model.to(device)
        self.model.eval()

        self.context_len = int(context_len)
        self.ref_mu = ref_mu.astype(np.float32)
        self.ref_sd = ref_sd.astype(np.float32)

        self.threshold_from_train = float(threshold_from_train)
        self.hi = float(hi) if hi is not None else max(0.55, self.threshold_from_train)
        self.lo = float(lo) if lo is not None else min(0.45, self.threshold_from_train - 0.1)

        self.smoother = RealtimeSmoother(win=smooth_win)
        self.hysteresis = RealtimeHysteresis(
            hi=self.hi,
            lo=self.lo,
            min_run=min_run,
        )

        self.imputer = CausalFeatureImputer(
            BEAT_FEATURES,
            max_gap=SHORT_GAP_THRESH,
        )

        self.beat_window = deque(maxlen=self.context_len)
        self.device = device

    def update_with_beat(self, beat_feat):
        imputed = self.imputer.transform_one(beat_feat)

        raw_vec = np.array(
            [imputed[c] for c in BEAT_FEATURES],
            dtype=np.float32,
        )

        norm_vec = normalize_feature_vector(
            raw_vec,
            self.ref_mu,
            self.ref_sd,
        )

        self.beat_window.append(norm_vec)

        time_sec = float(beat_feat["sp_sample"]) / FS

        if len(self.beat_window) < self.context_len:
            return {
                "time_sec": time_sec,
                "p_apnea": None,
                "p_apnea_smooth": None,
                "pred_label": None,
                "status": "warming_up",
            }

        x = np.stack(list(self.beat_window), axis=0).astype(np.float32)
        x_t = torch.from_numpy(x[None, :, :]).to(self.device)

        with torch.no_grad():
            p = torch.sigmoid(self.model(x_t)).cpu().item()

        p_smooth = self.smoother.update(p)
        label = self.hysteresis.update(p_smooth)

        return {
            "time_sec": time_sec,
            "p_apnea": float(p),
            "p_apnea_smooth": float(p_smooth),
            "pred_label": int(label),
            "status": "ok",
        }


def detect_wear_green(ppg_green, fs=FS):
    """
    기존 ppg 앱의 wear_runtime.wear_green_to_pred()를 그대로 사용.
    """
    try:
        from ppg.wear_runtime import wear_green_to_pred

        if hasattr(ppg_green, "tolist"):
            ppg_green = ppg_green.tolist()

        result = wear_green_to_pred(list(ppg_green))

        return {
            "valid": result.get("valid", False),
            "label": result.get("label"),
            "prob": result.get("prob"),
            "error": result.get("error"),
        }
    except Exception as e:
        logger.warning(f"[detect_wear_green] failed: {e}")
        return {
            "valid": False,
            "label": None,
            "prob": None,
            "error": str(e),
        }


def compute_r_ratio_series(ppg_red: list, ppg_ir: list, fs: float = FS) -> list:
    """R ratio = (AC/DC of Red) / (AC/DC of IR). 2초 윈도우, 1초 슬라이딩."""
    red  = np.asarray(ppg_red, dtype=float)
    ir   = np.asarray(ppg_ir, dtype=float)
    win  = int(fs * 2)
    step = int(fs)
    n    = min(len(red), len(ir))
    ratios = []

    for i in range(0, n - win + 1, step):
        seg_r = red[i: i + win]
        seg_i = ir[i: i + win]

        dc_r = np.nanmean(seg_r)
        dc_i = np.nanmean(seg_i)

        if dc_r < 1e-6 or dc_i < 1e-6:
            ratios.append(None)
            continue

        ac_r = float(np.sqrt(np.nanmean((seg_r - dc_r) ** 2)))
        ac_i = float(np.sqrt(np.nanmean((seg_i - dc_i) ** 2)))

        pi_r = ac_r / dc_r
        pi_i = ac_i / dc_i

        if pi_i < 1e-9:
            ratios.append(None)
            continue

        ratios.append(round(pi_r / pi_i, 4))

    return ratios


class ApneaEngine:
    _instance: Optional["ApneaEngine"] = None
    _cls_lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._model_cfg = None
        self._model_ready = False
        self._dev_lock = threading.Lock()

        self._extractors: Dict[str, RealtimeBeatExtractor] = {}
        self._detectors: Dict[str, RealtimeApneaDetector] = {}
        self._baseline_buf: Dict[str, List[float]] = {}
        self._baseline_done: Dict[str, bool] = {}
        self._packet_count: Dict[str, int] = {}
        self._baseline_active: Dict[str, bool] = {}
        self._session_pk: Dict[str, int] = {}
        # 새 baseline 실패 시 기존 baseline 복구용
        self._backup_detectors = {}
        self._backup_extractors = {}
        self._backup_baseline_done = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def load_model(self, path: str) -> bool:
        try:
            ckpt = torch.load(path, map_location="cpu")
            cfg = ckpt["config"]
            device = "cuda" if torch.cuda.is_available() else "cpu"

            model = CausalTransformerBinary(
                input_dim=len(BEAT_FEATURES),
                d_model=int(cfg["d_model"]),
                nhead=int(cfg["nhead"]),
                num_layers=int(cfg["num_layers"]),
                dropout=float(cfg["dropout"]),
                context_len=int(cfg["context_len"]),
            )

            model.load_state_dict(ckpt["model_state"])
            model.to(device).eval()

            self._model = model
            self._model_cfg = cfg
            self._model_ready = True

            logger.info(f"[ApneaEngine] model loaded: {path} device={device}")
            return True

        except Exception as e:
            logger.exception(f"[ApneaEngine] load failed: {e}")
            self._model_ready = False
            return False

    @property
    def model_ready(self):
        return self._model_ready

    @property
    def model_config(self):
        return dict(self._model_cfg) if self._model_cfg else None

    def start_session(
        self,
        device_id: str,
        session_pk: int = None,
    ):
        self._ensure_device(device_id)

        with self._dev_lock:

            # =====================================================
            # ★ 현재 정상 동작 중인 baseline 상태 백업
            # =====================================================

            self._backup_detectors[device_id] = (
                self._detectors.get(device_id)
            )

            self._backup_extractors[device_id] = (
                self._extractors.get(device_id)
            )

            self._backup_baseline_done[device_id] = (
                self._baseline_done.get(
                    device_id,
                    False,
                )
            )

            # =====================================================
            # 새 baseline 측정 시작
            # =====================================================

            self._baseline_buf[device_id] = []

            self._baseline_done[device_id] = False

            self._baseline_active[device_id] = True

            self._packet_count[device_id] = 0

            self._session_pk[device_id] = session_pk

            # 새 baseline용 extractor
            self._extractors[device_id] = (
                RealtimeBeatExtractor(
                    fs=FS,
                    rolling_seconds=13.0,
                    safe_margin_seconds=0.6,
                )
            )

            # 새 baseline 측정 중에는 inference 중지.
            # 기존 detector는 위 backup에 보관되어 있음.
            self._detectors.pop(
                device_id,
                None,
            )

        logger.info(
            "[ApneaEngine] baseline session started: "
            "device=%s session=%s "
            "previous_baseline=%s",
            device_id,
            session_pk,
            self._backup_baseline_done.get(
                device_id,
                False,
            ),
        )

    def _ensure_device(self, device_id: str):
        if device_id in self._packet_count:
            return

        with self._dev_lock:
            # 다른 thread가 먼저 만들었을 수도 있으므로 재확인
            if device_id in self._packet_count:
                return

            self._baseline_buf[device_id] = []
            self._baseline_done[device_id] = False
            self._baseline_active[device_id] = False
            self._packet_count[device_id] = 0

            self._extractors[device_id] = RealtimeBeatExtractor(
                fs=FS,
                rolling_seconds=13.0,
                safe_margin_seconds=0.6,
            )

        # DB 접근은 lock 밖에서
        restored = self.restore_selected_baseline(
            device_id
        )

        if restored:
            logger.info(
                f"[ApneaEngine] saved baseline restored: "
                f"{device_id}"
            )
        else:
            logger.info(
                f"[ApneaEngine] no saved baseline: "
                f"{device_id}"
            )

    def _fail_baseline(
        self,
        device_id: str,
        reason: str,
    ):
        logger.error(
            "[baseline] FAILED device=%s reason=%s",
            device_id,
            reason,
        )

        with self._dev_lock:

            # =====================================================
            # 새 baseline 측정 전에 사용하던 상태 가져오기
            # =====================================================

            old_detector = (
                self._backup_detectors.pop(
                    device_id,
                    None,
                )
            )

            old_extractor = (
                self._backup_extractors.pop(
                    device_id,
                    None,
                )
            )

            old_done = (
                self._backup_baseline_done.pop(
                    device_id,
                    False,
                )
            )

            # =====================================================
            # 새 baseline 측정 종료
            # =====================================================

            self._baseline_active[device_id] = False

            self._baseline_buf[device_id] = []


            # =====================================================
            # ★ 기존 baseline이 있었다면 복구
            # =====================================================

            if (
                old_done
                and old_detector is not None
            ):

                self._detectors[device_id] = (
                    old_detector
                )

                if old_extractor is not None:
                    self._extractors[device_id] = (
                        old_extractor
                    )

                self._baseline_done[device_id] = True

                logger.info(
                    "[baseline] previous baseline restored: "
                    "device=%s",
                    device_id,
                )

            # =====================================================
            # 기존 baseline 자체가 없었던 경우
            # =====================================================

            else:

                self._detectors.pop(
                    device_id,
                    None,
                )

                self._baseline_done[device_id] = False

                logger.info(
                    "[baseline] no previous baseline: "
                    "device=%s",
                    device_id,
                )

        logger.info(
            "[baseline] state after failure: "
            "device=%s active=%s done=%s detector=%s",
            device_id,
            self._baseline_active.get(device_id),
            self._baseline_done.get(device_id),
            self._detectors.get(device_id) is not None,
        )

    def _finalize_baseline(
        self,
        device_id: str,
        session_db,
    ):
        import json

        session_pk = self._session_pk.get(device_id)

        # session_pk가 없으면 전달받은 session 객체에서 확보
        if not session_pk and session_db is not None:
            session_pk = getattr(
                session_db,
                "pk",
                None,
            )

        if not session_pk:
            logger.error(
                "[baseline] %s: no session_pk available",
                device_id,
            )

        # =========================================================
        # 1. baseline raw 가져오기
        # =========================================================

        with self._dev_lock:
            raw_buffer = list(
                self._baseline_buf.get(
                    device_id,
                    [],
                )
            )

        raw = np.asarray(
            raw_buffer,
            dtype=float,
        )

        logger.info(
            "[baseline] finalize: "
            "device=%s samples=%d required=%d",
            device_id,
            len(raw),
            MIN_BASELINE_SAMPLES,
        )

        # 혹시 750개 미만에서 호출됐다면 실패
        if len(raw) < MIN_BASELINE_SAMPLES:
            self._fail_baseline(
                device_id,
                (
                    f"not enough samples: "
                    f"{len(raw)}/"
                    f"{MIN_BASELINE_SAMPLES}"
                ),
            )
            return

        # =========================================================
        # 2. Offline beat feature 추출
        # =========================================================

        try:
            bt = process_raw_to_beat_table_offline(
                raw,
                fs=FS,
            )

            ref_seq = beat_table_to_seq_for_ref(
                bt
            )

        except Exception as e:
            logger.exception(
                "[baseline] feature extraction error "
                "device=%s: %s",
                device_id,
                e,
            )

            self._fail_baseline(
                device_id,
                f"feature extraction error: {e}",
            )
            return

        # =========================================================
        # 3. beat 개수 검사
        # =========================================================

        if len(ref_seq) < 10:
            self._fail_baseline(
                device_id,
                f"too few beats: {len(ref_seq)}",
            )
            return

        # =========================================================
        # 4. baseline statistics 계산
        # =========================================================

        try:
            ref_mu, ref_sd = (
                compute_baseline_stats(
                    ref_seq
                )
            )

            ref_mu = np.asarray(
                ref_mu,
                dtype=np.float64,
            )

            ref_sd = np.asarray(
                ref_sd,
                dtype=np.float64,
            )

        except Exception as e:
            logger.exception(
                "[baseline] stats calculation error "
                "device=%s: %s",
                device_id,
                e,
            )

            self._fail_baseline(
                device_id,
                f"stats calculation error: {e}",
            )
            return

        # =========================================================
        # 5. NaN / Inf 검사
        # =========================================================

        if not np.all(
            np.isfinite(ref_mu)
        ):
            self._fail_baseline(
                device_id,
                (
                    "invalid ref_mu: "
                    f"{ref_mu.tolist()}"
                ),
            )
            return

        if not np.all(
            np.isfinite(ref_sd)
        ):
            self._fail_baseline(
                device_id,
                (
                    "invalid ref_sd: "
                    f"{ref_sd.tolist()}"
                ),
            )
            return

        # =========================================================
        # 6. 모델 설정
        # =========================================================

        cfg = self._model_cfg or {}

        context_len = int(
            cfg.get(
                "context_len",
                20,
            )
        )

        threshold = float(
            cfg.get(
                "threshold",
                0.5,
            )
        )

        device_str = (
            "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )

        # =========================================================
        # 7. detector 생성
        # =========================================================

        try:
            detector = RealtimeApneaDetector(
                model=self._model,
                context_len=context_len,
                ref_mu=ref_mu,
                ref_sd=ref_sd,
                threshold_from_train=threshold,
                device=device_str,
            )

        except Exception as e:
            logger.exception(
                "[baseline] detector creation error "
                "device=%s: %s",
                device_id,
                e,
            )

            self._fail_baseline(
                device_id,
                f"detector creation error: {e}",
            )
            return

        # =========================================================
        # 8. 기존 로직 유지
        #
        # baseline 마지막 최대 10 beats만 prefill
        # =========================================================

        last_beats = (
            ref_seq[-10:]
            if len(ref_seq) >= 10
            else ref_seq
        )

        try:
            for feat_vec in last_beats:

                norm_vec = (
                    normalize_feature_vector(
                        feat_vec.astype(
                            np.float32
                        ),
                        ref_mu,
                        ref_sd,
                    )
                )

                # normalized feature에도 NaN/Inf가
                # 없는지 방어
                if not np.all(
                    np.isfinite(norm_vec)
                ):
                    self._fail_baseline(
                        device_id,
                        (
                            "invalid normalized "
                            "prefill feature: "
                            f"{norm_vec.tolist()}"
                        ),
                    )
                    return

                detector.beat_window.append(
                    norm_vec
                )

        except Exception as e:
            logger.exception(
                "[baseline] prefill error "
                "device=%s: %s",
                device_id,
                e,
            )

            self._fail_baseline(
                device_id,
                f"prefill error: {e}",
            )
            return

        # =========================================================
        # 9. 기존 realtime extractor 상태 이어받기
        # =========================================================

        old = self._extractors.get(
            device_id
        )

        new_ext = RealtimeBeatExtractor(
                fs=FS,
                rolling_seconds=13.0,
                safe_margin_seconds=0.6,
        )

        if old is not None:

            new_ext.raw_buffer = deque(
                old.raw_buffer,
                maxlen=new_ext.rolling_n,
            )

            new_ext.global_sample_idx = (
                old.global_sample_idx
            )

            new_ext.buffer_start_global_idx = (
                old.buffer_start_global_idx
            )

            new_ext.prev_sp_global = (
                old.prev_sp_global
            )

            new_ext.last_processed_sp_global = (
                old.last_processed_sp_global
            )

            new_ext.beat_idx = (
                old.beat_idx
            )

        # =========================================================
        # 10. DB 저장용 baseline_stats
        # =========================================================

        baseline_stats = {
            "ref_mu": ref_mu.tolist(),
            "ref_sd": ref_sd.tolist(),

            "num_beats": int(
                len(ref_seq)
            ),

            # 실제 넣은 개수.
            # 최대 10.
            "prefilled_beats": int(
                len(last_beats)
            ),

            "baseline_samples": int(
                len(raw)
            ),

            "baseline_seconds": float(
                len(raw) / FS
            ),

            "rolling_seconds": 20.0,
            "safe_margin_seconds": 1.0,
            "min_rr_sec": 0.45,
            "max_rr_sec": 1.5,
        }

        # =========================================================
        # 11. SQLite JSONField에 저장하기 전에 검증
        # =========================================================

        try:
            json.dumps(
                baseline_stats,
                allow_nan=False,
            )

        except (
            ValueError,
            TypeError,
        ) as e:

            self._fail_baseline(
                device_id,
                (
                    "invalid baseline JSON: "
                    f"{e}"
                ),
            )
            return

        # =========================================================
        # 12. ApneaSession 저장
        # =========================================================

        saved_ok = False

        if not session_pk:
            self._fail_baseline(
                device_id,
                "session_pk missing",
            )
            return

        try:
            updated = (
                ApneaSession.objects
                .filter(
                    pk=session_pk
                )
                .update(
                    baseline_ready=True,
                    baseline_stats=baseline_stats,
                    model_config=dict(cfg),
                )
            )

            if updated != 1:

                self._fail_baseline(
                    device_id,
                    (
                        "session update failed: "
                        f"pk={session_pk} "
                        f"updated={updated}"
                    ),
                )
                return

            saved_ok = True

            logger.info(
                "[baseline] DB saved: "
                "device=%s "
                "pk=%s "
                "samples=%d "
                "beats=%d",
                device_id,
                session_pk,
                len(raw),
                len(ref_seq),
            )

        except Exception as e:

            logger.exception(
                "[baseline] DB save error "
                "(pk=%s): %s",
                session_pk,
                e,
            )

            self._fail_baseline(
                device_id,
                f"DB save error: {e}",
            )
            return

        # =========================================================
        # 13. DB 저장 검증
        # =========================================================

        try:
            row = (
                ApneaSession.objects
                .filter(
                    pk=session_pk
                )
                .values(
                    "baseline_ready",
                    "baseline_stats",
                )
                .first()
            )

            if (
                not row
                or not row["baseline_ready"]
                or row["baseline_stats"] is None
            ):

                self._fail_baseline(
                    device_id,
                    (
                        "DB verify failed: "
                        f"pk={session_pk}"
                    ),
                )
                return

        except Exception as e:

            logger.exception(
                "[baseline] DB verify error: %s",
                e,
            )

            self._fail_baseline(
                device_id,
                f"DB verify error: {e}",
            )
            return

        # =========================================================
        # 14. 성공한 baseline을 현재 baseline으로 자동 선택
        # =========================================================

        try:
            baseline_session = (
                ApneaSession.objects.get(
                    pk=session_pk,
                    device_id=device_id,
                    baseline_ready=True,
                )
            )

            config, _ = (
                ApneaDeviceConfig.objects
                .get_or_create(
                    device_id=device_id
                )
            )

            config.selected_baseline_session = (
                baseline_session
            )

            config.save(
                update_fields=[
                    "selected_baseline_session",
                    "updated_at",
                ]
            )

            logger.info(
                "[baseline] auto selected: "
                "device=%s "
                "session=%s "
                "started_at=%s",
                device_id,
                session_pk,
                baseline_session.started_at,
            )

        except Exception as e:
            # 여기서는 baseline 자체의 DB 저장은 이미 성공했음.
            # 따라서 baseline 측정 자체를 실패 처리하지 않고
            # 자동 선택 실패만 기록한다.
            logger.exception(
                "[baseline] auto-select failed "
                "device=%s session=%s: %s",
                device_id,
                session_pk,
                e,
            )

        # =========================================================
        # 15. 성공했을 때만 메모리 상태 전환
        # =========================================================

        with self._dev_lock:

            self._detectors[device_id] = (
                detector
            )

            self._extractors[device_id] = (
                new_ext
            )

            self._baseline_done[device_id] = (
                True
            )

            self._baseline_active[device_id] = (
                False
            )

            self._baseline_buf[device_id] = []

        # =========================================================
        # 16. 완료
        # =========================================================

        logger.info(
            "[ApneaEngine] baseline SUCCESS: "
            "device=%s "
            "pk=%s "
            "samples=%d "
            "seconds=%.2f "
            "beats=%d "
            "prefill=%d "
            "db_saved=%s",
            device_id,
            session_pk,
            len(raw),
            len(raw) / FS,
            len(ref_seq),
            len(last_beats),
            saved_ok,
        )

    def select_baseline_session(
        self,
        device_id: str,
        session: ApneaSession,
    ) -> bool:

        try:
            if not self._model_ready:
                logger.error(
                    "[baseline-select] model is not ready"
                )
                return False

            # 안전 검사
            if session.device_id != device_id:
                logger.error(
                    f"[baseline-select] device mismatch: "
                    f"requested={device_id}, "
                    f"session_device={session.device_id}"
                )
                return False

            if not session.baseline_ready:
                logger.error(
                    f"[baseline-select] baseline not ready: "
                    f"session={session.pk}"
                )
                return False

            stats = session.baseline_stats or {}

            ref_mu_raw = stats.get("ref_mu")
            ref_sd_raw = stats.get("ref_sd")

            if ref_mu_raw is None or ref_sd_raw is None:
                logger.error(
                    f"[baseline-select] ref_mu/ref_sd missing: "
                    f"session={session.pk}"
                )
                return False

            ref_mu = np.asarray(
                ref_mu_raw,
                dtype=np.float32,
            )

            ref_sd = np.asarray(
                ref_sd_raw,
                dtype=np.float32,
            )

            # feature 개수 검증
            if (
                len(ref_mu) != len(BEAT_FEATURES)
                or len(ref_sd) != len(BEAT_FEATURES)
            ):
                logger.error(
                    f"[baseline-select] invalid stats shape: "
                    f"mu={len(ref_mu)}, "
                    f"sd={len(ref_sd)}, "
                    f"expected={len(BEAT_FEATURES)}"
                )
                return False

            # 가능하면 baseline 측정 당시 모델 설정 사용
            cfg = session.model_config or self._model_cfg or {}

            context_len = int(
                cfg.get(
                    "context_len",
                    (self._model_cfg or {}).get("context_len", 20),
                )
            )

            threshold = float(
                cfg.get(
                    "threshold",
                    (self._model_cfg or {}).get("threshold", 0.5),
                )
            )

            device_str = (
                "cuda"
                if torch.cuda.is_available()
                else "cpu"
            )

            detector = RealtimeApneaDetector(
                model=self._model,
                context_len=context_len,
                ref_mu=ref_mu,
                ref_sd=ref_sd,
                threshold_from_train=threshold,
                device=device_str,
            )

            # baseline을 바꾸는 순간 이전 baseline으로 만들어진
            # extractor/context를 섞지 않고 새로 시작
            extractor = RealtimeBeatExtractor(
                fs=FS,
                rolling_seconds=13.0,
                safe_margin_seconds=0.6,
            )

            with self._dev_lock:
                self._detectors[device_id] = detector
                self._extractors[device_id] = extractor

                self._baseline_buf[device_id] = []
                self._baseline_done[device_id] = True
                self._baseline_active[device_id] = False

                if device_id not in self._packet_count:
                    self._packet_count[device_id] = 0

            logger.info(
                    f"[baseline-select] applied: "
                    f"device={device_id} "
                    f"session={session.pk} "
                    f"started_at={session.started_at}"
                )

            return True

        except Exception as e:
            logger.exception(
                f"[baseline-select] failed: "
                f"device={device_id}, "
                f"session={getattr(session, 'pk', None)}, "
                f"error={e}"
            )

            return False

    def restore_selected_baseline(
        self,
        device_id: str,
    ) -> bool:

        try:
            config = (
                ApneaDeviceConfig.objects
                .select_related("selected_baseline_session")
                .filter(device_id=device_id)
                .first()
            )

            if config is None:
                logger.info(
                    f"[baseline-restore] no config: "
                    f"{device_id}"
                )
                return False

            session = config.selected_baseline_session

            if session is None:
                logger.info(
                    f"[baseline-restore] no selected baseline: "
                    f"{device_id}"
                )
                return False

            return self.select_baseline_session(
                device_id=device_id,
                session=session,
            )

        except Exception as e:
            logger.exception(
                f"[baseline-restore] failed: "
                f"device={device_id}: {e}"
            )
            return False
            
    def process_chunk(
        self,
        device_id: str,
        ppg_green: list,
        ppg_ir: list = None,
        ppg_red: list = None,
        session_db=None,
        packet_timestamp=None,
    ) -> dict:

        # =========================================================
        # 1. device 상태 준비
        # =========================================================

        self._ensure_device(device_id)

        with self._dev_lock:
            self._packet_count[device_id] += 1

            packet_idx = self._packet_count[device_id]

            baseline_done = self._baseline_done.get(
                device_id,
                False,
            )

            baseline_active = self._baseline_active.get(
                device_id,
                False,
            )

        # =========================================================
        # 2. 입력 PPG
        # =========================================================

        arr = np.asarray(
            ppg_green,
            dtype=float,
        )

        # =========================================================
        # 3. 착용 여부
        # =========================================================

        wear = detect_wear_green(arr)

        # =========================================================
        # 4. realtime extractor
        # =========================================================

        extractor = self._extractors.get(
            device_id
        )

        # =========================================================
        # 5. R ratio
        #
        # 기존 로직 그대로 유지
        # =========================================================

        if ppg_ir and ppg_red:

            r_ratio = compute_r_ratio_series(
                ppg_red,
                ppg_ir,
                fs=FS,
            )

        else:

            r_ratio = []

        # =========================================================
        # 6. 현재 baseline 진행률
        # =========================================================

        current_baseline_samples = len(
            self._baseline_buf.get(
                device_id,
                [],
            )
        )

        baseline_progress = min(
            1.0,
            current_baseline_samples
            / MIN_BASELINE_SAMPLES,
        )

        # =========================================================
        # 7. 기본 결과
        #
        # 중요:
        # 기본값은 is_baseline=False
        # 실제 baseline 측정에 사용되는 packet에서만 True로 바꿈
        # =========================================================

        result = {
            "packet_index": packet_idx,

            "baseline_ready": baseline_done,

            "baseline_progress": baseline_progress,

            "is_baseline": False,

            "wear": wear,

            "r_ratio_series": r_ratio,

            "beat_results": [],

            "p_apnea": None,

            "p_apnea_smooth": None,

            "pred_label": None,

            "pred_status": "waiting_for_session",

            "phase": "waiting",
        }

        # =========================================================
        # 8. BASELINE 측정 중인 경우
        # =========================================================

        if baseline_active:

            # =====================================================
            # 9. 이 packet이 baseline 시작 이후 데이터인지 확인
            # =====================================================

            include_in_baseline = True

            if (
                packet_timestamp is not None
                and session_db is not None
                and session_db.started_at is not None
            ):

                from datetime import timedelta

                # 기존 코드 기준:
                # watch packet 하나가 약 12초 구간
                watch_end_time = (
                    packet_timestamp
                    + timedelta(seconds=12)
                )

                # packet 전체가 baseline 시작 이전이면 제외
                if (
                    watch_end_time
                    <= session_db.started_at
                ):

                    include_in_baseline = False

                    logger.info(
                        "[baseline] skip packet before session: "
                        "device=%s "
                        "watch_end=%s "
                        "started_at=%s",
                        device_id,
                        watch_end_time,
                        session_db.started_at,
                    )

            # =====================================================
            # 10. baseline 시작 전 packet
            # =====================================================

            if not include_in_baseline:

                result["is_baseline"] = False

                result["pred_status"] = (
                    "before_session"
                )

                result["phase"] = "waiting"

                # realtime extractor 시간축은 유지
                if extractor is not None:
                    extractor.feed_packet(arr)

                return result

            # =====================================================
            # 11. 여기부터 실제 BASELINE packet
            # =====================================================

            result["is_baseline"] = True

            result["pred_status"] = "baseline"

            result["phase"] = "baseline"

    

           # =====================================================
            # 12. BASELINE DATA 수집
            #
            # 최소 30초(750 samples)가 확보될 때까지
            # packet을 통째로 저장한다.
            #
            # 마지막 packet을 750에서 자르지 않는다.
            #
            # 예:
            #   packet 1 -> 300
            #   packet 2 -> 600
            #   packet 3 -> 900
            #
            # 그러면 900 samples 전체를 baseline으로 사용한다.
            # =====================================================

            with self._dev_lock:

                self._baseline_buf[
                    device_id
                ].extend(
                    arr.tolist()
                )

                collected_n = len(
                    self._baseline_buf[
                        device_id
                    ]
                )

            logger.info(
                "[baseline] collecting: "
                "device=%s "
                "%d samples "
                "(minimum=%d)",
                device_id,
                collected_n,
                MIN_BASELINE_SAMPLES,
            )

            # =====================================================
            # 13. extractor에는 packet 전체 공급
            #
            # baseline buffer는 750개까지만 사용하지만
            # extractor는 실제 시간축 유지를 위해 packet 전체 사용
            # =====================================================

            if extractor is not None:

                extractor.feed_packet(arr)

            # =====================================================
            # 14. 진행률 업데이트
            # =====================================================

            result["baseline_progress"] = min(
                1.0,
                collected_n
                / MIN_BASELINE_SAMPLES,
            )

            result["baseline_ready"] = False

            # =====================================================
            # 15. 아직 750 samples 미만
            # =====================================================

            if (
                collected_n
                < MIN_BASELINE_SAMPLES
            ):

                return result

            # =====================================================
            # 16. 750 samples 도달
            #
            # 여기에서 한 번 finalize
            # =====================================================

            logger.info(
                "[baseline] 30s reached: "
                "device=%s "
                "samples=%d",
                device_id,
                collected_n,
            )

            self._finalize_baseline(
                device_id,
                session_db,
            )

            # =====================================================
            # 17. finalize 이후 상태 다시 확인
            # =====================================================

            with self._dev_lock:

                baseline_done_after = (
                    self._baseline_done.get(
                        device_id,
                        False,
                    )
                )

                baseline_active_after = (
                    self._baseline_active.get(
                        device_id,
                        False,
                    )
                )

            # =====================================================
            # 18. BASELINE 성공
            # =====================================================

            if baseline_done_after:

                result["baseline_ready"] = True

                result["baseline_progress"] = 1.0

                # 이 packet은 baseline에 실제 사용됐음
                result["is_baseline"] = True

                result["pred_status"] = (
                    "baseline_just_completed"
                )

                result["phase"] = "baseline"

                logger.info(
                    "[baseline] COMPLETE: "
                    "device=%s",
                    device_id,
                )

                return result

            # =====================================================
            # 19. BASELINE 실패
            #
            # 이 packet 자체는 baseline 측정에 사용됐으므로
            # is_baseline=True가 맞음.
            #
            # 하지만 _fail_baseline()에서
            # baseline_active=False가 됐기 때문에
            # 다음 packet부터는 여기로 들어오지 않음.
            # =====================================================

            result["baseline_ready"] = False

            result["baseline_progress"] = 1.0

            result["is_baseline"] = True

            result["pred_status"] = (
                "baseline_failed"
            )

            result["phase"] = "baseline"

            logger.error(
                "[baseline] FINALIZE FAILED: "
                "device=%s "
                "active=%s",
                device_id,
                baseline_active_after,
            )

            return result

        # =========================================================
        # 20. 여기부터는 BASELINE 측정 중이 아님
        # =========================================================

        # 최신 상태 다시 확인
        baseline_done = self._baseline_done.get(
            device_id,
            False,
        )

        # =========================================================
        # 21. 사용할 baseline이 없는 상태
        #
        # baseline 실패 후에도 여기로 들어온다.
        #
        # 중요:
        # 이 packet은 baseline이 아니다.
        # =========================================================

        if not baseline_done:

            result["is_baseline"] = False

            result["baseline_ready"] = False

            result["baseline_progress"] = 0.0

            result["pred_status"] = (
                "waiting_for_session"
            )

            result["phase"] = "waiting"

            # baseline은 아니지만 extractor 시간축은 유지
            extractor = self._extractors.get(
                device_id
            )

            if extractor is not None:

                extractor.feed_packet(arr)

            return result

        # =========================================================
        # 22. BASELINE 완료 후 inference
        # =========================================================

        detector = self._detectors.get(
            device_id
        )

        extractor = self._extractors.get(
            device_id
        )

        # =========================================================
        # 23. inference 준비 여부 확인
        # =========================================================

        if (
            extractor is None
            or detector is None
            or not self._model_ready
        ):

            result["is_baseline"] = False

            result["baseline_ready"] = True

            result["phase"] = "error"

            result["pred_status"] = (
                "not_ready"
            )

            return result

        # =========================================================
        # 24. realtime beat 추출
        # =========================================================

        new_beats = extractor.feed_packet(
            arr
        )

        beat_results = []

        last = None

        # =========================================================
        # 25. beat별 apnea inference
        # =========================================================

        for beat_feat in new_beats:

            r = detector.update_with_beat(
                beat_feat
            )

            if r is None:
                continue

            beat_results.append({
                "time_sec": (
                    r.get("time_sec")
                ),

                "p_apnea": (
                    r.get("p_apnea")
                ),

                "p_apnea_smooth": (
                    r.get(
                        "p_apnea_smooth"
                    )
                ),

                "pred_label": (
                    r.get("pred_label")
                ),

                "status": (
                    r.get(
                        "status",
                        "ok",
                    )
                ),
            })

            last = r

        # =========================================================
        # 26. inference 결과
        # =========================================================

        result["is_baseline"] = False

        result["phase"] = "inference"

        result["baseline_ready"] = True

        result["baseline_progress"] = 1.0

        result["beat_results"] = beat_results

        # =========================================================
        # 27. 마지막 beat 결과
        # =========================================================

        if last is not None:

            result["p_apnea"] = (
                last.get(
                    "p_apnea"
                )
            )

            result["p_apnea_smooth"] = (
                last.get(
                    "p_apnea_smooth"
                )
            )

            result["pred_label"] = (
                last.get(
                    "pred_label"
                )
            )

            result["pred_status"] = (
                last.get(
                    "status",
                    "ok",
                )
            )

        else:

            result["pred_status"] = (
                "no_beats_detected"
            )

        # =========================================================
        # 28. 완료
        # =========================================================

        return result