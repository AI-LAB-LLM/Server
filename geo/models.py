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