#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
C안 test: 단일 연속 파일에서 앞 90초를 reference normal로 사용하고,
그 이후 구간을 streaming window로 추론하여 apnea onset 여부와 segment를 출력.
+ 결과 시각화 PNG 저장.
"""
from __future__ import annotations
import sys
import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

FS = 25.0
SHORT_GAP_THRESH = 3
BEAT_FEATURES = ["FO_SP_s", "Downstroke_vel", "HR_bpm", "RR_s", "QI"]


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def butter_bandpass(low: float, high: float, fs: float, order: int = 3):
    nyq = 0.5 * fs
    low_n = max(1e-6, low / nyq)
    high_n = min(0.999, high / nyq)
    return butter(order, [low_n, high_n], btype="band")


def bandpass_filter(x: np.ndarray, fs: float, low: float = 0.5, high: float = 8.0, order: int = 3) -> np.ndarray:
    b, a = butter_bandpass(low, high, fs, order)
    return filtfilt(b, a, x)


def normalize_minmax(x: np.ndarray) -> np.ndarray:
    mn, mx = np.nanmin(x), np.nanmax(x)
    return (x - mn) / (mx - mn) if (mx - mn) > 1e-12 else np.zeros_like(x)


def zero_crossings(x: np.ndarray) -> np.ndarray:
    s = np.sign(x)
    return np.where(np.diff(s) != 0)[0]


def adaptive_prominence(y: np.ndarray, base_prom: float = 0.03) -> float:
    q1, q3 = np.nanquantile(y, [0.25, 0.75])
    iqr = max(1e-6, float(q3 - q1))
    return base_prom * iqr


def detect_sp(ppg_norm: np.ndarray, fs: float, min_rr_sec: float = 0.45, base_prom: float = 0.03) -> np.ndarray:
    min_distance = int(min_rr_sec * fs)
    prom = adaptive_prominence(ppg_norm, base_prom)
    peaks, _ = find_peaks(ppg_norm, distance=max(1, min_distance), prominence=prom)
    return peaks.astype(int)


def refine_sp_indices(ppg_norm: np.ndarray, peaks: np.ndarray, fs: float, window_sec: float = 0.08) -> np.ndarray:
    if window_sec <= 0.0 or peaks.size == 0:
        return peaks
    w = max(1, int(window_sec * fs))
    n = len(ppg_norm)
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
    i0 = int(max(0, i_peak - search_sec * fs))
    seg = ppg_diff[i0:i_peak + 1]
    zc = zero_crossings(seg)
    cand = [i0 + k for k in zc if k < len(seg)-1 and seg[k] < 0 <= seg[k + 1]]
    if cand:
        return int(cand[-1])
    return int(i0 + int(np.nanargmin(ppg_norm[i0:i_peak + 1])))


def find_notch_after_peak(ppg_norm, i_peak, fs, search_lo=0.06, search_hi=0.40):
    i1 = i_peak + int(search_lo * fs)
    i2 = min(len(ppg_norm) - 1, i_peak + int(search_hi * fs))
    if i1 >= i2:
        return None
    inv = -ppg_norm[i1:i2]
    mins, _ = find_peaks(inv, prominence=0.005)
    return int(i1 + mins[0]) if mins.size > 0 else None


def _check_fiducial_order(fid: Fiducials) -> bool:
    points = [(n, v) for n, v in [("FO", fid.FO), ("SP", fid.SP), ("DN", fid.DN), ("DP", fid.DP)] if v is not None]
    order = ["FO", "SP", "DN", "DP"]
    prev = -1
    for name, _ in sorted(points, key=lambda x: x[1]):
        curr = order.index(name)
        if curr <= prev:
            return False
        prev = curr
    return True


def clean_fiducials(fid: Fiducials, fs: float) -> Fiducials:
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
    start, end = max(0, idx - window_samples), min(len(ppg), idx + window_samples + 1)
    return ppg[idx] <= np.min(ppg[start:end]) * 1.05


def quality_index(fid: Fiducials, feats: Dict, ppg_norm: np.ndarray, fs: float) -> float:
    if fid.FO is None or fid.SP is None or not _check_fiducial_order(fid):
        return 0.0
    score = 1.0
    amp_sp = feats.get("Amp_SP", np.nan)
    if math.isnan(amp_sp) or amp_sp <= 0:
        score -= 0.4
    t_fo_sp = feats.get("FO_SP_time_s", np.nan)
    if not (0.1 <= t_fo_sp <= 0.6):
        score -= 0.3
    if math.isnan(feats.get("Downstroke_vel", np.nan)):
        score -= 0.05
    if fid.DN is not None and not _is_local_minimum(ppg_norm, fid.DN, int(0.04 * fs)):
        score -= 0.2
    return max(0.0, float(score))


def process_raw_to_beat_table(raw: np.ndarray, fs: float = FS) -> pd.DataFrame:
    raw = np.asarray(raw, dtype=float)
    raw = raw[np.isfinite(raw)]
    if len(raw) < int(fs * 5):
        return pd.DataFrame()
    ppg = bandpass_filter(raw, fs=fs, low=0.5, high=8.0)
    ppg_norm = normalize_minmax(ppg)
    diff = np.gradient(ppg_norm) * fs

    sp_idx = detect_sp(ppg_norm, fs, min_rr_sec=0.45, base_prom=0.03)
    sp_idx = refine_sp_indices(ppg_norm, sp_idx, fs, window_sec=0.08)
    if len(sp_idx) < 5:
        return pd.DataFrame()

    rows = []
    prev_sp = None
    for i, sp in enumerate(sp_idx):
        fo = find_onset_before_peak(ppg_norm, diff, sp, fs)
        dn = find_notch_after_peak(ppg_norm, sp, fs)
        fid = clean_fiducials(Fiducials(FO=fo, SP=sp, DN=dn, DP=None), fs)

        def tdiff(a, b):
            return max(0.0, (b - a) / fs) if (a is not None and b is not None) else np.nan

        fo_sp_s = tdiff(fid.FO, fid.SP)
        amp_sp = float(ppg_norm[fid.SP] - ppg_norm[fid.FO]) if (fid.FO is not None and fid.SP is not None) else np.nan
        dn_v = float(np.nanmin(diff[fid.SP: fid.DN + 1])) if (fid.SP is not None and fid.DN is not None and fid.DN > fid.SP) else np.nan
        qi = quality_index(fid, {"FO_SP_time_s": fo_sp_s, "Amp_SP": amp_sp, "Downstroke_vel": dn_v}, ppg_norm, fs)
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


def impute_column_short(col: np.ndarray, thresh: int = SHORT_GAP_THRESH) -> np.ndarray:
    col = col.copy().astype(float)
    n = len(col)
    i = 0
    while i < n:
        if not np.isfinite(col[i]):
            j = i
            while j < n and not np.isfinite(col[j]):
                j += 1
            gap_len = j - i
            if gap_len <= thresh:
                v_left = col[i - 1] if i > 0 and np.isfinite(col[i - 1]) else 0.0
                v_right = col[j] if j < n and np.isfinite(col[j]) else 0.0
                for k in range(gap_len):
                    col[i + k] = v_left + (v_right - v_left) * (k + 1) / (gap_len + 1)
            i = j
        else:
            i += 1
    return col


def impute_beat_table(beat_df: pd.DataFrame, feature_cols: List[str], thresh: int = SHORT_GAP_THRESH) -> pd.DataFrame:
    df = beat_df.copy()
    for col in feature_cols:
        df[col] = impute_column_short(df[col].to_numpy(float), thresh)
    return df


def beat_table_to_seq(beat_df: pd.DataFrame) -> np.ndarray:
    if beat_df.empty or len(beat_df) < 5:
        return np.empty((0, len(BEAT_FEATURES)), dtype=np.float32)
    beat_df = impute_beat_table(beat_df, BEAT_FEATURES, thresh=SHORT_GAP_THRESH)
    return beat_df[BEAT_FEATURES].to_numpy(dtype=np.float32)


def compute_baseline_stats(base_seq: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    mu = np.nanmean(base_seq, axis=0)
    sd = np.nanstd(base_seq, axis=0)
    sd = np.where(sd > 1e-6, sd, 1.0)
    return mu, sd


def normalize_seq(seq: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    out = (seq - mu[None, :]) / sd[None, :]
    return np.where(np.isfinite(out), out, 0.0).astype(np.float32)


class CausalTransformerBinary(nn.Module):
    def __init__(self, input_dim: int, d_model: int, nhead: int, num_layers: int, dropout: float, context_len: int):
        super().__init__()
        self.input_proj = nn.Linear(input_dim, d_model)
        self.pos_emb = nn.Embedding(context_len, d_model)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        causal = torch.triu(torch.full((context_len, context_len), float("-inf")), diagonal=1)
        self.register_buffer("causal_mask", causal)
        self.head = nn.Sequential(
            nn.Linear(d_model, d_model),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model, 1),
        )

    def forward(self, x):
        n = x.shape[1]
        pos = torch.arange(n, device=x.device)
        h = self.input_proj(x) + self.pos_emb(pos)
        h = self.encoder(h, mask=self.causal_mask)
        return self.head(h[:, -1, :]).squeeze(-1)


def moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x.copy()
    kernel = np.ones(win, dtype=float) / win
    return np.convolve(x, kernel, mode="same")


def hysteresis_labels(prob: np.ndarray, hi: float, lo: float, min_run: int) -> np.ndarray:
    state = 0
    labels = np.zeros(len(prob), dtype=int)
    hi_count = 0
    lo_count = 0
    for i, p in enumerate(prob):
        if state == 0:
            hi_count = hi_count + 1 if p >= hi else 0
            if hi_count >= min_run:
                state = 1
                hi_count = 0
        else:
            lo_count = lo_count + 1 if p <= lo else 0
            if lo_count >= min_run:
                state = 0
                lo_count = 0
        labels[i] = state
    return labels


def segments_from_labels(times_sec: np.ndarray, labels: np.ndarray) -> List[Dict]:
    segments = []
    if len(labels) == 0:
        return segments
    start = 0
    curr = labels[0]
    for i in range(1, len(labels)):
        if labels[i] != curr:
            segments.append({
                "label": int(curr),
                "start_sec": float(times_sec[start]),
                "end_sec": float(times_sec[i - 1]),
            })
            start = i
            curr = labels[i]
    segments.append({
        "label": int(curr),
        "start_sec": float(times_sec[start]),
        "end_sec": float(times_sec[-1]),
    })
    return segments


def plot_result(
    raw: np.ndarray,
    win_df: pd.DataFrame,
    seg_df: pd.DataFrame,
    summary: Dict,
    out_png: str,
    ref_seconds: float = 90.0,
):
    t = np.arange(len(raw)) / FS
    hi = float(summary.get("hi", 0.6))
    lo = float(summary.get("lo", 0.4))
    onset_sec = summary.get("apnea_onset_sec", None)

    fig = plt.figure(figsize=(16, 9))

    ax1 = plt.subplot(2, 1, 1)
    ax1.plot(t, raw, linewidth=1.0)
    ax1.axvspan(0, ref_seconds, alpha=0.15, color="green", label="Reference normal (first 90s)")

    for _, row in seg_df.iterrows():
        s = float(row["start_sec"])
        e = float(row["end_sec"])
        lab = int(row["label"])
        if lab == 1:
            ax1.axvspan(s, e, alpha=0.25, color="red", label="Predicted apnea")
        else:
            ax1.axvspan(s, e, alpha=0.10, color="blue", label="Predicted normal")

    if onset_sec is not None:
        ax1.axvline(float(onset_sec), color="red", linestyle="--", linewidth=2,
                    label=f"Detected onset: {onset_sec:.1f}s")

    ax1.set_title("Raw PPG with predicted normal/apnea segments")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("PPG value")

    handles, labels = ax1.get_legend_handles_labels()
    uniq_handles, uniq_labels = [], []
    for h, l in zip(handles, labels):
        if l not in uniq_labels:
            uniq_handles.append(h)
            uniq_labels.append(l)
    ax1.legend(uniq_handles, uniq_labels, loc="upper right")

    ax2 = plt.subplot(2, 1, 2)
    ax2.plot(win_df["time_sec"], win_df["p_apnea"], linewidth=1.0, alpha=0.6, label="p_apnea")
    if "p_apnea_smooth" in win_df.columns:
        ax2.plot(win_df["time_sec"], win_df["p_apnea_smooth"], linewidth=2.0, label="p_apnea_smooth")

    ax2.axhline(hi, color="red", linestyle="--", linewidth=1.5, label=f"hi={hi:.2f}")
    ax2.axhline(lo, color="orange", linestyle="--", linewidth=1.5, label=f"lo={lo:.2f}")

    if onset_sec is not None:
        ax2.axvline(float(onset_sec), color="red", linestyle="--", linewidth=2,
                    label=f"Detected onset: {onset_sec:.1f}s")

    ax2.set_title("Apnea probability over time")
    ax2.set_xlabel("Time (s)")
    ax2.set_ylabel("Probability")
    ax2.set_ylim(-0.02, 1.02)
    ax2.legend(loc="upper right")

    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved visualization to: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_csv", type=str, required=True)
    ap.add_argument("--model_path", type=str, required=True)
    ap.add_argument("--outdir", type=str, default="test_outputs_c_stream_change")
    ap.add_argument("--ref_seconds", type=float, default=90.0)
    ap.add_argument("--smooth_win", type=int, default=5)
    ap.add_argument("--hi", type=float, default=None)
    ap.add_argument("--lo", type=float, default=None)
    ap.add_argument("--min_run", type=int, default=3)
    args = ap.parse_args()

    ensure_dir(args.outdir)
    ckpt = torch.load(args.model_path, map_location="cpu")
    cfg = ckpt["config"]
    context_len = int(cfg["context_len"])
    thr = float(cfg.get("threshold", 0.5))
    hi = float(args.hi) if args.hi is not None else max(0.55, thr)
    lo = float(args.lo) if args.lo is not None else min(0.45, thr - 0.1)

    model = CausalTransformerBinary(
        input_dim=len(BEAT_FEATURES),
        d_model=int(cfg["d_model"]),
        nhead=int(cfg["nhead"]),
        num_layers=int(cfg["num_layers"]),
        dropout=float(cfg["dropout"]),
        context_len=context_len,
    )
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    df = pd.read_csv(args.input_csv)
    if "value" not in df.columns:
        raise ValueError("input csv must contain 'value' column")
    raw = df["value"].astype(float).to_numpy()
    ref_n = int(args.ref_seconds * FS)
    if len(raw) <= ref_n + int(10 * FS):
        raise ValueError("input is too short for 90s reference + query")

    ref_raw = raw[:ref_n]
    qry_raw = raw[ref_n:]

    ref_bt = process_raw_to_beat_table(ref_raw, fs=FS)
    qry_bt = process_raw_to_beat_table(qry_raw, fs=FS)
    ref_seq = beat_table_to_seq(ref_bt)
    qry_seq = beat_table_to_seq(qry_bt)
    if len(ref_seq) < context_len + 2:
        raise ValueError("reference beat sequence too short")
    if len(qry_seq) < 3:
        raise ValueError("query beat sequence too short")

    mu, sd = compute_baseline_stats(ref_seq)
    ref_norm = normalize_seq(ref_seq, mu, sd)
    qry_norm = normalize_seq(qry_seq, mu, sd)
    stream = np.concatenate([ref_norm, qry_norm], axis=0)
    boundary = len(ref_norm)

    xs = []
    beat_times_sec = []
    qry_sp_sec = qry_bt["sp_sample"].to_numpy(dtype=float) / FS + args.ref_seconds
    for t in range(max(context_len, boundary), len(stream) + 1):
        xs.append(stream[t - context_len:t])
        q_idx = t - boundary - 1
        if q_idx < 0 or q_idx >= len(qry_sp_sec):
            beat_times_sec.append(np.nan)
        else:
            beat_times_sec.append(float(qry_sp_sec[q_idx]))
    xs = np.stack(xs, axis=0).astype(np.float32)
    with torch.no_grad():
        logits = model(torch.from_numpy(xs))
        prob = torch.sigmoid(logits).cpu().numpy()
    prob_s = moving_average(prob, args.smooth_win)
    pred_state = hysteresis_labels(prob_s, hi=hi, lo=lo, min_run=args.min_run)

    out_df = pd.DataFrame({
        "time_sec": beat_times_sec,
        "p_apnea": prob,
        "p_apnea_smooth": prob_s,
        "pred_label": pred_state,
    }).dropna().reset_index(drop=True)
    out_df.to_csv(os.path.join(args.outdir, "window_predictions.csv"), index=False)

    segs = segments_from_labels(out_df["time_sec"].to_numpy(), out_df["pred_label"].to_numpy())
    seg_df = pd.DataFrame(segs)
    seg_df.to_csv(os.path.join(args.outdir, "segment_predictions.csv"), index=False)

    apnea_seg = seg_df[seg_df["label"] == 1]
    onset_sec = None if apnea_seg.empty else float(apnea_seg.iloc[0]["start_sec"])
    summary = {
        "input_csv": args.input_csv,
        "ref_seconds": args.ref_seconds,
        "threshold_from_train": thr,
        "hi": hi,
        "lo": lo,
        "min_run": args.min_run,
        "smooth_win": args.smooth_win,
        "num_query_windows": int(len(out_df)),
        "onset_detected": onset_sec is not None,
        "apnea_onset_sec": onset_sec,
        "final_label": int(out_df["pred_label"].iloc[-1]) if len(out_df) else None,
    }
    with open(os.path.join(args.outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ── 시각화 저장 ────────────────────────────────────────────────────────
    out_png = os.path.join(args.outdir, "result_visualization.png")
    plot_result(
        raw=raw,
        win_df=out_df,
        seg_df=seg_df,
        summary=summary,
        out_png=out_png,
        ref_seconds=args.ref_seconds,
    )


if __name__ == "__main__":
    main()