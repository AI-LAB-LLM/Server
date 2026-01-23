from django.db import models

# 보호대상자 기본 정보
class Protectee(models.Model):
    name = models.CharField(max_length=100)
    # 필요하면 전화번호, 주소 등 추가 가능
    device_id=models.CharField(max_length=100, unique=True)
    gender = models.CharField(max_length=20, null=True, blank=True)
    age = models.IntegerField(null=True, blank=True)
    address = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        return f"{self.id} - {self.name}"


# 센서 이벤트 
class Event(models.Model):
    class EventType(models.TextChoices):
        PPG_ALERT = "PPG_ALERT", "PPG 알람"
        IMU_ALERT = "IMU_ALERT", "IMU 알람"
        GEO_ALERT = "GEO_ALERT", "GEO 알람"

    protectee = models.ForeignKey(Protectee, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    event_type = models.CharField(max_length=20, choices=EventType.choices)

    # IMU
    imu_danger_level = models.IntegerField(null=True, blank=True)

    #PPG
    ppg_holding = models.IntegerField(null=True, blank=True)

    # GEO
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)


    class Meta:
        indexes = [
            models.Index(fields=['protectee', 'timestamp']),
            models.Index(fields=['event_type', 'timestamp']),
        ]
        ordering = ['timestamp']