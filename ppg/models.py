from django.db import models

class SensorData(models.Model):
    device_id   = models.CharField(max_length=64, db_index=True)
    timestamp   = models.DateTimeField(db_index=True)
    ppg_green = models.JSONField(default=list)
    ppg_ir = models.JSONField(default=list)
    ppg_red = models.JSONField(default=list)
    predictions = models.JSONField(null=True, blank=True)  # {"model_a":{"prob":..,"label":..}, ...}
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.device_id} @ {self.timestamp.isoformat()}"

