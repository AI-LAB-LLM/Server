from django.db import models
from monitoring.models import Protectee


class ImuData(models.Model):
    protectee = models.ForeignKey(
        Protectee,
        on_delete=models.CASCADE,
        related_name="imu_data",
    )

    # 워치 앱에서 보내는 window_index
    window_index = models.IntegerField()

    # window 시작/끝 시간
    start_timestamp = models.DateTimeField()
    end_timestamp = models.DateTimeField()

    # 25Hz * 12초 = 300 samples
    sample_rate = models.IntegerField(default=25)
    window_sec = models.IntegerField(default=12)

    # 300개 샘플 배열 저장
    samples = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "imu_data"
        ordering = ["start_timestamp"]
        indexes = [
            models.Index(fields=["protectee", "start_timestamp"]),
            models.Index(fields=["protectee", "window_index"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return (
            f"protectee_id={self.protectee_id} | "
            f"window={self.window_index} | "
            f"{self.start_timestamp} ~ {self.end_timestamp} | "
            f"{self.sample_rate}Hz, {self.window_sec}s"
        )