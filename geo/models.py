from django.db import models
from monitoring.models import Protectee


class GeoData(models.Model):
    protectee = models.ForeignKey(
        Protectee,
        on_delete=models.CASCADE,
        related_name="geo_data"
    )
    device_id = models.CharField(max_length=100)
    timestamp = models.DateTimeField()
    pos_success = models.BooleanField()
    longitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    accuracy_h = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "geo_data"
        indexes = [
            models.Index(fields=["device_id", "timestamp"]),
            models.Index(fields=["protectee", "timestamp"]),
            models.Index(fields=["pos_success"]),
        ]
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.device_id} - {self.timestamp} - success={self.pos_success}"


# GEO - 워치7 연동용 테이블
class GeoProcessedData(models.Model):
    protectee = models.ForeignKey(
        Protectee,
        on_delete=models.CASCADE,
        related_name="geo_processed_data"
    )

    device_id = models.CharField(max_length=100)
    timestamp = models.DateTimeField()

    # 원본 GPS
    raw_latitude = models.FloatField(null=True, blank=True)
    raw_longitude = models.FloatField(null=True, blank=True)

    # 최종 보정 GPS
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    # GPS 수집 성공 여부
    pos_success = models.BooleanField(default=True)

    # GPRRuntime 처리 결과 컬럼
    gps_quality = models.CharField(max_length=50, null=True, blank=True)
    gps_filter_decision = models.CharField(max_length=100, null=True, blank=True)
    use_raw_for_gpr = models.BooleanField(null=True, blank=True)
    interp_method = models.CharField(max_length=100, null=True, blank=True)

    predicted_latitude = models.FloatField(null=True, blank=True)
    predicted_longitude = models.FloatField(null=True, blank=True)
    predicted_uncertainty_m = models.FloatField(null=True, blank=True)
    predicted_confidence_level = models.CharField(max_length=50, null=True, blank=True)

    # MOVE / STOP / UNKNOWN 등
    state_primary = models.CharField(max_length=50, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "geo_processed_data"
        indexes = [
            models.Index(fields=["device_id", "timestamp"]),
            models.Index(fields=["protectee", "timestamp"]),
            models.Index(fields=["state_primary"]),
            models.Index(fields=["pos_success"]),
        ]
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.device_id} - {self.timestamp} - lat={self.latitude}, lon={self.longitude}"


# DTW 이상탐지 결과 저장용
class GeoTripAnomalyResult(models.Model):
    class RouteLabel(models.TextChoices):
        KNOWN_NORMAL = "known_normal", "기존 정상 경로"
        UNSEEN_PATH_SAME_OD = "unseen_path_same_od", "같은 OD의 새로운 경로"
        ANOMALY = "anomaly", "이상 경로"

    protectee = models.ForeignKey(
        Protectee,
        on_delete=models.CASCADE,
        related_name="geo_trip_anomaly_results"
    )

    device_id = models.CharField(max_length=100)

    trip_start_time = models.DateTimeField()
    trip_end_time = models.DateTimeField()

    final_route_label = models.CharField(
        max_length=50,
        choices=RouteLabel.choices
    )

    od_key = models.CharField(max_length=100, null=True, blank=True)
    dtw_score = models.FloatField(null=True, blank=True)
    threshold = models.FloatField(null=True, blank=True)

    message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "geo_trip_anomaly_result"
        indexes = [
            models.Index(fields=["device_id", "trip_start_time"]),
            models.Index(fields=["protectee", "trip_start_time"]),
            models.Index(fields=["final_route_label"]),
        ]
        ordering = ["trip_start_time"]

    def __str__(self):
        return f"{self.device_id} - {self.final_route_label} - {self.trip_start_time}"
    