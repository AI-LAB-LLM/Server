# 보정 좌표들이 쌓인 뒤 trip 단위로 실행되는 이상탐지 로직
# geo/anomaly_services.py

from pathlib import Path
import traceback
from datetime import timedelta

import numpy as np
import pandas as pd

from django.conf import settings
from django.db import transaction

from .models import GeoProcessedData, GeoTripAnomalyResult
from .anomaly_runtime import AnomalyRuntime


# =========================
# Anomaly 모델 설정
# =========================

# 현재 anomaly 모델도 특정 device_id 전용
GEO_MODEL_DEVICE_ID = "19395f6a434f4ca6"
ANOMALY_VERSION = "0427"

GEO_MODEL_DIR = (
    Path(settings.BASE_DIR)
    / "media"
    / "models"
    / "geo_7"
    / "jy"
)

ANOMALY_MODEL_PATH = (
    GEO_MODEL_DIR
    / f"anomaly_{ANOMALY_VERSION}_device_{GEO_MODEL_DEVICE_ID}.joblib"
)


# =========================
# 공통 유틸
# =========================

def safe_value(value):
    """
    pandas / numpy 값을 Django DB에 저장 가능한 Python 기본 타입으로 변환.
    - NaN, NaT, None -> None
    - numpy scalar -> Python scalar
    - pandas Timestamp -> Python datetime
    """

    if value is None:
        return None

    if isinstance(value, np.generic):
        return safe_value(value.item())

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.to_pydatetime()

    return value


def check_anomaly_model_file():
    """
    AnomalyRuntime 실행에 필요한 joblib 파일 존재 여부 확인.
    """
    if not ANOMALY_MODEL_PATH.exists():
        return str(ANOMALY_MODEL_PATH)
    return None


def is_supported_anomaly_device(device_id):
    """
    현재 anomaly 모델이 지원하는 device_id인지 확인.
    """
    return str(device_id) == GEO_MODEL_DEVICE_ID


# =========================
# DB → AnomalyRuntime 입력 DataFrame 변환
# =========================

def build_processed_gps_dataframe_for_anomaly(
    device_id,
    reference_time,
    minutes=180,
):
    """
    GeoProcessedData에서 최근 보정 GPS 데이터를 조회해서
    AnomalyRuntime 입력 형식의 DataFrame으로 변환.

    AnomalyRuntime 주요 필요 컬럼:
    - device_id
    - Timestamp
    - Latitude
    - longitude
    - Longitude
    - state_primary

    주의:
    - raw_latitude / raw_longitude가 아니라
      GPR 보정 후 저장된 latitude / longitude를 넣어야 함.
    """

    start_time = reference_time - timedelta(minutes=minutes)

    qs = (
        GeoProcessedData.objects.filter(
            device_id=device_id,
            timestamp__gte=start_time,
            timestamp__lte=reference_time,
            latitude__isnull=False,
            longitude__isnull=False,
        )
        .order_by("timestamp")
        .values(
            "device_id",
            "timestamp",
            "latitude",
            "longitude",
            "predicted_latitude",
            "predicted_longitude",
            "predicted_confidence_level",
            "interp_method",
            "state_primary",
        )
    )

    rows = []

    for row in qs:
        rows.append(
            {
                "device_id": row["device_id"],
                "Timestamp": row["timestamp"],

                # anomaly_runtime.py 내부에서 Latitude / Longitude 사용
                "Latitude": row["latitude"],
                "longitude": row["longitude"],
                "Longitude": row["longitude"],

                # GPR 예측/보정 관련 tolerance 계산용
                "Predicted_Latitude": row["predicted_latitude"],
                "Predicted_longitude": row["predicted_longitude"],
                "Predicted_confidence_level": row["predicted_confidence_level"],
                "interp_method": row["interp_method"],

                # trip 생성에 필요
                "state_primary": row["state_primary"],
            }
        )

    return pd.DataFrame(rows)


# =========================
# 결과 저장
# =========================

def save_anomaly_result_if_needed(geo_obj, latest_result):
    """
    AnomalyRuntime 결과 1개 row를 GeoTripAnomalyResult에 저장.
    이미 같은 trip_start_time / trip_end_time 결과가 있으면 중복 저장하지 않음.
    """

    final_route_label = safe_value(latest_result.get("final_route_label"))
    runtime_status = safe_value(latest_result.get("status"))

    trip_start_time = safe_value(latest_result.get("start_time"))
    trip_end_time = safe_value(latest_result.get("end_time"))

    od_key = safe_value(latest_result.get("od_key"))
    dtw_score = safe_value(latest_result.get("score_topk_mean"))
    threshold = safe_value(latest_result.get("threshold"))

    if not final_route_label:
        final_route_label = GeoTripAnomalyResult.RouteLabel.ANOMALY

    # trip 시간이 없으면 DB 저장 기준이 애매함
    if trip_start_time is None or trip_end_time is None:
        return None, {
            "anomaly_status": "ok_but_not_saved",
            "reason": "trip_time_missing",
            "runtime_status": runtime_status,
            "final_route_label": final_route_label,
            "od_key": od_key,
            "dtw_score": dtw_score,
            "threshold": threshold,
        }

    with transaction.atomic():
        existing = (
            GeoTripAnomalyResult.objects
            .select_for_update()
            .filter(
                device_id=geo_obj.device_id,
                trip_start_time=trip_start_time,
                trip_end_time=trip_end_time,
            )
            .first()
        )

        if existing:
            return existing, {
                "anomaly_status": "already_saved",
                "result_id": existing.id,
                "final_route_label": existing.final_route_label,
                "od_key": existing.od_key,
                "dtw_score": existing.dtw_score,
                "threshold": existing.threshold,
                "runtime_status": existing.message,
            }

        result_obj = GeoTripAnomalyResult.objects.create(
            protectee=geo_obj.protectee,
            device_id=geo_obj.device_id,
            trip_start_time=trip_start_time,
            trip_end_time=trip_end_time,
            final_route_label=final_route_label,
            od_key=od_key,
            dtw_score=dtw_score,
            threshold=threshold,
            message=runtime_status,
        )

    return result_obj, {
        "anomaly_status": "saved",
        "result_id": result_obj.id,
        "final_route_label": result_obj.final_route_label,
        "od_key": result_obj.od_key,
        "dtw_score": result_obj.dtw_score,
        "threshold": result_obj.threshold,
        "runtime_status": runtime_status,
    }


# =========================
# AnomalyRuntime 실행
# =========================

def run_anomaly_for_latest(geo_obj, minutes=180):
    """
    방금 GPR 보정이 끝난 GeoProcessedData row 기준으로
    최근 보정 GPS를 조회하고, trip 단위 이상탐지를 실행한다.

    반환:
    - anomaly_status: skipped / saved / already_saved / error 등
    """

    # 현재 모델이 특정 device_id 전용이면 다른 워치에는 실행하지 않음
    if not is_supported_anomaly_device(geo_obj.device_id):
        return {
            "anomaly_status": "skipped",
            "reason": "unsupported_device",
            "device_id": geo_obj.device_id,
            "model_device_id": GEO_MODEL_DEVICE_ID,
        }

    # 아직 GPR 결과 좌표가 없는 row라면 anomaly 실행 불가
    if geo_obj.latitude is None or geo_obj.longitude is None:
        return {
            "anomaly_status": "skipped",
            "reason": "latest_corrected_gps_missing",
            "geo_processed_id": geo_obj.id,
        }

    missing_file = check_anomaly_model_file()
    if missing_file:
        return {
            "anomaly_status": "skipped",
            "reason": "model_file_missing",
            "missing_file": missing_file,
        }

    processed_df = build_processed_gps_dataframe_for_anomaly(
        device_id=geo_obj.device_id,
        reference_time=geo_obj.timestamp,
        minutes=minutes,
    )

    if processed_df.empty:
        return {
            "anomaly_status": "skipped",
            "reason": "processed_df_empty",
        }

    # state_primary가 없거나 전부 null이면 trip 생성 불가
    if "state_primary" not in processed_df.columns:
        return {
            "anomaly_status": "skipped",
            "reason": "state_primary_missing",
        }

    if processed_df["state_primary"].dropna().empty:
        return {
            "anomaly_status": "skipped",
            "reason": "state_primary_all_null",
        }

    # MOVE가 하나도 없으면 trip 생성 불가
    if not processed_df["state_primary"].astype(str).eq("MOVE").any():
        return {
            "anomaly_status": "skipped",
            "reason": "no_move_state",
        }

    # 현재 이동 중이면 목적지 앵커를 알 수 없으므로 trip 완료 후(STOP) 실행
    if str(geo_obj.state_primary) == "MOVE":
        return {
            "anomaly_status": "skipped",
            "reason": "trip_in_progress",
        }

    try:
        anomaly = AnomalyRuntime(str(ANOMALY_MODEL_PATH))
        result_df = anomaly.predict_from_processed_gps(processed_df)

        if result_df.empty:
            return {
                "anomaly_status": "skipped",
                "reason": "result_df_empty",
            }

        latest_result = result_df.iloc[-1]

        runtime_status = safe_value(latest_result.get("status"))
        final_route_label = safe_value(latest_result.get("final_route_label"))

        # 아직 trip이 충분히 안 만들어진 상태
        if runtime_status == "no_trip_detected":
            return {
                "anomaly_status": "skipped",
                "reason": "no_trip_detected",
                "final_route_label": final_route_label,
            }

        result_obj, response = save_anomaly_result_if_needed(
            geo_obj=geo_obj,
            latest_result=latest_result,
        )

        return response

    except Exception as e:
        print("========== ANOMALY ERROR ==========")
        print(traceback.format_exc())
        print("========== ANOMALY ERROR END ==========")

        return {
            "anomaly_status": "error",
            "reason": str(e),
            "traceback": traceback.format_exc(),
        }