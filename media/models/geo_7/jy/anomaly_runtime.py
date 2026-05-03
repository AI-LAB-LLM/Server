# -*- coding: utf-8 -*-
"""
anomaly_runtime.py

서버 추론용 DTW 기반 경로 이상탐지 런타임.

역할
- GPRRuntime에서 보정된 Latitude / longitude 기준으로 trip 생성
- train anchor 기준 OD labeling
- baseline trip과 test trip을 OD별 DTW 비교
- OD threshold와 비교
- final_route_label 반환

주의
- 지도 표시용 좌표 보정은 5분마다 가능하지만,
  DTW 이상탐지는 단일 좌표가 아니라 trip 단위 비교입니다.
- 따라서 MOVE 구간이 충분히 형성된 뒤 AnomalyRuntime 결과가 의미 있습니다.
"""

import math
import joblib
import numpy as np
import pandas as pd


TOP_K_DEFAULT = 3
MIN_BASELINES_PER_OD = 2

A_COST = 1.0
B_COST = 0.0
D0_M = 30.0
T0_DEG = 15.0
UNSEEN_MARGIN_RATIO = 1.35

CONFIDENCE_TOLERANCE_MAP_M = {
    "HIGH": 8.0,
    "MEDIUM": 15.0,
    "LOW": 25.0,
}

INTERP_TOLERANCE_MAP_M = {
    "stale_linear": 12.0,
    "linear_fallback": 18.0,
    "forward_fill_fallback": 25.0,
    "gpr": 25.0,
}


# =========================================================
# 기본 유틸
# =========================================================
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [float(lat1), float(lon1), float(lat2), float(lon2)]
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1, lon1, lat2, lon2):
    lat1 = math.radians(float(lat1))
    lat2 = math.radians(float(lat2))
    dlon = math.radians(float(lon2) - float(lon1))
    y = math.sin(dlon) * math.cos(lat2)
    x = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_diff_deg(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def compute_bearings(latlon):
    n = len(latlon)
    if n == 0:
        return np.array([], dtype=float)
    if n == 1:
        return np.array([0.0], dtype=float)

    out = np.zeros(n, dtype=float)
    for i in range(1, n):
        out[i] = bearing_deg(latlon[i - 1, 0], latlon[i - 1, 1], latlon[i, 0], latlon[i, 1])
    out[0] = out[1]
    return out


def confidence_to_tolerance(level):
    if level is None or pd.isna(level):
        return 0.0
    return float(CONFIDENCE_TOLERANCE_MAP_M.get(str(level).strip().upper(), 0.0))


# =========================================================
# DTW cost
# =========================================================
def local_cost(p1, p2, b1, b2, tol1_m=0.0, tol2_m=0.0):
    dist = haversine_m(p1[0], p1[1], p2[0], p2[1])
    ang = angle_diff_deg(b1, b2)

    tol_eff_m = max(
        0.0 if pd.isna(tol1_m) else float(tol1_m),
        0.0 if pd.isna(tol2_m) else float(tol2_m),
    )
    dist_eff = max(float(dist) - tol_eff_m, 0.0)

    cost = A_COST * (dist_eff / D0_M)
    if B_COST > 0:
        cost += B_COST * (ang / T0_DEG)
    return float(cost)


def dtw_distance_latlon(seq1_latlon, seq2_latlon, seq1_tol=None, seq2_tol=None):
    n = len(seq1_latlon)
    m = len(seq2_latlon)

    if n == 0 or m == 0:
        return np.inf

    if seq1_tol is None:
        seq1_tol = np.zeros(n, dtype=float)
    if seq2_tol is None:
        seq2_tol = np.zeros(m, dtype=float)

    b1 = compute_bearings(seq1_latlon)
    b2 = compute_bearings(seq2_latlon)

    dp = np.full((n + 1, m + 1), np.inf, dtype=float)
    dp[0, 0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = local_cost(
                seq1_latlon[i - 1],
                seq2_latlon[j - 1],
                b1[i - 1],
                b2[j - 1],
                seq1_tol[i - 1],
                seq2_tol[j - 1],
            )
            dp[i, j] = c + min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])

    return float(dp[n, m] / max(n, m, 1))


# =========================================================
# 컬럼 정규화
# =========================================================
def normalize_processed_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "longitude" in df.columns and "Longitude" not in df.columns:
        df["Longitude"] = df["longitude"]
    if "Longitude" in df.columns and "longitude" not in df.columns:
        df["longitude"] = df["Longitude"]
    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    elif "Datetime" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Datetime"], errors="coerce")
    return df


# =========================================================
# trip sequence 구성
# =========================================================
def build_trip_sequence_dict(points_df: pd.DataFrame, apply_tolerance=True):
    df = normalize_processed_columns(points_df)
    required = ["trip_id", "Latitude", "Longitude"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"points_df에 필수 컬럼이 없습니다: {col}")

    if "Timestamp" in df.columns:
        df = df.sort_values(["trip_id", "Timestamp"]).reset_index(drop=True)
    else:
        df["_row_order"] = np.arange(len(df))
        df = df.sort_values(["trip_id", "_row_order"]).reset_index(drop=True)

    seq_dict = {}
    for trip_id, group in df.groupby("trip_id", sort=False):
        group = group.dropna(subset=["Latitude", "Longitude"]).copy()
        if len(group) < 2:
            continue

        latlon = group[["Latitude", "Longitude"]].to_numpy(dtype=float)
        tol_m = np.zeros(len(group), dtype=float)

        if apply_tolerance:
            if "Predicted_confidence_level" in group.columns:
                conf_tol = group["Predicted_confidence_level"].apply(confidence_to_tolerance).to_numpy(dtype=float)
                if "Predicted_Latitude" in group.columns and "Predicted_longitude" in group.columns:
                    has_pred = (group["Predicted_Latitude"].notna() & group["Predicted_longitude"].notna()).to_numpy()
                else:
                    has_pred = np.zeros(len(group), dtype=bool)
                tol_m[has_pred] = np.maximum(tol_m[has_pred], conf_tol[has_pred])

            if "interp_method" in group.columns:
                methods = group["interp_method"].fillna("").astype(str).str.lower().to_numpy()
                for method, tol in INTERP_TOLERANCE_MAP_M.items():
                    mask = methods == method.lower()
                    tol_m[mask] = np.maximum(tol_m[mask], float(tol))

        seq_dict[str(trip_id)] = {"latlon": latlon, "tol_m": tol_m}

    return seq_dict


def build_trip_meta(summary_df: pd.DataFrame) -> pd.DataFrame:
    meta = summary_df.copy()
    if "trip_id" not in meta.columns:
        raise ValueError("summary_df에 trip_id 컬럼이 필요합니다.")
    if "od_key" not in meta.columns:
        raise ValueError("summary_df에 od_key 컬럼이 필요합니다.")
    meta["trip_id"] = meta["trip_id"].astype(str)
    meta["od_key"] = meta["od_key"].astype(str)
    return meta


def build_baseline_library(baseline_points, baseline_summary):
    baseline_seq_dict = build_trip_sequence_dict(baseline_points, apply_tolerance=False)
    meta = build_trip_meta(baseline_summary)
    meta = meta[meta["trip_id"].isin(baseline_seq_dict.keys())].copy()

    od_to_trip_ids = {}
    for od_key, group in meta.groupby("od_key", sort=False):
        trip_ids = group["trip_id"].astype(str).tolist()
        if len(trip_ids) >= MIN_BASELINES_PER_OD:
            od_to_trip_ids[str(od_key)] = trip_ids
    return baseline_seq_dict, od_to_trip_ids


def choose_top_k(n_candidates):
    if n_candidates <= 0:
        return 0
    if n_candidates >= TOP_K_DEFAULT:
        return TOP_K_DEFAULT
    if n_candidates == 1:
        return 1
    return min(2, n_candidates)


# =========================================================
# DTW score
# =========================================================
def score_one_test_trip(test_trip_id, test_seq, test_od_key, baseline_seq_dict, od_to_trip_ids):
    row = {
        "trip_id": test_trip_id,
        "od_key": test_od_key,
        "n_baselines": 0,
        "top_k": 0,
        "score_topk_mean": np.nan,
        "score_min": np.nan,
        "baseline_match_trip_ids": "",
        "status": "",
    }

    if test_od_key not in od_to_trip_ids:
        row["status"] = "unknown_od"
        return row

    dists = []
    for baseline_trip_id in od_to_trip_ids[test_od_key]:
        baseline_seq = baseline_seq_dict.get(baseline_trip_id)
        if baseline_seq is None:
            continue
        d = dtw_distance_latlon(
            test_seq["latlon"],
            baseline_seq["latlon"],
            test_seq["tol_m"],
            baseline_seq["tol_m"],
        )
        dists.append((baseline_trip_id, d))

    if len(dists) == 0:
        row["status"] = "no_valid_baseline"
        return row

    dists = sorted(dists, key=lambda x: x[1])
    k = choose_top_k(len(dists))
    topk = dists[:k]

    row["n_baselines"] = len(dists)
    row["top_k"] = k
    row["score_topk_mean"] = float(np.mean([x[1] for x in topk]))
    row["score_min"] = float(np.min([x[1] for x in dists]))
    row["baseline_match_trip_ids"] = ",".join([x[0] for x in topk])
    row["status"] = "ok"
    return row


def score_test_trips(baseline_points, baseline_summary, test_points, test_summary):
    baseline_seq_dict, od_to_trip_ids = build_baseline_library(baseline_points, baseline_summary)
    test_seq_dict = build_trip_sequence_dict(test_points, apply_tolerance=True)
    test_meta = build_trip_meta(test_summary)
    test_meta = test_meta[test_meta["trip_id"].isin(test_seq_dict.keys())].copy()

    rows = []
    for _, r in test_meta.iterrows():
        trip_id = str(r["trip_id"])
        od_key = str(r["od_key"])
        score_row = score_one_test_trip(trip_id, test_seq_dict[trip_id], od_key, baseline_seq_dict, od_to_trip_ids)
        merged = dict(r.to_dict())
        merged.update(score_row)
        rows.append(merged)
    return pd.DataFrame(rows)


def attach_threshold_and_flag(scored_df, threshold_df, threshold_col="score_p95"):
    if len(scored_df) == 0:
        return scored_df.copy()

    out = scored_df.copy()
    if len(threshold_df) == 0:
        out["threshold"] = np.nan
        out["is_anomaly"] = np.nan
        return out

    th = threshold_df[["od_key", threshold_col]].copy()
    th = th.rename(columns={threshold_col: "threshold"})
    out = out.merge(th, on="od_key", how="left")

    def decide(row):
        if row.get("status") != "ok":
            return np.nan
        if pd.isna(row.get("threshold")):
            return np.nan
        return int(row["score_topk_mean"] > row["threshold"])

    out["is_anomaly"] = out.apply(decide, axis=1)
    return out


def add_final_route_label_simple(scored_df, unseen_margin_ratio=UNSEEN_MARGIN_RATIO):
    out = scored_df.copy()

    def classify(row):
        status = row.get("status", "")
        score = row.get("score_topk_mean", np.nan)
        threshold = row.get("threshold", np.nan)

        if status != "ok":
            return "anomaly"
        if pd.isna(score) or pd.isna(threshold):
            return "anomaly"
        if score <= threshold:
            return "known_normal"
        if score <= threshold * unseen_margin_ratio:
            return "unseen_path_same_od"
        return "anomaly"

    out["final_route_label"] = out.apply(classify, axis=1)
    return out


# =========================================================
# Runtime class
# =========================================================
class AnomalyRuntime:
    def __init__(self, anomaly_model_path: str):
        self.artifact = joblib.load(anomaly_model_path)
        self.device_id = self.artifact["device_id"]
        self.version = self.artifact["version"]
        self.baseline_trip_points = self.artifact["baseline_trip_points"]
        self.baseline_trip_summary = self.artifact["baseline_trip_summary"]
        self.anchor_zones = self.artifact["anchor_zones"]
        self.od_thresholds = self.artifact["od_thresholds"]
        self.config = self.artifact.get("config", {})

    def make_test_trips(self, processed_df: pd.DataFrame):
        return extract_strict_test_trips(processed_df, self.anchor_zones)

    def predict_from_processed_gps(self, processed_df: pd.DataFrame) -> pd.DataFrame:
        test_trip_points, test_trip_summary = self.make_test_trips(processed_df)

        if len(test_trip_points) == 0 or len(test_trip_summary) == 0:
            return pd.DataFrame([{
                "status": "no_trip_detected",
                "final_route_label": "anomaly",
            }])

        scored_df = score_test_trips(
            baseline_points=self.baseline_trip_points,
            baseline_summary=self.baseline_trip_summary,
            test_points=test_trip_points,
            test_summary=test_trip_summary,
        )

        scored_df = attach_threshold_and_flag(
            scored_df=scored_df,
            threshold_df=self.od_thresholds,
            threshold_col="score_p95",
        )

        scored_df = add_final_route_label_simple(
            scored_df,
            unseen_margin_ratio=self.config.get("UNSEEN_MARGIN_RATIO", UNSEEN_MARGIN_RATIO),
        )
        return scored_df


# =========================================================
# strict anchor-context extractor override
# =========================================================
def extract_strict_test_trips(processed_df: pd.DataFrame, anchor_zones: pd.DataFrame):
    """
    기존 make_test_baseline_0409_2.py의 핵심 정책을 서버용으로 반영한 trip extractor.

    - test에서 anchor를 새로 만들지 않고 anomaly artifact의 train anchor를 그대로 사용
    - nearest fallback 없음: anchor 반경 안에 실제로 들어온 경우만 OD로 인정
    - origin/destination은 MOVE block 경계점 하나가 아니라 block 전후 문맥으로 판정
    - train에 없는 새로운 장소는 od_key=None으로 남고, score 단계에서 unknown_od/anomaly 처리
    """
    df = normalize_processed_columns(processed_df)
    if "state_primary" not in df.columns:
        raise ValueError("processed_df에 state_primary 컬럼이 필요합니다.")
    if "device_id" not in df.columns:
        raise ValueError("processed_df에 device_id 컬럼이 필요합니다.")

    MIN_MOVE_STEPS_LOCAL = 2
    MIN_NET_DISP_M_LOCAL = 100.0
    MIN_PATH_LEN_M_LOCAL = 200.0
    SHORT_MIN_DISP_M = 30.0
    SHORT_MIN_PATH_M = 30.0
    SHORT_MIN_DWELL_MIN = 10.0
    SAME_PLACE_EPS_M = 30.0
    PRE_CONTEXT_STEPS = 6
    POST_CONTEXT_STEPS = 6

    def anchor_cols(anchors):
        if anchors is None or len(anchors) == 0:
            return None
        if {"anchor_id", "anchor_lat", "anchor_lon", "anchor_radius_m"}.issubset(anchors.columns):
            return "anchor_id", "anchor_lat", "anchor_lon", "anchor_radius_m"
        if {"zone_id", "center_lat", "center_lon", "radius_m"}.issubset(anchors.columns):
            return "zone_id", "center_lat", "center_lon", "radius_m"
        return None

    def anchors_for_device(anchors, device_id):
        if anchors is None or len(anchors) == 0:
            return pd.DataFrame()
        sub = anchors.copy()
        if "device_id" in sub.columns and sub["device_id"].notna().any():
            sub = sub[sub["device_id"].astype(str) == str(device_id)].copy()
        return sub

    def inside_anchor_ids(lat, lon, anchors, device_id):
        if pd.isna(lat) or pd.isna(lon):
            return []
        sub = anchors_for_device(anchors, device_id)
        cols = anchor_cols(sub)
        if cols is None:
            return []
        id_col, lat_col, lon_col, r_col = cols
        found = []
        for _, a in sub.iterrows():
            if pd.isna(a[lat_col]) or pd.isna(a[lon_col]) or pd.isna(a[r_col]):
                continue
            d = haversine_m(lat, lon, a[lat_col], a[lon_col])
            if d <= float(a[r_col]):
                found.append((int(a[id_col]), float(d)))
        found.sort(key=lambda x: x[1])
        return found

    def contiguous_blocks(mask):
        idx = np.where(mask.to_numpy())[0]
        if len(idx) == 0:
            return []
        blocks = []
        s = int(idx[0])
        for i in range(1, len(idx)):
            if idx[i] != idx[i - 1] + 1:
                blocks.append((s, int(idx[i - 1])))
                s = int(idx[i])
        blocks.append((s, int(idx[-1])))
        return blocks

    def dist_row(g, i, j):
        return haversine_m(g.iloc[i]["Latitude"], g.iloc[i]["Longitude"], g.iloc[j]["Latitude"], g.iloc[j]["Longitude"])

    def block_path_m(g, s, e):
        if e <= s:
            return 0.0
        total = 0.0
        for i in range(s + 1, e + 1):
            total += dist_row(g, i - 1, i)
        return float(total)

    def block_net_m(g, s, e):
        if e <= s:
            return 0.0
        return float(dist_row(g, s, e))

    def with_prev_stop_path(g, s, e):
        if s - 1 >= 0 and str(g.iloc[s - 1]["state_primary"]) == "STOP":
            return block_path_m(g, s - 1, e)
        return block_path_m(g, s, e)

    def with_prev_stop_net(g, s, e):
        if s - 1 >= 0 and str(g.iloc[s - 1]["state_primary"]) == "STOP":
            return block_net_m(g, s - 1, e)
        return block_net_m(g, s, e)

    def find_origin_context(g, block_s, trim_s, device_id):
        search_s = max(0, block_s - PRE_CONTEXT_STEPS)
        search_e = trim_s
        last_found = None
        for i in range(search_s, search_e + 1):
            inside = inside_anchor_ids(g.iloc[i]["Latitude"], g.iloc[i]["Longitude"], anchor_zones, device_id)
            if inside:
                last_found = inside[0]
        if last_found is None:
            return None, None
        return last_found[0], last_found[1]

    def find_dest_context(g, trim_e, block_e, device_id):
        search_s = trim_e
        search_e = min(len(g) - 1, block_e + POST_CONTEXT_STEPS)
        for i in range(search_s, search_e + 1):
            inside = inside_anchor_ids(g.iloc[i]["Latitude"], g.iloc[i]["Longitude"], anchor_zones, device_id)
            if inside:
                return inside[0][0], inside[0][1]
        return None, None

    def nearest_pre_stop(g, block_s):
        for i in range(block_s - 1, -1, -1):
            if str(g.iloc[i]["state_primary"]) == "STOP":
                return i
            if str(g.iloc[i]["state_primary"]) == "MOVE":
                break
        return None

    def post_stop_run(g, block_e):
        s = block_e + 1
        if s >= len(g) or str(g.iloc[s]["state_primary"]) != "STOP":
            return None, None
        e = s
        while e + 1 < len(g) and str(g.iloc[e + 1]["state_primary"]) == "STOP":
            e += 1
        return s, e

    def dwell_min(g, s, e):
        if s is None or e is None or s > e:
            return 0.0
        t0 = pd.to_datetime(g.iloc[s]["Timestamp"], errors="coerce")
        t1 = pd.to_datetime(g.iloc[e]["Timestamp"], errors="coerce")
        if pd.isna(t0) or pd.isna(t1):
            return 0.0
        return float((t1 - t0).total_seconds() / 60.0)

    def short_trip_ok(g, block_s, block_e, device_id):
        info = {
            "short_disp_m": np.nan,
            "short_path_m": np.nan,
            "short_dwell_min": 0.0,
            "short_pre_stop_idx": np.nan,
            "short_post_stop_start_idx": np.nan,
            "short_post_stop_end_idx": np.nan,
        }
        if block_e - block_s + 1 < 2:
            return False, info
        pre_idx = nearest_pre_stop(g, block_s)
        post_s, post_e = post_stop_run(g, block_e)
        if pre_idx is None or post_s is None:
            return False, info
        dm = dwell_min(g, post_s, post_e)
        disp = dist_row(g, pre_idx, post_s)
        path = with_prev_stop_path(g, block_s, block_e)
        o_id, _ = find_origin_context(g, block_s, block_s, device_id)
        d_id, _ = find_dest_context(g, block_e, block_e, device_id)
        same_anchor = o_id is not None and d_id is not None and int(o_id) == int(d_id)
        same_place = disp < SAME_PLACE_EPS_M
        info.update({
            "short_disp_m": disp,
            "short_path_m": path,
            "short_dwell_min": dm,
            "short_pre_stop_idx": pre_idx,
            "short_post_stop_start_idx": post_s,
            "short_post_stop_end_idx": post_e,
        })
        if dm < SHORT_MIN_DWELL_MIN:
            return False, info
        if disp < SHORT_MIN_DISP_M or path < SHORT_MIN_PATH_M:
            return False, info
        if same_anchor or same_place:
            return False, info
        return True, info

    df = df.dropna(subset=["Timestamp"]).copy()
    df = df.sort_values(["device_id", "Timestamp"]).reset_index(drop=True)

    trip_points = []
    trip_summary = []

    for device_id, group in df.groupby("device_id", sort=True):
        g = normalize_processed_columns(group).dropna(subset=["Latitude", "Longitude"]).copy().reset_index(drop=True)
        if len(g) == 0:
            continue

        blocks = contiguous_blocks(g["state_primary"].astype(str).eq("MOVE"))
        trip_seq = 1

        for block_s, block_e in blocks:
            raw_steps = int(block_e - block_s + 1)
            raw_path = block_path_m(g, block_s, block_e)
            raw_net = block_net_m(g, block_s, block_e)
            raw_path_prev = with_prev_stop_path(g, block_s, block_e)
            raw_net_prev = with_prev_stop_net(g, block_s, block_e)

            is_long = raw_steps >= MIN_MOVE_STEPS_LOCAL and (
                raw_net >= MIN_NET_DISP_M_LOCAL or raw_path >= MIN_PATH_LEN_M_LOCAL or
                raw_net_prev >= MIN_NET_DISP_M_LOCAL or raw_path_prev >= MIN_PATH_LEN_M_LOCAL
            )
            is_short, short_info = short_trip_ok(g, block_s, block_e, device_id)
            if not is_long and not is_short:
                continue

            # 기존 test 코드 정책: start는 첫 MOVE 유지, end는 block 끝 기준으로 두되 OD는 전후 문맥으로 판정
            trim_s = block_s
            trim_e = block_e
            if trim_s >= trim_e:
                continue

            trim_steps = int(trim_e - trim_s + 1)
            trim_path = block_path_m(g, trim_s, trim_e)
            trim_net = block_net_m(g, trim_s, trim_e)

            if is_long:
                if trim_steps < MIN_MOVE_STEPS_LOCAL:
                    continue
                if trim_net < MIN_NET_DISP_M_LOCAL and trim_path < MIN_PATH_LEN_M_LOCAL:
                    if raw_net_prev < MIN_NET_DISP_M_LOCAL and raw_path_prev < MIN_PATH_LEN_M_LOCAL:
                        continue
            else:
                if trim_steps < 2:
                    continue

            origin_id, origin_dist = find_origin_context(g, block_s, trim_s, device_id)
            dest_id, dest_dist = find_dest_context(g, trim_e, block_e, device_id)

            trip_id = f"uid{device_id}_trip{trip_seq}"
            od_key = None
            if origin_id is not None and dest_id is not None:
                od_key = f"uid{device_id}_O{origin_id}_D{dest_id}"

            sub = g.iloc[trim_s:trim_e + 1].copy().reset_index(drop=True)
            sub["trip_id"] = trip_id
            sub["trip_seq"] = trip_seq
            sub["od_key"] = od_key
            sub["origin_anchor_id"] = origin_id
            sub["dest_anchor_id"] = dest_id
            sub["origin_anchor_dist_m"] = origin_dist
            sub["dest_anchor_dist_m"] = dest_dist
            sub["is_test_trip"] = 1
            sub["trip_type"] = "long_trip" if is_long else "short_trip"
            trip_points.append(sub)

            t0 = pd.to_datetime(sub.iloc[0]["Timestamp"], errors="coerce")
            t1 = pd.to_datetime(sub.iloc[-1]["Timestamp"], errors="coerce")
            duration = (t1 - t0).total_seconds() / 60.0 if pd.notna(t0) and pd.notna(t1) else np.nan
            trip_summary.append({
                "device_id": device_id,
                "trip_id": trip_id,
                "trip_seq": trip_seq,
                "trip_type": "long_trip" if is_long else "short_trip",
                "od_key": od_key,
                "origin_anchor_id": origin_id,
                "dest_anchor_id": dest_id,
                "origin_anchor_dist_m": origin_dist,
                "dest_anchor_dist_m": dest_dist,
                "raw_block_start_idx": block_s,
                "raw_block_end_idx": block_e,
                "trim_start_idx": trim_s,
                "trim_end_idx": trim_e,
                "raw_steps": raw_steps,
                "raw_path_m": raw_path,
                "raw_net_m": raw_net,
                "raw_path_with_prev_stop_m": raw_path_prev,
                "raw_net_with_prev_stop_m": raw_net_prev,
                "trim_steps": trim_steps,
                "trim_path_m": trim_path,
                "trim_net_m": trim_net,
                "short_pre_stop_idx": short_info.get("short_pre_stop_idx", np.nan),
                "short_post_stop_start_idx": short_info.get("short_post_stop_start_idx", np.nan),
                "short_post_stop_end_idx": short_info.get("short_post_stop_end_idx", np.nan),
                "short_disp_m": short_info.get("short_disp_m", np.nan),
                "short_path_m": short_info.get("short_path_m", np.nan),
                "short_dwell_min": short_info.get("short_dwell_min", np.nan),
                "start_time": t0,
                "end_time": t1,
                "duration_min": duration,
                "n_points": len(sub),
            })
            trip_seq += 1

    trip_points_df = pd.concat(trip_points, ignore_index=True, sort=False) if trip_points else pd.DataFrame()
    trip_summary_df = pd.DataFrame(trip_summary)
    return trip_points_df, trip_summary_df
