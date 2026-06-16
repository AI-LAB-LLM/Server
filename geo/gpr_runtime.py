# -*- coding: utf-8 -*-
"""
gpr_runtime.py

주의
- GPR 모델은 WINDOW_SIZE=8 기반이므로 단일 GPS 1건만으로는 예측할 수 없습니다.
- 5분 주기 데이터 기준 최소 최근 40분 데이터가 필요합니다.
- 결측/null/jump 제거를 고려해 서버에서는 최근 60분 데이터를 조회해 이 런타임에 넣는 것을 권장합니다.
"""

import os
import math
import joblib
import numpy as np
import pandas as pd


WINDOW_SIZE = 8

RAW_LAT_COL = "Raw_Latitude"
RAW_LON_COL = "Raw_longitude"
GPS_QUALITY_COL = "gps_quality"
GPS_DECISION_COL = "gps_filter_decision"
USE_RAW_FOR_GPR_COL = "use_raw_for_gpr"
INTERP_METHOD_COL = "interp_method"

JUMP_HARD_REJECT_DIST_M = 1000.0
JUMP_HARD_REJECT_SPEED_KMPH = 120.0
JUMP_SUSPECT_DIST_M = 500.0
JUMP_SUSPECT_SPEED_KMPH = 80.0
JUMP_ACTIVE_CLUSTER_RADIUS_M = 1500.0
JUMP_RETURN_MAX_SPEED_KMPH = 80.0

STOPPAGE_THRESHOLD_SECONDS = 10 * 60
LOCATION_EPSILON_METERS = 60.0
MOVE_SPEED_THRESHOLD_MPS = 0.5

# MOVE 구간만 학습한 GPR을 쓰기 위한 런타임 기준
# window 8개 중 이 개수 이상이 MOVE 성격이어야 GPR을 사용한다.
# 너무 엄격하게 8로 두면 GPS 흔들림 때문에 GPR이 거의 안 돌 수 있어 기본 6으로 둔다.
GPR_MIN_MOVE_POINTS_IN_WINDOW = 6

GPR_MAX_UNCERTAINTY_M = None

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


def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [float(lat1), float(lon1), float(lat2), float(lon2)]
    )
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def normalize_input_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    elif "Datetime" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Datetime"], errors="coerce")
    else:
        raise ValueError("Timestamp 또는 Datetime 컬럼이 필요합니다.")

    if "longitude" not in df.columns:
        if "Longitude" in df.columns:
            df = df.rename(columns={"Longitude": "longitude"})
        elif "Longtitude" in df.columns:
            df = df.rename(columns={"Longtitude": "longitude"})
        else:
            raise ValueError("longitude 또는 Longitude 컬럼이 필요합니다.")

    if "Latitude" not in df.columns:
        if "latitude" in df.columns:
            df = df.rename(columns={"latitude": "Latitude"})
        else:
            raise ValueError("Latitude 컬럼이 필요합니다.")

    if "device_id" not in df.columns:
        raise ValueError("device_id 컬럼이 필요합니다.")

    df["device_id"] = df["device_id"].astype(str)
    df = df.sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    return df


def ensure_quality_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if RAW_LAT_COL not in df.columns:
        df[RAW_LAT_COL] = df["Latitude"]
    if RAW_LON_COL not in df.columns:
        df[RAW_LON_COL] = df["longitude"]
    if GPS_QUALITY_COL not in df.columns:
        df[GPS_QUALITY_COL] = "HIGH"
    if GPS_DECISION_COL not in df.columns:
        df[GPS_DECISION_COL] = "raw_used"
    if USE_RAW_FOR_GPR_COL not in df.columns:
        df[USE_RAW_FOR_GPR_COL] = True
    if INTERP_METHOD_COL not in df.columns:
        df[INTERP_METHOD_COL] = ""

    missing = df["Latitude"].isna() | df["longitude"].isna()
    df.loc[missing, GPS_QUALITY_COL] = "MISSING"
    df.loc[missing, GPS_DECISION_COL] = "gpr_fill_needed"
    df.loc[missing, USE_RAW_FOR_GPR_COL] = False
    return df


def mark_bad_for_gpr_input(df: pd.DataFrame, idx, method="jump_outlier") -> None:
    # 원본 Raw_*는 보존하고, GPR 입력/최종 working 좌표만 NaN 처리
    df.loc[idx, GPS_QUALITY_COL] = "BAD"
    df.loc[idx, GPS_DECISION_COL] = "gpr_fill_needed"
    df.loc[idx, USE_RAW_FOR_GPR_COL] = False
    df.loc[idx, INTERP_METHOD_COL] = method
    df.loc[idx, "Latitude"] = np.nan
    df.loc[idx, "longitude"] = np.nan


# =========================================================
# jump/outlier 처리
# =========================================================
def detect_and_tag_jump_outliers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df = ensure_quality_columns(df)

    if "is_jump_outlier" not in df.columns:
        df["is_jump_outlier"] = False
    if "is_jump_suspect" not in df.columns:
        df["is_jump_suspect"] = False

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()
        last_good_idx = None
        active_cluster_center = None

        for idx in idxs:
            lat_cur = df.loc[idx, "Latitude"]
            lon_cur = df.loc[idx, "longitude"]
            t_cur = pd.to_datetime(df.loc[idx, "Timestamp"], errors="coerce")

            if pd.isna(lat_cur) or pd.isna(lon_cur) or pd.isna(t_cur):
                continue

            if last_good_idx is None:
                last_good_idx = idx
                continue

            lat_last = df.loc[last_good_idx, "Latitude"]
            lon_last = df.loc[last_good_idx, "longitude"]
            t_last = pd.to_datetime(df.loc[last_good_idx, "Timestamp"], errors="coerce")

            if pd.isna(lat_last) or pd.isna(lon_last) or pd.isna(t_last):
                last_good_idx = idx
                active_cluster_center = None
                continue

            dt_h = (t_cur - t_last).total_seconds() / 3600.0
            if dt_h <= 0:
                continue

            dist_m = haversine_m(lat_last, lon_last, lat_cur, lon_cur)
            speed_kmph = (dist_m / 1000.0) / dt_h

            if active_cluster_center is not None:
                dist_to_bad_cluster = haversine_m(
                    lat_cur,
                    lon_cur,
                    active_cluster_center[0],
                    active_cluster_center[1],
                )

                if dist_to_bad_cluster <= JUMP_ACTIVE_CLUSTER_RADIUS_M:
                    mark_bad_for_gpr_input(df, idx, method="jump_outlier")
                    df.loc[idx, "is_jump_outlier"] = True
                    continue

                if speed_kmph <= JUMP_RETURN_MAX_SPEED_KMPH:
                    df.loc[idx, GPS_QUALITY_COL] = "HIGH"
                    df.loc[idx, GPS_DECISION_COL] = "raw_used_return_from_jump"
                    df.loc[idx, USE_RAW_FOR_GPR_COL] = True
                    last_good_idx = idx
                    active_cluster_center = None
                    continue

            is_hard_bad = (
                dist_m >= JUMP_HARD_REJECT_DIST_M
                and speed_kmph >= JUMP_HARD_REJECT_SPEED_KMPH
            )
            is_suspect = (
                dist_m >= JUMP_SUSPECT_DIST_M
                and speed_kmph >= JUMP_SUSPECT_SPEED_KMPH
            )

            if is_hard_bad:
                active_cluster_center = (float(lat_cur), float(lon_cur))
                mark_bad_for_gpr_input(df, idx, method="jump_outlier")
                df.loc[idx, "is_jump_outlier"] = True
                continue

            if is_suspect:
                df.loc[idx, GPS_QUALITY_COL] = "LOW"
                df.loc[idx, GPS_DECISION_COL] = "suspect_kept"
                df.loc[idx, USE_RAW_FOR_GPR_COL] = True
                df.loc[idx, "is_jump_suspect"] = True
                if not str(df.loc[idx, INTERP_METHOD_COL]).strip():
                    df.loc[idx, INTERP_METHOD_COL] = "jump_suspect_kept"

            last_good_idx = idx

    return df


# =========================================================
# stale 처리
# =========================================================
def detect_and_fix_stale_gps_linear(
    df: pd.DataFrame,
    dist_same_m=1.0,
    dist_jump_m=1000.0,
    min_gap_min=5.0,
    post_check_n=3,
) -> pd.DataFrame:
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df = ensure_quality_columns(df)

    if "is_stale" not in df.columns:
        df["is_stale"] = False

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()
        if len(idxs) < 3:
            continue

        for k in range(1, len(idxs) - 1):
            i_prev = idxs[k - 1]
            i_curr = idxs[k]

            lat_prev = df.loc[i_prev, "Latitude"]
            lon_prev = df.loc[i_prev, "longitude"]
            lat_curr = df.loc[i_curr, "Latitude"]
            lon_curr = df.loc[i_curr, "longitude"]

            if any(pd.isna([lat_prev, lon_prev, lat_curr, lon_curr])):
                continue

            dist_prev = haversine_m(lat_prev, lon_prev, lat_curr, lon_curr)
            if dist_prev > dist_same_m:
                continue

            t_prev = pd.to_datetime(df.loc[i_prev, "Timestamp"], errors="coerce")
            t_curr = pd.to_datetime(df.loc[i_curr, "Timestamp"], errors="coerce")
            if pd.isna(t_prev) or pd.isna(t_curr):
                continue

            gap_min = (t_curr - t_prev).total_seconds() / 60.0
            if gap_min < min_gap_min:
                continue

            i_next_jump = None
            for j in range(1, post_check_n + 1):
                if k + j >= len(idxs):
                    break
                i_next = idxs[k + j]
                lat_next = df.loc[i_next, "Latitude"]
                lon_next = df.loc[i_next, "longitude"]
                if pd.isna(lat_next) or pd.isna(lon_next):
                    continue
                dist_next = haversine_m(lat_curr, lon_curr, lat_next, lon_next)
                if dist_next >= dist_jump_m:
                    i_next_jump = i_next
                    break

            if i_next_jump is None:
                continue

            t_next = pd.to_datetime(df.loc[i_next_jump, "Timestamp"], errors="coerce")
            if pd.isna(t_next) or t_next <= t_prev:
                new_lat = float(lat_prev)
                new_lon = float(lon_prev)
            else:
                total = (t_next - t_prev).total_seconds()
                alpha = (t_curr - t_prev).total_seconds() / total
                alpha = min(max(alpha, 0.0), 1.0)

                lat_next = float(df.loc[i_next_jump, "Latitude"])
                lon_next = float(df.loc[i_next_jump, "longitude"])
                new_lat = float(lat_prev) + alpha * (lat_next - float(lat_prev))
                new_lon = float(lon_prev) + alpha * (lon_next - float(lon_prev))

            df.loc[i_curr, "Latitude"] = new_lat
            df.loc[i_curr, "longitude"] = new_lon
            df.loc[i_curr, "is_stale"] = True
            df.loc[i_curr, INTERP_METHOD_COL] = "stale_linear"
            df.loc[i_curr, GPS_QUALITY_COL] = "LOW"
            df.loc[i_curr, GPS_DECISION_COL] = "corrected_stale_linear"
            df.loc[i_curr, USE_RAW_FOR_GPR_COL] = False

    return df


def fill_missing_gps_linear_between_valid_points(
    df: pd.DataFrame,
    max_gap_minutes: float = 60.0,
    max_gap_rows: int = 12,
    max_bridge_speed_kmph: float = 120.0,
    decision_value: str = "linear_filled_gpr_fallback",
    method_value: str = "linear_initial_or_short_window",
) -> pd.DataFrame:
    """
    GPR로도 채우지 못한 NULL 결측을 마지막 fallback으로 선형보간
    """
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df = ensure_quality_columns(df)

    if "is_missing_linear_filled" not in df.columns:
        df["is_missing_linear_filled"] = False

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()
        n = len(idxs)
        k = 0

        while k < n:
            idx = idxs[k]
            lat = df.loc[idx, "Latitude"]
            lon = df.loc[idx, "longitude"]

            # 현재 row가 결측이 아니면 다음 row로 이동
            if not (pd.isna(lat) or pd.isna(lon)):
                k += 1
                continue

            # 연속 결측 block 탐색
            block_start_pos = k
            while k < n:
                cur_idx = idxs[k]
                lat_cur = df.loc[cur_idx, "Latitude"]
                lon_cur = df.loc[cur_idx, "longitude"]
                if not (pd.isna(lat_cur) or pd.isna(lon_cur)):
                    break
                k += 1
            block_end_pos = k - 1

            prev_pos = block_start_pos - 1
            next_pos = k

            # 앞 정상점과 뒤 정상점이 모두 있어야 선형보간 가능
            if prev_pos < 0 or next_pos >= n:
                continue

            prev_idx = idxs[prev_pos]
            next_idx = idxs[next_pos]

            lat_prev = df.loc[prev_idx, "Latitude"]
            lon_prev = df.loc[prev_idx, "longitude"]
            lat_next = df.loc[next_idx, "Latitude"]
            lon_next = df.loc[next_idx, "longitude"]

            if any(pd.isna([lat_prev, lon_prev, lat_next, lon_next])):
                continue

            t_prev = pd.to_datetime(df.loc[prev_idx, "Timestamp"], errors="coerce")
            t_next = pd.to_datetime(df.loc[next_idx, "Timestamp"], errors="coerce")
            if pd.isna(t_prev) or pd.isna(t_next) or t_next <= t_prev:
                continue

            gap_minutes = (t_next - t_prev).total_seconds() / 60.0
            block_len = block_end_pos - block_start_pos + 1

            if gap_minutes > max_gap_minutes:
                continue
            if block_len > max_gap_rows:
                continue

            bridge_dist_m = haversine_m(
                float(lat_prev), float(lon_prev),
                float(lat_next), float(lon_next),
            )
            bridge_speed_kmph = (bridge_dist_m / 1000.0) / (gap_minutes / 60.0)
            if bridge_speed_kmph > max_bridge_speed_kmph:
                continue

            total_seconds = (t_next - t_prev).total_seconds()

            for fill_pos in range(block_start_pos, block_end_pos + 1):
                fill_idx = idxs[fill_pos]
                t_cur = pd.to_datetime(df.loc[fill_idx, "Timestamp"], errors="coerce")
                if pd.isna(t_cur):
                    continue

                alpha = (t_cur - t_prev).total_seconds() / total_seconds
                alpha = min(max(alpha, 0.0), 1.0)

                new_lat = float(lat_prev) + alpha * (float(lat_next) - float(lat_prev))
                new_lon = float(lon_prev) + alpha * (float(lon_next) - float(lon_prev))

                df.loc[fill_idx, "Latitude"] = new_lat
                df.loc[fill_idx, "longitude"] = new_lon
                df.loc[fill_idx, GPS_QUALITY_COL] = "LOW"
                df.loc[fill_idx, GPS_DECISION_COL] = decision_value
                df.loc[fill_idx, USE_RAW_FOR_GPR_COL] = False
                df.loc[fill_idx, INTERP_METHOD_COL] = method_value
                df.loc[fill_idx, "is_missing_linear_filled"] = True

    return df


# =========================================================
# feature 생성
# =========================================================
def add_relative_time_feature(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df["stoppage_duration_at_point"] = 0.0
    df["trip_id"] = 0
    df["relative_time"] = 0.0

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()
        trip_id = 1
        rel_time = 0.0
        stop_duration = 0.0

        for pos, idx in enumerate(idxs):
            if pos == 0:
                df.loc[idx, "trip_id"] = trip_id
                continue

            prev_idx = idxs[pos - 1]
            t_prev = pd.to_datetime(df.loc[prev_idx, "Timestamp"])
            t_cur = pd.to_datetime(df.loc[idx, "Timestamp"])
            dt = max((t_cur - t_prev).total_seconds(), 0.0)

            lat_prev = df.loc[prev_idx, "Latitude"]
            lon_prev = df.loc[prev_idx, "longitude"]
            lat_cur = df.loc[idx, "Latitude"]
            lon_cur = df.loc[idx, "longitude"]

            same_place = False
            if not any(pd.isna([lat_prev, lon_prev, lat_cur, lon_cur])):
                same_place = haversine_m(lat_prev, lon_prev, lat_cur, lon_cur) <= LOCATION_EPSILON_METERS

            if same_place:
                stop_duration += dt
            else:
                stop_duration = 0.0
                rel_time += dt

            df.loc[idx, "trip_id"] = trip_id
            df.loc[idx, "relative_time"] = rel_time
            df.loc[idx, "stoppage_duration_at_point"] = stop_duration

    return df


def add_motion_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df["cumulative_distance_km"] = 0.0
    df["velocity_kmph"] = 0.0
    df["bearing"] = 0.0

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()
        cum = 0.0
        prev_bearing = 0.0

        for k in range(1, len(idxs)):
            prev_idx = idxs[k - 1]
            cur_idx = idxs[k]

            lat1 = df.loc[prev_idx, "Latitude"]
            lon1 = df.loc[prev_idx, "longitude"]
            lat2 = df.loc[cur_idx, "Latitude"]
            lon2 = df.loc[cur_idx, "longitude"]

            if any(pd.isna([lat1, lon1, lat2, lon2])):
                df.loc[cur_idx, "bearing"] = prev_bearing
                continue

            dt = (pd.to_datetime(df.loc[cur_idx, "Timestamp"]) - pd.to_datetime(df.loc[prev_idx, "Timestamp"])).total_seconds()
            dist_m = haversine_m(lat1, lon1, lat2, lon2)
            dist_km = dist_m / 1000.0
            cum += dist_km

            df.loc[cur_idx, "cumulative_distance_km"] = cum
            df.loc[cur_idx, "velocity_kmph"] = dist_km / dt * 3600 if dt > 0 else 0.0

            if dist_m > 0.1:
                prev_bearing = calculate_bearing(lat1, lon1, lat2, lon2)
            df.loc[cur_idx, "bearing"] = prev_bearing

    return df


def add_trip_pos(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df["trip_pos"] = df.groupby(["device_id", "trip_id"]).cumcount()
    return df


def recompute_features(df: pd.DataFrame) -> pd.DataFrame:
    df = add_relative_time_feature(df)
    df = add_motion_features(df)
    df = add_trip_pos(df)
    return df


def build_gpr_feature_from_window(window_df: pd.DataFrame) -> np.ndarray:
    feature_cols = [
        "Latitude",
        "longitude",
        "relative_time",
        "cumulative_distance_km",
        "velocity_kmph",
        "bearing",
    ]
    return window_df[feature_cols].values.flatten().reshape(1, -1)


# =========================================================
# GPR predict only
# =========================================================
def gpr_predict_only_for_move_windows(
    df,
    gpr_lat,
    gpr_lon,
    scaler_X,
    scaler_y_lat,
    scaler_y_lon,
):
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)

    # feature 및 STOP/MOVE 생성
    df = recompute_features(df)
    df = detect_stop_move_primary(df)

    # 예측 결과 컬럼 생성
    if "Predicted_Latitude" not in df.columns:
        df["Predicted_Latitude"] = np.nan
    if "Predicted_longitude" not in df.columns:
        df["Predicted_longitude"] = np.nan
    if "Predicted_uncertainty_m" not in df.columns:
        df["Predicted_uncertainty_m"] = np.nan
    if "Predicted_confidence_level" not in df.columns:
        df["Predicted_confidence_level"] = ""
    if "gpr_prediction_available" not in df.columns:
        df["gpr_prediction_available"] = False

    required = [
        "Latitude",
        "longitude",
        "relative_time",
        "cumulative_distance_km",
        "velocity_kmph",
        "bearing",
    ]

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()

        for pos, idx in enumerate(idxs):

            # 이전 8개 포인트가 있어야 현재 idx를 예측 가능
            if pos < WINDOW_SIZE:
                continue

            win_idxs = idxs[pos - WINDOW_SIZE:pos]
            win = df.loc[win_idxs]

            # 장기 정지 상태 아닌 경우만 예측
            if (
                "stoppage_duration_at_point" in df.columns
                and pd.notna(df.loc[idx, "stoppage_duration_at_point"])
                and df.loc[idx, "stoppage_duration_at_point"] >= STOPPAGE_THRESHOLD_SECONDS
            ):
                continue

            # GPR 입력 window에 NaN이 있으면 예측 불가
            if win[required].isna().any().any():
                continue

            X = build_gpr_feature_from_window(win)
            X_scaled = scaler_X.transform(X)

            pred_lat_scaled, std_lat_scaled = gpr_lat.predict(
                X_scaled,
                return_std=True,
            )
            pred_lon_scaled, std_lon_scaled = gpr_lon.predict(
                X_scaled,
                return_std=True,
            )

            pred_lat = scaler_y_lat.inverse_transform(
                pred_lat_scaled.reshape(-1, 1)
            ).ravel()[0]

            pred_lon = scaler_y_lon.inverse_transform(
                pred_lon_scaled.reshape(-1, 1)
            ).ravel()[0]

            lat_std = std_lat_scaled[0] * scaler_y_lat.scale_[0]
            lon_std = std_lon_scaled[0] * scaler_y_lon.scale_[0]

            meter_per_deg_lat = 111000.0
            meter_per_deg_lon = 111000.0 * math.cos(math.radians(pred_lat))

            uncertainty_m = math.sqrt(
                (lat_std * meter_per_deg_lat) ** 2
                + (lon_std * meter_per_deg_lon) ** 2
            )

            df.loc[idx, "Predicted_Latitude"] = float(pred_lat)
            df.loc[idx, "Predicted_longitude"] = float(pred_lon)
            df.loc[idx, "Predicted_uncertainty_m"] = float(uncertainty_m)
            df.loc[idx, "gpr_prediction_available"] = True

            if uncertainty_m <= 15:
                conf = "HIGH"
            elif uncertainty_m <= 30:
                conf = "MEDIUM"
            else:
                conf = "LOW"

            df.loc[idx, "Predicted_confidence_level"] = conf

    return df

def apply_gpr_prediction_to_missing_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    GPR 예측값이 있는 경우, raw가 missing/jump라서 Latitude/longitude가 비어 있는 row를
    Predicted_Latitude / Predicted_longitude로 채운다.

    정상 raw 좌표는 덮어쓰지 않는다.
    """
    df = df.copy()

    required_cols = [
        "Latitude",
        "longitude",
        "Predicted_Latitude",
        "Predicted_longitude",
        "gpr_prediction_available",
    ]

    for col in required_cols:
        if col not in df.columns:
            return df

    need_fill = (
        (
            df["Latitude"].isna()
            | df["longitude"].isna()
            | df[GPS_DECISION_COL].astype(str).str.contains("gpr_fill_needed", na=False)
        )
        & df["gpr_prediction_available"].eq(True)
        & df["Predicted_Latitude"].notna()
        & df["Predicted_longitude"].notna()
    )

    df.loc[need_fill, "Latitude"] = df.loc[need_fill, "Predicted_Latitude"]
    df.loc[need_fill, "longitude"] = df.loc[need_fill, "Predicted_longitude"]

    df.loc[need_fill, GPS_QUALITY_COL] = "LOW"
    df.loc[need_fill, GPS_DECISION_COL] = "gpr_filled"
    df.loc[need_fill, USE_RAW_FOR_GPR_COL] = False
    df.loc[need_fill, INTERP_METHOD_COL] = "gpr"

    return df


# =========================================================
# GPR autoregressive + 초기부족 구간 선형보간 fallback
# =========================================================
def _is_valid_coord_row(df: pd.DataFrame, idx) -> bool:
    t = pd.to_datetime(df.loc[idx, "Timestamp"], errors="coerce")
    return (
        pd.notna(df.loc[idx, "Latitude"])
        and pd.notna(df.loc[idx, "longitude"])
        and pd.notna(t)
    )


def _find_prev_next_valid_coord(
    df: pd.DataFrame,
    idxs,
    pos: int,
):
    prev_idx = None
    next_idx = None

    for j in range(pos - 1, -1, -1):
        cand = idxs[j]
        if _is_valid_coord_row(df, cand):
            prev_idx = cand
            break

    for j in range(pos + 1, len(idxs)):
        cand = idxs[j]
        if _is_valid_coord_row(df, cand):
            next_idx = cand
            break

    return prev_idx, next_idx


def _linear_fill_single_between(
    df: pd.DataFrame,
    fill_idx,
    prev_idx,
    next_idx,
    max_gap_minutes: float = 60.0,
    max_bridge_speed_kmph: float = 120.0,
    method: str = "linear_initial_or_short_window",
) -> bool:
    """
    fill_idx 한 row를 prev_idx~next_idx 사이 시간 비율로 선형보간한다.
    성공하면 True, 아니면 False.
    """
    if prev_idx is None or next_idx is None:
        return False

    if not (_is_valid_coord_row(df, prev_idx) and _is_valid_coord_row(df, next_idx)):
        return False

    t_prev = pd.to_datetime(df.loc[prev_idx, "Timestamp"], errors="coerce")
    t_cur = pd.to_datetime(df.loc[fill_idx, "Timestamp"], errors="coerce")
    t_next = pd.to_datetime(df.loc[next_idx, "Timestamp"], errors="coerce")

    if pd.isna(t_prev) or pd.isna(t_cur) or pd.isna(t_next):
        return False
    if not (t_prev < t_cur < t_next):
        return False

    gap_minutes = (t_next - t_prev).total_seconds() / 60.0
    if gap_minutes <= 0 or gap_minutes > max_gap_minutes:
        return False

    lat_prev = float(df.loc[prev_idx, "Latitude"])
    lon_prev = float(df.loc[prev_idx, "longitude"])
    lat_next = float(df.loc[next_idx, "Latitude"])
    lon_next = float(df.loc[next_idx, "longitude"])

    bridge_dist_m = haversine_m(lat_prev, lon_prev, lat_next, lon_next)
    bridge_speed_kmph = (bridge_dist_m / 1000.0) / (gap_minutes / 60.0)
    if bridge_speed_kmph > max_bridge_speed_kmph:
        return False

    total_seconds = (t_next - t_prev).total_seconds()
    alpha = (t_cur - t_prev).total_seconds() / total_seconds
    alpha = min(max(alpha, 0.0), 1.0)

    new_lat = lat_prev + alpha * (lat_next - lat_prev)
    new_lon = lon_prev + alpha * (lon_next - lon_prev)

    df.loc[fill_idx, "Latitude"] = float(new_lat)
    df.loc[fill_idx, "longitude"] = float(new_lon)
    df.loc[fill_idx, GPS_QUALITY_COL] = "LOW"
    df.loc[fill_idx, GPS_DECISION_COL] = "linear_filled_short_window"
    df.loc[fill_idx, USE_RAW_FOR_GPR_COL] = False
    df.loc[fill_idx, INTERP_METHOD_COL] = method

    if "is_missing_linear_filled" not in df.columns:
        df["is_missing_linear_filled"] = False
    df.loc[fill_idx, "is_missing_linear_filled"] = True

    return True


def _window_move_count(win: pd.DataFrame) -> int:
    """
    MOVE-only로 학습한 GPR에 넣어도 되는 window인지 보기 위한 간단 기준.
    state_primary가 있으면 그것을 우선 사용하고, 없으면 velocity_kmph 기준을 쓴다.
    """
    if "state_primary" in win.columns:
        return int(win["state_primary"].astype(str).eq("MOVE").sum())

    if "velocity_kmph" not in win.columns:
        return 0

    v = pd.to_numeric(win["velocity_kmph"], errors="coerce").fillna(0.0)
    return int((v / 3.6 >= MOVE_SPEED_THRESHOLD_MPS).sum())


def gpr_fill_missing_hybrid_autoregressive(
    df: pd.DataFrame,
    gpr_lat,
    gpr_lon,
    scaler_X,
    scaler_y_lat,
    scaler_y_lon,
    window_size: int = WINDOW_SIZE,
    min_move_points: int = GPR_MIN_MOVE_POINTS_IN_WINDOW,
    max_uncertainty_m = GPR_MAX_UNCERTAINTY_M,
    linear_max_gap_minutes: float = 60.0,
    linear_max_bridge_speed_kmph: float = 120.0,
) -> pd.DataFrame:
    """
    결측 row를 순차적으로 채운다.

    우선순위:
    1) 이전 window_size개 좌표가 모두 있고 MOVE context가 충분하면 GPR autoregressive
    2) GPR을 못 쓰는 초기/짧은 window 구간이고 앞뒤 정상 좌표가 있으면 선형보간
    3) 둘 다 안 되면 그대로 NaN 유지 → state_primary는 UNKNOWN

    중요한 차이:
    - GPR 예측이 성공하면 즉시 Latitude/longitude에 반영한다.
    - 따라서 다음 결측 row는 직전 GPR 예측값을 window에 포함해 다시 예측할 수 있다.
    """
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    df = ensure_quality_columns(df)

    for col, default in [
        ("Predicted_Latitude", np.nan),
        ("Predicted_longitude", np.nan),
        ("Predicted_uncertainty_m", np.nan),
        ("Predicted_confidence_level", ""),
        ("gpr_prediction_available", False),
        ("gpr_autoreg_attempted", False),
        ("gpr_autoreg_skip_reason", ""),
    ]:
        if col not in df.columns:
            df[col] = default

    required = [
        "Latitude",
        "longitude",
        "relative_time",
        "cumulative_distance_km",
        "velocity_kmph",
        "bearing",
    ]

    # 최초 feature/state 계산
    df = recompute_features(df)
    df = detect_stop_move_primary(df)

    for device_id, group in df.groupby("device_id", sort=True):
        idxs = group.index.tolist()

        for pos, idx in enumerate(idxs):
            needs_fill = (
                pd.isna(df.loc[idx, "Latitude"])
                or pd.isna(df.loc[idx, "longitude"])
                or str(df.loc[idx, GPS_DECISION_COL]).find("gpr_fill_needed") >= 0
            )

            if not needs_fill:
                continue

            df.loc[idx, "gpr_autoreg_attempted"] = True

            can_gpr = True
            skip_reason = ""

            if pos < window_size:
                can_gpr = False
                skip_reason = "short_window"

            if can_gpr:
                win_idxs = idxs[pos - window_size:pos]
                win = df.loc[win_idxs].copy()

                if win[required].isna().any().any():
                    can_gpr = False
                    skip_reason = "window_has_nan"
                else:
                    move_count = _window_move_count(win)
                    if move_count < min_move_points:
                        can_gpr = False
                        skip_reason = f"not_enough_move_context:{move_count}/{window_size}"

            if can_gpr:
                try:
                    X = build_gpr_feature_from_window(win)
                    X_scaled = scaler_X.transform(X)

                    pred_lat_scaled, std_lat_scaled = gpr_lat.predict(
                        X_scaled,
                        return_std=True,
                    )
                    pred_lon_scaled, std_lon_scaled = gpr_lon.predict(
                        X_scaled,
                        return_std=True,
                    )

                    pred_lat = scaler_y_lat.inverse_transform(
                        pred_lat_scaled.reshape(-1, 1)
                    ).ravel()[0]
                    pred_lon = scaler_y_lon.inverse_transform(
                        pred_lon_scaled.reshape(-1, 1)
                    ).ravel()[0]

                    lat_std = std_lat_scaled[0] * scaler_y_lat.scale_[0]
                    lon_std = std_lon_scaled[0] * scaler_y_lon.scale_[0]

                    meter_per_deg_lat = 111000.0
                    meter_per_deg_lon = 111000.0 * math.cos(math.radians(pred_lat))
                    uncertainty_m = math.sqrt(
                        (lat_std * meter_per_deg_lat) ** 2
                        + (lon_std * meter_per_deg_lon) ** 2
                    )

                    if max_uncertainty_m is not None and uncertainty_m > max_uncertainty_m:
                        can_gpr = False
                        skip_reason = f"uncertainty_too_high:{uncertainty_m:.1f}m"
                    else:
                        df.loc[idx, "Predicted_Latitude"] = float(pred_lat)
                        df.loc[idx, "Predicted_longitude"] = float(pred_lon)
                        df.loc[idx, "Predicted_uncertainty_m"] = float(uncertainty_m)
                        df.loc[idx, "gpr_prediction_available"] = True

                        if uncertainty_m <= 15:
                            conf = "HIGH"
                        elif uncertainty_m <= 30:
                            conf = "MEDIUM"
                        else:
                            conf = "LOW"
                        df.loc[idx, "Predicted_confidence_level"] = conf

                        # 핵심: 예측값을 즉시 working 좌표에 반영한다.
                        df.loc[idx, "Latitude"] = float(pred_lat)
                        df.loc[idx, "longitude"] = float(pred_lon)
                        df.loc[idx, GPS_QUALITY_COL] = "LOW"
                        df.loc[idx, GPS_DECISION_COL] = "gpr_autoregressive_filled"
                        df.loc[idx, USE_RAW_FOR_GPR_COL] = False
                        df.loc[idx, INTERP_METHOD_COL] = "gpr_autoregressive"
                        df.loc[idx, "gpr_autoreg_skip_reason"] = ""

                        # 다음 결측 row가 이 예측값을 window로 쓸 수 있게 feature/state 즉시 재계산
                        df = recompute_features(df)
                        df = detect_stop_move_primary(df)
                        continue

                except Exception as e:
                    can_gpr = False
                    skip_reason = f"gpr_error:{type(e).__name__}"

            # GPR 불가: 초기 window 부족/STOP context/window NaN이면 앞뒤 정상점이 있을 때만 선형보간
            df.loc[idx, "gpr_autoreg_skip_reason"] = skip_reason

            prev_idx, next_idx = _find_prev_next_valid_coord(df, idxs, pos)
            filled_linear = _linear_fill_single_between(
                df,
                fill_idx=idx,
                prev_idx=prev_idx,
                next_idx=next_idx,
                max_gap_minutes=linear_max_gap_minutes,
                max_bridge_speed_kmph=linear_max_bridge_speed_kmph,
                method="linear_initial_or_short_window",
            )

            if filled_linear:
                # 다음 row에서 선형보간값을 window로 쓸 수 있게 즉시 재계산
                df = recompute_features(df)
                df = detect_stop_move_primary(df)
            else:
                if not skip_reason:
                    skip_reason = "no_gpr_no_linear_anchor"
                df.loc[idx, "gpr_autoreg_skip_reason"] = skip_reason

    return df


# =========================================================
# STOP/MOVE 판정
# =========================================================
def detect_stop_move_primary(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values(["device_id", "Timestamp"]).reset_index(drop=True)
    if "velocity_kmph" not in df.columns:
        df = add_motion_features(df)

    # 아직 최종 좌표가 없는 row를 STOP으로 오판하지 않기 위해 UNKNOWN으로 둔다.
    df["state_primary"] = "UNKNOWN"

    for idx in df.index:
        if pd.isna(df.loc[idx, "Latitude"]) or pd.isna(df.loc[idx, "longitude"]):
            df.loc[idx, "state_primary"] = "UNKNOWN"
            continue

        v = pd.to_numeric(df.loc[idx, "velocity_kmph"], errors="coerce")
        if pd.isna(v):
            df.loc[idx, "state_primary"] = "UNKNOWN"
            continue

        v_mps = float(v) / 3.6
        df.loc[idx, "state_primary"] = "MOVE" if v_mps >= MOVE_SPEED_THRESHOLD_MPS else "STOP"

    return df


# =========================================================
# Runtime class
# =========================================================
class GPRRuntime:
    def __init__(self, model_dir: str, version: str, device_id: str):
        self.model_dir = model_dir
        self.version = version
        self.device_id = str(device_id)

        safe_id = self.device_id.replace("/", "_").replace("\\", "_").replace(":", "_")

        bundle_path = os.path.join(
            model_dir,
            f"gpr_bundle_{version}_device_{safe_id}.joblib"
        )

        if not os.path.exists(bundle_path):
            raise FileNotFoundError(f"GPR bundle 파일이 없습니다: {bundle_path}")

        self.bundle_path = bundle_path
        self.bundle = joblib.load(bundle_path)

        bundle_device_id = str(self.bundle.get("device_id"))
        if bundle_device_id != self.device_id:
            raise ValueError(
                f"GPR bundle device_id 불일치: "
                f"request={self.device_id}, bundle={bundle_device_id}"
            )

        main_gpr = self.bundle.get("main_gpr")
        if main_gpr is None:
            raise ValueError("bundle 안에 main_gpr가 없습니다.")

        self.gpr_lat = main_gpr["gpr_lat"]
        self.gpr_lon = main_gpr["gpr_lon"]
        self.scaler_X = main_gpr["scaler_X"]
        self.scaler_y_lat = main_gpr["scaler_y_lat"]
        self.scaler_y_lon = main_gpr["scaler_y_lon"]

        self.long_gap_gpr = self.bundle.get("long_gap_gpr")
        self.anchor_zones = self.bundle.get("anchor_zones")
        self.window_size = int(self.bundle.get("window_size", WINDOW_SIZE))

    def preprocess_and_predict(self, recent_df: pd.DataFrame) -> pd.DataFrame:
        """
        recent_df:
            서버 DB에서 조회한 최근 데이터.
            5분 간격 기준 최근 60분 조회 권장.

        return:
            보정된 Latitude / longitude와 state_primary가 포함된 DataFrame.
        """
        df = normalize_input_columns(recent_df)
        df = ensure_quality_columns(df)

        # 1. jump/outlier 좌표를 GPR 입력에서 제외
        df = detect_and_tag_jump_outliers(df)

        # 2. stale GPS는 가능한 경우 선형보간 교정
        df = detect_and_fix_stale_gps_linear(df)

        # 3. feature 생성
        df = recompute_features(df)

        # 4. gpr autogressive
        df = gpr_fill_missing_hybrid_autoregressive(
            df=df,
            gpr_lat=self.gpr_lat,
            gpr_lon=self.gpr_lon,
            scaler_X=self.scaler_X,
            scaler_y_lat=self.scaler_y_lat,
            scaler_y_lon=self.scaler_y_lon,
            window_size=self.window_size,
            min_move_points=GPR_MIN_MOVE_POINTS_IN_WINDOW,
            max_uncertainty_m=GPR_MAX_UNCERTAINTY_M,
            linear_max_gap_minutes=60.0,
            linear_max_bridge_speed_kmph=120.0,
        )

        df = fill_missing_gps_linear_between_valid_points(
            df,
            max_gap_minutes=60.0,
            max_gap_rows=12,
            max_bridge_speed_kmph=120.0,
            decision_value="linear_filled_final_fallback",
            method_value="linear_final_fallback",
        )

        # 6. 최종 좌표 기준 feature/state 재계산
        df = recompute_features(df)
        df = detect_stop_move_primary(df)

        return df