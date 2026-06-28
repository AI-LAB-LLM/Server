import csv
import time
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring.models import Protectee
from geo.models import GeoProcessedData, GeoTripAnomalyResult
from geo.gpr_services import run_gpr_and_update_latest
from geo.anomaly_services import run_anomaly_for_latest


MODEL_DEVICE_ID = "212e15388f880450"


def parse_float_or_none(value):
    """
    CSV의 latitude / longitude 값 변환.
    빈 문자열이면 None 처리.
    """
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    return float(value)


# def parse_timestamp(value):
#     """
#     CSV timestamp 예시:
#     2026-06-01 11:37
#     """
#     value = str(value).strip()

#     dt = datetime.strptime(value, "%Y-%m-%d %H:%M")

#     if timezone.is_naive(dt):
#         dt = timezone.make_aware(dt, timezone.get_current_timezone())

#     return dt
def parse_timestamp(value):
    value = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",      # 2026-05-04 13:20:22
        "%Y-%m-%d %H:%M",         # 2026-05-04 13:20
        "%Y-%m-%dT%H:%M:%S",      # 2026-05-04T13:20:22
        "%Y-%m-%dT%H:%M",         # 2026-05-04T13:20
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)

            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)

            return dt

        except ValueError:
            continue

    raise ValueError(f"지원하지 않는 timestamp 형식입니다: {value}")


class Command(BaseCommand):
    help = "Read timestamp, latitude, longitude from test_skt.csv and run GPR like live GPS input."

    def add_arguments(self, parser):  
        parser.add_argument(
            "--csv-path",
            type=str,
            default="geo/management/commands/SKT_0619_e.csv",
            help="CSV file path.",
        )

        parser.add_argument(
            "--protectee-id",
            type=int,
            default=2,
            help="Protectee id to save test data.",
        )

        parser.add_argument(
            "--device-id",
            type=str,
            default=MODEL_DEVICE_ID,
            help="Device id for GPR model. Default is model-supported device id.",
        )

        parser.add_argument(
            "--interval",
            type=float,
            default=5,
            help="Seconds between each CSV row insert. Use 300 for real 5-minute interval.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of rows to process. 0 means all rows.",
        )

        parser.add_argument(
            "--run-anomaly",
            action="store_true",
            help="GPR 실행 후 anomaly 이상탐지도 함께 실행한다.",
        )

        parser.add_argument(
            "--clear",
            action="store_true",
            help="실행 전 해당 device_id의 GeoProcessedData / GeoTripAnomalyResult를 삭제한다.",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        protectee_id = options["protectee_id"]
        device_id = options["device_id"]
        interval = options["interval"]
        limit = options["limit"]
        run_anomaly = options["run_anomaly"]
        clear = options["clear"]

        protectee = Protectee.objects.get(id=protectee_id)

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("[LIVE_GPR_CSV_TEST] START"))
        self.stdout.write(f"csv_path={csv_path}")
        self.stdout.write(f"protectee_id={protectee_id}")
        self.stdout.write(f"device_id={device_id}")
        self.stdout.write(f"interval={interval}s")
        self.stdout.write(f"run_anomaly={run_anomaly}")
        self.stdout.write(f"clear={clear}")
        self.stdout.write("=" * 80)

        if clear:
            gpd_count, _ = GeoProcessedData.objects.filter(device_id=device_id).delete()
            anom_count, _ = GeoTripAnomalyResult.objects.filter(device_id=device_id).delete()
            self.stdout.write(
                self.style.WARNING(
                    f"[CLEAR] GeoProcessedData {gpd_count}건, GeoTripAnomalyResult {anom_count}건 삭제됨"
                )
            )

        if device_id != MODEL_DEVICE_ID:
            self.stdout.write(
                self.style.WARNING(
                    f"현재 GPR 모델은 {MODEL_DEVICE_ID} 전용입니다. "
                    f"다른 device_id를 쓰면 GPR이 skipped 될 수 있습니다."
                )
            )

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            required_cols = {"timestamp", "latitude", "longitude"}
            missing_cols = required_cols - set(reader.fieldnames or [])

            if missing_cols:
                raise ValueError(f"CSV에 필요한 컬럼이 없습니다: {missing_cols}")

            processed_count = 0

            for row_index, row in enumerate(reader, start=1):
                if limit > 0 and processed_count >= limit:
                    break

                # 여기서 CSV의 timestamp, latitude, longitude만 사용
                timestamp = parse_timestamp(row["timestamp"])
                latitude = parse_float_or_none(row["latitude"])
                longitude = parse_float_or_none(row["longitude"])

                pos_success = latitude is not None and longitude is not None

                self.stdout.write("-" * 80)
                self.stdout.write(f"[CSV_ROW #{row_index}]")
                self.stdout.write(f"timestamp={timestamp}")
                self.stdout.write(f"raw_latitude={latitude}")
                self.stdout.write(f"raw_longitude={longitude}")
                self.stdout.write(f"pos_success={pos_success}")

                geo_obj = GeoProcessedData.objects.create(
                    protectee=protectee,
                    device_id=device_id,
                    timestamp=timestamp,

                    raw_latitude=latitude,
                    raw_longitude=longitude,

                    # GPR 실행 전에는 최종 좌표를 비워둠
                    latitude=None,
                    longitude=None,

                    pos_success=pos_success,

                    gps_quality=None,
                    gps_filter_decision=None,
                    use_raw_for_gpr=None,
                    interp_method=None,

                    predicted_latitude=None,
                    predicted_longitude=None,
                    predicted_uncertainty_m=None,
                    predicted_confidence_level=None,

                    state_primary=None,
                )

                gpr_result = run_gpr_and_update_latest(geo_obj)

                geo_obj.refresh_from_db()

                self.stdout.write(f"geo_processed_id={geo_obj.id}")
                self.stdout.write(f"final_latitude={geo_obj.latitude}")
                self.stdout.write(f"final_longitude={geo_obj.longitude}")
                self.stdout.write(f"gps_quality={geo_obj.gps_quality}")
                self.stdout.write(f"gps_filter_decision={geo_obj.gps_filter_decision}")
                self.stdout.write(f"use_raw_for_gpr={geo_obj.use_raw_for_gpr}")
                self.stdout.write(f"interp_method={geo_obj.interp_method}")
                self.stdout.write(f"predicted_latitude={geo_obj.predicted_latitude}")
                self.stdout.write(f"predicted_longitude={geo_obj.predicted_longitude}")
                self.stdout.write(f"predicted_uncertainty_m={geo_obj.predicted_uncertainty_m}")
                self.stdout.write(f"predicted_confidence_level={geo_obj.predicted_confidence_level}")
                self.stdout.write(f"state_primary={geo_obj.state_primary}")
                self.stdout.write(f"gpr_result={gpr_result}")

                if run_anomaly:
                    anomaly_result = run_anomaly_for_latest(geo_obj)
                    anomaly_status = anomaly_result.get("anomaly_status")
                    self.stdout.write(f"  [ANOMALY] status={anomaly_status}")
                    if anomaly_status == "skipped":
                        self.stdout.write(f"  [ANOMALY] reason={anomaly_result.get('reason')}")
                    elif anomaly_status in ("saved", "already_saved"):
                        self.stdout.write(f"  [ANOMALY] final_route_label={anomaly_result.get('final_route_label')}")
                        self.stdout.write(f"  [ANOMALY] od_key={anomaly_result.get('od_key')}")
                        self.stdout.write(f"  [ANOMALY] runtime_status={anomaly_result.get('runtime_status')}")
                        self.stdout.write(f"  [ANOMALY] dtw_score={anomaly_result.get('dtw_score')}")
                        self.stdout.write(f"  [ANOMALY] threshold={anomaly_result.get('threshold')}")
                    elif anomaly_status == "error":
                        self.stdout.write(self.style.ERROR(f"  [ANOMALY] error={anomaly_result.get('reason')}"))

                processed_count += 1

                if interval > 0:
                    time.sleep(interval)

        self.stdout.write("=" * 80)
        self.stdout.write(
            self.style.SUCCESS(
                f"[LIVE_GPR_CSV_TEST] FINISHED. processed_count={processed_count}"
            )
        )
        self.stdout.write("=" * 80)