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

수정 사항
- 너무 짧은 MOVE block이 trip으로 생성되는 문제 방지
- 5분 단위 데이터 기준, 첫 MOVE부터 다음 STOP/non-MOVE까지 30분 이상일 때만 trip 생성
- 마지막이 MOVE로 끝나는 경우는 아직 이동 중이므로 trip 생성하지 않음
"""

import math
import joblib
import numpy as np
import pandas as pd


TOP_K_DEFAULT = 3
MIN_BASELINES_PER_OD = 1

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
        out[i] = bearing_deg(
            latlon[i - 1, 0],
            latlon[i - 1, 1],
            latlon[i, 0],
            latlon[i, 1],
        )
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

    # 현재는 B_COST = 0.0 이므로 방향각 비용은 실제로 반영되지 않음
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
            dp[i, j] = c + min(
                dp[i - 1, j],
                dp[i, j - 1],
                dp[i - 1, j - 1],
            )

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
                conf_tol = (
                    group["Predicted_confidence_level"]
                    .apply(confidence_to_tolerance)
                    .to_numpy(dtype=float)
                )

                if "Predicted_Latitude" in group.columns and "Predicted_longitude" in group.columns:
                    has_pred = (
                        group["Predicted_Latitude"].notna()
                        & group["Predicted_longitude"].notna()
                    ).to_numpy()
                else:
                    has_pred = np.zeros(len(group), dtype=bool)

                tol_m[has_pred] = np.maximum(tol_m[has_pred], conf_tol[has_pred])

            if "interp_method" in group.columns:
                methods = (
                    group["interp_method"]
                    .fillna("")
                    .astype(str)
                    .str.lower()
                    .to_numpy()
                )

                for method, tol in INTERP_TOLERANCE_MAP_M.items():
                    mask = methods == method.lower()
                    tol_m[mask] = np.maximum(tol_m[mask], float(tol))

        seq_dict[str(trip_id)] = {
            "latlon": latlon,
            "tol_m": tol_m,
        }

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
    baseline_seq_dict = build_trip_sequence_dict(
        baseline_points,
        apply_tolerance=False,
    )

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

def score_one_test_trip(
    test_trip_id,
    test_seq,
    test_od_key,
    baseline_seq_dict,
    od_to_trip_ids,
):
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


def score_test_trips(
    baseline_points,
    baseline_summary,
    test_points,
    test_summary,
):
    baseline_seq_dict, od_to_trip_ids = build_baseline_library(
        baseline_points,
        baseline_summary,
    )

    test_seq_dict = build_trip_sequence_dict(
        test_points,
        apply_tolerance=True,
    )

    test_meta = build_trip_meta(test_summary)
    test_meta = test_meta[test_meta["trip_id"].isin(test_seq_dict.keys())].copy()

    rows = []

    for _, r in test_meta.iterrows():
        trip_id = str(r["trip_id"])
        od_key = str(r["od_key"])

        score_row = score_one_test_trip(
            trip_id,
            test_seq_dict[trip_id],
            od_key,
            baseline_seq_dict,
            od_to_trip_ids,
        )

        merged = dict(r.to_dict())
        merged.update(score_row)
        rows.append(merged)

    return pd.DataFrame(rows)


def attach_threshold_and_flag(
    scored_df,
    threshold_df,
    threshold_col="score_p95",
):
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


def add_final_route_label_simple(
    scored_df,
    unseen_margin_ratio=UNSEEN_MARGIN_RATIO,
):
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
        return extract_strict_test_trips(
            processed_df,
            self.anchor_zones,
        )

    def predict_from_processed_gps(self, processed_df: pd.DataFrame) -> pd.DataFrame:
        test_trip_points, test_trip_summary = self.make_test_trips(processed_df)

        if len(test_trip_points) == 0 or len(test_trip_summary) == 0:
            return pd.DataFrame([
                {
                    "status": "no_trip_detected",
                    "final_route_label": "anomaly",
                }
            ])

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
            unseen_margin_ratio=self.config.get(
                "UNSEEN_MARGIN_RATIO",
                UNSEEN_MARGIN_RATIO,
            ),
        )

        return scored_df

def extract_strict_test_trips(
    processed_df: pd.DataFrame,
    anchor_zones: pd.DataFrame,
):
    """
    baseline 생성 코드와 같은 방식으로 test trip을 추출한다.

    baseline과 맞춘 핵심 정책:
    - MOVE block 추출
    - block 시작/끝 좌표에 가장 가까운 train anchor를 origin/dest로 지정
    - origin anchor에서 충분히 벗어난 지점으로 start trim
    - destination anchor에 도착하기 직전으로 end trim
    - trim된 구간만 DTW sequence로 사용

    서버용 추가 정책:
    - sliding window 중간에서 시작한 MOVE block은 제외
    - 5분 단위 데이터 기준 30분 미만 MOVE는 제외
    - 마지막이 MOVE로 끝난 경우는 아직 이동 중이므로 제외
    """

    df = normalize_processed_columns(processed_df)

    if "state_primary" not in df.columns:
        raise ValueError("processed_df에 state_primary 컬럼이 필요합니다.")

    if "device_id" not in df.columns:
        raise ValueError("processed_df에 device_id 컬럼이 필요합니다.")

    # =====================================================
    # baseline 코드와 맞춘 설정값
    # =====================================================
    MIN_MOVE_STEPS_LOCAL = 3
    MIN_NET_DISP_M_LOCAL = 300.0
    MIN_PATH_LEN_M_LOCAL = 500.0

    START_EXIT_DIST_M = 30.0
    START_MIN_PATH_FROM_BLOCK_START_M = 150.0
    START_KEEP_MOVE_STEPS = 2

    END_STOP_CONFIRM_STEPS = 2
    END_LOW_SPEED_MPS = 0.5
    END_MIN_DWELL_MIN = 10.0

    REQUIRE_STOP_AROUND_BLOCK = False

    # 서버에서 짧은 MOVE가 anomaly로 저장되는 문제 방지용
    MIN_MOVE_DURATION_MIN_LOCAL = 30.0

    def norm_state(value):
        return str(value).strip().upper()

    def is_move(value):
        return norm_state(value) == "MOVE"

    def is_stop(value):
        return norm_state(value) == "STOP"

    # =====================================================
    # anchor DataFrame 정규화
    # =====================================================
    def normalize_anchor_table(anchors, device_id):
        if anchors is None or len(anchors) == 0:
            return pd.DataFrame()

        sub = anchors.copy()

        if "device_id" in sub.columns and sub["device_id"].notna().any():
            sub = sub[sub["device_id"].astype(str) == str(device_id)].copy()

        if {
            "anchor_id",
            "anchor_lat",
            "anchor_lon",
            "anchor_radius_m",
        }.issubset(sub.columns):
            out = sub[
                ["anchor_id", "anchor_lat", "anchor_lon", "anchor_radius_m"]
            ].copy()

        elif {
            "zone_id",
            "center_lat",
            "center_lon",
            "radius_m",
        }.issubset(sub.columns):
            out = sub[
                ["zone_id", "center_lat", "center_lon", "radius_m"]
            ].copy()
            out = out.rename(
                columns={
                    "zone_id": "anchor_id",
                    "center_lat": "anchor_lat",
                    "center_lon": "anchor_lon",
                    "radius_m": "anchor_radius_m",
                }
            )

        else:
            return pd.DataFrame()

        out = out.dropna(
            subset=["anchor_id", "anchor_lat", "anchor_lon", "anchor_radius_m"]
        ).copy()

        if len(out) == 0:
            return pd.DataFrame()

        out["anchor_id"] = out["anchor_id"].astype(int)
        out["anchor_lat"] = out["anchor_lat"].astype(float)
        out["anchor_lon"] = out["anchor_lon"].astype(float)
        out["anchor_radius_m"] = out["anchor_radius_m"].astype(float)

        return out.reset_index(drop=True)

    def nearest_anchor(lat, lon, anchors_norm):
        if anchors_norm is None or len(anchors_norm) == 0:
            return None, None

        if pd.isna(lat) or pd.isna(lon):
            return None, None

        best_id = None
        best_dist = float("inf")

        for _, a in anchors_norm.iterrows():
            d = haversine_m(
                lat,
                lon,
                a["anchor_lat"],
                a["anchor_lon"],
            )

            if d < best_dist:
                best_dist = d
                best_id = int(a["anchor_id"])

        return best_id, float(best_dist)

    def get_anchor_by_id(anchor_id, anchors_norm):
        if anchor_id is None:
            return None

        if anchors_norm is None or len(anchors_norm) == 0:
            return None

        sub = anchors_norm[anchors_norm["anchor_id"].astype(int) == int(anchor_id)]

        if len(sub) == 0:
            return None

        return sub.iloc[0]

    def point_inside_anchor(lat, lon, anchor):
        if anchor is None:
            return False

        if pd.isna(lat) or pd.isna(lon):
            return False

        d = haversine_m(
            lat,
            lon,
            anchor["anchor_lat"],
            anchor["anchor_lon"],
        )

        return bool(d <= float(anchor["anchor_radius_m"]))

    # =====================================================
    # 기본 거리 / 시간 유틸
    # =====================================================
    def pair_dist_m(lat1, lon1, lat2, lon2):
        if any(pd.isna(x) for x in [lat1, lon1, lat2, lon2]):
            return np.nan

        return float(haversine_m(lat1, lon1, lat2, lon2))

    def compute_step_distance(g):
        out = [0.0]

        for i in range(1, len(g)):
            d = pair_dist_m(
                g.iloc[i - 1]["Latitude"],
                g.iloc[i - 1]["Longitude"],
                g.iloc[i]["Latitude"],
                g.iloc[i]["Longitude"],
            )

            out.append(0.0 if pd.isna(d) else d)

        return pd.Series(out, index=g.index, dtype=float)

    def compute_step_seconds(g):
        t = pd.to_datetime(g["Timestamp"], errors="coerce")
        dt = t.diff().dt.total_seconds().fillna(0.0)
        dt = dt.clip(lower=0.0)

        return dt.astype(float)

    def compute_speed_mps(g):
        dist = compute_step_distance(g)
        secs = compute_step_seconds(g)

        speed = dist / secs.replace(0.0, np.nan)
        speed = speed.replace([np.inf, -np.inf], np.nan).fillna(0.0)

        return speed.astype(float)

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

    def block_path_m(g, s, e):
        if e <= s:
            return 0.0

        total = 0.0

        for i in range(s + 1, e + 1):
            d = pair_dist_m(
                g.iloc[i - 1]["Latitude"],
                g.iloc[i - 1]["Longitude"],
                g.iloc[i]["Latitude"],
                g.iloc[i]["Longitude"],
            )

            total += 0.0 if pd.isna(d) else d

        return float(total)

    def block_net_m(g, s, e):
        if e <= s:
            return 0.0

        d = pair_dist_m(
            g.iloc[s]["Latitude"],
            g.iloc[s]["Longitude"],
            g.iloc[e]["Latitude"],
            g.iloc[e]["Longitude"],
        )

        return 0.0 if pd.isna(d) else float(d)

    def block_steps(s, e):
        return int(e - s + 1)

    def has_stop_before_after(g, s, e):
        before_ok = s - 1 >= 0 and is_stop(g.iloc[s - 1]["state_primary"])
        after_ok = e + 1 < len(g) and is_stop(g.iloc[e + 1]["state_primary"])

        return bool(before_ok and after_ok)

    # =====================================================
    # 서버용 완료 MOVE 확인
    # =====================================================
    def next_non_move_idx(g, block_e):
        next_idx = block_e + 1

        if next_idx >= len(g):
            return None

        if not is_move(g.iloc[next_idx]["state_primary"]):
            return next_idx

        return None

    def completed_move_duration_min(g, block_s, block_e):
        end_context_idx = next_non_move_idx(g, block_e)

        if end_context_idx is None:
            return None

        t0 = pd.to_datetime(g.iloc[block_s]["Timestamp"], errors="coerce")
        t1 = pd.to_datetime(g.iloc[end_context_idx]["Timestamp"], errors="coerce")

        if pd.isna(t0) or pd.isna(t1):
            return None

        return float((t1 - t0).total_seconds() / 60.0)

    # =====================================================
    # baseline과 같은 origin/dest anchor 지정
    # =====================================================
    def assign_nearest_anchor_to_block_ends(g, block_s, block_e, anchors_norm):
        s_lat = g.iloc[block_s]["Latitude"]
        s_lon = g.iloc[block_s]["Longitude"]
        e_lat = g.iloc[block_e]["Latitude"]
        e_lon = g.iloc[block_e]["Longitude"]

        origin_id, origin_dist = nearest_anchor(s_lat, s_lon, anchors_norm)
        dest_id, dest_dist = nearest_anchor(e_lat, e_lon, anchors_norm)

        return origin_id, dest_id, origin_dist, dest_dist

    # =====================================================
    # baseline과 같은 start trimming
    # =====================================================
    def find_trip_start_idx(g, block_s, block_e, origin_anchor):
        """
        baseline 코드 기준:
        - origin anchor 밖
        - anchor 중심에서 START_EXIT_DIST_M 이상
        - block 시작 후 누적 이동 START_MIN_PATH_FROM_BLOCK_START_M 이상
        - 이후 START_KEEP_MOVE_STEPS 만큼 MOVE 유지
        """

        if block_s > block_e:
            return None

        cum_path = 0.0
        prev_lat = g.iloc[block_s]["Latitude"]
        prev_lon = g.iloc[block_s]["Longitude"]

        for i in range(block_s, block_e + 1):
            lat = g.iloc[i]["Latitude"]
            lon = g.iloc[i]["Longitude"]

            if i > block_s:
                step_d = pair_dist_m(prev_lat, prev_lon, lat, lon)
                cum_path += 0.0 if pd.isna(step_d) else step_d
                prev_lat = lat
                prev_lon = lon

            keep_move_ok = True

            for k in range(START_KEEP_MOVE_STEPS):
                j = i + k

                if j > block_e or not is_move(g.iloc[j]["state_primary"]):
                    keep_move_ok = False
                    break

            if not keep_move_ok:
                continue

            if origin_anchor is not None:
                d_anchor = haversine_m(
                    lat,
                    lon,
                    origin_anchor["anchor_lat"],
                    origin_anchor["anchor_lon"],
                )

                outside_anchor = d_anchor > float(origin_anchor["anchor_radius_m"])
                far_enough = d_anchor >= START_EXIT_DIST_M

                if (
                    outside_anchor
                    and far_enough
                    and cum_path >= START_MIN_PATH_FROM_BLOCK_START_M
                ):
                    return i

            else:
                if cum_path >= START_MIN_PATH_FROM_BLOCK_START_M:
                    return i

        return block_s

    # =====================================================
    # baseline과 같은 destination dwell 확인
    # =====================================================
    def confirm_destination_dwell(g, enter_idx, dest_anchor):
        if dest_anchor is None:
            return False

        last_idx = min(
            len(g) - 1,
            enter_idx + max(END_STOP_CONFIRM_STEPS + 2, 4),
        )

        stop_count = 0
        inside_idxs = []

        speed = compute_speed_mps(g)

        for i in range(enter_idx, last_idx + 1):
            lat = g.iloc[i]["Latitude"]
            lon = g.iloc[i]["Longitude"]

            if point_inside_anchor(lat, lon, dest_anchor):
                inside_idxs.append(i)

                if is_stop(g.iloc[i]["state_primary"]):
                    stop_count += 1

        if len(inside_idxs) == 0:
            return False

        if stop_count >= END_STOP_CONFIRM_STEPS:
            return True

        mean_speed = (
            float(speed.iloc[inside_idxs].mean())
            if len(inside_idxs) > 0
            else np.inf
        )

        if mean_speed <= END_LOW_SPEED_MPS:
            return True

        t = pd.to_datetime(g["Timestamp"], errors="coerce")

        dwell_min = 0.0
        if len(inside_idxs) >= 2:
            dwell_min = (
                t.iloc[inside_idxs[-1]] - t.iloc[inside_idxs[0]]
            ).total_seconds() / 60.0

        if dwell_min >= END_MIN_DWELL_MIN:
            return True

        return False

    def find_trip_end_idx(g, block_s, block_e, dest_anchor):
        """
        baseline 코드 기준:
        destination anchor에 확실히 도착하기 직전 점을 end로 사용.
        """

        if block_s > block_e:
            return None

        if dest_anchor is not None:
            for i in range(block_s, block_e + 1):
                lat = g.iloc[i]["Latitude"]
                lon = g.iloc[i]["Longitude"]

                if point_inside_anchor(lat, lon, dest_anchor):
                    if confirm_destination_dwell(g, i, dest_anchor):
                        return max(block_s, i - 1)

        return block_e

    # =====================================================
    # 본 처리
    # =====================================================
    df = df.dropna(subset=["Timestamp"]).copy()
    df = df.sort_values(["device_id", "Timestamp"]).reset_index(drop=True)

    trip_points = []
    trip_summary = []

    for device_id, group in df.groupby("device_id", sort=True):
        g = (
            normalize_processed_columns(group)
            .dropna(subset=["Latitude", "Longitude"])
            .copy()
            .reset_index(drop=True)
        )

        if len(g) == 0:
            continue

        anchors_norm = normalize_anchor_table(anchor_zones, device_id)

        if anchors_norm.empty:
            continue

        move_mask = g["state_primary"].apply(is_move)
        move_blocks = contiguous_blocks(move_mask)

        trip_seq = 1

        for block_s, block_e in move_blocks:
            # sliding window가 MOVE 중간에서 시작한 경우 제외
            if block_s == 0:
                continue

            if REQUIRE_STOP_AROUND_BLOCK and not has_stop_before_after(g, block_s, block_e):
                continue

            raw_steps = block_steps(block_s, block_e)
            raw_path_m = block_path_m(g, block_s, block_e)
            raw_net_m = block_net_m(g, block_s, block_e)

            if raw_steps < MIN_MOVE_STEPS_LOCAL:
                continue

            if raw_net_m < MIN_NET_DISP_M_LOCAL:
                continue

            if raw_path_m < MIN_PATH_LEN_M_LOCAL:
                continue

            move_duration_min = completed_move_duration_min(g, block_s, block_e)

            if move_duration_min is None:
                continue

            if move_duration_min < MIN_MOVE_DURATION_MIN_LOCAL:
                continue

            origin_anchor_id, dest_anchor_id, origin_anchor_dist_m, dest_anchor_dist_m = (
                assign_nearest_anchor_to_block_ends(
                    g,
                    block_s,
                    block_e,
                    anchors_norm,
                )
            )

            if origin_anchor_id is None or dest_anchor_id is None:
                continue

            origin_anchor = get_anchor_by_id(origin_anchor_id, anchors_norm)
            dest_anchor = get_anchor_by_id(dest_anchor_id, anchors_norm)

            trim_s = find_trip_start_idx(
                g,
                block_s,
                block_e,
                origin_anchor,
            )

            trim_e = find_trip_end_idx(
                g,
                block_s,
                block_e,
                dest_anchor,
            )

            if trim_s is None or trim_e is None:
                continue

            if trim_s >= trim_e:
                continue

            trim_steps = block_steps(trim_s, trim_e)
            trim_path_m = block_path_m(g, trim_s, trim_e)
            trim_net_m = block_net_m(g, trim_s, trim_e)

            if trim_steps < MIN_MOVE_STEPS_LOCAL:
                continue

            if trim_net_m < MIN_NET_DISP_M_LOCAL:
                continue

            if trim_path_m < MIN_PATH_LEN_M_LOCAL:
                continue

            trip_id = f"{device_id}_trip{trip_seq}"
            od_key = f"{device_id}_O{origin_anchor_id}_D{dest_anchor_id}"

            t0 = pd.to_datetime(g.iloc[trim_s]["Timestamp"], errors="coerce")
            t1 = pd.to_datetime(g.iloc[trim_e]["Timestamp"], errors="coerce")

            duration_min = (
                (t1 - t0).total_seconds() / 60.0
                if pd.notna(t0) and pd.notna(t1)
                else np.nan
            )

            sub = g.iloc[trim_s:trim_e + 1].copy().reset_index(drop=True)

            sub["trip_id"] = trip_id
            sub["trip_seq"] = trip_seq
            sub["od_key"] = od_key
            sub["origin_anchor_id"] = origin_anchor_id
            sub["dest_anchor_id"] = dest_anchor_id
            sub["origin_anchor_dist_m"] = origin_anchor_dist_m
            sub["dest_anchor_dist_m"] = dest_anchor_dist_m
            sub["is_test_trip"] = 1
            sub["trip_type"] = "baseline_matched_trim"
            sub["move_duration_min"] = move_duration_min
            sub["min_move_duration_min"] = MIN_MOVE_DURATION_MIN_LOCAL

            trip_points.append(sub)

            trip_summary.append(
                {
                    "device_id": device_id,
                    "trip_id": trip_id,
                    "trip_seq": trip_seq,
                    "trip_type": "baseline_matched_trim",
                    "od_key": od_key,
                    "origin_anchor_id": origin_anchor_id,
                    "dest_anchor_id": dest_anchor_id,
                    "origin_anchor_dist_m": origin_anchor_dist_m,
                    "dest_anchor_dist_m": dest_anchor_dist_m,
                    "raw_block_start_idx": block_s,
                    "raw_block_end_idx": block_e,
                    "trim_start_idx": trim_s,
                    "trim_end_idx": trim_e,
                    "raw_steps": raw_steps,
                    "raw_path_m": raw_path_m,
                    "raw_net_m": raw_net_m,
                    "trim_steps": trim_steps,
                    "trim_path_m": trim_path_m,
                    "trim_net_m": trim_net_m,
                    "move_duration_min": move_duration_min,
                    "min_move_duration_min": MIN_MOVE_DURATION_MIN_LOCAL,
                    "start_time": t0,
                    "end_time": t1,
                    "duration_min": duration_min,
                    "n_points": len(sub),
                }
            )

            trip_seq += 1

    trip_points_df = (
        pd.concat(trip_points, ignore_index=True, sort=False)
        if trip_points
        else pd.DataFrame()
    )

    trip_summary_df = pd.DataFrame(trip_summary)

    return trip_points_df, trip_summary_df