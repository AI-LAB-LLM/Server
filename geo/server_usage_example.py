# -*- coding: utf-8 -*-
"""
server_usage_example.py

서버 연동 예시.
실서비스에서는 on_receive_gps() 내부의 DB 함수만 서버 코드에 맞게 구현하면 됩니다.
"""

import pandas as pd

from geo.gpr_runtime import GPRRuntime
from geo.anomaly_runtime import AnomalyRuntime


DEVICE_ID = "19395f6a434f4ca6"
VERSION = "0427"
MODEL_DIR = "models"

GPR = GPRRuntime(
    model_dir=MODEL_DIR,
    version=VERSION,
    device_id=DEVICE_ID,
)

ANOMALY = AnomalyRuntime(
    anomaly_model_path=f"{MODEL_DIR}/anomaly_{VERSION}_device_{DEVICE_ID}.joblib"
)


def process_recent_window(recent_df: pd.DataFrame):
    """
    recent_df:
        해당 device_id의 최근 60분 데이터 조회 결과.

    return:
        processed_df: 지도 표시 및 DB 저장용 보정 GPS
        result_df: trip 단위 DTW 이상탐지 결과
    """
    processed_df = GPR.preprocess_and_predict(recent_df)
    result_df = ANOMALY.predict_from_processed_gps(processed_df)
    return processed_df, result_df


# 아래 함수들은 실제 서버 DB 함수로 교체하세요.
def save_raw_gps_to_db(new_gps_row: dict):
    raise NotImplementedError


def load_recent_gps_from_db(device_id: str, minutes: int = 60) -> pd.DataFrame:
    raise NotImplementedError


def save_processed_gps_to_db(latest_processed_row: pd.Series):
    raise NotImplementedError


def save_anomaly_result_to_db(result_df: pd.DataFrame):
    raise NotImplementedError


def on_receive_gps(new_gps_row: dict):
    """
    5분마다 새 GPS 1건이 들어올 때 서버에서 호출하는 예시 함수.

    처리 흐름:
    1. raw GPS DB 저장
    2. 해당 device_id 최근 60분 데이터 조회
    3. GPRRuntime으로 보정 좌표 생성
    4. 최신 Latitude/longitude를 지도 표시용으로 저장/전송
    5. trip이 만들어지면 AnomalyRuntime 결과 저장
    """
    save_raw_gps_to_db(new_gps_row)

    recent_df = load_recent_gps_from_db(
        device_id=str(new_gps_row["device_id"]),
        minutes=60,
    )

    processed_df, result_df = process_recent_window(recent_df)

    latest_processed = processed_df.iloc[-1]
    save_processed_gps_to_db(latest_processed)

    if len(result_df) > 0:
        save_anomaly_result_to_db(result_df)

    return {
        "map_latitude": float(latest_processed["Latitude"]) if pd.notna(latest_processed["Latitude"]) else None,
        "map_longitude": float(latest_processed["longitude"]) if pd.notna(latest_processed["longitude"]) else None,
        "gps_quality": latest_processed.get("gps_quality"),
        "gps_filter_decision": latest_processed.get("gps_filter_decision"),
        "anomaly_result": result_df.to_dict(orient="records"),
    }


if __name__ == "__main__":
    # 로컬 테스트용: sample_recent_gps.csv를 넣고 실행
    recent_df = pd.read_csv("sample_recent_gps.csv")
    processed_df, result_df = process_recent_window(recent_df)

    processed_df.to_csv("processed_gps_output.csv", index=False, encoding="utf-8-sig")
    result_df.to_csv("anomaly_result_output.csv", index=False, encoding="utf-8-sig")

    print("[processed latest]")
    print(processed_df.tail(1))
    print("\n[anomaly result]")
    print(result_df)
